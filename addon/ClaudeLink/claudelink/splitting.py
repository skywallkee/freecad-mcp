"""Splitting parts for printing: to fit the bed (split) or one piece per colour (color_split), with
glue-free joints (press-fit pins / pegs, press-fit inlay pockets) and an assembly check.

Everything works on copies; the original objects are never modified."""
import math
import re
import time

import FreeCAD
import Part

from . import cl
from .analysis import global_shape, _vec
from .server import UserError
from .snapshot import fmt

V = FreeCAD.Vector
AX = (V(1, 0, 0), V(0, 1, 0), V(0, 0, 1))
AXN = "xyz"
WALL = 1.2          # material kept around every pin hole (3 perimeters of a 0.4 nozzle)


def _doc(name=None):
    from .handlers import _doc as d
    return d(name)


def _obj(doc, key):
    from .handlers import _obj as o
    return o(doc, key)


def _prof():
    from . import profile
    return profile.load()


def _fit(cls, mat=None):
    from . import profile
    return profile.fit(cls, mat)


def _fit_tag(mat=None):
    from . import profile
    name, m, _ = profile.material(mat)
    return "%s, %s" % (name, "measured" if m.get("measured") else "GUESS - print the FitGauge")


def _r(x, n=2):
    return fmt(round(x, n))


def _baked(s):
    """Copy with its Placement applied to the geometry (offsets and some booleans fail on placed shapes)."""
    t = s.copy()
    m = t.Placement.toMatrix()
    t.Placement = FreeCAD.Placement()
    t.transformShape(m, True)   # copy=True rebuilds the geometry; False would only store a location
    return t


def _size(s):
    try:
        b = s.optimalBoundingBox(False, False)   # exact; the default (triangulation) can be off
    except Exception:
        b = s.BoundBox
    return "x".join(_r(v, 1) for v in (b.XLength, b.YLength, b.ZLength))


# ------------------------------------------------------------------ geometry helpers

def _plane(shape, k, pos, pad=5.0):
    b = shape.BoundBox
    if k == 0:
        return Part.makePlane(b.YLength + 2 * pad, b.ZLength + 2 * pad, V(pos, b.YMin - pad, b.ZMin - pad), V(1, 0, 0), V(0, 1, 0))
    if k == 1:
        return Part.makePlane(b.XLength + 2 * pad, b.ZLength + 2 * pad, V(b.XMax + pad, pos, b.ZMin - pad), V(0, 1, 0), V(-1, 0, 0))
    return Part.makePlane(b.XLength + 2 * pad, b.YLength + 2 * pad, V(b.XMin - pad, b.YMin - pad, pos), V(0, 0, 1))


def _section(shape, k, pos):
    try:
        sec = shape.common(_plane(shape, k, pos))
    except Exception:
        return []
    return [f for f in sec.Faces if f.Area > 1e-4]


def _halfspace(shape, k, lo, hi, pad=10.0):
    b = shape.BoundBox
    mn = [b.XMin - pad, b.YMin - pad, b.ZMin - pad]
    mx = [b.XMax + pad, b.YMax + pad, b.ZMax + pad]
    if lo is not None:
        mn[k] = lo
    if hi is not None:
        mx[k] = hi
    return Part.makeBox(mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2], V(*mn))


def _solid(s):
    sol = [x for x in s.Solids if x.Volume > 1e-6]
    if not sol:
        return None
    return sol[0] if len(sol) == 1 else Part.makeCompound(sol)


def _contains(piece, tool, tol=1e-3):
    """tool lies completely inside piece."""
    try:
        return abs(piece.common(tool).Volume - tool.Volume) <= max(tol * tool.Volume, 1e-3)
    except Exception:
        return False


def _cyl(r, h, p, d):
    return Part.makeCylinder(r, h, p, d)


def _grid_points(face, n_axis, step, margin):
    """Points inside a planar face at least `margin` from its boundary."""
    b = face.BoundBox
    edges = Part.Compound(face.Edges)
    u, w = [a for a in range(3) if a != n_axis]
    lo = [b.XMin, b.YMin, b.ZMin]
    hi = [b.XMax, b.YMax, b.ZMax]
    c = [(lo[i] + hi[i]) / 2 for i in range(3)]
    out = []
    nu = max(1, int((hi[u] - lo[u]) / step))
    nw = max(1, int((hi[w] - lo[w]) / step))
    if nu * nw > 900:
        f = math.sqrt(nu * nw / 900.0)
        nu, nw = max(1, int(nu / f)), max(1, int(nw / f))
    for i in range(nu + 1):
        for j in range(nw + 1):
            q = list(c)
            q[u] = lo[u] + (hi[u] - lo[u]) * (i / float(nu) if nu else 0.5)
            q[w] = lo[w] + (hi[w] - lo[w]) * (j / float(nw) if nw else 0.5)
            p = V(*q)
            if not face.isInside(p, 1e-4, True):
                continue
            dist = Part.Vertex(p).distToShape(edges)[0]
            if dist >= margin:
                out.append((dist, p))
    return out


