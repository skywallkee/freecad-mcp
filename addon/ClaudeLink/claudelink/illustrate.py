"""Assembly and usage illustrations rendered from the model:
parts + tools sheet, LEGO-style assembly steps, multi-view product shots and cutaways.

Everything is drawn in a scratch document (the user's document is never touched) and composed
with QPainter into PNG pages saved next to the project (images/) or in the cache folder."""
import base64
import math
import os
import re
import time

import FreeCAD
import Part

from .analysis import global_shape, _resolve_objects, _vec
from .server import UserError
from .snapshot import fmt

V = FreeCAD.Vector

PALETTE = [(0.22, 0.47, 0.85), (0.96, 0.56, 0.12), (0.27, 0.66, 0.32), (0.86, 0.22, 0.26), (0.56, 0.37, 0.76),
           (0.12, 0.70, 0.76), (0.95, 0.80, 0.18), (0.58, 0.42, 0.27), (0.91, 0.47, 0.67), (0.47, 0.52, 0.57)]
ARROW_RGB = (0.90, 0.12, 0.10)
SKIP_RE = re.compile(r".*(_collision|_print|_print_supports)$")
# removal directions in order of preference (insertion = the opposite: from above first)
REMOVE_DIRS = [V(0, 0, 1), V(1, 0, 0), V(-1, 0, 0), V(0, -1, 0), V(0, 1, 0), V(0, 0, -1)]
VIEWS = {  # camera LOOK direction
    "iso": (-1, 1, -1), "iso_left": (1, 1, -1), "iso_back": (1, -1, -1), "iso_back_right": (-1, -1, -1),
    "iso_below": (-1, 1, 1), "iso_below_left": (1, 1, 1), "iso_below_back": (1, -1, 1),
    "iso_below_back_right": (-1, -1, 1),
    "front": (0, 1, 0), "back": (0, -1, 0), "right": (-1, 0, 0), "left": (1, 0, 0), "top": (0, 0, -1),
    "bottom": (0, 0, 1),
}
VIEW_TITLES = {"iso": "front-right", "iso_left": "front-left", "iso_back": "back-left",
               "iso_back_right": "back-right", "iso_below": "from below"}


def _doc(name=None):
    from .handlers import _doc as d
    return d(name)


def _obj(doc, key):
    from .handlers import _obj as o
    return o(doc, key)


def _r(x, n=1):
    return fmt(round(x, n))


def _vdir(v):
    if isinstance(v, str):
        if v.startswith("dir:"):
            v = [float(c) for c in v[4:].split(",")]
        elif v in VIEWS:
            v = VIEWS[v]
        else:
            raise UserError("unknown view %r; use %s or dir:x,y,z" % (v, ", ".join(VIEWS)))
    v = V(*v)
    if v.Length < 1e-9:
        raise UserError("zero view direction")
    return v.normalize()


def _udir(v):
    """Insertion direction: 'x', '-z', [x,y,z]."""
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip().lower()
        sign = -1.0 if s.startswith("-") else 1.0
        s = s.lstrip("+-")
        if s not in ("x", "y", "z"):
            raise UserError("dir must be x/y/z with optional sign or [x,y,z], got %r" % v)
        return V(*[sign if a == s else 0.0 for a in "xyz"])
    v = V(*v)
    return v.normalize() if v.Length > 1e-9 else None


def _from_words(u):
    names = {(0, 0, -1): "from above", (0, 0, 1): "from below", (1, 0, 0): "from the left",
             (-1, 0, 0): "from the right", (0, 1, 0): "from the front", (0, -1, 0): "from the back"}
    k = tuple(int(round(c)) for c in u)
    if abs(V(*k).Length - 1) < 1e-6 and (V(*k) - u).Length < 1e-6:
        return names.get(k, "along %s" % _vec(u))
    return "along %s" % _vec(u)


def _cam_rot(d):
    zc = -d                      # camera +Z points back at the viewer
    up = V(0, 0, 1)
    if abs(zc.dot(up)) > 0.999:
        up = V(0, 1, 0)
    yc = (up - zc * up.dot(zc)).normalize()
    xc = yc.cross(zc)
    m = FreeCAD.Matrix(xc.x, yc.x, zc.x, 0, xc.y, yc.y, zc.y, 0, xc.z, yc.z, zc.z, 0, 0, 0, 0, 1)
    return FreeCAD.Rotation(m)


def _default_rgb():
    try:
        u = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/View").GetUnsigned("DefaultShapeColor", 0)
        if u:
            return ((u >> 24) & 255) / 255.0, ((u >> 16) & 255) / 255.0, ((u >> 8) & 255) / 255.0
    except Exception:
        pass
    return None


def _is_grey(rgb):
    if rgb is None:
        return True
    for ref in ((0.8, 0.8, 0.8), _default_rgb()):
        if ref and all(abs(a - b) < 0.03 for a, b in zip(rgb[:3], ref)):
            return True
    return False


def _baked(s):
    """Copy with the Placement baked into the geometry (OCC booleans/offsets behave better)."""
    t = s.copy()
    if t.Placement.isIdentity():
        return t
    m = t.Placement.toMatrix()
    t.Placement = FreeCAD.Placement()
    t.transformShape(m, True)
    return t


def _dims(s):
    b = s.BoundBox
    d = sorted([b.XLength, b.YLength, b.ZLength], reverse=True)
    return " x ".join(_r(x) for x in d) + " mm"


def _key(s):
    b = s.BoundBox
    return (round(s.Volume, 0), round(s.Area, 0),
            tuple(round(x * 2) / 2.0 for x in sorted([b.XLength, b.YLength, b.ZLength])))


