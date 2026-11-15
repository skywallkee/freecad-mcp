"""Phase 2: measurement and verification handlers (all compact text; section adds an image)."""
import base64
import itertools
import math
import random
import time

import FreeCAD
import Part

from . import cl
from .server import UserError
from .snapshot import fmt

V = FreeCAD.Vector


# ------------------------------------------------------------------ refs

def _doc(name=None):
    from .handlers import _doc as d
    return d(name)


def _obj(doc, key):
    from .handlers import _obj as o
    return o(doc, key)


def global_shape(o):
    """Shape in global coordinates (accounts for parent App::Part / Body placements)."""
    s = getattr(o, "Shape", None)
    if s is None or s.isNull():
        raise UserError("%s has no shape" % o.Name)
    s = s.copy()
    try:
        gp = o.getGlobalPlacement()
        if hasattr(o, "Placement") and not gp.isSame(o.Placement, 1e-9):
            s.Placement = gp
    except Exception:
        pass
    return s


def _ref(d, ref):
    """'Obj' or 'Obj:selector' -> (shape, label, note)."""
    name, _, selector = ref.partition(":")
    o = _obj(d, name.strip())
    s = global_shape(o)
    if not selector.strip():
        return s, o.Name, ""
    items = cl.sel(s, selector)
    if not items:
        raise UserError("selector %r matched nothing on %s" % (selector, o.Name))
    sub = items[0]
    nm = cl.name_of(s, sub) or sub.ShapeType
    note = "" if len(items) == 1 else " (first of %d matches)" % len(items)
    return sub, "%s.%s" % (o.Name, nm), note


def _vec(v):
    return "(%s, %s, %s)" % (fmt(v.x), fmt(v.y), fmt(v.z))


def _resolve_objects(d, objects, solids_only=True):
    if objects:
        return [_obj(d, k) for k in objects]
    out = []
    for o in d.Objects:
        s = getattr(o, "Shape", None)
        try:
            vis = o.Visibility
        except Exception:
            vis = True
        if not vis or s is None or s.isNull():
            continue
        if o.TypeId in ("App::Part", "App::DocumentObjectGroup", "App::Origin", "App::Line", "App::Plane"):
            continue
        if solids_only and not s.Solids:
            continue
        out.append(o)
    return out


# ------------------------------------------------------------------ measure

def _describe(sub, label):
    t = sub.ShapeType
    lines = ["%s: %s" % (label, t)]
    if t == "Vertex":
        lines[0] += " at " + _vec(sub.Point)
    elif t == "Edge":
        c = sub.Curve
        lines[0] += " %s, length %s" % (type(c).__name__, fmt(sub.Length))
        if isinstance(c, Part.Circle):
            lines.append("  radius %s (dia %s), centre %s, axis %s" % (fmt(c.Radius), fmt(2 * c.Radius), _vec(c.Center), _vec(c.Axis)))
        elif isinstance(c, (Part.Line, Part.LineSegment)):
            a, b = sub.Vertexes[0].Point, sub.Vertexes[-1].Point
            lines.append("  from %s to %s, dir %s" % (_vec(a), _vec(b), _vec((b - a).normalize())))
        else:
            lines.append("  ends %s, %s" % (_vec(sub.Vertexes[0].Point), _vec(sub.Vertexes[-1].Point)))
    elif t == "Face":
        srf = sub.Surface
        lines[0] += " %s, area %s, centre %s" % (type(srf).__name__, fmt(sub.Area), _vec(sub.CenterOfMass))
        if isinstance(srf, Part.Plane):
            lines.append("  normal %s" % _vec(cl._normal(sub)))
        for attr in ("Radius", "MajorRadius", "MinorRadius", "SemiAngle"):
            if hasattr(srf, attr):
                v = getattr(srf, attr)
                if attr == "SemiAngle":
                    v = math.degrees(v)
                lines.append("  %s %s" % (attr, fmt(v)))
        if hasattr(srf, "Axis") and not isinstance(srf, Part.Plane):
            ctr = getattr(srf, "Center", None) or getattr(srf, "Location", None)
            lines.append("  axis %s through %s" % (_vec(srf.Axis), _vec(ctr) if ctr is not None else "-"))
        b = sub.BoundBox
        lines.append("  bbox z %s..%s" % (fmt(b.ZMin), fmt(b.ZMax)))
    else:
        b = sub.BoundBox
        lines[0] += ", %d solids, bbox min %s max %s" % (len(sub.Solids), _vec(V(b.XMin, b.YMin, b.ZMin)), _vec(V(b.XMax, b.YMax, b.ZMax)))
        if sub.Solids:
            lines.append("  volume %s, area %s, centre of mass %s" % (fmt(sub.Volume), fmt(sub.Area),
                                                                       _vec(sub.Solids[0].CenterOfMass if len(sub.Solids) == 1 else b.Center)))
    return lines