def _spread(cands, n, min_sep):
    """Farthest-point pick, starting from the point deepest inside."""
    if not cands:
        return []
    cands = sorted(cands, key=lambda t: -t[0])
    picked = [cands[0][1]]
    while len(picked) < n:
        best = None
        for dist, p in cands:
            m = min((p - q).Length for q in picked)
            if m >= min_sep and (best is None or m > best[0]):
                best = (m, p)
        if best is None:
            break
        picked.append(best[1])
    return picked


def _fps_order(cands, n):
    """Up to n candidates in farthest-point order (spread over the face, deepest first)."""
    if len(cands) <= 1:
        return list(cands)
    rest = sorted(cands, key=lambda t: -t[0])
    out = [rest.pop(0)]
    dmin = [(c[1] - out[0][1]).Length for c in rest]
    while rest and len(out) < n:
        i = max(range(len(rest)), key=lambda k: dmin[k] + 0.25 * rest[k][0])
        c = rest.pop(i)
        dmin.pop(i)
        out.append(c)
        dmin = [min(dm, (r_[1] - c[1]).Length) for dm, r_ in zip(dmin, rest)]
    return out


def _good_spots(cands, a, b, nvec, rh, dpin, cont_a=None, cont_b=None, limit=40):
    """Candidate pin centres where hole + wall fit inside both sides; longest depth first."""
    ca, cb = cont_a or a, cont_b or b
    good = []
    for dist, p in _fps_order(cands, 3 * limit):
        for L in (2.0 * dpin, 1.5 * dpin, dpin, max(0.75 * dpin, 3.0)):
            if L > 14:
                continue
            wa = _cyl(rh + WALL, L + 0.2 + WALL, p - nvec * (L + 0.2 + WALL), nvec)
            wb = _cyl(rh + WALL, L + 0.2 + WALL, p, nvec)
            if _contains(ca, wa) and _contains(cb, wb):
                good.append((dist, p, L))
                break
        if len(good) >= limit:
            break
    return good


def _add_pin(out, joint, p, nvec, dpin, L, clr):
    r, rh = dpin / 2.0, dpin / 2.0 + clr
    if joint == "peg":
        # integral peg on a, socket in b
        peg = _cyl(r, L - 0.2, p, nvec)
        try:
            peg = peg.makeChamfer(min(0.4, r * 0.2), [e for e in peg.Edges
                                                      if abs((e.CenterOfMass - p).dot(nvec) - (L - 0.2)) < 1e-6])
        except Exception:
            pass
        out["fuse_a"].append(peg)
        out["cut_b"].append(_cyl(rh, L + 0.2, p, nvec))
        out["pins"].append(("peg", p, dpin, L, clr, None))
    else:
        out["cut_a"].append(_cyl(rh, L + 0.2, p - nvec * (L + 0.2), nvec))
        out["cut_b"].append(_cyl(rh, L + 0.2, p, nvec))
        pin = _cyl(r, 2 * L - 0.2, p - nvec * (L - 0.1), nvec)
        try:
            pin = pin.makeChamfer(min(0.4, r * 0.2), [e for e in pin.Edges if isinstance(e.Curve, Part.Circle)])
        except Exception:
            pass
        out["pins"].append(("pin", p, dpin, 2 * L - 0.2, clr, pin))


def _boss_prism(f0, l, mo, nvec, R, Hb, back, Lb, m0=None):
    """Trapezoid boss (45 deg flanks, prints without support when its axis is horizontal), extruded
    along nvec from -Lb to +Lb. Profile: from m=-back (inside the wall) to m=Hb above the wall face f0."""
    w0 = R + Hb
    m0 = -back if m0 is None else m0
    pts = [(-w0, m0), (w0, m0), (w0, 0.0), (R, Hb), (-R, Hb), (-w0, 0.0)]
    if m0 >= 0:
        pts = [(-(R + Hb - m0), m0), (R + Hb - m0, m0), (R, Hb), (-R, Hb)]
    vs = [f0 + l * u + mo * m - nvec * Lb for u, m in pts]
    w = Part.makePolygon(vs + [vs[0]])
    return Part.Face(w).extrude(nvec * (2 * Lb))