def _letters(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


# ------------------------------------------------------------------ assembly planning

class _Planner:
    def __init__(self, insts):
        self.insts = insts
        self.free = {}
        self.touch = {}

    @staticmethod
    def _span(s, u):
        b = s.BoundBox
        vals = [V(x, y, z).dot(u) for x in (b.XMin, b.XMax) for y in (b.YMin, b.YMax) for z in (b.ZMin, b.ZMax)]
        return min(vals), max(vals)

    def free_dirs(self, r, c):
        """Removal directions (indices into REMOVE_DIRS) along which instance r is not blocked by c."""
        k = (r, c)
        if k in self.free:
            return self.free[k]
        rs, cs = self.insts[r]["shape"], self.insts[c]["shape"]
        out = set()
        tol = max(1e-3 * min(rs.Volume, cs.Volume), 1e-4)
        try:
            v0 = rs.common(cs).Volume if rs.BoundBox.intersect(cs.BoundBox) else 0.0
        except Exception:
            v0 = 0.0
        for i, dv in enumerate(REMOVE_DIRS):
            r0, r1 = self._span(rs, dv)
            c0, c1 = self._span(cs, dv)
            if c1 <= r0 + 1e-6:          # c lies entirely behind r
                out.add(i)
                continue
            sb = FreeCAD.BoundBox(rs.BoundBox)
            far = rs.BoundBox
            sb.add(FreeCAD.BoundBox(far.XMin + dv.x * (c1 - r0 + 1), far.YMin + dv.y * (c1 - r0 + 1),
                                    far.ZMin + dv.z * (c1 - r0 + 1), far.XMax + dv.x * (c1 - r0 + 1),
                                    far.YMax + dv.y * (c1 - r0 + 1), far.ZMax + dv.z * (c1 - r0 + 1)))
            cb = FreeCAD.BoundBox(cs.BoundBox)
            cb.enlarge(-0.01)
            if not sb.intersect(cb):
                out.add(i)
                continue
            ext = max(min(r1 - r0, c1 - c0), 0.2)
            reach = c1 - r0 + 0.1
            ts = [min(0.05 * ext, 0.3)]
            t = 0.5 * ext
            while t < reach and len(ts) < 60:
                ts.append(t)
                t += 0.5 * ext
            ts.append(reach)
            ok = True
            for t in ts:
                m = rs.copy()
                m.translate(dv * t)
                try:
                    if not m.BoundBox.intersect(cs.BoundBox):
                        continue
                    if m.common(cs).Volume > v0 + tol:
                        ok = False
                        break
                except Exception:
                    ok = False
                    break
            if ok:
                out.add(i)
        self.free[k] = out
        return out

    def touches(self, r, c):
        k = (min(r, c), max(r, c))
        if k not in self.touch:
            a, b = self.insts[r]["shape"], self.insts[c]["shape"]
            ab, bb = FreeCAD.BoundBox(a.BoundBox), b.BoundBox
            ab.enlarge(0.6)
            t = False
            if ab.intersect(bb):
                try:
                    t = a.distToShape(b)[0] < 0.5
                except Exception:
                    t = False
            self.touch[k] = t
        return self.touch[k]

    def insertable(self, r, placed):
        """Free removal directions of r against all placed parts, best first: the one pointing most
        away from the parts it touches (a lid comes off upwards, not sideways), then REMOVE_DIRS order."""
        dirs = set(range(len(REMOVE_DIRS)))
        for c in placed:
            dirs &= self.free_dirs(r, c)
            if not dirs:
                return []
        near = [c for c in placed if self.touches(r, c)] or list(placed)
        cen = V()
        for c in near:
            cen += self.insts[c]["shape"].BoundBox.Center
        away = self.insts[r]["shape"].BoundBox.Center - cen * (1.0 / len(near))
        if away.Length > 1e-6:
            away.normalize()
        return sorted(dirs, key=lambda i: (-round(REMOVE_DIRS[i].dot(away), 2), i))

    def plan(self, base=None):
        n = len(self.insts)
        if base:
            P = [i for i in range(n) if self.insts[i]["obj"] in base]
            if not P:
                raise UserError("base %r matched none of the parts" % (base,))
        else:
            P = [max(range(n), key=lambda i: self.insts[i]["shape"].Volume)]
        steps = [{"add": list(P), "dirs": {i: None for i in P}}]
        R = [i for i in range(n) if i not in P]
        warn = []
        while R:
            groups = {}
            for i in R:
                groups.setdefault(self.insts[i]["key"], []).append(i)
            cand = []
            for members in groups.values():
                ok = [m for m in members if any(self.touches(m, c) for c in P) and self.insertable(m, P)]
                if ok:
                    cand.append(ok)
            if not cand:
                for members in groups.values():
                    ok = [m for m in members if self.insertable(m, P)]
                    if ok:
                        cand.append(ok)
            if not cand:
                i = max(R, key=lambda j: self.insts[j]["shape"].Volume)
                warn.append("%s can't go in along a straight line once the earlier parts are in place - "
                            "shown in position without an arrow; check the order or the design" % self.insts[i]["obj"])
                steps.append({"add": [i], "dirs": {i: None}})
                P.append(i)
                R.remove(i)
                continue

            def score(ok):
                after = P + ok
                still = sum(1 for r in R if r not in ok and self.insertable(r, after))
                return (still, sum(self.insts[m]["shape"].Volume for m in ok))
            ok = max(cand, key=score)
            dirs = {}
            for m in ok:
                ins = self.insertable(m, P)
                dirs[m] = -REMOVE_DIRS[ins[0]] if ins else None
            steps.append({"add": ok, "dirs": dirs})
            P += ok
            R = [r for r in R if r not in ok]
        return steps, warn


# ------------------------------------------------------------------ scene (scratch document)

class _Scene:
    def __init__(self):
        import FreeCADGui
        self.G = FreeCADGui
        self.doc = FreeCAD.newDocument("Illustration", hidden=False)
        gd = FreeCADGui.getDocument(self.doc.Name)
        vs = gd.mdiViewsOfType("Gui::View3DInventor")
        if not vs:
            raise UserError("could not get a 3D view for the illustration scene")
        self.v = vs[0]
        self.v.setCameraType("Orthographic")
        try:
            self.v.setAnimationEnabled(False)
        except Exception:
            pass
        base = FreeCAD.getUserCachePath() if hasattr(FreeCAD, "getUserCachePath") else os.environ.get("TEMP", ".")
        self.tmp = os.path.join(base, "ClaudeLink")
        os.makedirs(self.tmp, exist_ok=True)
        self.n = 0
        self.line_w = 2.5

    def add(self, shape, name, rgb, lines=True, face_colors=None, transparency=0):
        o = self.doc.addObject("Part::Feature", name)
        o.Shape = shape
        vo = o.ViewObject
        try:
            vo.ShapeColor = tuple(rgb[:3])
            vo.LineColor = (0.12, 0.12, 0.12) if lines else tuple(rgb[:3])
            vo.LineWidth = self.line_w if lines else 1.0
            vo.PointSize = 1.0
            vo.PointColor = vo.LineColor
            _matte(vo, rgb)
            if transparency:
                vo.Transparency = transparency
            if face_colors:
                _face_colours(vo, face_colors)
        except Exception:
            pass
        return o

    def drop(self, objs):
        for o in objs:
            try:
                self.doc.removeObject(o.Name)
            except Exception:
                pass

    def shot(self, names, d, side):
        from PySide import QtGui, QtCore
        names = set(names)
        for o in self.doc.Objects:
            want = o.Name in names
            if bool(o.ViewObject.Visibility) != want:
                o.ViewObject.Visibility = want
        self.v.setCameraOrientation(_cam_rot(d).Q)
        self.v.fitAll()
        self.G.updateGui()
        f = os.path.join(self.tmp, "illustrate_%d.png" % self.n)
        self.n += 1
        px = max(200, min(int(side * 2), 3000))
        self.v.saveImage(f, px, px, "White")
        img = QtGui.QImage(f)
        try:
            os.remove(f)
        except Exception:
            pass
        img = _crop(img)
        return img.scaled(max(1, img.width() // 2), max(1, img.height() // 2),
                          QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)

    def close(self):
        try:
            FreeCAD.closeDocument(self.doc.Name)
        except Exception:
            pass


def _matte(vo, rgb):
    """Low-shine material so faces seen straight on don't wash out to white."""
    try:
        m = FreeCAD.Material()
        m.DiffuseColor = tuple(rgb[:3])
        m.AmbientColor = tuple(c * 0.45 for c in rgb[:3])
        m.SpecularColor = (0.08, 0.08, 0.08)
        m.EmissiveColor = (0.0, 0.0, 0.0)
        m.Shininess = 0.05
        if hasattr(vo, "ShapeAppearance"):
            vo.ShapeAppearance = (m,)
        else:
            vo.ShapeMaterial = m
    except Exception:
        pass


def _face_colours(vo, cols):
    """Per-face colours (cols = [(r,g,b), ...]), opaque, on FreeCAD 1.0 (ShapeAppearance) or older."""
    if hasattr(vo, "ShapeAppearance"):
        try:
            mats = []
            for c in cols:
                m = FreeCAD.Material()
                m.DiffuseColor = tuple(c[:3])
                m.AmbientColor = tuple(x * 0.45 for x in c[:3])
                m.SpecularColor = (0.08, 0.08, 0.08)
                m.Shininess = 0.05
                m.Transparency = 0.0
                mats.append(m)
            vo.ShapeAppearance = tuple(mats)
            return
        except Exception:
            pass
    try:
        vo.DiffuseColor = [tuple(c[:3]) + (0.0,) for c in cols]
    except Exception:
        pass


def _crop(img, pad=8):
    from PySide import QtGui
    img = img.convertToFormat(QtGui.QImage.Format_RGB32)
    w, h = img.width(), img.height()
    try:
        import numpy as np
        bits = img.constBits()
        try:
            bits.setsize(img.bytesPerLine() * h)
        except Exception:
            pass
        a = np.frombuffer(bits, np.uint8, count=img.bytesPerLine() * h).reshape(h, img.bytesPerLine() // 4, 4)
        mask = (a[:, :w, :3] < 246).any(axis=2)
        ys = np.where(mask.any(axis=1))[0]
        xs = np.where(mask.any(axis=0))[0]
        if not len(ys):
            return img
        x0, x1 = max(0, int(xs[0]) - pad), min(w - 1, int(xs[-1]) + pad)
        y0, y1 = max(0, int(ys[0]) - pad), min(h - 1, int(ys[-1]) + pad)
        return img.copy(x0, y0, x1 - x0 + 1, y1 - y0 + 1)
    except Exception:
        return img


# ------------------------------------------------------------------ 2D drawing helpers

def _canvas(w, h):
    from PySide import QtGui
    img = QtGui.QImage(int(w), int(h), QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor(255, 255, 255))
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
    p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
    return img, p


def _font(px, bold=False):
    from PySide import QtGui
    f = QtGui.QFont("Segoe UI")
    f.setPixelSize(max(8, int(px)))
    f.setBold(bold)
    return f


def _text(p, x, y, w, h, s, px, bold=False, rgb=(40, 40, 40), align="left", wrap=False):
    from PySide import QtGui, QtCore
    p.setFont(_font(px, bold))
    p.setPen(QtGui.QColor(*rgb))
    a = {"left": QtCore.Qt.AlignLeft, "center": QtCore.Qt.AlignHCenter, "right": QtCore.Qt.AlignRight}[align]
    flags = a | QtCore.Qt.AlignVCenter | (QtCore.Qt.TextWordWrap if wrap else 0)
    p.drawText(QtCore.QRectF(x, y, w, h), flags, s)


def _place(p, img, x, y, w, h, upscale=1.6):
    """Draw img scaled to fit (x, y, w, h), centred; small images are not blown up more than upscale."""
    from PySide import QtCore
    if img is None or img.width() < 1:
        return
    k = min(w / float(img.width()), h / float(img.height()), upscale)
    dw, dh = img.width() * k, img.height() * k
    p.drawImage(QtCore.QRectF(x + (w - dw) / 2, y + (h - dh) / 2, dw, dh), img)


def _box(p, x, y, w, h, fill=(255, 255, 255), border=(200, 200, 200), radius=10, width=1.5):
    from PySide import QtGui, QtCore
    p.setPen(QtGui.QPen(QtGui.QColor(*border), width))
    p.setBrush(QtGui.QBrush(QtGui.QColor(*fill)))
    p.drawRoundedRect(QtCore.QRectF(x, y, w, h), radius, radius)


def _bubble(p, cx, cy, r, s, rgb=(40, 40, 40)):
    from PySide import QtGui, QtCore
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(QtGui.QBrush(QtGui.QColor(*rgb)))
    p.drawEllipse(QtCore.QPointF(cx, cy), r, r)
    _text(p, cx - r, cy - r, 2 * r, 2 * r, s, r * 1.1, True, (255, 255, 255), "center")


def _encode(img, fmt_="PNG"):
    from PySide import QtCore
    buf = QtCore.QBuffer()
    buf.open(QtCore.QIODevice.WriteOnly)
    img.save(buf, fmt_)
    return bytes(buf.data())


# ------------------------------------------------------------------ fastening notes

def _hole_axes(shape):
    """Small holes of a part (screw clearance / insert / tapped holes): [(point on axis, unit axis, radius)]."""
    out = []
    for f in shape.Faces:
        s = f.Surface
        if not isinstance(s, Part.Cylinder) or not 0.7 <= s.Radius <= 4.5:
            continue
        try:
            u0, u1, v0, v1 = f.ParameterRange
            pm = f.valueAt((u0 + u1) / 2, (v0 + v1) / 2)
            radial = pm - s.Center
            radial = radial - s.Axis * radial.dot(s.Axis)
            if f.normalAt((u0 + u1) / 2, (v0 + v1) / 2).dot(radial) >= 0:
                continue                             # a pin or boss, not a hole
        except Exception:
            continue
        a = V(s.Axis)
        a.normalize()
        out.append((pm - radial, a, s.Radius))
    return out


def _fastenings(insts, new, placed, hardware):
    """Screw positions where a hole of a part added in this step lines up with a hole of a part already placed.
    Returns a sentence for the step caption ('' when nothing lines up)."""
    old = [h for i in placed for h in _hole_axes(insts[i]["shape"])]
    if not old:
        return ""
    seen, radii = [], []
    for i in new:
        for p, a, r in _hole_axes(insts[i]["shape"]):
            for q, b, r2 in old:
                if abs(abs(a.dot(b)) - 1) > 1e-3:
                    continue
                d = q - p
                if (d - a * d.dot(a)).Length > 0.3 or abs(d.dot(a)) > 25:
                    continue
                if any(((p - s0) - a * (p - s0).dot(a)).Length < 0.3 and abs((p - s0).dot(a)) < 12 for s0 in seen):
                    break                            # same screw seen through another face of the hole
                seen.append(p)
                radii.append((r, r2))
                break
    if not seen:
        return ""
    # the screw size shows in its clearance hole (M3 -> d3.4); fall back to the smaller hole of the pair
    clear = {2: 1.2, 2.5: 1.45, 3: 1.7, 4: 2.25, 5: 2.75, 6: 3.3, 8: 4.5}
    size = None
    for pair in radii:
        for r in pair:
            m = min(clear, key=lambda k: abs(clear[k] - r))
            if abs(clear[m] - r) <= 0.2:
                size = m
                break
        if size:
            break
    if size is None:
        r = min(radii[0])
        size = max([m for m in clear if m / 2.0 <= r + 0.3] or [2])
    name = None
    for n, _q in _parse_items(hardware):
        if re.search(r"screw|bolt", n.lower()) and re.search(r"\bm%s(\b|x)" % re.escape(str(size)), n.lower()):
            name = n
            break
    return "fasten with %dx %s" % (len(seen), name or ("M%s screw" % size))


# ------------------------------------------------------------------ tool / hardware icons

TOOL_KEYS = [(r"hex|allen", "hexkey"), (r"screwdriver|phillips|torx|flat.?head", "screwdriver"),
             (r"solder|heat.?set|iron", "iron"), (r"mallet|hammer", "mallet"), (r"clamp|vise|vice|press", "clamp"),
             (r"glue|adhesive|cyano|epoxy|\bca\b", "glue"), (r"plier", "pliers"),
             (r"knife|deburr|cutter|blade|scalpel", "knife"), (r"\bfile\b|sandpaper|sand", "file"),
             (r"wrench|spanner", "wrench"), (r"tweezer", "tweezers")]
HW_KEYS = [(r"insert", "insert"), (r"\bnut", "nut"), (r"washer", "washer"), (r"magnet", "magnet"),
           (r"bearing", "bearing"), (r"spring", "spring"), (r"screw|bolt", "screw")]


def _icon_kind(name, table):
    s = name.lower()
    for pat, k in table:
        if re.search(pat, s):
            return k
    return None


def _icon(p, kind, x, y, s, label=""):
    from PySide import QtGui, QtCore
    P = QtCore.QPointF
    dark = QtGui.QColor(55, 55, 60)
    fill = QtGui.QColor(185, 190, 198)
    accent = QtGui.QColor(220, 90, 40)

    def pt(a, b):
        return P(a * s, b * s)

    def pen(wf, col=dark):
        q = QtGui.QPen(col, max(1.0, wf * s))
        q.setCapStyle(QtCore.Qt.RoundCap)
        q.setJoinStyle(QtCore.Qt.RoundJoin)
        return q

    def rect(a, b, w, h, col=fill, rad=0.03):
        p.setPen(pen(0.025))
        p.setBrush(QtGui.QBrush(col))
        p.drawRoundedRect(QtCore.QRectF(a * s, b * s, w * s, h * s), rad * s, rad * s)

    def line(*pts, wf=0.04, col=dark):
        p.setPen(pen(wf, col))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawPolyline([pt(a, b) for a, b in pts])

    def circle(cx, cy, r, col=fill, wf=0.025):
        p.setPen(pen(wf))
        p.setBrush(QtGui.QBrush(col) if col is not None else QtCore.Qt.NoBrush)
        p.drawEllipse(pt(cx, cy), r * s, r * s)

    p.save()
    p.translate(x, y)
    p.translate(s / 2, s / 2)
    if kind in ("screwdriver", "iron", "knife", "file"):
        p.rotate(-35)
    p.translate(-s / 2, -s / 2)
    if kind == "hexkey":
        line((0.32, 0.12), (0.32, 0.78), (0.74, 0.78), wf=0.09)
    elif kind == "screwdriver":
        line((0.5, 0.5), (0.92, 0.5), wf=0.045)
        rect(0.06, 0.4, 0.44, 0.2, accent, 0.08)
    elif kind == "iron":
        line((0.08, 0.5), (0.0, 0.62), (0.05, 0.8), wf=0.025)
        rect(0.08, 0.42, 0.36, 0.16, accent, 0.06)
        rect(0.44, 0.46, 0.34, 0.08, fill, 0.02)
        line((0.78, 0.5), (0.96, 0.5), wf=0.025)
    elif kind == "mallet":
        rect(0.46, 0.4, 0.08, 0.52, QtGui.QColor(190, 150, 100), 0.03)
        rect(0.18, 0.12, 0.64, 0.28, QtGui.QColor(90, 90, 95), 0.08)
    elif kind == "clamp":
        line((0.74, 0.22), (0.26, 0.22), (0.26, 0.84), (0.74, 0.84), wf=0.08)
        line((0.6, 0.06), (0.6, 0.62), wf=0.04)
        line((0.48, 0.06), (0.72, 0.06), wf=0.05)
        rect(0.5, 0.62, 0.2, 0.07, fill, 0.02)
    elif kind == "glue":
        rect(0.3, 0.42, 0.4, 0.5, QtGui.QColor(240, 240, 240), 0.08)
        poly = QtGui.QPolygonF([pt(0.4, 0.42), pt(0.6, 0.42), pt(0.53, 0.24), pt(0.47, 0.24)])
        p.setPen(pen(0.025))
        p.setBrush(QtGui.QBrush(accent))
        p.drawPolygon(poly)
        line((0.5, 0.24), (0.5, 0.08), wf=0.03)
        rect(0.36, 0.56, 0.28, 0.16, accent, 0.02)
    elif kind == "pliers":
        line((0.22, 0.92), (0.5, 0.5), (0.43, 0.08), wf=0.06)
        line((0.78, 0.92), (0.5, 0.5), (0.57, 0.08), wf=0.06)
        line((0.22, 0.92), (0.36, 0.71), wf=0.11, col=accent)
        line((0.78, 0.92), (0.64, 0.71), wf=0.11, col=accent)
        circle(0.5, 0.5, 0.045)
    elif kind == "knife":
        rect(0.06, 0.43, 0.42, 0.14, accent, 0.05)
        poly = QtGui.QPolygonF([pt(0.48, 0.43), pt(0.94, 0.43), pt(0.48, 0.6)])
        p.setPen(pen(0.025))
        p.setBrush(QtGui.QBrush(fill))
        p.drawPolygon(poly)
    elif kind == "file":
        rect(0.12, 0.42, 0.6, 0.16, fill, 0.02)
        for k in range(8):
            line((0.16 + 0.07 * k, 0.43), (0.2 + 0.07 * k, 0.57), wf=0.012)
        rect(0.72, 0.45, 0.2, 0.1, accent, 0.04)
    elif kind == "wrench":
        line((0.2, 0.82), (0.6, 0.42), wf=0.11)
        circle(0.68, 0.32, 0.17, fill, 0.03)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255)))
        p.drawRect(QtCore.QRectF(0.66 * s, 0.08 * s, 0.14 * s, 0.24 * s))
    elif kind == "tweezers":
        line((0.5, 0.08), (0.38, 0.92), wf=0.04)
        line((0.5, 0.08), (0.62, 0.92), wf=0.04)
    elif kind == "screw":
        rect(0.34, 0.1, 0.32, 0.14, fill, 0.04)
        rect(0.43, 0.24, 0.14, 0.66, fill, 0.0)
        for k in range(9):
            line((0.43, 0.3 + 0.065 * k), (0.57, 0.27 + 0.065 * k), wf=0.012)
    elif kind == "nut":
        poly = QtGui.QPolygonF([pt(0.5 + 0.36 * math.cos(math.radians(60 * k + 30)),
                                   0.5 + 0.36 * math.sin(math.radians(60 * k + 30))) for k in range(6)])
        p.setPen(pen(0.025))
        p.setBrush(QtGui.QBrush(fill))
        p.drawPolygon(poly)
        circle(0.5, 0.5, 0.15, QtGui.QColor(255, 255, 255))
    elif kind == "insert":
        rect(0.34, 0.22, 0.32, 0.56, QtGui.QColor(205, 170, 90), 0.03)
        for k in range(6):
            line((0.34, 0.26 + 0.09 * k), (0.66, 0.34 + 0.09 * k), wf=0.012)
            line((0.34, 0.34 + 0.09 * k), (0.66, 0.26 + 0.09 * k), wf=0.012)
    elif kind == "washer":
        circle(0.5, 0.5, 0.36, fill)
        circle(0.5, 0.5, 0.15, QtGui.QColor(255, 255, 255))
    elif kind == "magnet":
        rect(0.18, 0.36, 0.32, 0.28, QtGui.QColor(215, 60, 50), 0.02)
        rect(0.5, 0.36, 0.32, 0.28, QtGui.QColor(60, 90, 200), 0.02)
        _text(p, 0.18 * s, 0.36 * s, 0.32 * s, 0.28 * s, "N", 0.18 * s, True, (255, 255, 255), "center")
        _text(p, 0.5 * s, 0.36 * s, 0.32 * s, 0.28 * s, "S", 0.18 * s, True, (255, 255, 255), "center")
    elif kind == "bearing":
        circle(0.5, 0.5, 0.4, fill)
        circle(0.5, 0.5, 0.27, QtGui.QColor(255, 255, 255))
        for k in range(8):
            a = math.radians(45 * k)
            circle(0.5 + 0.335 * math.cos(a), 0.5 + 0.335 * math.sin(a), 0.05, QtGui.QColor(120, 120, 125), 0.01)
        circle(0.5, 0.5, 0.13, QtGui.QColor(255, 255, 255))
    elif kind == "spring":
        pts = [(0.5, 0.06)] + [(0.3 if k % 2 else 0.7, 0.12 + 0.08 * k) for k in range(10)] + [(0.5, 0.94)]
        line(*pts, wf=0.035)
    else:
        rect(0.12, 0.12, 0.76, 0.76, QtGui.QColor(240, 240, 242), 0.12)
        ini = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", label)[:2]).upper() or "?"
        _text(p, 0.12 * s, 0.12 * s, 0.76 * s, 0.76 * s, ini, 0.3 * s, True, (80, 80, 85), "center")
    p.restore()


