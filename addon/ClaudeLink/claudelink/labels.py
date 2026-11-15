"""Embossed / engraved text, version stamps and QR codes on planar faces, with a printability check of the
actual letter strokes (measured with a 2D inward offset, not guessed from the font size)."""
import datetime
import math
import os
import time

import FreeCAD
import Part

from . import cl
from .analysis import global_shape
from .server import UserError
from .snapshot import fmt

V = FreeCAD.Vector
FONT_DIRS = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"), "/usr/share/fonts/truetype/dejavu",
             "/System/Library/Fonts/Supplemental", "/Library/Fonts"]
FONTS = {"sans": ["arialbd.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"],
         "sans-regular": ["arial.ttf", "DejaVuSans.ttf", "Arial.ttf"],
         "mono": ["consolab.ttf", "DejaVuSansMono-Bold.ttf", "Courier New Bold.ttf"],
         "condensed": ["bahnschrift.ttf", "DejaVuSansCondensed-Bold.ttf", "Arial Narrow Bold.ttf"]}


def _doc(name=None):
    from .handlers import _doc as d
    return d(name)


def _obj(doc, key):
    from .handlers import _obj as o
    return o(doc, key)


def _f(x, n=2):
    return fmt(round(float(x), n))


def _font(font):
    if font and os.path.isfile(font):
        return font
    for name in FONTS.get(font or "sans", [font] if font else FONTS["sans"]):
        for dd in FONT_DIRS:
            p = os.path.join(dd, name)
            if os.path.isfile(p):
                return p
    raise UserError("font %r not found; give a .ttf path or one of %s" % (font, ", ".join(FONTS)))


def text_faces(text, size, font=None, spacing=0.0):
    """Planar faces of `text` in the XY plane, centred on the origin; size = cap height (mm)."""
    path = _font(font)
    lines = text.split("\n")
    faces = []
    y = 0.0
    for line in lines:
        if not line.strip():
            y -= size * 1.5
            continue
        chars = Part.makeWireString(line, path, size, spacing)
        lf = []
        for ch in chars:
            wires = [w if isinstance(w, Part.Wire) else Part.Wire(w) for w in ch]
            wires = [w for w in wires if w.isClosed()]
            if not wires:
                continue
            try:
                f = Part.makeFace(wires, "Part::FaceMakerBullseye")
            except Exception:
                continue
            lf += [x for x in f.Faces if x.Area > 1e-6]
        if lf:
            c = Part.makeCompound(lf)
            b = c.BoundBox
            c.translate(V(-b.Center.x, y - b.YMin, 0))
            faces += c.Faces
        y -= size * 1.5
    if not faces:
        raise UserError("no printable glyphs in %r" % text)
    comp = Part.makeCompound(faces)
    b = comp.BoundBox
    comp.translate(V(-b.Center.x, -b.Center.y, 0))
    return comp


def _stroke_check(comp, min_stroke):
    """Glyph faces that vanish or split when shrunk by min_stroke/2: strokes narrower than min_stroke."""
    bad = 0
    for f in comp.Faces:
        try:
            g = f.makeOffset2D(-min_stroke / 2.0, 0, False, False, False)
            if g.isNull() or g.Area < 0.05 * f.Area:
                bad += 1
        except Exception:
            bad += 1
    return bad


def _frame(face, xdir=None):
    """(centre, normal(out), x, y) of a planar face; text reads left-to-right seen from outside."""
    if not isinstance(face.Surface, Part.Plane):
        raise UserError("labels need a planar face")
    c = face.CenterOfMass
    u, v = face.Surface.parameter(c)
    n = V(face.normalAt(u, v)).normalize()
    if xdir is not None:
        from .fasteners import _axis
        x = _axis(xdir)
    elif abs(n.z) > 0.9:
        x = V(1, 0, 0)
    else:
        x = V(0, 0, 1).cross(n)
    x = (x - n * x.dot(n)).normalize()
    y = n.cross(x)
    return c, n, x, y


def _place2d(comp, c, n, x, y, lift):
    m = FreeCAD.Matrix(x.x, y.x, n.x, 0, x.y, y.y, n.y, 0, x.z, y.z, n.z, 0, 0, 0, 0, 1)
    t = comp.copy()
    t.transformShape(m, True)
    t.translate(c + n * lift)
    return t