def _boss_pins(face, n_axis, nvec, a, b, whole, joint, clr, out, dpin=5.0):
    """Thin wall at the cut: add small bosses on the free side of the wall (prefer the inside of the
    part), each carrying one pin. Returns the number of bosses added."""
    pos = face.CenterOfMass[n_axis]
    cands = _grid_points(face, n_axis, 0.6, 0.0)
    if not cands:
        return 0
    top = max(c[0] for c in cands)
    if top < 0.4:
        return 0
    ridge = [c for c in cands if c[0] >= 0.7 * top]
    r, rh = dpin / 2.0, dpin / 2.0 + clr
    R = rh + WALL
    L = 1.5 * dpin
    Lb = L + 0.2 + WALL
    edges = Part.Compound(face.Edges)
    centre = whole.BoundBox.Center
    n_target = max(2, min(4, int(face.Length / 2 / 120.0) + 2)) if hasattr(face, "Length") else 2
    picks = _spread(ridge, n_target * 5, 3.0 * R)
    added = 0
    for p in picks:
        if added >= n_target:
            break
        dist, pts_, _ = Part.Vertex(p).distToShape(edges)
        q = pts_[0][1]
        m_in = p - q
        m_in = m_in - nvec * m_in.dot(nvec)
        if m_in.Length < 1e-6:
            continue
        m_in.normalize()
        t_half = dist
        Hb = max(2 * R - 2 * t_half, 0.6 * R)
        sides = sorted((-m_in, m_in), key=lambda mo: -(centre - p).dot(mo))   # towards the part centre first
        for mo in sides:
            f0 = p + mo * t_half
            l = nvec.cross(mo)
            check = _boss_prism(f0, l, mo, nvec, R, Hb, 0.0, Lb, m0=0.3)
            wb_ = whole.BoundBox
            wb_.enlarge(1e-3)
            if not wb_.isInside(check.BoundBox):
                continue
            foot = _boss_prism(f0, l, mo, nvec, R, Hb, t_half, Lb).common(_boss_prism(f0, l, mo, nvec, R, 0.0, t_half, Lb))
            try:
                if whole.common(check).Volume > 0.01 * check.Volume:
                    continue
                if foot.Volume > 0 and whole.common(foot).Volume < 0.9 * foot.Volume:
                    continue   # the boss would hang over the end of the wall
            except Exception:
                continue
            boss = _boss_prism(f0, l, mo, nvec, R, Hb, t_half, Lb)
            c = f0 + mo * (Hb - R)
            ba = _solid(boss.common(_halfspace(boss, n_axis, None, pos)))
            bb_ = _solid(boss.common(_halfspace(boss, n_axis, pos, None)))
            if ba is None or bb_ is None:
                continue
            try:
                ok = _good_spots([(0, c)], a, b, nvec, rh, dpin, a.fuse(ba), b.fuse(bb_), 1)
            except Exception:
                ok = []
            if not ok:
                continue
            out["boss_a"].append(ba)
            out["boss_b"].append(bb_)
            _add_pin(out, joint, c, nvec, dpin, ok[0][2], clr)
            added += 1
            break
    return added


def _pins_for(faces, n_axis, nvec, a, b, whole, mat, joint, fit, pin_d=None, max_pins=3, bosses=True):
    """Design pins across a planar interface; a is on the -nvec side, b on the +nvec side."""
    clr = _fit(fit, mat)
    out = {"cut_a": [], "cut_b": [], "fuse_a": [], "boss_a": [], "boss_b": [], "pins": [], "notes": [], "bosses": 0}
    for face in faces:
        n_target = 1 if face.Area < 60 else 2 if face.Area < 3000 else max_pins
        best = None
        for dpin in ([pin_d] if pin_d else (8.0, 6.0, 5.0, 4.0, 3.0)):
            rh = dpin / 2.0 + clr
            cands = _grid_points(face, n_axis, max(dpin * 0.375, 0.8), rh + WALL)
            if not cands:
                continue
            good = _good_spots(cands, a, b, nvec, rh, dpin)
            pts = _spread([(g[0], g[1]) for g in good], n_target, 2.5 * dpin)
            if best is None or len(pts) > len(best[1]):
                best = (dpin, pts, good)
            if len(pts) >= n_target:
                break
        if best and len(best[1]) >= n_target:
            for p in best[1]:
                _add_pin(out, joint, p, nvec, best[0], next(g[2] for g in best[2] if g[1] is p), clr)
            continue
        nb = _boss_pins(face, n_axis, nvec, a, b, whole, joint, clr, out, pin_d or 5.0) if bosses else 0
        if nb:
            out["bosses"] += nb
            if nb < 2 and not (best and best[1]):
                out["notes"].append("only 1 pin fits the interface at %s - the pieces can turn about it; "
                                    "a drop of glue keeps it aligned" % _vec(face.CenterOfMass))
            continue
        if best and best[1]:
            for p in best[1]:
                _add_pin(out, joint, p, nvec, best[0], next(g[2] for g in best[2] if g[1] is p), clr)
            if len(best[1]) == 1 and len(faces) == 1:
                out["notes"].append("only 1 pin fits the interface at %s - the pieces can turn about it; "
                                    "a drop of glue keeps it aligned" % _vec(face.CenterOfMass))
            continue
        out["notes"].append("no room for a pin or boss in the %s mm2 interface at %s - glue it" % (
            _r(face.Area, 0), _vec(face.CenterOfMass)))
    return out


def _pin_print_shape(pin, p, nvec, dpin):
    """Lay a pin flat (axis along X) with a small flat on its underside so it prints without supports."""
    s = pin.copy()
    s.translate(-p)
    rot = FreeCAD.Rotation(nvec, V(1, 0, 0))
    s.transformShape(FreeCAD.Placement(V(), rot).toMatrix())
    b = s.BoundBox
    flat = 0.08 * dpin
    s = s.cut(Part.makeBox(b.XLength + 2, b.YLength + 2, flat + 1, V(b.XMin - 1, b.YMin - 1, b.ZMin - 1)))
    try:
        s = s.removeSplitter()
    except Exception:
        pass
    b = s.BoundBox
    s.translate(V(-b.XMin, -b.YMin, -b.ZMin))
    return s