def _parse_items(items, default_qty=1):
    out = []
    for it in items or []:
        if isinstance(it, dict):
            out.append((str(it.get("name", "?")), int(it.get("qty", default_qty))))
        else:
            s = str(it).strip()
            m = re.match(r"^(\d+)\s*[x×]\s+(.*)$", s, re.I)
            out.append((m.group(2), int(m.group(1))) if m else (s, default_qty))
    return out


# ------------------------------------------------------------------ main

def illustrate(kind="all", objects=None, steps=None, base=None, tools=None, hardware=None, title=None,
               view="iso", views=None, cut=None, at=None, width=1000, out_dir=None, doc=None, preview=True,
               preview_width=1000, bought=None):
    """Render assembly / usage illustrations of an assembly (see the MCP tool for the parameters)."""
    if not FreeCAD.GuiUp:
        raise UserError("illustrate needs the FreeCAD GUI (this is a headless instance)")
    from PySide import QtGui
    from .output import _KeepActive, _safe
    t0 = time.time()
    kinds = ["parts", "steps", "views", "cutaway"] if kind in (None, "all") else \
        ([kind] if isinstance(kind, str) else list(kind))
    for k in kinds:
        if k not in ("parts", "steps", "views", "cutaway", "manual"):
            raise UserError("kind must be parts, steps, views, cutaway, manual or all (got %r)" % k)
    if "manual" in kinds:     # the PDF manual needs every page
        kinds = ["parts", "steps", "views", "cutaway", "manual"]
    width = max(500, min(int(width), 2400))
    d = _doc(doc)
    # ---- collect parts
    if objects:
        objs = [_obj(d, k) for k in objects]
    else:
        objs = [o for o in _resolve_objects(d, None) if not SKIP_RE.match(o.Name)]
        names = {o.Name for o in objs}
        for o in d.Objects:
            if o.Name.endswith("_pins_placed") and o.Name not in names:
                objs.append(o)
        placed = {o.Name[:-len("_placed")] for o in objs if o.Name.endswith("_pins_placed")}
        objs = [o for o in objs if o.Name not in placed]
    if not objs:
        raise UserError("no solids to illustrate (pass objects=[...])")
    insts = []
    for o in objs:
        s = _baked(global_shape(o))
        rgb = None
        try:
            rgb = tuple(o.ViewObject.ShapeColor[:3])
        except Exception:
            pass
        label = "pin" if o.Name.endswith("_pins_placed") else o.Label.replace("_", " ")
        sols = s.Solids or [s]
        for so in sols:
            insts.append({"obj": o.Name, "label": label, "shape": so, "rgb": rgb,
                          "key": (_key(so), tuple(round(c, 2) for c in rgb) if rgb else None)})
    # group identical parts; colour defaults
    groups = []
    gidx = {}
    for i, it in enumerate(insts):
        k = it["key"]
        if k not in gidx:
            gidx[k] = len(groups)
            groups.append({"members": [], "label": it["label"]})
        it["g"] = gidx[k]
        groups[gidx[k]]["members"].append(i)
    for g in groups:   # Screw0..Screw3 -> Screw
        objn = {insts[m]["obj"] for m in g["members"]}
        if len(objn) > 1:
            g["label"] = re.sub(r"[_\s-]*\d+$", "", g["label"]) or g["label"]
    pal = 0
    distinct = {tuple(round(c, 2) for c in insts[g["members"][0]]["rgb"] or ()) for g in groups}
    same = len(groups) > 1 and len(distinct) == 1
    for g in groups:
        rgb = insts[g["members"][0]]["rgb"]
        g["auto"] = False
        if same or _is_grey(rgb):
            rgb = PALETTE[pal % len(PALETTE)]
            pal += 1
            g["auto"] = True
        g["rgb"] = rgb
        for m in g["members"]:
            insts[m]["rgb"] = rgb
    # modelled parts that are bought, not printed (O-ring, lens, LED strip, bearing ...)
    bset = set([bought] if isinstance(bought, str) else (bought or []))
    unknown = [b for b in bset if not any(it["obj"] == b or it["label"] == b for it in insts)]
    if unknown:
        raise UserError("bought: unknown part(s) %s" % ", ".join(sorted(unknown)))
    for g in groups:
        g["bought"] = any(insts[m]["obj"] in bset or insts[m]["label"] in bset for m in g["members"])
    allb = FreeCAD.BoundBox()
    for it in insts:
        allb.add(it["shape"].BoundBox)
    diag = allb.DiagonalLength
    title = title or d.Label
    # ---- assembly plan (needed for steps, and to order the parts list)
    lines, warns = [], []
    plan = None
    planner = _Planner(insts)
    if "steps" in kinds or "parts" in kinds:
        if steps:
            plan = []
            placed = []
            for si, st in enumerate(steps):
                add = st.get("add") or []
                add = [add] if isinstance(add, str) else add
                ids = [i for i, it in enumerate(insts) if it["obj"] in add or it["label"] in add]
                missing = [a for a in add if not any(insts[i]["obj"] == a or insts[i]["label"] == a for i in ids)]
                if missing:
                    raise UserError("step %d: unknown part(s) %s" % (si + 1, ", ".join(missing)))
                u = _udir(st.get("dir"))
                dirs = {}
                for i in ids:
                    if u is not None or not placed:
                        dirs[i] = u
                    else:
                        ins = planner.insertable(i, placed)
                        dirs[i] = -REMOVE_DIRS[ins[0]] if ins else None
                        if not ins:
                            warns.append("step %d: %s can't go in straight - no arrow" % (si + 1, insts[i]["obj"]))
                plan.append({"add": ids, "dirs": dirs, "note": st.get("note"), "view": st.get("view")})
                placed += ids
            bnames = [base] if isinstance(base, str) else list(base or [])
            bids = [i for i, it in enumerate(insts) if i not in placed and (it["obj"] in bnames or it["label"] in bnames)]
            if bids and plan:   # the base is already in place in step 1, as in the automatic plan
                plan[0]["add"] = bids + plan[0]["add"]
                plan[0]["dirs"].update({i: None for i in bids})
                placed += bids
            left = [it["obj"] for i, it in enumerate(insts) if i not in placed]
            if left:
                warns.append("not in any step: " + ", ".join(sorted(set(left))))
        else:
            plan, w = planner.plan(base=[base] if isinstance(base, str) else base)
            warns += w
            if len(plan) > 1:   # step 1 = base in place + the first addition
                first = plan.pop(0)
                plan[0]["add"] = first["add"] + plan[0]["add"]
                plan[0]["dirs"].update(first["dirs"])
    # letters in order of first appearance
    order = []
    for st in plan or []:
        for i in st["add"]:
            if insts[i]["g"] not in order:
                order.append(insts[i]["g"])
    order += [gi for gi in range(len(groups)) if gi not in order]
    for n, gi in enumerate(order):
        groups[gi]["letter"] = _letters(n)
    # ---- output folder
    if out_dir:
        folder = out_dir
    elif d.FileName:
        folder = os.path.join(os.path.dirname(d.FileName), "images")
    else:
        base_c = FreeCAD.getUserCachePath() if hasattr(FreeCAD, "getUserCachePath") else os.environ.get("TEMP", ".")
        folder = os.path.join(base_c, "ClaudeLink", "illustrations")
    os.makedirs(folder, exist_ok=True)
    prefix = _safe(title)
    files = []
    pages = []      # (label, QImage) for the preview

    def save(img, fname):
        path = os.path.join(folder, fname)
        img.save(path, "PNG")
        files.append(path)
        return path

    vdef = _vdir(view)
    step_imgs = []
    with _KeepActive():
        sc = _Scene()
        try:
            feats = []
            for i, it in enumerate(insts):
                feats.append(sc.add(it["shape"], "P%d" % i, it["rgb"]))
            all_names = [f.Name for f in feats]
            thumbs = {}
            if "parts" in kinds or "steps" in kinds:
                for gi, g in enumerate(groups):
                    thumbs[gi] = sc.shot([feats[g["members"][0]].Name], vdef, 360)
            # ---------------- parts + tools
            if "parts" in kinds:
                img = _parts_page(groups, order, insts, thumbs, _parse_items(tools), _parse_items(hardware),
                                  title, width)
                save(img, prefix + "_parts.png")
                pages.append(("parts", img))
                nb = sum(len(g["members"]) for g in groups if g.get("bought"))
                lines.append("parts: %d printed (%d unique)%s%s%s" % (
                    len(insts) - nb, len([g for g in groups if not g.get("bought")]),
                    ", %d bought" % nb if nb else "",
                    ", hardware: " + ", ".join("%dx %s" % (q, n) for n, q in _parse_items(hardware)) if hardware else "",
                    ", tools: " + ", ".join(n for n, _ in _parse_items(tools)) if tools else ""))
            # ---------------- steps
            if "steps" in kinds:
                for f in os.listdir(folder):
                    if re.fullmatch(re.escape(prefix) + r"_step\d+\.png", f):
                        try:
                            os.remove(os.path.join(folder, f))
                        except Exception:
                            pass
                placed = []
                for si, st in enumerate(plan):
                    if not st.get("note") and placed:
                        try:
                            st["fasten"] = _fastenings(insts, st["add"], placed, hardware)
                        except Exception:
                            st["fasten"] = ""
                    img, vused, desc = _step_page(sc, si + 1, len(plan), st, placed, insts, groups, feats, thumbs,
                                                  vdef, diag, width)
                    save(img, "%s_step%02d.png" % (prefix, si + 1))
                    step_imgs.append(img)
                    lines.append("  step %d: %s%s" % (si + 1, desc, "" if vused is None else " [view %s]" % vused))
                    placed += st["add"]
                ov = _grid(step_imgs, 2 if len(step_imgs) > 1 else 1, width)
                save(ov, prefix + "_steps.png")
                pages.append(("steps", ov))
                lines.insert(len(lines) - len(plan), "steps: %d" % len(plan))
            # ---------------- views
            if "views" in kinds:
                vlist = views or ["iso", "iso_back", "front", "right", "top"]
                shots = [(v, sc.shot(all_names, _vdir(v), width * 0.7 if k == 0 else width * 0.4))
                         for k, v in enumerate(vlist)]
                img = _views_page(shots, title, allb, width)
                save(img, prefix + "_views.png")
                pages.append(("views", img))
                lines.append("views: " + ", ".join(vlist))
            # ---------------- cutaway
            if "cutaway" in kinds:
                img, desc = _cutaway_page(sc, insts, feats, allb, cut, at, vdef, title, width)
                save(img, prefix + "_cutaway.png")
                pages.append(("cutaway", img))
                lines.append("cutaway: " + desc)
        finally:
            sc.close()
    # ---------------- print list + PDF manual
    if "manual" in kinds:
        img, plines = _print_list_page(groups, order, insts, thumbs, title, width)
        save(img, prefix + "_print_list.png")
        lines += plines
        named = dict(pages)
        pdf = os.path.join(folder, prefix + "_manual.pdf")
        n = _write_manual(pdf, title, [named.get("views"), named.get("parts"), img], step_imgs,
                          [named.get("cutaway")])
        files.append(pdf)
        lines.append("manual: %s (%d A4 pages)" % (os.path.basename(pdf), n))
        pages.insert(2, ("print list", img))
    out = ["%s: illustrations of %d part(s) -> %s" % (d.Name, len(insts), folder)]
    out += lines
    out += ["files: " + ", ".join(os.path.basename(f) for f in files)]
    out += ["warning: " + w for w in warns]
    out.append("(%.1fs)" % (time.time() - t0))
    text = "\n".join(out)
    if not preview:
        return text
    if len(pages) == 1:
        prev = pages[0][1]
    else:
        prev = _stack([p for _, p in pages], preview_width)
    from PySide import QtCore
    if prev.width() > preview_width:
        prev = prev.scaledToWidth(preview_width, QtCore.Qt.SmoothTransformation)
    return {"text": text, "image_b64": base64.b64encode(_encode(prev)).decode("ascii"), "mime": "image/png"}


