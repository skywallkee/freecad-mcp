"""Lightweighting and vents: hex / grid / circle / slot cut-out patterns on a planar face, kept a margin away
from edges and holes, with mass saved and an optional before/after strength check."""
import math
import re
import time

import FreeCAD
import Part

from . import cl
from .analysis import global_shape
from .server import UserError
from .snapshot import fmt

V = FreeCAD.Vector


def _doc(name=None):
    from .handlers import _doc as d
    return d(name)


def _obj(doc, key):
    from .handlers import _obj as o
    return o(doc, key)


def _f(x, n=2):
    return fmt(round(float(x), n))


def _cell(pattern, cx, cy, size, slot_len):
    if pattern == "hex":
        R = size / math.sqrt(3)          # across flats = size (flats left/right)
        pts = [V(cx + R * math.cos(math.radians(30 + 60 * k)), cy + R * math.sin(math.radians(30 + 60 * k)), 0)
               for k in range(6)]
        return Part.Face(Part.makePolygon(pts + [pts[0]])), R
    if pattern == "grid":
        h = size / 2.0
        pts = [V(cx - h, cy - h, 0), V(cx + h, cy - h, 0), V(cx + h, cy + h, 0), V(cx - h, cy + h, 0)]
        return Part.Face(Part.makePolygon(pts + [pts[0]])), h * math.sqrt(2)
    if pattern == "circles":
        return Part.Face(Part.Wire(Part.makeCircle(size / 2.0, V(cx, cy, 0)))), size / 2.0
    if pattern == "slots":
        r = size / 2.0
        L = max(slot_len - size, 0.0)
        a = Part.Face(Part.Wire(Part.makeCircle(r, V(cx - L / 2, cy, 0))))
        b = Part.Face(Part.Wire(Part.makeCircle(r, V(cx + L / 2, cy, 0))))
        box = Part.Face(Part.makePolygon([V(cx - L / 2, cy - r, 0), V(cx + L / 2, cy - r, 0), V(cx + L / 2, cy + r, 0),
                                          V(cx - L / 2, cy + r, 0), V(cx - L / 2, cy - r, 0)]))
        f = a.fuse([b, box]).removeSplitter() if L > 0 else a
        return Part.Face(f.Faces[0].OuterWire) if len(f.Faces) == 1 else f.Faces[0], math.hypot(L / 2 + r, r)
    raise UserError("pattern must be hex, grid, circles or slots")


def _centres(pattern, b, cell, slot_len):
    out = []
    if pattern == "hex":
        dy = cell * math.sqrt(3) / 2.0
        j = 0
        y = b.YMin
        while y <= b.YMax + cell:
            x = b.XMin + (cell / 2.0 if j % 2 else 0.0)
            while x <= b.XMax + cell:
                out.append((x, y))
                x += cell
            y += dy
            j += 1
    elif pattern == "slots":
        px, py = slot_len + 0.6 * cell, cell     # slot pitch along / across
        y = b.YMin
        while y <= b.YMax + cell:
            x = b.XMin
            while x <= b.XMax + px:
                out.append((x, y))
                x += px
            y += py
    else:
        y = b.YMin
        while y <= b.YMax + cell:
            x = b.XMin
            while x <= b.XMax + cell:
                out.append((x, y))
                x += cell
            y += cell
    return out


def _strength_sf(d, obj, check):
    from .strength import strength
    args = dict(check)
    args.pop("object", None)
    r = strength(obj, doc=d.Name, image=False, **args)
    txt = r if isinstance(r, str) else r.get("text", "")
    sfs = [float(x) for x in re.findall(r"safety factor ([0-9.]+)", txt)]
    dm = re.search(r"deflection[^0-9]*([0-9.]+) mm", txt)
    return (min(sfs) if sfs else None), (float(dm.group(1)) if dm else None)