def _placed_pins(d, base, solids):
    """Hidden copy of the pins in their assembled positions (used by assembly illustrations)."""
    if not solids:
        return None
    po = cl.show(Part.makeCompound(solids), base + "_pins_placed", doc=d)
    if FreeCAD.GuiUp:
        try:
            po.ViewObject.Visibility = False
        except Exception:
            pass
    return po


def _layout(shapes, gap=4.0):
    out, x = [], 0.0
    for s in shapes:
        s = s.copy()
        b = s.BoundBox
        s.translate(V(x - b.XMin, -b.YMin, -b.ZMin))
        out.append(s)
        x += b.XLength + gap
    return out


# ------------------------------------------------------------------ split to fit the bed

def _cut_positions(shape, k, limit, avoid):
    b = shape.BoundBox
    lo, hi = [b.XMin, b.YMin, b.ZMin][k], [b.XMax, b.YMax, b.ZMax][k]
    L = hi - lo
    n = int(math.ceil(L / limit))
    if n <= 1:
        return []
    cuts, start = [], lo
    for j in range(n - 1):
        remaining = n - 1 - j
        a = max(start + 0.3 * limit, hi - (remaining) * limit + 1.0)
        z = min(start + limit - 1.0, hi - 5.0)
        if a > z:
            a = z
        best = None
        for i in range(13):
            pos = a + (z - a) * i / 12.0
            if any(abs(pos - q) < 0.6 for q in avoid):
                continue
            faces = _section(shape, k, pos)
            if not faces:
                continue
            area = sum(f.Area for f in faces)
            # prefer small, compact interfaces (fewer islands); ties -> nearer the even split
            score = area * (1 + 0.5 * (len(faces) - 1)) + abs(pos - (start + L / n)) * 0.01
            if best is None or score < best[0]:
                best = (score, pos)
        if best is None:
            raise UserError("no usable cut position along %s between %s and %s" % (AXN[k], _r(a), _r(z)))
        cuts.append(best[1])
        start = best[1]
    return cuts