# ------------------------------------------------------------------ print list + PDF manual

_UP_WORDS = {(0, 0, 1): "as modelled", (0, 0, -1): "upside down", (1, 0, 0): "on its side, +X up",
             (-1, 0, 0): "on its side, -X up", (0, 1, 0): "on its side, +Y up", (0, -1, 0): "on its side, -Y up"}


def _orient_words(up):
    k = tuple(int(round(c)) for c in up)
    if (V(*k) - up).Length < 1e-3 and k in _UP_WORDS:
        return _UP_WORDS[k]
    return "lie on the face facing (%s)" % ", ".join(_r(c, 2) for c in up)


def _print_list_page(groups, order, insts, thumbs, title, width):
    from .printprep import _rank, _prof
    from .strength import mech
    from .splitting import _colour_name
    from . import profile
    prof = _prof()
    pf = profile.load()
    mat = profile.material(None)[0]
    dens = mech(mat)[1].get("density", 1.25)
    rows = []
    total = 0.0
    for gi in order:
        g = groups[gi]
        if g.get("bought"):
            continue
        sh = insts[g["members"][0]]["shape"]
        q = len(g["members"])
        if g["label"] == "pin":
            how, sup = "lying flat on its flat (as laid out in the _pins object)", 0.0
        else:
            try:
                best = _rank(sh, 50.0, 10.0, None, prof)[0]
                how, sup = _orient_words(best["up"]), best["support_vol"]
            except Exception:
                how, sup = "as modelled", 0.0
        grams = sh.Volume / 1000.0 * dens * q
        total += grams
        colour = "any" if g.get("auto") else (_colour_name(g["rgb"]) or "as shown")
        rows.append((gi, g["letter"], g["label"], q, colour, g["rgb"], how, "supports" if sup >= 1 else "no supports",
                     _dims(sh), grams))
    row_h = 74
    H = int(60 + 40 + 34 + len(rows) * row_h + 150)
    img, p = _canvas(width, H)
    _header(p, width, title, "print list")
    settings = "%s, nozzle %s mm, layer %s mm, %s" % (pf.get("printer") or "printer", fmt(pf.get("nozzle", 0.4)),
                                                     fmt(pf.get("layer", 0.2)), mat)
    _text(p, 24, 56, width - 48, 30, settings, 15, False, (90, 90, 95))
    cols = [(24, 90, "part"), (114, 230, "name"), (344, 50, "qty"), (394, 120, "colour"), (514, 300, "orientation"),
            (814, 170, "size / weight")]
    y = 100
    for x, w, hd in cols:
        _text(p, x * width / 1000.0, y, w * width / 1000.0, 28, hd, 14, True, (110, 110, 115))
    y += 34
    from PySide import QtGui, QtCore
    for k, (gi, letter, label, q, colour, rgb, how, sup, dims, grams) in enumerate(rows):
        yy = y + k * row_h
        if k % 2 == 0:
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QBrush(QtGui.QColor(246, 246, 248)))
            p.drawRect(QtCore.QRectF(12, yy, width - 24, row_h))
        sx = width / 1000.0
        _bubble(p, 24 * sx + 12, yy + row_h / 2, 12, letter, (45, 45, 50))
        _place(p, thumbs.get(gi), 24 * sx + 28, yy + 6, 90 * sx - 30, row_h - 12)
        _text(p, 114 * sx, yy, 230 * sx, row_h, label, 15, True, (45, 45, 50), wrap=True)
        _text(p, 344 * sx, yy, 50 * sx, row_h, "%dx" % q, 18, True, (200, 60, 30) if q > 1 else (60, 60, 65))
        if colour != "any":
            p.setPen(QtGui.QPen(QtGui.QColor(120, 120, 120), 1))
            p.setBrush(QtGui.QBrush(QtGui.QColor(*[int(c * 255) for c in rgb[:3]])))
            p.drawRoundedRect(QtCore.QRectF(394 * sx, yy + row_h / 2 - 10, 20, 20), 4, 4)
        _text(p, 394 * sx + (26 if colour != "any" else 0), yy, 94 * sx, row_h, colour, 14, False, (60, 60, 65))
        _text(p, 514 * sx, yy + 6, 300 * sx - 10, row_h / 2, how, 14, False, (50, 50, 55), wrap=True)
        _text(p, 514 * sx, yy + row_h / 2, 300 * sx, row_h / 2 - 6, sup, 13, False,
              (60, 140, 70) if sup == "no supports" else (190, 110, 30))
        _text(p, 814 * sx, yy + 6, 170 * sx, row_h / 2, dims, 13, False, (70, 70, 75))
        _text(p, 814 * sx, yy + row_h / 2, 170 * sx, row_h / 2 - 6, "%s g%s" % (_r(grams, 0), " total" if q > 1 else ""),
              13, False, (70, 70, 75))
    yy = y + len(rows) * row_h + 16
    _text(p, 24, yy, width - 48, 30, "total about %s g of %s (solid parts; less with sparse infill)" % (
        _r(total, 0), mat), 15, True, (50, 50, 55))
    tips = ("Orientation is the one that needs the least support material. Print fit tests first if the clearances "
            "are new; press-fit parts should go together by hand or with a soft mallet.")
    _text(p, 24, yy + 34, width - 48, 80, tips, 14, False, (100, 100, 105), wrap=True)
    p.end()
    plines = ["print list: %d unique part(s), ~%s g %s; %d need supports" % (
        len(rows), _r(total, 0), mat, sum(1 for r in rows if r[7] == "supports"))]
    return img, plines