def lighten(object, at="faces >Z", pattern="hex", cell=None, wall=None, margin=None, depth=None, slot_len=None,
            angle=0.0, check=None, name=None, doc=None):
    """See the MCP tool."""
    from . import profile
    from .labels import _frame, _place2d
    from .output import auto_checkpoint
    d = _doc(doc)
    o = _obj(d, object)
    s = global_shape(o)
    if not s.Placement.isIdentity():
        m = s.Placement.toMatrix()
        s.Placement = FreeCAD.Placement()
        s.transformShape(m, True)
    prof = profile.load()
    nozzle = prof["nozzle"]
    t0 = time.time()
    try:
        faces = [x for x in cl.sel(s, at) if x.ShapeType == "Face"]
    except ValueError as e:
        raise UserError(str(e))
    if not faces:
        raise UserError("at %r matched no face" % at)
    face = faces[0]
    c, n, x, y = _frame(face)
    if angle:
        ca, sa = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        x, y = x * ca + y * sa, y * ca - x * sa
    wall = float(wall or max(4 * nozzle, 1.6))
    cell = float(cell or 10.0)
    if cell <= wall + 1.0:
        raise UserError("cell must be bigger than wall + 1 mm")
    margin = float(margin if margin is not None else max(wall * 1.5, 3.0))
    slot_len = float(slot_len or 3 * cell)
    size = cell - wall
    # face -> local 2D frame (origin at the face centre)
    m = FreeCAD.Matrix(x.x, x.y, x.z, 0, y.x, y.y, y.z, 0, n.x, n.y, n.z, 0, 0, 0, 0, 1)
    loc = face.copy()
    loc.translate(-c)
    loc.transformShape(m, True)
    try:
        # inset the outline, then grow every hole/pocket opening separately (a plain inward offset of a face
        # with small holes can leave them un-grown, and cells then cut into the holes)
        outer = loc.OuterWire
        region = Part.Face(outer).makeOffset2D(-margin, 0, False, False, False)
        for w in loc.Wires:
            if w.isSame(outer):
                continue
            try:
                hole = Part.Face(Part.Wire(w.Edges))
                region = region.cut(hole.makeOffset2D(margin, 0, False, False, False))
            except Exception:
                b = w.BoundBox
                region = region.cut(Part.Face(Part.Wire(Part.makeCircle(b.DiagonalLength / 2 + margin, b.Center))))
    except Exception as e:
        raise UserError("could not inset the face by the margin (%s)" % e)
    if region.isNull() or region.Area < 1e-3:
        raise UserError("face too small for a %s mm margin" % _f(margin))
    rf = region.Faces
    rb = region.BoundBox
    edges = Part.makeCompound([e for f in rf for e in f.Edges])
    cells = []
    for cx, cy in _centres(pattern, rb, cell, slot_len):
        f, R = _cell(pattern, cx, cy, size, slot_len)
        p = V(cx, cy, 0)
        if not any(g.isInside(p, 1e-6, True) for g in rf):
            continue
        try:     # centre inside + cell outline clear of every region edge = cell fully inside
            if edges.distToShape(Part.Vertex(p))[0] < R - 1e-6 and f.distToShape(edges)[0] < 1e-6:
                continue
        except Exception:
            continue
        cells.append(f)
    if not cells:
        raise UserError("no %s cells fit (cell %s, margin %s) - smaller cell or margin" % (pattern, _f(cell), _f(margin)))
    comp = Part.makeCompound(cells)
    span = [V(px, py, pz).dot(n) for px in (s.BoundBox.XMin, s.BoundBox.XMax) for py in (s.BoundBox.YMin, s.BoundBox.YMax)
            for pz in (s.BoundBox.ZMin, s.BoundBox.ZMax)]
    through = depth is None
    dep = (c.dot(n) - min(span)) + 0.5 if through else float(depth)
    prism = _place2d(comp, c, n, x, y, 0.02).extrude(n * -(dep + 0.02))
    sf0 = sf1 = df0 = df1 = None
    if check:
        try:
            sf0, df0 = _strength_sf(d, o.Name, check)
        except Exception as e:
            check = None
            sf_err = str(e)
    res = s.cut(prism)
    try:
        res = res.removeSplitter()
    except Exception:
        pass
    if not res.isValid() or not res.Solids:
        raise UserError("pattern cut failed - try another cell size or margin")
    notes = []
    if len(res.Solids) > len(s.Solids):
        raise UserError("the pattern would cut the part into %d pieces - bigger margin or a depth" % len(res.Solids))
    ck = auto_checkpoint(d, "before lighten " + o.Name)
    d.openTransaction("lighten")
    try:
        if name is None and o.TypeId == "Part::Feature":
            gp = o.getGlobalPlacement()
            r2 = res.copy()
            r2.transformShape(gp.inverse().toMatrix(), True)
            r2.Placement = o.Placement
            o.Shape = r2
            target = o.Name
        else:
            target = cl.show(res, name or (o.Name + "_light"), doc=d).Name
        d.recompute()
    finally:
        d.commitTransaction()
    v0, v1 = s.Volume, res.Volume
    from .strength import mech
    dens = mech(profile.material(None)[0])[1].get("density", 1.25)
    out = ["%s: %d %s cut-outs (%s mm cells, %s mm walls, %s mm from edges/holes, %s) -> %s" % (
        o.Name, len(cells), pattern, _f(cell), _f(wall), _f(margin), "through" if through else _f(dep) + " mm deep",
        target)]
    out.append("volume %s -> %s cm3 (-%s%%), ~%s g saved (solid)" % (
        _f(v0 / 1000.0), _f(v1 / 1000.0), _f(100 * (v0 - v1) / v0, 1), _f((v0 - v1) / 1000.0 * dens, 1)))
    if wall < 2 * nozzle:
        notes.append("walls under two perimeters (%s mm)" % _f(2 * nozzle))
    if check:
        try:
            sf1, df1 = _strength_sf(d, target, check)
            out.append("strength: safety factor %s -> %s, deflection %s -> %s mm%s" % (
                _f(sf0, 2) if sf0 else "?", _f(sf1, 2) if sf1 else "?", _f(df0, 3) if df0 else "?",
                _f(df1, 3) if df1 else "?",
                "  - TOO WEAK now (< 3): smaller cells, wider walls or a bigger margin" if sf1 and sf1 < 3 else ""))
        except Exception as e:
            out.append("strength check after failed: %s" % e)
    out += ["note: " + x_ for x_ in notes]
    if ck:
        out.append(ck)
    out.append("(%.1fs)" % (time.time() - t0))
    return "\n".join(out)


METHODS = {"lighten": lighten}