def split(object, axis=None, positions=None, joint="pin", fit="press", pin_d=None, margin=5.0, doc=None, name=None,
          bosses=True):
    """Split one solid into pieces that fit the print bed, joined by press-fit pins (or integral pegs)."""
    d = _doc(doc)
    o = _obj(d, object)
    s = _baked(global_shape(o))
    if not s.Solids:
        raise UserError("%s has no solid" % o.Name)
    t0 = time.time()
    prof = _prof()
    mat = prof.get("default_material")
    bed = prof["bed"]
    lim = [bed[0] - margin, bed[1] - margin, bed[2] - margin]
    b = s.BoundBox
    ext = [b.XLength, b.YLength, b.ZLength]
    lines = []
    plan = []   # (axis index, [positions])
    if positions:
        if axis is None:
            raise UserError("positions need an axis ('x', 'y' or 'z')")
        plan.append((AXN.index(axis.lower()), sorted(float(p) for p in positions)))
    else:
        axes = [AXN.index(axis.lower())] if axis else [0, 1, 2]
        # footprint may be rotated 90 deg on the bed: only split XY if it fits neither way
        xy_fits = (ext[0] <= lim[0] and ext[1] <= lim[1]) or (ext[0] <= lim[1] and ext[1] <= lim[0])
        for k in axes:
            if k < 2 and xy_fits and not axis:
                continue
            if ext[k] > lim[k] or axis:
                avoid = [f.CenterOfMass[k] for f in s.Faces if isinstance(f.Surface, Part.Plane)
                         and cl._normal(f) is not None and abs(abs(cl._normal(f).dot(AX[k])) - 1) < 1e-6]
                ps = _cut_positions(s, k, lim[k], avoid)
                if ps:
                    plan.append((k, ps))
    if not plan:
        return "%s (%s) already fits the bed %s (margin %s mm) - nothing to split" % (
            o.Name, _size(s), "x".join(fmt(v) for v in bed), fmt(margin))
    # cut
    pieces, cells = [s], [{}]
    cut_info = []
    for k, ps in plan:
        new, new_cells = [], []
        for pc, cell in zip(pieces, cells):
            bounds = [None] + ps + [None]
            for i in range(len(bounds) - 1):
                part = _solid(pc.common(_halfspace(pc, k, bounds[i], bounds[i + 1])))
                if part is not None:
                    new.append(part)
                    c2 = dict(cell)
                    c2[k] = (bounds[i], bounds[i + 1])
                    new_cells.append(c2)
        pieces, cells = new, new_cells
        cut_info += [(k, p) for p in ps]
    # joints at every cut: pieces on the - side get cuts_a, on the + side cuts_b
    tools = {i: {"cut": [], "fuse": [], "boss": []} for i in range(len(pieces))}
    pins, notes, n_boss = [], [], 0
    for k, pos in cut_info:
        nvec = AX[k]
        for ia, pa in enumerate(pieces):
            if abs([pa.BoundBox.XMax, pa.BoundBox.YMax, pa.BoundBox.ZMax][k] - pos) > 1e-4:
                continue
            for ib, pb in enumerate(pieces):
                if abs([pb.BoundBox.XMin, pb.BoundBox.YMin, pb.BoundBox.ZMin][k] - pos) > 1e-4:
                    continue
                fa = _section(pa, k, pos - 1e-3)
                fb = _section(pb, k, pos + 1e-3)
                if not fa or not fb:
                    continue
                inter = []
                for f1 in fa:
                    for f2 in fb:
                        f2t = f2.copy()
                        f2t.translate(nvec * (-2e-3))
                        try:
                            c = f1.common(f2t)
                        except Exception:
                            continue
                        inter += [f for f in c.Faces if f.Area > 1.0]
                if not inter or joint == "none":
                    continue
                res = _pins_for(inter, k, nvec, pa, pb, s, mat, joint, fit, pin_d, bosses=bosses)
                tools[ia]["cut"] += res["cut_a"]
                tools[ia]["fuse"] += res["fuse_a"]
                tools[ia]["boss"] += res["boss_a"]
                tools[ib]["cut"] += res["cut_b"]
                tools[ib]["boss"] += res["boss_b"]
                pins += [(x, k) for x in res["pins"]]
                notes += res["notes"]
                n_boss += res["bosses"]
    out_pieces = []
    for i, pc in enumerate(pieces):
        t = tools[i]
        if t["boss"]:
            pc = pc.fuse(t["boss"])
            # bosses of one cut must not reach across another cut into the neighbour's cell
            box = None
            for k, (lo, hi) in cells[i].items():
                hs = _halfspace(s, k, lo, hi, pad=50.0)
                box = hs if box is None else box.common(hs)
            if box is not None:
                pc = _solid(pc.common(box)) or pc
        if t["fuse"]:
            pc = pc.fuse(t["fuse"])
        if t["cut"]:
            pc = pc.cut(Part.makeCompound(t["cut"]) if len(t["cut"]) > 1 else t["cut"][0])
        try:
            pc = pc.removeSplitter()
        except Exception:
            pass
        out_pieces.append(pc)
    base = name or (o.Name + "_split")
    from .output import auto_checkpoint
    ck = auto_checkpoint(d, "before split " + o.Name)
    if ck:
        notes.append(ck)
    # remove stale outputs of a previous run
    for x in list(d.Objects):
        if re.fullmatch(re.escape(base) + r"\d+|" + re.escape(base) + "_pins(_placed)?", x.Name):
            d.removeObject(x.Name)
    lines.append("%s %s -> %d piece(s); bed %s, margin %s mm" % (
        o.Name, _size(s), len(out_pieces), "x".join(fmt(v) for v in bed), fmt(margin)))
    for k, pos in cut_info:
        area = sum(f.Area for f in _section(s, k, pos))
        lines.append("  cut %s = %s (section %s mm2)" % (AXN[k], _r(pos), _r(area, 0)))
    shown = []
    for i, pc in enumerate(out_pieces, 1):
        bb = pc.BoundBox
        fits = ((bb.XLength <= bed[0] and bb.YLength <= bed[1]) or (bb.XLength <= bed[1] and bb.YLength <= bed[0])) \
            and bb.ZLength <= bed[2]
        po = cl.show(pc, "%s%d" % (base, i), doc=d)
        shown.append(po.Name)
        ok = pc.isValid() and len(pc.Solids) >= 1
        lines.append("  %s: %s, %s cm3%s%s" % (po.Name, _size(pc), _r(pc.Volume / 1000.0),
                                               "" if fits else " - STILL TOO BIG for the bed",
                                               "" if ok else " - INVALID"))
    if pins:
        kinds = {x[0][0] for x in pins}
        dset = sorted({x[0][2] for x in pins})
        clr = pins[0][0][4]
        lines.append("joints: %d %s(s) d=%s mm, holes d+%s mm (%s fit, %s) - holds without glue" % (
            len(pins), "/".join(sorted(kinds)), "/".join(fmt(v) for v in dset), _r(2 * clr), fit, _fit_tag(mat)))
        if n_boss:
            lines.append("  %d wall(s) too thin for a pin got a small 45-deg boss on the inside at the cut to carry it" % n_boss)
        pin_shapes = [_pin_print_shape(x[0][5], x[0][1], AX[x[1]], x[0][2]) for x in pins if x[0][0] == "pin"]
        if pin_shapes:
            laid = _layout(pin_shapes, 3.0)
            po = cl.show(Part.makeCompound(laid), base + "_pins", doc=d)
            shown.append(po.Name)
            _placed_pins(d, base, [x[0][5] for x in pins if x[0][0] == "pin"])
            lines.append("  %s: %d pin(s) laid flat on a small flat (print as is), lengths %s mm" % (
                po.Name, len(pin_shapes), "/".join(sorted({_r(x[0][3], 1) for x in pins if x[0][0] == "pin"}))))
    elif joint != "none":
        lines.append("joints: none placed")
    lines += ["note: " + n for n in notes]
    if cl.color_of(o) is not None:
        try:
            col = cl.color_of(o)
            for nm in shown:
                cl.paint(d.getObject(nm), col)
        except Exception:
            pass
    d.recompute()
    lines.append("print each piece cut-face down (holes vertical, flat interface). (%.1fs)" % (time.time() - t0))
    return "\n".join(lines)