def _write_manual(path, title, front, steps, back):
    """A4 PDF: cover (views), parts + tools, print list, steps two per page, cutaway."""
    from PySide import QtCore
    prev = None

    def quiet(mode, ctx, msg):      # Qt warns "Device has no metric information" for QPdfWriter: harmless
        if "no metric information" not in str(msg) and prev is not None:
            prev(mode, ctx, msg)
    try:
        prev = QtCore.qInstallMessageHandler(quiet)
    except Exception:
        prev = None
    try:
        return _write_manual_pdf(path, title, front, steps, back)
    finally:
        try:
            QtCore.qInstallMessageHandler(prev)
        except Exception:
            pass


def _write_manual_pdf(path, title, front, steps, back):
    from PySide import QtGui, QtCore
    w = QtGui.QPdfWriter(path)
    try:
        w.setPageSize(QtGui.QPageSize(QtGui.QPageSize.A4))
    except Exception:
        pass
    w.setResolution(150)
    try:
        w.setPageMargins(QtCore.QMarginsF(0, 0, 0, 0))
    except Exception:
        pass
    w.setTitle(title)
    sheets = [[i] for i in front if i is not None]
    for k in range(0, len(steps), 2):
        sheets.append(steps[k:k + 2])
    sheets += [[i] for i in back if i is not None]
    p = QtGui.QPainter(w)
    p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    # QPdfWriter reports no metrics before the first paint on some Qt builds: use A4 at 150 dpi
    try:
        r = w.pageLayout().fullRectPixels(150)
        W, H = r.width(), r.height()
    except Exception:
        W, H = 0, 0
    if W < 100 or H < 100:
        W, H = 1240, 1754
    m = int(W * 0.06)
    for n, imgs in enumerate(sheets):
        if n:
            w.newPage()
        y = m
        avail = H - 2 * m - 40
        each = (avail - (len(imgs) - 1) * 30) / float(len(imgs))
        for im in imgs:
            k = min((W - 2 * m) / float(im.width()), each / float(im.height()))
            dw, dh = im.width() * k, im.height() * k
            p.drawImage(QtCore.QRectF(m + (W - 2 * m - dw) / 2, y, dw, dh), im)
            y += dh + 30
        # footer drawn into an image: text painted straight onto QPdfWriter floods the log with metric warnings
        fi, fp = _canvas(W - 2 * m, 30)
        _text(fp, 0, 0, W - 2 * m, 30, "%s  -  %d / %d" % (title, n + 1, len(sheets)), 18, False, (150, 150, 155),
              "right")
        fp.end()
        p.drawImage(QtCore.QRectF(m, H - m, W - 2 * m, 30), fi)
    p.end()
    return len(sheets)