def _axis_of(s):
    if s.ShapeType == "Face" and hasattr(s.Surface, "Axis") and not isinstance(s.Surface, Part.Plane):
        srf = s.Surface
        return srf.Axis, (getattr(srf, "Center", None) or srf.Location), getattr(srf, "Radius", None)
    if s.ShapeType == "Edge" and isinstance(s.Curve, Part.Circle):
        return s.Curve.Axis, s.Curve.Center, s.Curve.Radius
    return None


def measure(a, b=None, doc=None):
    d = _doc(doc)
    sa, la, na = _ref(d, a)
    if b is None:
        out = _describe(sa, la)
        out[0] += na
        return "\n".join(out)
    sb, lb, nb = _ref(d, b)
    dist, pts, _ = sa.distToShape(sb)
    p1, p2 = pts[0]
    lines = ["%s%s <-> %s%s" % (la, na, lb, nb),
             "min distance %s  (%s -> %s)" % (fmt(dist), _vec(p1), _vec(p2))]
    if dist < 1e-7:
        try:
            com = sa.common(sb)
            if com.Solids and com.Volume > 1e-6:
                lines.append("OVERLAP volume %s" % fmt(com.Volume))
            else:
                lines.append("touching / intersecting at surface")
        except Exception:
            lines.append("touching or overlapping")
    if sa.ShapeType == "Face" and sb.ShapeType == "Face" and isinstance(sa.Surface, Part.Plane) and isinstance(sb.Surface, Part.Plane):
        n1, n2 = cl._normal(sa), cl._normal(sb)
        ang = math.degrees(n1.getAngle(n2))
        lines.append("angle between normals %s deg" % fmt(ang))
        if min(ang, 180 - ang) < 1e-4:
            lines.append("parallel planes, separation %s" % fmt(abs((sb.CenterOfMass - sa.CenterOfMass).dot(n1))))
    if sa.ShapeType == "Edge" and sb.ShapeType == "Edge":
        da, db = cl._edge_dir(sa), cl._edge_dir(sb)
        if da and db:
            lines.append("angle %s deg" % fmt(math.degrees(da.getAngle(db))))
    xa, xb = _axis_of(sa), _axis_of(sb)
    if xa and xb:
        (ax1, c1, r1), (ax2, c2, r2) = xa, xb
        par = abs(abs(ax1.dot(ax2)) - 1) < 1e-6
        if par:
            w = c2 - c1
            off = (w - ax1 * w.dot(ax1)).Length
            lines.append("axes parallel, axis offset %s" % fmt(off))
            if r1 and r2:
                ro, ri = max(r1, r2), min(r1, r2)
                s = "radii %s / %s -> radial clearance %s (diametral %s) when centred" % (
                    fmt(r1), fmt(r2), fmt(ro - ri), fmt(2 * (ro - ri)))
                if off < ro:  # only meaningful when one is (nearly) inside the other
                    s += "; min %s at this offset" % fmt(ro - ri - off)
                    s += _fit_class(ro - ri)
                lines.append(s)
        else:
            lines.append("axes angle %s deg" % fmt(math.degrees(ax1.getAngle(ax2))))
    return "\n".join(lines)