# ------------------------------------------------------------------ colour split

_COLOURS = {
    "black": (0.1, 0.1, 0.1), "white": (0.95, 0.95, 0.95), "grey": (0.55, 0.55, 0.55), "red": (0.85, 0.1, 0.1),
    "orange": (1.0, 0.5, 0.0), "yellow": (0.95, 0.85, 0.1), "green": (0.1, 0.65, 0.2), "blue": (0.1, 0.3, 0.85),
    "purple": (0.5, 0.2, 0.7), "pink": (0.95, 0.5, 0.7), "brown": (0.45, 0.28, 0.12), "cyan": (0.1, 0.75, 0.85),
    "steel": (0.8, 0.8, 0.8),
}


def _colour_name(rgb):
    if rgb is None:
        return None
    rgb = tuple(rgb[:3])
    return min(_COLOURS, key=lambda k: sum((a - b) ** 2 for a, b in zip(_COLOURS[k], rgb)))


def _free_direction(small, big, dirs):
    """First direction small can be pulled out of big along without hitting it."""
    b = small.BoundBox
    T = b.DiagonalLength + 1.0
    tol = max(1e-3 * small.Volume, 1e-3)
    for dv in dirs:
        ok = True
        for f in (0.02, 0.15, 0.4, 1.0):
            m = small.copy()
            m.translate(dv * (T * f))
            try:
                if m.common(big).Volume > tol:
                    ok = False
                    break
            except Exception:
                ok = False
                break
        if ok:
            return dv
    return None


def _contact_faces(small, big, eps=0.02):
    """Faces of small that touch big (probe a point just outside each face)."""
    out = []
    for f in small.Faces:
        try:
            u0, u1, v0, v1 = f.ParameterRange
            hits = 0
            probes = 0
            for fu, fv in ((0.5, 0.5), (0.25, 0.25), (0.75, 0.75), (0.25, 0.75), (0.75, 0.25)):
                u, v = u0 + (u1 - u0) * fu, v0 + (v1 - v0) * fv
                if not f.isInside(f.valueAt(u, v), 1e-6, True):
                    continue
                n = f.normalAt(u, v)
                probes += 1
                if big.isInside(f.valueAt(u, v) + n * eps, 1e-6, True):
                    hits += 1
            if probes and hits * 2 >= probes:
                out.append(f)
        except Exception:
            continue
    return out


def _grow_prism(small, dv, c):
    """small grown sideways by c, assuming it is a prism along dv (logo, text, inlay); None if not."""
    for f in small.Faces:
        if isinstance(f.Surface, Part.Plane):
            n = cl._normal(f)
            if n is not None and abs(abs(n.dot(dv)) - 1) < 1e-6:
                continue
            if n is not None and abs(n.dot(dv)) < 1e-6:
                continue
            return None
        # curved faces must be parallel to dv everywhere (cylinders/extrusions along dv)
        try:
            u0, u1, v0, v1 = f.ParameterRange
            for fu in (0.2, 0.5, 0.8):
                for fv in (0.2, 0.5, 0.8):
                    if abs(f.normalAt(u0 + (u1 - u0) * fu, v0 + (v1 - v0) * fv).dot(dv)) > 1e-4:
                        return None
        except Exception:
            return None
    b = small.BoundBox
    ks = [b.XMin, b.YMin, b.ZMin]
    ke = [b.XMax, b.YMax, b.ZMax]
    k = max(range(3), key=lambda i: abs(dv[i]))
    if abs(abs(dv[k]) - 1) > 1e-6:
        return None
    mid = (ks[k] + ke[k]) / 2.0
    sec = _section(small, k, mid)
    if not sec:
        return None
    grown = []
    for f in sec:
        try:
            g = f.makeOffset2D(c, 2, False, False, False)
        except Exception:
            return None
        grown += g.Faces
    L = ke[k] - ks[k]
    sol = []
    for g in grown:
        g = g.copy()
        g.translate(AX[k] * (ks[k] - mid - c))
        sol.append(g.extrude(AX[k] * (L + 2 * c)))
    return sol[0] if len(sol) == 1 else sol[0].fuse(sol[1:])


def _grown(small, dv, c):
    g = _grow_prism(small, dv, c)
    if g is not None:
        return g, "exact (prism)"
    try:
        g = small.makeOffsetShape(c, 1e-3, join=2)
        if g.isValid() and g.Volume > small.Volume:
            return g, "3D offset"
    except Exception:
        pass
    cen = small.BoundBox.Center
    b = small.BoundBox
    sc = 1 + 2 * c / max(1.0, min(b.XLength, b.YLength, b.ZLength))
    g = small.copy()
    g = g.transformGeometry(FreeCAD.Matrix(sc, 0, 0, cen.x * (1 - sc), 0, sc, 0, cen.y * (1 - sc), 0, 0, sc, cen.z * (1 - sc)))
    return g, "approximate (scaled - check the fit)"