# ------------------------------------------------------------------ pages

def _header(p, w, s, sub=""):
    _text(p, 24, 10, w - 48, 40, s, 26, True, (30, 30, 35))
    if sub:
        _text(p, 24, 10, w - 48, 40, sub, 16, False, (120, 120, 125), "right")


def _parts_page(groups, order, insts, thumbs, tools, hardware, title, width):
    n = len(order)
    cols = max(2, min(5, int(math.ceil(math.sqrt(n * 1.4)))))
    tw = (width - 48 - (cols - 1) * 14) / float(cols)
    th = tw * 0.95
    rows = int(math.ceil(n / float(cols)))
    extra = [("Hardware", hardware, HW_KEYS), ("Tools", tools, TOOL_KEYS)]
    ex_h = 0
    icon = min(110, tw * 0.55)
    for _, items, _k in extra:
        if items:
            per = max(1, int((width - 48) // (icon + 130)))
            ex_h += 44 + int(math.ceil(len(items) / float(per))) * (icon + 40)
    H = int(60 + 34 + rows * (th + 14) + ex_h + 20)
    img, p = _canvas(width, H)
    nb = sum(len(groups[gi]["members"]) for gi in order if groups[gi].get("bought"))
    _header(p, width, title, "%d parts to print%s" % (len(insts) - nb, ", %d to buy" % nb if nb else ""))
    y = 60
    _text(p, 24, y, 400, 30, "Parts" if nb else "Printed parts", 19, True, (70, 70, 75))
    y += 34
    for k, gi in enumerate(order):
        g = groups[gi]
        x = 24 + (k % cols) * (tw + 14)
        yy = y + (k // cols) * (th + 14)
        _box(p, x, yy, tw, th, (250, 250, 251), (215, 215, 220))
        _place(p, thumbs.get(gi), x + 10, yy + 34, tw - 20, th - 34 - 48)
        _bubble(p, x + 22, yy + 22, 14, g["letter"], (45, 45, 50))
        q = len(g["members"])
        _text(p, x + 42, yy + 6, tw - 52, 32, "%dx" % q, 22, True, (200, 60, 30) if q > 1 else (60, 60, 65), "right")
        sh = insts[g["members"][0]]["shape"]
        _text(p, x + 10, yy + th - 48, tw - 20, 24, g["label"], 15, True, (50, 50, 55), "center")
        if g.get("bought"):
            _text(p, x + 10, yy + th - 26, tw - 20, 20, "buy - " + _dims(sh), 12, True, (30, 110, 170), "center")
        else:
            _text(p, x + 10, yy + th - 26, tw - 20, 20, _dims(sh), 12, False, (120, 120, 125), "center")
    y += rows * (th + 14)
    for head, items, table in extra:
        if not items:
            continue
        y += 8
        _text(p, 24, y, 400, 30, head, 19, True, (70, 70, 75))
        y += 36
        per = max(1, int((width - 48) // (icon + 130)))
        for k, (name, q) in enumerate(items):
            x = 24 + (k % per) * (icon + 130)
            yy = y + (k // per) * (icon + 40)
            _box(p, x, yy, icon + 120, icon + 30, (250, 250, 251), (215, 215, 220))
            _icon(p, _icon_kind(name, table), x + 8, yy + 6, icon, name)
            _text(p, x + icon + 8, yy + 8, 104, 34, "%dx" % q, 22, True, (60, 60, 65))
            _text(p, x + icon + 8, yy + 42, 106, icon - 20, name, 13, False, (60, 60, 65), wrap=True)
        y += int(math.ceil(len(items) / float(per))) * (icon + 40)
    p.end()
    return img


def _arrow(p0, p1, r):
    dv = p1 - p0
    L = dv.Length
    if L < 1e-6:
        return None
    u = dv / L
    head = min(L * 0.45, r * 6)
    parts = [Part.makeCone(r * 2.6, 0, head, p0 + u * (L - head), u)]
    if L - head > 1e-3:
        parts.append(Part.makeCylinder(r, L - head, p0, u))
    return parts[0] if len(parts) == 1 else parts[0].fuse(parts[1])


def _pick_view(vdef, us):
    def score(d):
        sc_ = 10.0
        for u in us:
            c = u.dot(d)
            perp = math.sqrt(max(0.0, 1 - c * c))
            sc_ = min(sc_, perp + (0.6 if c > -0.05 else 0.0))
        return sc_
    if not us or score(vdef) >= 1.1:
        return vdef, None
    best = max((k for k in VIEWS if k.startswith("iso")),
               key=lambda k: score(_vdir(k)) - (0.25 if "below" in k else 0.0))
    return _vdir(best), best


def _step_page(sc, num, total, st, placed, insts, groups, feats, thumbs, vdef, diag, width):
    H = int(width * 0.72)
    new = st["add"]
    tmp = []
    anchor = placed + [i for i in new if st["dirs"].get(i) is None]
    visible = [feats[i].Name for i in placed]
    moving = [i for i in new if st["dirs"].get(i) is not None and anchor]
    us = [st["dirs"][i] for i in moving]
    if st.get("view"):
        d, vname = _vdir(st["view"]), st["view"]
    else:
        d, vname = _pick_view(vdef, us)
    r_arrow = max(0.3, min(3.0, diag * 0.006))
    for i in new:
        if i not in moving:
            visible.append(feats[i].Name)
            continue
        u = st["dirs"][i]
        s = insts[i]["shape"]
        lo, hi = _Planner._span(s, u)
        ext = hi - lo
        gap = max(0.15 * diag, min(0.4 * ext, 0.3 * diag), 6.0)
        dist = ext + gap
        ex = s.copy()
        ex.translate(-u * dist)
        tmp.append(sc.add(ex, "X%d" % i, insts[i]["rgb"]))
        bb = s.BoundBox
        small = min(bb.XLength, bb.YLength, bb.ZLength)
        r = max(0.25, min(r_arrow, 0.12 * small))
        c = bb.Center
        cp = c - u * c.dot(u)
        # a wide part would hide an arrow drawn through its middle: put it beside the part, camera side
        w = -d - u * (-d).dot(u)
        if w.Length > 1e-6:
            w.normalize()
            wlo, whi = _Planner._span(s, w)
            if min(whi - wlo, max(_Planner._span(s, w.cross(u))[1] - _Planner._span(s, w.cross(u))[0], 0)) > 1.5 * gap:
                cp = cp + w * (whi - c.dot(w) + 4 * r)
        m = gap * 0.12
        a = _arrow(cp + u * (lo - gap + m), cp + u * (lo - m), r)
        if a is not None:
            tmp.append(sc.add(a, "A%d" % i, ARROW_RGB, lines=False))
    visible += [o.Name for o in tmp]
    shot = sc.shot(visible, d, H)
    sc.drop(tmp)
    # description
    by_g = {}
    for i in new:
        by_g.setdefault(insts[i]["g"], []).append(i)
    desc_parts = []
    for gi, ids in by_g.items():
        g = groups[gi]
        u = st["dirs"].get(ids[0])
        what = "%s%s %s" % ("%dx " % len(ids) if len(ids) > 1 else "", g["letter"], g["label"])
        if u is None or not anchor:
            desc_parts.append("place " + what)
        else:
            desc_parts.append("add %s %s" % (what, _from_words(u)))
    desc = ", ".join(desc_parts)
    desc = desc[:1].upper() + desc[1:]
    if st.get("fasten"):
        desc += ", then " + st["fasten"]
    # page
    img, p = _canvas(width, H)
    from PySide import QtGui
    _text(p, 20, 6, 120, 80, str(num), 64, True, (30, 30, 35))
    # callout with the parts of this step
    cw, ch = 118, 112
    cx, cy = 130, 14
    nbox = len(by_g)
    _box(p, cx, cy, nbox * (cw + 8) + 8, ch + 16, (255, 252, 235), (200, 180, 120), 8, 2)
    for k, (gi, ids) in enumerate(by_g.items()):
        x = cx + 8 + k * (cw + 8)
        _place(p, thumbs.get(gi), x + 4, cy + 22, cw - 8, ch - 26)
        _text(p, x, cy + 4, cw, 22, "%dx" % len(ids), 18, True, (200, 60, 30) if len(ids) > 1 else (60, 60, 65), "right")
        _bubble(p, x + 13, cy + 17, 11, groups[gi]["letter"], (45, 45, 50))
    _text(p, width - 140, 10, 120, 30, "%d / %d" % (num, total), 15, False, (150, 150, 155), "right")
    top = cy + ch + 26
    _place(p, shot, 20, top, width - 40, H - top - 50, upscale=2.5)
    note = st.get("note") or desc
    _text(p, 24, H - 46, width - 48, 40, note, 17, False, (60, 60, 65), wrap=True)
    from PySide import QtCore
    p.setPen(QtGui.QColor(225, 225, 228))
    p.setBrush(QtCore.Qt.NoBrush)
    p.drawRect(0, 0, width - 1, H - 1)
    p.end()
    return img, vname, desc


def _views_page(shots, title, allb, width):
    hero = shots[0]
    rest = shots[1:]
    hw = int(width * 0.58)
    H = int(60 + hw + 50)
    img, p = _canvas(width, H)
    _header(p, width, title, "overall %s x %s x %s mm (X x Y x Z)" % (_r(allb.XLength), _r(allb.YLength),
                                                                     _r(allb.ZLength)))
    _box(p, 20, 60, hw, hw, (255, 255, 255), (225, 225, 228), 8)
    _place(p, hero[1], 30, 70, hw - 20, hw - 40, upscale=3)
    _text(p, 30, 60 + hw - 30, hw - 20, 24, VIEW_TITLES.get(hero[0], hero[0]), 14, False, (130, 130, 135), "center")
    if rest:
        cols = 2 if len(rest) > 1 else 1
        rows = int(math.ceil(len(rest) / float(cols)))
        x0 = 20 + hw + 14
        cw = (width - x0 - 20 - (cols - 1) * 10) / float(cols)
        chh = (hw - (rows - 1) * 10) / float(rows)
        for k, (nm, im) in enumerate(rest):
            x = x0 + (k % cols) * (cw + 10)
            y = 60 + (k // cols) * (chh + 10)
            _box(p, x, y, cw, chh, (255, 255, 255), (225, 225, 228), 8)
            _place(p, im, x + 8, y + 8, cw - 16, chh - 34, upscale=3)
            _text(p, x, y + chh - 26, cw, 22, VIEW_TITLES.get(nm, nm), 13, False, (130, 130, 135), "center")
    p.end()
    return img


def _cut_colour(rgb):
    lum = 0.3 * rgb[0] + 0.59 * rgb[1] + 0.11 * rgb[2]
    if lum < 0.18:
        return tuple(min(1.0, c * 0.5 + 0.45) for c in rgb[:3])
    return tuple(c * 0.55 for c in rgb[:3])


def _cutaway_page(sc, insts, feats, allb, cut, at, vdef, title, width):
    c = allb.Center
    big = allb.DiagonalLength * 2 + 10
    # 1) quarter cut facing the main view: remove the quadrant towards the camera
    sx = 1 if vdef.x < 0 else -1      # camera sits on +x when it looks along -x
    sy = 1 if vdef.y < 0 else -1
    q = Part.makeBox(big, big, big, V(c.x if sx > 0 else c.x - big, c.y if sy > 0 else c.y - big, c.z - big / 2))
    # 2) straight half cut through `cut` axis (default y: look at the section from the front)
    ax = (cut or "y").lower().lstrip("+-")
    if ax not in ("x", "y", "z"):
        raise UserError("cut must be x, y or z")
    pos = float(at) if at is not None else getattr(c, ax)
    lo = V(c.x - big / 2, c.y - big / 2, c.z - big / 2)
    setattr(lo, ax, pos - big)
    half = Part.makeBox(big, big, big, lo)
    nrm = V(*[1.0 if a == ax else 0.0 for a in "xyz"])
    look = {"x": V(1, 0, 0), "y": V(0, 1, 0), "z": V(0, 0, -1)}[ax]
    if ax == "z":       # removing the lower half would look from below; remove the top half instead
        lo = V(c.x - big / 2, c.y - big / 2, pos)
        half = Part.makeBox(big, big, big, lo)
        look = V(0, 0, -1)
    planes_q = [(V(1, 0, 0), c.x), (V(0, 1, 0), c.y)]
    planes_h = [(nrm, pos)]
    shots = []
    for tool, planes, dview, label in ((q, planes_q, vdef, "quarter cutaway"),
                                        (half, planes_h, look, "section at %s = %s mm" % (ax.upper(), _r(pos)))):
        tmp = []
        for i, it in enumerate(insts):
            try:
                s = it["shape"].cut(tool)
            except Exception:
                continue
            if s.isNull() or s.Volume < 1e-6:
                continue
            rgb = it["rgb"]
            cc = _cut_colour(rgb)
            fc = []
            for f in s.Faces:
                on = False
                try:
                    if f.Surface.__class__.__name__ == "Plane":
                        n = f.Surface.Axis
                        for pn, pv in planes:
                            if abs(abs(n.dot(pn)) - 1) < 1e-6 and abs(f.CenterOfMass.dot(pn) - pv) < 1e-4:
                                on = True
                except Exception:
                    pass
                fc.append(tuple(cc if on else rgb[:3]))
            tmp.append(sc.add(s, "C%d" % i, rgb, face_colors=fc))
        shots.append((label, sc.shot([o.Name for o in tmp], dview, width * 0.55)))
        sc.drop(tmp)
    hw = int((width - 54) / 2)
    H = int(60 + hw + 40)
    img, p = _canvas(width, H)
    _header(p, width, title, "cut faces shaded darker")
    for k, (label, im) in enumerate(shots):
        x = 20 + k * (hw + 14)
        _box(p, x, 60, hw, hw, (255, 255, 255), (225, 225, 228), 8)
        _place(p, im, x + 10, 70, hw - 20, hw - 44, upscale=3)
        _text(p, x, 60 + hw - 30, hw, 24, label, 14, False, (110, 110, 115), "center")
    p.end()
    return img, "quarter cut from %s + %s" % ("the main view", shots[1][0])


def _grid(imgs, cols, width):
    from PySide import QtCore
    if not imgs:
        img, p = _canvas(10, 10)
        p.end()
        return img
    cw = (width - (cols + 1) * 10) / float(cols)
    scaled = [i.scaledToWidth(int(cw), QtCore.Qt.SmoothTransformation) for i in imgs]
    rows = int(math.ceil(len(scaled) / float(cols)))
    rh = max(s.height() for s in scaled)
    img, p = _canvas(width, rows * (rh + 10) + 10)
    for k, s in enumerate(scaled):
        p.drawImage(int(10 + (k % cols) * (cw + 10)), int(10 + (k // cols) * (rh + 10)), s)
    p.end()
    return img


def _stack(imgs, width):
    from PySide import QtCore
    scaled = [i.scaledToWidth(width, QtCore.Qt.SmoothTransformation) if i.width() != width else i for i in imgs]
    H = sum(s.height() for s in scaled) + 8 * (len(scaled) - 1)
    img, p = _canvas(width, H)
    y = 0
    for s in scaled:
        p.drawImage(0, y, s)
        y += s.height() + 8
    p.end()
    return img


METHODS = {"illustrate": illustrate}