# ------------------------------------------------------------------ section

_COLORS = [(31, 119, 180), (255, 127, 14), (44, 160, 44), (214, 39, 40), (148, 103, 189),
           (140, 86, 75), (227, 119, 194), (127, 127, 127), (188, 189, 34), (23, 190, 207)]


def _plane_basis(plane):
    p = plane.strip().lower()
    if p in ("z", "xy"):
        return V(0, 0, 1), V(1, 0, 0), V(0, 1, 0), "X", "Y"
    if p in ("y", "xz"):
        return V(0, 1, 0), V(1, 0, 0), V(0, 0, 1), "X", "Z"
    if p in ("x", "yz"):
        return V(1, 0, 0), V(0, 1, 0), V(0, 0, 1), "Y", "Z"
    n = V(*(float(c) for c in p.split(","))).normalize()
    ref = V(0, 0, 1) if abs(n.z) < 0.9 else V(1, 0, 0)
    u = ref.cross(n).normalize()
    v = n.cross(u).normalize()
    return n, u, v, "u", "v"


def _nice_step(span, target=8):
    raw = span / target
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for m in (1, 2, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def section(objects=None, plane="z", offset=0.0, doc=None, size=560, image=True, region=None,
            gaps=True, max_gap=5.0, angle=None):
    d = _doc(doc)
    objs = _resolve_objects(d, objects)
    if not objs:
        raise UserError("no objects to section")
    n, u, v, ul, vl = _plane_basis(plane)
    if angle:  # rotate the in-plane basis (e.g. to align with a feature)
        r = FreeCAD.Rotation(n, angle)
        u, v = r.multVec(u), r.multVec(v)
    origin = n * offset
    lines = ["section plane %s offset %s (normal %s; image axes %s right, %s up)" % (plane, fmt(offset), _vec(n), ul, vl)]
    secs = []
    for o in objs:
        s = global_shape(o)
        try:
            wires = s.slice(n, offset)
        except Exception as e:
            lines.append("%s: slice failed (%s)" % (o.Name, e))
            continue
        if not wires:
            lines.append("%s: not cut by this plane" % o.Name)
            continue
        face = None
        try:
            face = Part.makeFace(wires, "Part::FaceMakerBullseye")
            if not face.isValid():
                face = None
        except Exception:
            face = None
        pts_all = []
        polys = []
        for w in wires:
            defl = max(w.BoundBox.DiagonalLength / 1500.0, 0.002)
            try:
                pts = w.discretize(Deflection=defl)
            except Exception:
                pts = [vx.Point for vx in w.Vertexes]
            poly = [((p - origin).dot(u), (p - origin).dot(v)) for p in pts]
            polys.append(poly)
            pts_all += poly
        us = [p[0] for p in pts_all]
        vs = [p[1] for p in pts_all]
        bb = (min(us), min(vs), max(us), max(vs))
        area = face.Area if face is not None else None
        secs.append({"name": o.Name, "wires": wires, "face": face, "polys": polys, "bb": bb, "area": area})
        lines.append("%s: %d loops, area %s, %s %s..%s, %s %s..%s" % (
            o.Name, len(wires), fmt(area) if area is not None else "?", ul, fmt(bb[0]), fmt(bb[2]), vl, fmt(bb[1]), fmt(bb[3])))
    marks = []
    if gaps and len(secs) > 1:
        rows = []
        for A, B in itertools.combinations(secs, 2):
            a = A["face"] or Part.makeCompound(A["wires"])
            b = B["face"] or Part.makeCompound(B["wires"])
            try:
                if A["face"] is not None and B["face"] is not None:
                    com = A["face"].common(B["face"])
                    if com.Area > 1e-6:
                        where = []
                        for f in sorted(com.Faces, key=lambda f: -f.Area)[:4]:
                            cb = f.CenterOfMass
                            cu, cv = (cb - origin).dot(u), (cb - origin).dot(v)
                            marks.append((cu, cv, cu, cv, "overlap"))
                            where.append("%s %s,%s %s (area %s)" % (ul, fmt(cu), vl, fmt(cv), fmt(f.Area)))
                        rows.append((0, "%s x %s: OVERLAP area %s in this plane at %s" % (
                            A["name"], B["name"], fmt(com.Area), "; ".join(where))))
                        continue
                dist, pts, _ = a.distToShape(b)
                if dist <= max_gap:
                    p1, p2 = pts[0]
                    rows.append((dist, "%s - %s: gap %s near %s" % (A["name"], B["name"], fmt(dist), _vec((p1 + p2) * 0.5))))
                    marks.append(((p1 - origin).dot(u), (p1 - origin).dot(v), (p2 - origin).dot(u), (p2 - origin).dot(v), fmt(dist)))
            except Exception as e:
                rows.append((1e9, "%s - %s: gap check failed (%s)" % (A["name"], B["name"], e)))
        rows.sort(key=lambda r: r[0])
        if rows:
            lines.append("in-plane gaps (<= %s):" % fmt(max_gap))
            lines += ["  " + r[1] for r in rows[:12]]
    if not image or not secs:
        return "\n".join(lines)
    if not FreeCAD.GuiUp:
        lines.append("(no image: headless)")
        return "\n".join(lines)
    data = _draw_section(secs, marks, size, region, ul, vl)
    return {"text": "\n".join(lines), "image_b64": base64.b64encode(data).decode("ascii"), "mime": "image/png"}


def _draw_section(secs, marks, size, region, ul, vl):
    from PySide import QtCore, QtGui
    if region:
        u0, v0, u1, v1 = region
    else:
        u0 = min(s["bb"][0] for s in secs); v0 = min(s["bb"][1] for s in secs)
        u1 = max(s["bb"][2] for s in secs); v1 = max(s["bb"][3] for s in secs)
    span = max(u1 - u0, v1 - v0, 1e-6)
    pad = span * 0.06
    cu, cv = (u0 + u1) / 2, (v0 + v1) / 2
    half = span / 2 + pad
    u0, u1, v0, v1 = cu - half, cu + half, cv - half, cv + half
    W = H = int(size)
    m_l, m_b, m_t, m_r = 46, 30, 8, 8
    pw, ph = W - m_l - m_r, H - m_t - m_b
    sc = min(pw / (u1 - u0), ph / (v1 - v0))

    def X(uu):
        return m_l + (uu - u0) * sc

    def Y(vv):
        return m_t + ph - (vv - v0) * sc

    img = QtGui.QImage(W, H, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor(255, 255, 255))
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    font = QtGui.QFont()
    font.setPixelSize(11)
    p.setFont(font)
    step = _nice_step(u1 - u0)
    p.setPen(QtGui.QPen(QtGui.QColor(232, 232, 232), 1))
    g = math.ceil(u0 / step) * step
    while g <= u1:
        p.drawLine(QtCore.QPointF(X(g), m_t), QtCore.QPointF(X(g), m_t + ph))
        g += step
    g = math.ceil(v0 / step) * step
    while g <= v1:
        p.drawLine(QtCore.QPointF(m_l, Y(g)), QtCore.QPointF(m_l + pw, Y(g)))
        g += step
    p.setPen(QtGui.QColor(90, 90, 90))
    g = math.ceil(u0 / step) * step
    while g <= u1:
        p.drawText(QtCore.QPointF(X(g) - 12, H - 16), fmt(round(g, 6)))
        g += step
    g = math.ceil(v0 / step) * step
    while g <= v1:
        p.drawText(QtCore.QPointF(4, Y(g) + 4), fmt(round(g, 6)))
        g += step
    p.drawText(QtCore.QPointF(W - 60, H - 3), "%s (mm) ->" % ul)
    p.drawText(QtCore.QPointF(4, m_t + 10), vl)
    p.setClipRect(QtCore.QRectF(m_l, m_t, pw, ph))
    for i, s in enumerate(secs):
        r, gg, b = _COLORS[i % len(_COLORS)]
        path = QtGui.QPainterPath()
        path.setFillRule(QtCore.Qt.OddEvenFill)
        for poly in s["polys"]:
            if len(poly) < 2:
                continue
            path.moveTo(X(poly[0][0]), Y(poly[0][1]))
            for uu, vv in poly[1:]:
                path.lineTo(X(uu), Y(vv))
            path.closeSubpath()
        p.fillPath(path, QtGui.QColor(r, gg, b, 90))
        p.setPen(QtGui.QPen(QtGui.QColor(r, gg, b), 1.3))
        p.drawPath(path)
    p.setPen(QtGui.QPen(QtGui.QColor(220, 0, 0), 1.6))
    for (a1, b1, a2, b2, lab) in marks[:8]:
        p.drawLine(QtCore.QPointF(X(a1), Y(b1)), QtCore.QPointF(X(a2), Y(b2)))
        p.drawEllipse(QtCore.QPointF(X(a1), Y(b1)), 2.5, 2.5)
        p.drawText(QtCore.QPointF(X(a2) + 4, Y(b2) - 4), lab)
    p.setClipping(False)
    y = m_t + 14
    for i, s in enumerate(secs):
        r, gg, b = _COLORS[i % len(_COLORS)]
        p.fillRect(QtCore.QRectF(W - m_r - 110, y - 9, 10, 10), QtGui.QColor(r, gg, b))
        p.setPen(QtGui.QColor(40, 40, 40))
        p.drawText(QtCore.QPointF(W - m_r - 96, y), s["name"][:16])
        y += 14
    p.drawText(QtCore.QPointF(m_l + 4, H - 3), "grid %s mm" % fmt(step))
    p.end()
    buf = QtCore.QBuffer()
    buf.open(QtCore.QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


# ------------------------------------------------------------------ geometry check

def check_geometry(objects=None, doc=None, bop=True):
    d = _doc(doc)
    objs = _resolve_objects(d, objects, solids_only=False)
    if not objs:
        return "no visible shapes to check"
    lines, bad = [], 0
    for o in objs:
        try:
            s = global_shape(o)
        except UserError as e:
            lines.append("%s: %s" % (o.Name, e))
            continue
        issues = []
        if not s.isValid():
            issues.append("isValid=False")
        try:
            s.check(bop)
        except Exception as e:
            msg = " ".join(str(e).split())
            issues.append("check: " + (msg[:240] + "..." if len(msg) > 240 else msg))
        open_sh = [sh for sh in s.Shells if not sh.isClosed()]
        if open_sh:
            issues.append("%d open shell(s)" % len(open_sh))
        if s.Solids:
            if s.Volume < 0:
                issues.append("negative volume (inside-out)")
            if len(s.Solids) > 1:
                issues.append("%d separate solids" % len(s.Solids))
        elif s.Faces:
            issues.append("no solid (faces only)")
        try:
            tol = s.getTolerance(1)
            if tol > 0.01:
                issues.append("max tolerance %s" % fmt(tol))
        except Exception:
            pass
        tiny_e = sum(1 for e in s.Edges if e.Length < 1e-3)
        if tiny_e:
            issues.append("%d tiny edges (<0.001)" % tiny_e)
        tiny_f = sum(1 for f in s.Faces if f.Area < 1e-4)
        if tiny_f:
            issues.append("%d tiny faces" % tiny_f)
        summary = "%d solid%s, %d faces" % (len(s.Solids), "" if len(s.Solids) == 1 else "s", len(s.Faces))
        if issues:
            bad += 1
            lines.append("%s: PROBLEMS (%s): %s" % (o.Name, summary, "; ".join(issues)))
        else:
            lines.append("%s: ok (%s)" % (o.Name, summary))
    lines.insert(0, "%d checked, %d with problems%s" % (len(objs), bad, " (BOP check on)" if bop else ""))
    return "\n".join(lines)


# ------------------------------------------------------------------ fit / interference

def _fit_class(gap):
    """' -> snug fit (PETG, measured)' for small gaps, using the print profile."""
    if gap is None or gap < 1e-6 or gap > 1.0:
        return ""
    try:
        from . import profile
        c = profile.classify(gap)
        return (" -> " + c) if c else ""
    except Exception:
        return ""


def fit(objects=None, doc=None, min_clearance=0.0, max_gap=5.0):
    d = _doc(doc)
    objs = _resolve_objects(d, objects)
    if len(objs) < 2:
        raise UserError("need at least 2 solid objects")
    shapes = [(o.Name, global_shape(o)) for o in objs]
    rows, far = [], 0
    t0 = time.time()
    for (na, a), (nb, b) in itertools.combinations(shapes, 2):
        ba, bb = a.BoundBox, b.BoundBox
        ga = FreeCAD.BoundBox(ba)
        ga.enlarge(max_gap)
        if not ga.intersect(bb):
            far += 1
            continue
        try:
            com = a.common(b)
            vol = com.Volume if com.Solids else 0.0
        except Exception as e:
            rows.append((-1, "%s x %s: boolean failed (%s)" % (na, nb, e)))
            continue
        if vol > 1e-6:
            c = com.BoundBox
            rows.append((-vol, "%s x %s: INTERFERENCE vol %s, region %s..%s" % (
                na, nb, fmt(vol), _vec(V(c.XMin, c.YMin, c.ZMin)), _vec(V(c.XMax, c.YMax, c.ZMax)))))
            continue
        dist, pts, _ = a.distToShape(b)
        if dist > max_gap:
            far += 1
            continue
        p1, p2 = pts[0]
        where = _vec((p1 + p2) * 0.5)
        cls = _fit_class(dist)
        if dist < 1e-6:
            rows.append((0, "%s - %s: contact (0 gap) at %s" % (na, nb, where)))
        elif dist < min_clearance:
            rows.append((dist, "%s - %s: TIGHT gap %s < %s at %s%s" % (na, nb, fmt(dist), fmt(min_clearance), where, cls)))
        else:
            rows.append((dist, "%s - %s: gap %s at %s%s" % (na, nb, fmt(dist), where, cls)))
    rows.sort(key=lambda r: r[0])
    n_int = sum(1 for r in rows if r[0] < 0)
    head = "%d parts, %d pairs: %d interfering, %d within %s, %d farther (%.1fs)" % (
        len(objs), len(shapes) * (len(shapes) - 1) // 2, n_int, len(rows) - n_int, fmt(max_gap), far, time.time() - t0)
    return "\n".join([head] + [r[1] for r in rows])


# ------------------------------------------------------------------ mesh-based analysis

def _defl(shape):
    """Mesh chord tolerance: fine enough for sub-0.01 mm thickness error on small parts."""
    return min(max(shape.BoundBox.DiagonalLength / 20000.0, 0.002), 0.05)


def _mesh(shape):
    import MeshPart
    return MeshPart.meshFromShape(Shape=shape, LinearDeflection=_defl(shape), AngularDeflection=0.1)


def _facets(mesh):
    pts, tris = mesh.Topology
    out = []
    for i, (a, b, c) in enumerate(tris):
        pa, pb, pc = pts[a], pts[b], pts[c]
        cr = (pb - pa).cross(pc - pa)
        area = cr.Length / 2
        if area < 1e-12:
            continue
        out.append((i, (pa + pb + pc) * (1.0 / 3), cr * (1.0 / (2 * area)), area, (pa, pb, pc), (a, b, c)))
    return out


def _regions(facets):
    """Group facets into connected regions (shared mesh vertices). Returns lists of facets."""
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for f in facets:
        a, b, c = f[5]
        ra = find(a)
        for v in (b, c):
            rv = find(v)
            if rv != ra:
                parent[rv] = ra
    groups = {}
    for f in facets:
        groups.setdefault(find(f[5][0]), []).append(f)
    return sorted(groups.values(), key=lambda g: -sum(f[3] for f in g))


def _thickness_samples(mesh, facets, n_samples, seed=1, dots=False):
    """(distance to the opposite wall, sample point) per ray; dots=True adds the dot product of the two
    surface normals (-1 = parallel wall, towards 0 = a ridge/wedge such as a thread crest)."""
    rnd = random.Random(seed)
    normal_of = {f[0]: f[2] for f in facets} if dots else None
    weights = [f[3] for f in facets]
    picks = rnd.choices(range(len(facets)), weights=weights, k=min(n_samples, 50000))
    res, miss = [], 0
    for k in picks:
        c, nrm = facets[k][1], facets[k][2]
        start = c - nrm * 1e-4
        # only accept exit facets (normal within 90 deg of the ray), so the entry facet is skipped
        hit = mesh.nearestFacetOnRay((start.x, start.y, start.z), (-nrm.x, -nrm.y, -nrm.z), math.pi / 2 - 0.01)
        if not hit:
            miss += 1
            continue
        hp = V(*list(hit.values())[0])
        dist = (hp - c).Length
        if dist < 2e-4:  # grazed its own facet
            miss += 1
            continue
        if dots:
            n2 = normal_of.get(list(hit.keys())[0])
            res.append((dist, c, nrm.dot(n2) if n2 is not None else -1.0))
        else:
            res.append((dist, c))
    return res, miss


def _clusters(points, radius, limit=6):
    """Greedy clusters of (value, point) sorted by value; returns [(min_value, centre, count)]."""
    out = []
    for val, p in sorted(points, key=lambda x: x[0]):
        for cl_ in out:
            if (cl_[1] - p).Length <= radius:
                cl_[2] += 1
                break
        else:
            out.append([val, p, 1])
    return out[:limit]


def wall_thickness(object, min_wall=1.0, samples=3000, doc=None):
    d = _doc(doc)
    o = _obj(d, object)
    s = global_shape(o)
    if not s.Solids:
        raise UserError("%s has no solid" % o.Name)
    t0 = time.time()
    mesh = _mesh(s)
    facets = _facets(mesh)
    res, miss = _thickness_samples(mesh, facets, samples)
    if not res:
        return "no thickness samples (mesh rays missed; normals may be inverted)"
    vals = sorted(r[0] for r in res)
    q = lambda f: vals[min(len(vals) - 1, int(f * len(vals)))]
    thin = [r for r in res if r[0] < min_wall]
    lines = ["%s wall thickness, %d ray samples, mesh tol %s (%.1fs): min %s, p5 %s, median %s, max %s" % (
        o.Name, len(vals), fmt(_defl(s)), time.time() - t0, fmt(vals[0]), fmt(q(0.05)), fmt(q(0.5)), fmt(vals[-1]))]
    lines.append("%d samples (%.1f%%) below %s" % (len(thin), 100.0 * len(thin) / len(vals), fmt(min_wall)))
    for val, ctr, cnt in _clusters(thin, max(2 * min_wall, 2.0)):
        lines.append("  thin %s near %s (%d samples)" % (fmt(val), _vec(ctr), cnt))
    if miss:
        lines.append("(%d rays missed)" % miss)
    return "\n".join(lines)


_UP = {"+z": V(0, 0, 1), "z": V(0, 0, 1), "-z": V(0, 0, -1), "+x": V(1, 0, 0), "x": V(1, 0, 0),
       "-x": V(-1, 0, 0), "+y": V(0, 1, 0), "y": V(0, 1, 0), "-y": V(0, -1, 0)}


def printability(object, nozzle=None, layer=None, max_overhang=45.0, bed=None, up="+z",
                 doc=None, thin_samples=2000):
    from . import profile
    prof = profile.load()
    nozzle = float(nozzle if nozzle is not None else prof["nozzle"])
    layer = float(layer if layer is not None else prof["layer"])
    bed = bed or prof["bed"]
    d = _doc(doc)
    o = _obj(d, object)
    s = global_shape(o)
    if not s.Solids:
        raise UserError("%s has no solid" % o.Name)
    t0 = time.time()
    upv = _UP.get(up.lower())
    if upv is None:
        upv = V(*(float(c) for c in up.split(","))).normalize()
    rot = FreeCAD.Rotation(upv, V(0, 0, 1))
    s = s.copy()
    s.transformShape(FreeCAD.Placement(V(), rot).toMatrix())
    b = s.BoundBox
    dims = sorted([b.XLength, b.YLength]) + [b.ZLength]
    lines = ["%s printability (up=%s, nozzle %s, layer %s, overhang limit %s deg from vertical)" % (
        o.Name, up, fmt(nozzle), fmt(layer), fmt(max_overhang))]
    bx, by, bz = bed
    fits = (b.XLength <= bx and b.YLength <= by) or (b.XLength <= by and b.YLength <= bx)
    lines.append("size %sx%sx%s on bed %sx%sx%s: %s" % (fmt(b.XLength), fmt(b.YLength), fmt(b.ZLength), bx, by, bz,
                                                        "fits" if fits and b.ZLength <= bz else "DOES NOT FIT"))
    if len(s.Solids) > 1:
        lines.append("WARNING %d separate bodies" % len(s.Solids))
    mesh = _mesh(s)
    facets = _facets(mesh)
    zmin = b.ZMin
    lim = -math.sin(math.radians(max_overhang))
    bed_area, over, ceil_area = 0.0, [], 0.0
    for f in facets:
        _, c, nrm, area, pts, _idx = f
        if nrm.z < -0.999 and max(p.z for p in pts) - zmin < 1e-3:
            bed_area += area
            continue
        if nrm.z < lim and c.z > zmin + layer:
            over.append(f)
            if nrm.z < -0.98:
                ceil_area += area
    tot = sum(f[3] for f in facets)
    lines.append("bed contact area %s mm2 (footprint %s mm2)%s" % (
        fmt(bed_area), fmt(b.XLength * b.YLength),
        "  WARNING small contact - may tip/detach" if bed_area < 0.1 * b.XLength * b.YLength else ""))
    oa = sum(f[3] for f in over)
    lines.append("overhang area %s mm2 (%.1f%% of surface), of which %s mm2 near-horizontal (bridges/ceilings)" % (
        fmt(oa), 100 * oa / tot if tot else 0, fmt(ceil_area)))
    back = rot.inverted()  # report locations in model coordinates

    def model(p):
        return back.multVec(p)

    if over:
        regs = [g for g in _regions(over) if sum(f[3] for f in g) >= 0.5]
        for g in regs[:6]:
            a = sum(f[3] for f in g)
            ctr = sum((f[1] * f[3] for f in g), V()) * (1.0 / a)
            zs = [f[1].z for f in g]
            lines.append("  overhang region %s mm2 centred %s, %s above bed" % (
                fmt(a), _vec(model(ctr)), fmt(min(zs) - zmin) if max(zs) - min(zs) < 1e-3 else "%s..%s" % (fmt(min(zs) - zmin), fmt(max(zs) - zmin))))
        if len(regs) > 6:
            lines.append("  ... %d smaller overhang regions" % (len(regs) - 6))
    res, miss = _thickness_samples(mesh, facets, thin_samples)
    if res:
        too_thin = [r for r in res if r[0] < nozzle]
        weak = [r for r in res if nozzle <= r[0] < 2 * nozzle]
        lines.append("walls: min %s; %d samples < nozzle %s (won't print), %d < %s (single-line, weak)" % (
            fmt(min(r[0] for r in res)), len(too_thin), fmt(nozzle), len(weak), fmt(2 * nozzle)))
        for val, ctr, cnt in _clusters(too_thin, 2.0, 4):
            lines.append("  too thin %s near %s" % (fmt(val), _vec(model(ctr))))
    lines.append("(%.1fs; locations in model coordinates)" % (time.time() - t0))
    return "\n".join(lines)


METHODS = {
    "measure": measure,
    "section": section,
    "check_geometry": check_geometry,
    "fit": fit,
    "wall_thickness": wall_thickness,
    "printability": printability,
}