def color_split(objects, colors=None, fit="press", joint="pin", doc=None, name=None, purge_g=0.2):
    """objects: colour regions in priority order (later ones win where they overlap: base first, inlays
    and details last). Makes one single-colour piece per region with glue-free joints."""
    d = _doc(doc)
    objs = [_obj(d, k) for k in objects]
    if len(objs) < 2:
        raise UserError("need at least 2 colour regions")
    t0 = time.time()
    prof = _prof()
    mat = prof.get("default_material")
    clr = _fit(fit, mat)
    shapes = [_baked(global_shape(o)) for o in objs]
    rgb = []
    for i, o in enumerate(objs):
        c = None
        if colors and i < len(colors) and colors[i]:
            c = _COLOURS.get(str(colors[i]).lower()) if isinstance(colors[i], str) else tuple(colors[i])
        else:
            c = cl.color_of(o)
        rgb.append(c)
    # 1. resolve overlaps: later regions win
    pieces = []
    for i, sh in enumerate(shapes):
        later = shapes[i + 1:]
        pc = sh
        if later:
            tool = later[0] if len(later) == 1 else later[0].fuse(later[1:])
            pc = sh.cut(tool)
        pc = _solid(pc)
        if pc is None:
            raise UserError("%s is completely covered by later regions" % objs[i].Name)
        pieces.append(pc)
    whole = pieces[0].fuse(pieces[1:])
    lines = []
    joints = []
    extra_cut = {i: [] for i in range(len(pieces))}
    extra_fuse = {i: [] for i in range(len(pieces))}
    extra_boss = {i: [] for i in range(len(pieces))}
    pins = []
    dirs = [V(0, 0, 1), V(0, 0, -1), V(1, 0, 0), V(-1, 0, 0), V(0, 1, 0), V(0, -1, 0)]
    for i in range(len(pieces)):
        for j in range(i + 1, len(pieces)):
            a, b = pieces[i], pieces[j]
            if a.distToShape(b)[0] > 1e-3:
                continue
            try:
                contact = (a.Area + b.Area - a.fuse(b).removeSplitter().Area) / 2.0
            except Exception:
                contact = 0.0
            if contact < 0.5:
                continue
            si, bi = (i, j) if a.Volume < b.Volume else (j, i)
            small, big = pieces[si], pieces[bi]
            dv = _free_direction(small, big, dirs)
            nm_s, nm_b = objs[si].Name, objs[bi].Name
            if dv is None:
                joints.append("%s in %s: LOCKED - no straight direction to assemble it; split %s further or glue" % (
                    nm_s, nm_b, nm_b))
                continue
            cf = _contact_faces(small, big)
            side = sum(f.Area for f in cf if abs(_fnormal(f).dot(dv)) < 0.3)
            depth = _engaged_depth(cf, dv)
            if side > 2.0 and depth >= 0.6:
                g, how = _grown(small, dv, clr)
                extra_cut[bi].append(g)
                hold = ("press fit %s mm per side (%s) - holds without glue" % (_r(clr), _fit_tag(mat)) if depth >= 1.2
                        else "only %s mm engaged - too shallow to hold by friction; make it >= 1.2 mm deep or glue" % _r(depth, 2))
                joints.append("%s into %s: inlay, push in along %s; pocket %s; %s" % (
                    nm_s, nm_b, _dstr(-dv), how, hold))
            else:
                k = max(range(3), key=lambda q: abs(dv[q]))
                if abs(abs(dv[k]) - 1) > 1e-6:
                    joints.append("%s on %s: flat contact, not axis-aligned - glue" % (nm_s, nm_b))
                    continue
                nvec = AX[k] if dv[k] > 0 else AX[k] * -1
                inter = _flat_interfaces(small, big, dv)
                if not inter:
                    joints.append("%s on %s: touching, but no flat face to pin through - glue" % (nm_s, nm_b))
                    continue
                if dv[k] > 0:   # small lies on the + side: a = big, b = small
                    res = _pins_for(inter, k, AX[k], big, small, whole, mat, joint, fit)
                    ia, ib = bi, si
                else:
                    res = _pins_for(inter, k, AX[k], small, big, whole, mat, joint, fit)
                    ia, ib = si, bi
                extra_cut[ia] += res["cut_a"]
                extra_fuse[ia] += res["fuse_a"]
                extra_boss[ia] += res["boss_a"]
                extra_cut[ib] += res["cut_b"]
                extra_boss[ib] += res["boss_b"]
                pins += [(x, k) for x in res["pins"]]
                np_ = len(res["pins"])
                joints.append("%s on %s: flat joint, %s; %d %s(s) d=%s mm (%s fit) - no glue needed%s" % (
                    nm_s, nm_b, "stack along %s" % _dstr(-dv), np_, joint,
                    "/".join(sorted({fmt(x[2]) for x in res["pins"]})) or "-", fit,
                    "".join("; " + n for n in res["notes"])))
                if not np_:
                    joints[-1] = joints[-1].replace(" - no glue needed", " - GLUE (no room for pins)")
    # 2. apply joint geometry
    base = name or (objs[0].Name + "_color")
    from .output import auto_checkpoint
    ck = auto_checkpoint(d, "before color_split")
    if ck:
        lines.append(ck)
    for x in list(d.Objects):
        if re.fullmatch(re.escape(base) + r"\d+(_\w+)?|" + re.escape(base) + "_pins(_placed)?", x.Name):
            d.removeObject(x.Name)
    out = []
    for i, pc in enumerate(pieces):
        if extra_boss[i]:
            pc = pc.fuse(extra_boss[i])
        if extra_fuse[i]:
            pc = pc.fuse(extra_fuse[i])
        if extra_cut[i]:
            pc = pc.cut(extra_cut[i][0] if len(extra_cut[i]) == 1 else Part.makeCompound(extra_cut[i]))
        try:
            pc = pc.removeSplitter()
        except Exception:
            pass
        out.append(pc)
    lines.append("%d colour regions -> %d single-colour pieces (later regions win overlaps; %s)" % (
        len(objs), len(out), "fit %s %s mm per side" % (fit, _r(clr))))
    shown = []
    for i, pc in enumerate(out):
        cname = _colour_name(rgb[i])
        nm = "%s%d%s" % (base, i + 1, ("_" + cname) if cname else "")
        po = cl.show(pc, nm, doc=d)
        shown.append(po.Name)
        if rgb[i] is not None:
            try:
                cl.paint(po, rgb[i])
            except Exception:
                pass
        lines.append("  %s (from %s%s): %s, %s cm3%s" % (
            po.Name, objs[i].Name, (", " + cname) if cname else "", _size(pc), _r(pc.Volume / 1000.0),
            "" if pc.isValid() else " - INVALID"))
    lines.append("assembly (in this order):")
    lines += ["  " + j for j in joints] or ["  (no touching pairs)"]
    pin_shapes = [_pin_print_shape(x[0][5], x[0][1], AX[x[1]], x[0][2]) for x in pins if x[0][0] == "pin"]
    if pin_shapes:
        po = cl.show(Part.makeCompound(_layout(pin_shapes, 3.0)), base + "_pins", doc=d)
        _placed_pins(d, base, [x[0][5] for x in pins if x[0][0] == "pin"])
        lines.append("  %s: %d pin(s), print in any colour (hidden)" % (po.Name, len(pin_shapes)))
    # 3. filament saved: a multi-colour print purges at every colour change on every mixed layer
    layer = prof.get("layer", 0.2)
    zs = [(pc.BoundBox.ZMin, pc.BoundBox.ZMax) for pc in pieces]
    colours = [(_colour_name(rgb[i]) or objs[i].Name) for i in range(len(pieces))]
    zmin, zmax = min(z[0] for z in zs), max(z[1] for z in zs)
    changes = 0
    z = zmin + layer / 2
    while z < zmax:
        present = {colours[i] for i, (a, b) in enumerate(zs) if a <= z <= b}
        changes += max(0, len(present) - 1)
        z += layer
    if changes:
        lines.append("vs one multi-colour print: ~%d colour changes (~%s g purge at %s g each, rough) avoided" % (
            changes, _r(changes * purge_g, 0), fmt(purge_g)))
    d.recompute()
    lines.append("(%.1fs) print each piece in its colour; freecad_fit on the pieces confirms the clearances" % (time.time() - t0))
    return "\n".join(lines)