def _qr_faces(text, module):
    from .qr import qr_matrix
    mat = qr_matrix(text)
    n = len(mat)
    rects = []
    for r, row in enumerate(mat):
        x0 = None
        for cidx in range(n + 1):
            dark = cidx < n and row[cidx]
            if dark and x0 is None:
                x0 = cidx
            elif not dark and x0 is not None:
                e = 0.01      # modules touching only at a corner would make a non-manifold solid: overlap them
                rects.append(Part.makePlane((cidx - x0) * module + 2 * e, module + 2 * e,
                                            V(x0 * module - e, (n - 1 - r) * module - e, 0)))
                x0 = None
    f = rects[0].fuse(rects[1:]) if len(rects) > 1 else rects[0]
    try:
        f = f.removeSplitter()
    except Exception:
        pass
    comp = Part.makeCompound(f.Faces)
    comp.translate(V(-n * module / 2.0, -n * module / 2.0, 0))
    return comp, n


def label(object, text=None, at="faces >Z", mode="emboss", size=None, depth=None, font=None, xdir=None,
          offset=None, kind="text", module=None, up="auto", name=None, doc=None):
    """See the MCP tool."""
    from . import profile
    from .output import auto_checkpoint
    d = _doc(doc)
    o = _obj(d, object)
    s = global_shape(o)
    if not s.Placement.isIdentity():
        mm = s.Placement.toMatrix()
        s.Placement = FreeCAD.Placement()
        s.transformShape(mm, True)
    prof = profile.load()
    nozzle, layer = prof["nozzle"], prof["layer"]
    t0 = time.time()
    notes = []
    if kind == "stamp":
        # on the face that sits on the bed in the print orientation
        from .ready import _orient
        upv, _ = _orient(s, up, prof)
        downs = [f for f in s.Faces if isinstance(f.Surface, Part.Plane) and cl._normal(f) is not None
                 and cl._normal(f).dot(upv) < -0.999]
        if not downs:
            raise UserError("no flat face on the bed side for a stamp (up=%s)" % up)
        lowest = min(f.CenterOfMass.dot(upv) for f in downs)
        face = max([f for f in downs if abs(f.CenterOfMass.dot(upv) - lowest) < 1e-3], key=lambda f: f.Area)
        text = text or datetime.date.today().isoformat()
        mode = "engrave"
        depth = depth or 2 * layer
    else:
        if isinstance(at, str):
            try:
                subs = [x for x in cl.sel(s, at) if x.ShapeType == "Face"]
            except ValueError as e:
                raise UserError(str(e))
            if not subs:
                raise UserError("at %r matched no face" % at)
            face = subs[0]
        else:
            raise UserError("at must be a face selector, e.g. 'faces >Z' or 'faces <Y'")
    if not text:
        raise UserError("text is required")
    c, n, x, y = _frame(face, xdir)
    if offset:
        c = c + x * float(offset[0]) + y * float(offset[1])
    fb = face.BoundBox
    # face extent in its own frame (for auto size and the fit check)
    pts = [v.Point for v in face.Vertexes] or [fb.Center]
    fw = max(p.dot(x) for p in pts) - min(p.dot(x) for p in pts) if len(pts) > 1 else fb.DiagonalLength
    fh = max(p.dot(y) for p in pts) - min(p.dot(y) for p in pts) if len(pts) > 1 else fb.DiagonalLength
    if kind == "qr":
        mod = float(module or max(1.0, 2.5 * nozzle))
        comp, nmod = _qr_faces(text, mod)
        mode = mode if mode in ("emboss", "engrave") else "emboss"
        depth = float(depth or max(0.6, 3 * layer))
        side = nmod * mod
        tag = "QR code (%dx%d modules of %s mm = %s mm, + %s mm quiet zone needed)" % (nmod, nmod, _f(mod), _f(side),
                                                                                       _f(4 * mod))
        if side + 8 * mod > min(fw, fh) + 1e-6:
            notes.append("QR + quiet zone (%s mm) is larger than the face (%sx%s) - use module=%s or a bigger face" % (
                _f(side + 8 * mod), _f(fw), _f(fh), _f(min(fw, fh) / (nmod + 8))))
        if mod < 2 * nozzle:
            notes.append("modules under %s mm blur with a %s nozzle" % (_f(2 * nozzle), _f(nozzle)))
        notes.append("print it in a second colour: change filament at the layer where the code starts (M600), or "
                     "engrave and fill")
    else:
        lines_ = text.split("\n")
        longest = max(len(l) for l in lines_)
        if size is None and kind == "stamp":
            size = 4.0
        if size is None:
            size = min(0.8 * fw / max(1, 0.68 * longest), 0.6 * fh / max(1, 1.5 * len(lines_) - 0.5))
            size = max(size, 2.0)
        size = float(size)
        depth = float(depth or (2 * layer if kind == "stamp" else max(0.6, 3 * layer)))
        comp = text_faces(text, size, font)
        min_stroke = (2 * nozzle) if mode == "emboss" else nozzle
        nbad = _stroke_check(comp, min_stroke)
        tag = "%s text %r, %s mm cap height, %s mm %s" % (mode, text if len(text) < 40 else text[:37] + "...", _f(size),
                                                           _f(depth), "high" if mode == "emboss" else "deep")
        if nbad:
            k = 1.0
            for _ in range(12):
                k *= 1.15
                if _stroke_check(text_faces(text, size * k, font), min_stroke) == 0:
                    break
            notes.append("%d glyph part(s) have strokes under %s mm (%s) - make it >= %s mm cap height%s" % (
                nbad, _f(min_stroke), "two perimeters" if mode == "emboss" else "one nozzle width",
                _f(size * k), ", or engrave instead" if mode == "emboss" else ""))
    cb = comp.BoundBox
    if kind != "qr" and (cb.XLength > fw + 1e-6 or cb.YLength > fh + 1e-6):
        notes.append("text (%sx%s) is bigger than the face (%sx%s)" % (_f(cb.XLength), _f(cb.YLength), _f(fw), _f(fh)))
    if mode == "emboss":
        prism = _place2d(comp, c, n, x, y, -0.02).extrude(n * (depth + 0.02))
        res = s.fuse(prism)
    elif mode == "engrave":
        prism = _place2d(comp, c, n, x, y, 0.02).extrude(n * -(depth + 0.02))
        res = s.cut(prism)
    else:
        raise UserError("mode must be emboss or engrave")
    try:
        res = res.removeSplitter()
    except Exception:
        pass
    if not res.isValid() or not res.Solids:
        raise UserError("label boolean failed - try another face or size")
    if len(res.Solids) > len(s.Solids):
        raise UserError("the label sticks out past the face and would leave %d loose piece(s) - smaller size, "
                        "another offset or a bigger face (the face picked is %sx%s mm)" % (
                            len(res.Solids) - len(s.Solids), _f(fw), _f(fh)))
    if mode == "emboss" and n.z < -0.9 and kind != "stamp":
        notes.append("embossed on a downward face: it will need supports - engrave instead or print the face up")
    ck = auto_checkpoint(d, "before label " + o.Name)
    d.openTransaction("label")
    try:
        if name is None and o.TypeId == "Part::Feature":
            gp = o.getGlobalPlacement()
            r2 = res.copy()
            r2.transformShape(gp.inverse().toMatrix(), True)
            r2.Placement = o.Placement
            o.Shape = r2
            target = o.Name
        else:
            no = cl.show(res, name or (o.Name + "_label"), doc=d)
            target = no.Name
        d.recompute()
    finally:
        d.commitTransaction()
    out = ["%s: %s on face centred %s (normal %s) -> %s (%s)" % (
        o.Name, tag, "(%s)" % ", ".join(_f(v, 2) for v in c), "(%s)" % ", ".join(_f(v, 2) for v in n), target,
        "valid" if res.isValid() else "INVALID")]
    out += ["note: " + x_ for x_ in notes]
    if ck:
        out.append(ck)
    out.append("(%.1fs)" % (time.time() - t0))
    return "\n".join(out)


METHODS = {"label": label}