def _fnormal(f):
    try:
        u0, u1, v0, v1 = f.ParameterRange
        return f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
    except Exception:
        return V(0, 0, 0)


def _engaged_depth(faces, dv):
    """How far side contact faces extend along the assembly direction."""
    side = [f for f in faces if abs(_fnormal(f).dot(dv)) < 0.3]
    if not side:
        return 0.0
    vals = [v.Point.dot(dv) for f in side for v in f.Vertexes]
    if not vals:
        return 0.0
    return max(vals) - min(vals)


def _flat_interfaces(small, big, dv):
    out = []
    for fs in small.Faces:
        if not isinstance(fs.Surface, Part.Plane):
            continue
        n = cl._normal(fs)
        if n is None or n.dot(dv) > -0.999:
            continue
        for fb in big.Faces:
            if not isinstance(fb.Surface, Part.Plane):
                continue
            nb = cl._normal(fb)
            if nb is None or nb.dot(dv) < 0.999:
                continue
            if abs((fb.CenterOfMass - fs.CenterOfMass).dot(dv)) > 1e-4:
                continue
            try:
                c = fs.common(fb)
            except Exception:
                continue
            out += [f for f in c.Faces if f.Area > 1.0]
    return out


def _dstr(v):
    for nm, w in (("+z", V(0, 0, 1)), ("-z", V(0, 0, -1)), ("+x", V(1, 0, 0)), ("-x", V(-1, 0, 0)),
                  ("+y", V(0, 1, 0)), ("-y", V(0, -1, 0))):
        if (v - w).Length < 1e-6:
            return nm
    return _vec(v)


METHODS = {"split": split, "color_split": color_split}
