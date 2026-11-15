"""Print orientation, print preparation and engineered (snap-off) supports.

All analysis runs on a print copy; the original object is never modified."""
import math
import time

import FreeCAD
import Part

from . import cl
from .analysis import global_shape, _mesh, _facets, _regions, _vec
from .server import UserError
from .snapshot import fmt

V = FreeCAD.Vector
Z = V(0, 0, 1)


def _doc(name=None):
    from .handlers import _doc as d
    return d(name)


def _obj(doc, key):
    from .handlers import _obj as o
    return o(doc, key)


def _prof():
    from . import profile
    return profile.load()


def _rot_up(up):
    """Rotation that makes model direction `up` point to +Z."""
    return FreeCAD.Rotation(up, Z)


def _parse_vec(s):
    m = {"+z": V(0, 0, 1), "z": V(0, 0, 1), "-z": V(0, 0, -1), "+x": V(1, 0, 0), "x": V(1, 0, 0),
         "-x": V(-1, 0, 0), "+y": V(0, 1, 0), "y": V(0, 1, 0), "-y": V(0, -1, 0)}
    if isinstance(s, (list, tuple)):
        return V(*s).normalize()
    v = m.get(str(s).lower())
    if v is not None:
        return v
    return V(*(float(c) for c in str(s).split(","))).normalize()


def _vstr(v):
    for name, w in (("+z", V(0, 0, 1)), ("-z", V(0, 0, -1)), ("+x", V(1, 0, 0)), ("-x", V(-1, 0, 0)),
                    ("+y", V(0, 1, 0)), ("-y", V(0, -1, 0))):
        if (v - w).Length < 1e-6:
            return name
    return "%s,%s,%s" % (fmt(round(v.x, 4)), fmt(round(v.y, 4)), fmt(round(v.z, 4)))


# ------------------------------------------------------------------ orientation

def _candidates(shape, n_faces=6):
    """'Up' directions to try: the six axes, plus 'lie flat on face' for the largest planar faces."""
    ups = [V(0, 0, 1), V(0, 0, -1), V(1, 0, 0), V(-1, 0, 0), V(0, 1, 0), V(0, -1, 0)]
    planar = [f for f in shape.Faces if isinstance(f.Surface, Part.Plane)]
    for f in sorted(planar, key=lambda f: -f.Area)[:n_faces]:
        n = cl._normal(f)
        if n is None:
            continue
        if f.Orientation == "Reversed":
            pass  # normalAt already accounts for orientation
        up = V(-n.x, -n.y, -n.z)  # lay this face on the bed: its outward normal points down
        if all(up.getAngle(u) > math.radians(1) for u in ups):
            ups.append(up)
    return ups


def _score(mesh_pts, tris, facet_list, up, prof, thr, max_bridge, load, shape=None):
    R = _rot_up(up)
    pz = [R.multVec(p) for p in mesh_pts]
    zmin = min(p.z for p in pz)
    zmax = max(p.z for p in pz)
    xs = [p.x for p in pz]
    ys = [p.y for p in pz]
    bx, by = max(xs) - min(xs), max(ys) - min(ys)
    layer = prof["layer"]
    lim = -math.sin(math.radians(thr))
    bed_area, over = 0.0, []
    for f in facet_list:
        i, _, n, area, _, idx = f
        nz = R.multVec(n).z
        zs = [pz[k].z for k in idx]
        if nz < -0.999 and max(zs) - zmin < 0.02:
            bed_area += area
            continue
        cz = sum(zs) / 3.0
        if nz < lim and cz > zmin + layer:
            over.append((f, nz, cz))
    # flat ceilings: decided on the B-rep (anchored bridges); everything steeper from the mesh
    support_area = support_vol = 0.0
    steep = [o for o in over if o[1] >= -0.98]
    if shape is not None:
        sr = shape.copy()
        sr.transformShape(FreeCAD.Placement(V(), R).toMatrix())
        support_area, support_vol = _flat_support(sr, layer, max_bridge)
    for f, nz, cz in steep:
        support_area += f[3]
        support_vol += f[3] * abs(nz) * (cz - zmin)
    bed = prof["bed"]
    fits = ((bx <= bed[0] and by <= bed[1]) or (bx <= bed[1] and by <= bed[0])) and (zmax - zmin) <= bed[2]
    lz = abs(R.multVec(load).z) if load is not None else 0.0
    footprint = bx * by
    small_contact = bed_area < 0.05 * footprint
    score = (support_vol / 1000.0) * 1.0 + (zmax - zmin) * 0.02 + (2.0 if small_contact else 0.0) \
        + 6.0 * lz + (100.0 if not fits else 0.0)
    return {"up": up, "score": score, "height": zmax - zmin, "bed_area": bed_area, "support_area": support_area,
            "support_vol": support_vol, "fits": fits, "load_across": lz, "small_contact": small_contact,
            "size": (bx, by, zmax - zmin)}


def _rank(shape, thr, max_bridge, load, prof):
    mesh = _mesh(shape)
    pts, tris = mesh.Topology
    facet_list = _facets(mesh)
    return sorted((_score(pts, tris, facet_list, up, prof, thr, max_bridge, load, shape) for up in _candidates(shape)),
                  key=lambda r: r["score"])


def orient(object, doc=None, max_overhang=50.0, max_bridge=10.0, load=None, top=5):
    """Rank print orientations for one solid."""
    d = _doc(doc)
    o = _obj(d, object)
    s = global_shape(o)
    if not s.Solids:
        raise UserError("%s has no solid" % o.Name)
    prof = _prof()
    t0 = time.time()
    lv = _parse_vec(load) if load is not None else None
    res = _rank(s, max_overhang, max_bridge, lv, prof)
    lines = ["%s: %d orientations scored (overhang limit %s deg from vertical, bridges <= %s mm, %.1fs)%s" % (
        o.Name, len(res), fmt(max_overhang), fmt(max_bridge), time.time() - t0,
        "; load along %s" % _vstr(lv) if lv else "")]
    lines.append("rank  up(model)       support cm3  support mm2  height  bed contact  notes")
    for k, r in enumerate(res[:top]):
        notes = []
        if not r["fits"]:
            notes.append("DOESN'T FIT BED")
        if r["small_contact"]:
            notes.append("small contact")
        if lv is not None:
            notes.append("load %d%% across layers" % round(100 * r["load_across"]))
        if r["support_vol"] < 1:
            notes.append("no supports")
        lines.append("%-5d %-15s %-12s %-12s %-7s %-12s %s" % (
            k + 1, _vstr(r["up"]), fmt(r["support_vol"] / 1000.0), fmt(round(r["support_area"], 1)),
            fmt(round(r["height"], 1)), fmt(round(r["bed_area"], 1)), ", ".join(notes)))
    best = res[0]
    lines.append("best: up=%r -> freecad_print_prep(object=%r, up=%r)" % (_vstr(best["up"]), o.Name, _vstr(best["up"])))
    return "\n".join(lines)


# ------------------------------------------------------------------ prep helpers

def _bottom_chamfer(s, size):
    if size <= 0:
        return s, "bottom chamfer off"
    edges = [e for e in s.Edges if e.BoundBox.ZMax < 1e-5]
    if not edges:
        return s, "no bed edges to chamfer"
    out, rep = cl.chamfer(s, edges, size)
    return out, "bottom chamfer %s on %d bed edges: %s" % (fmt(size), len(edges), rep)


def _holes_horizontal(s):
    """Horizontal cylindrical holes as (centre point, axis, radius, t0, t1)."""
    found = []
    for f in s.Faces:
        srf = f.Surface
        if not isinstance(srf, Part.Cylinder):
            continue
        a = srf.Axis
        if abs(a.z) > 1e-3:
            continue
        c0 = srf.Center
        u0, u1, v0, v1 = f.ParameterRange
        um, vm = (u0 + u1) / 2, (v0 + v1) / 2
        p = f.valueAt(um, vm)  # a point ON the surface (a full cylinder's centre of mass is on its axis)
        n = f.normalAt(um, vm)
        w = p - c0
        foot = c0 + a * w.dot(a)
        if (foot - p).dot(n) <= 0:  # normal points away from the axis: a boss, not a hole
            continue
        ts = [(v.Point - c0).dot(a) for v in f.Vertexes] or [0]
        b = f.BoundBox
        ts += [(V(x, y, zz) - c0).dot(a) for x in (b.XMin, b.XMax) for y in (b.YMin, b.YMax) for zz in (b.ZMin, b.ZMax)]
        t0, t1 = min(ts), max(ts)
        q = c0 - a * c0.dot(a)  # axis line identity: foot point in the plane through the origin
        key = (round(q.x, 3), round(q.y, 3), round(q.z, 3), round(srf.Radius, 3), round(abs(a.x), 3), round(abs(a.y), 3))
        found.append((key, c0, a, srf.Radius, t0, t1))
    merged = {}
    for key, c0, a, r, t0, t1 in found:
        if key in merged:
            m = merged[key]
            # project onto the stored axis frame
            dt = (c0 - m[1]).dot(m[2])
            merged[key] = (m[0], m[1], m[2], m[3], min(m[4], t0 + dt), max(m[5], t1 + dt))
        else:
            merged[key] = (key, c0, a, r, t0, t1)
    return [(m[1], m[2], m[3], m[4], m[5]) for m in merged.values()]


def _teardrops(s, min_r=1.0):
    tools, skipped = [], 0
    for c0, a, r, t0, t1 in _holes_horizontal(s):
        if r < min_r:
            continue
        w = a.cross(Z)
        if w.Length < 1e-9:
            continue
        w.normalize()
        k = r * math.sqrt(0.5)
        start = c0 + a * (t0 - 0.05)
        pts = [start + w * k + Z * k, start + Z * (r * math.sqrt(2)), start - w * k + Z * k]
        pts.append(pts[0])
        apex = c0 + a * ((t0 + t1) / 2) + Z * (r * math.sqrt(2))
        if not s.isInside(apex, 1e-6, True):
            skipped += 1  # roof would break through a thin wall above the hole
            continue
        face = Part.Face(Part.makePolygon(pts))
        tools.append(face.extrude(a * (t1 - t0 + 0.1)))
    if not tools:
        return s, "teardrops: no horizontal holes%s" % (" (%d skipped: wall above too thin)" % skipped if skipped else "")
    out = s.cut(Part.makeCompound(tools))
    try:
        out = out.removeSplitter()
    except Exception:
        pass
    if not out.isValid() or len(out.Solids) != len(s.Solids):
        return s, "teardrops: boolean failed, left holes round"
    return out, "teardrops on %d horizontal hole(s)%s" % (len(tools), (", %d skipped (thin wall above)" % skipped) if skipped else "")


def _fillet_suggestions(s, thr):
    lim = -math.sin(math.radians(thr))
    n = 0
    for f in s.Faces:
        if not isinstance(f.Surface, (Part.Toroid, Part.Cylinder)) or f.BoundBox.ZMin < 0.05:
            continue
        nrm = cl._normal(f)
        if nrm is not None and nrm.z < lim and f.Area < 400:
            n += 1
    return n


# ------------------------------------------------------------------ engineered supports

def _anchored_bridge(part, face, max_bridge):
    """A flat downward face the printer can bridge: two parallel straight edges at most max_bridge
    apart, each with material directly below and just outside it (a wall to bridge from)."""
    b = face.BoundBox
    if min(b.XLength, b.YLength) > max_bridge + 1e-6:
        return False
    c = face.CenterOfMass
    anchored = []
    for e in face.Edges:
        if not isinstance(e.Curve, (Part.Line, Part.LineSegment)) or e.Length < 0.5:
            continue
        a, bpt = e.Vertexes[0].Point, e.Vertexes[-1].Point
        mid = (a + bpt) * 0.5
        dirv = (bpt - a).normalize()
        out = V(dirv.y, -dirv.x, 0)  # in-plane perpendicular; flip it to point away from the face
        if out.dot(V(mid.x - c.x, mid.y - c.y, 0)) < 0:
            out = out * -1
        probe = mid + out * 0.3 - Z * 0.5
        if part.isInside(probe, 1e-6, True):
            anchored.append((mid, dirv))
    for i in range(len(anchored)):
        for j in range(i + 1, len(anchored)):
            (m1, d1), (m2, d2) = anchored[i], anchored[j]
            if abs(abs(d1.dot(d2)) - 1) > 1e-3:
                continue
            w = m2 - m1
            span = (w - d1 * w.dot(d1)).Length
            if span <= max_bridge + 1e-6:
                return True
    return False


def _flat_support(shape_rot, layer, max_bridge):
    """(area, volume) of flat downward faces that cannot bridge, for an already rotated shape."""
    zmin = shape_rot.BoundBox.ZMin
    area = vol = 0.0
    for f in shape_rot.Faces:
        if not isinstance(f.Surface, Part.Plane) or f.BoundBox.ZMax < zmin + layer + 1e-6:
            continue
        n = cl._normal(f)
        if n is None or n.z >= -0.98:
            continue
        if _anchored_bridge(shape_rot, f, max_bridge):
            continue
        area += f.Area
        vol += f.Area * abs(n.z) * (f.CenterOfMass.z - zmin)
    return area, vol


def _steep_regions(part, thr, max_bridge, min_area, layer):
    """Prisms (support volume before gaps) under every face area that needs support."""
    lim = -math.sin(math.radians(thr))
    prisms, skipped_bridges, mixed = [], 0, []
    zbig = part.BoundBox.ZMax + 10
    for f in part.Faces:
        if f.BoundBox.ZMax < layer + 1e-6:  # on or just above the bed
            continue
        if isinstance(f.Surface, Part.Plane):
            n = cl._normal(f)
            if n is None or n.z >= lim or f.Area < min_area:
                continue
            if n.z < -0.98 and _anchored_bridge(part, f, max_bridge):
                skipped_bridges += 1
                continue
            prisms.append(f.extrude(V(0, 0, -zbig)))
            continue
        # curved face: classify by sampled normals; whole face if (nearly) all steep, raster if mixed
        frac = _steep_fraction(f, lim)
        if frac is None or frac <= 0.05 or f.Area * frac < min_area:
            continue
        if frac >= 0.95:
            try:
                prisms.append(f.extrude(V(0, 0, -zbig)))
                continue
            except Exception:
                pass
        mixed.append(f)
    if mixed:
        prisms += _raster_columns(mixed, lim, layer)
    return prisms, skipped_bridges


def _steep_fraction(f, lim, n=7):
    """Share of a face (sampled on an n x n UV grid, oriented normals) steeper than lim."""
    try:
        u0, u1, v0, v1 = f.ParameterRange
    except Exception:
        return None
    tot = steep = 0
    for i in range(n):
        for j in range(n):
            u = u0 + (u1 - u0) * (i + 0.5) / n
            v = v0 + (v1 - v0) * (j + 0.5) / n
            try:
                if hasattr(f, "isPartOfDomain") and not f.isPartOfDomain(u, v):
                    continue
                nz = f.normalAt(u, v).z
            except Exception:
                continue
            tot += 1
            steep += nz < lim
    return (steep / float(tot)) if tot else None


def _raster_columns(faces, lim, layer, cell=1.0):
    """Columns (boxes from the bed up) under the steep parts of mixed curved faces, rasterised on a
    cell grid and merged along rows; the part cuts later shape their tops to the surface."""
    tops = {}
    for f in faces:
        try:
            pts, tris = f.tessellate(0.2)
        except Exception:
            continue
        for i, j, k in tris:
            pa, pb, pc = pts[i], pts[j], pts[k]
            mid = (pa + pb + pc) * (1.0 / 3)
            if mid.z <= layer:
                continue
            try:
                u, v = f.Surface.parameter(mid)
                nz = f.normalAt(u, v).z
            except Exception:
                continue
            if nz >= lim:
                continue
            ztop = max(pa.z, pb.z, pc.z)
            xs, ys = (pa.x, pb.x, pc.x), (pa.y, pb.y, pc.y)
            for ci in range(int(math.floor(min(xs) / cell)), int(math.floor(max(xs) / cell)) + 1):
                for cj in range(int(math.floor(min(ys) / cell)), int(math.floor(max(ys) / cell)) + 1):
                    key = (ci, cj)
                    if tops.get(key, -1e9) < ztop:
                        tops[key] = ztop
    boxes = []
    rows = {}
    for (ci, cj), zt in tops.items():
        rows.setdefault(cj, []).append((ci, zt))
    for cj, cells in rows.items():
        cells.sort()
        run = [cells[0]]
        for c in cells[1:] + [(None, None)]:
            if c[0] is not None and c[0] == run[-1][0] + 1 and abs(c[1] - run[0][1]) < 2.0:
                run.append(c)
                continue
            zt = max(z for _, z in run)
            boxes.append(Part.makeBox(cell * len(run), cell, zt + 0.5, V(run[0][0] * cell, cj * cell, -0.1)))
            run = [c] if c[0] is not None else []
    return boxes


def _plane_faces(shape, normal, pos):
    """Planar faces of a solid's cross-section at plane normal . p = pos (solid ∩ big plane face;
    more robust than slice() + face making, which fails on open section wires)."""
    b = shape.BoundBox
    pad = 5.0
    if normal.x > 0.5:
        base, L, W = V(pos, b.YMin - pad, b.ZMin - pad), b.YLength + 2 * pad, b.ZLength + 2 * pad
        plane = Part.makePlane(L, W, base, V(1, 0, 0), V(0, 1, 0))
    else:
        # with normal +Y the plane's 2nd axis is normal x dir; dir -X makes it +Z (dir +X would go down)
        base, L, W = V(b.XMax + pad, pos, b.ZMin - pad), b.XLength + 2 * pad, b.ZLength + 2 * pad
        plane = Part.makePlane(L, W, base, V(0, 1, 0), V(-1, 0, 0))
    try:
        sec = shape.common(plane)
    except Exception:
        return None
    faces = [f for f in sec.Faces if f.Area > 1e-6]
    if not faces:
        return None
    return faces[0] if len(faces) == 1 else Part.makeCompound(faces)


def _wall_profiles(part, need, axis, pos, wall, gap):
    """2D support profile in the vertical plane axis=pos: (need region) minus (part section,
    taken at the wall's centre and both faces, grown by `gap`). Returns planar faces or None."""
    n = V(1, 0, 0) if axis == "x" else V(0, 1, 0)
    need2d = _plane_faces(need, n, pos)
    if need2d is None:
        return None
    secs = []
    for dp in (-wall / 2, 0.0, wall / 2):
        f = _plane_faces(part, n, pos + dp)
        if f is not None:
            f.translate(n * (-dp))
            secs.append(f)
    if secs:
        p2d = secs[0].fuse(secs[1:]) if len(secs) > 1 else secs[0]
        try:
            p2d = p2d.removeSplitter()
        except Exception:
            pass
        grown_faces = []
        for f in p2d.Faces:  # offset face by face: makeOffset2D rejects some fused compounds
            try:
                g = f.makeOffset2D(gap, 0, False, False, False)
            except Exception:
                return None  # can't guarantee the gap here: leave this wall out
            grown_faces += g.Faces
        if not grown_faces:
            return None
        grown = grown_faces[0].fuse(grown_faces[1:]) if len(grown_faces) > 1 else grown_faces[0]
        prof2d = need2d.cut(grown)
    else:
        prof2d = need2d
    faces = [f for f in prof2d.Faces if f.Area > wall * 1.0]
    return faces or None


def build_supports(part, prof, thr=50.0, max_bridge=10.0, spacing=3.0, z_gap=None, xy_gap=None, wall=None,
                   min_area=2.0, tabs=True, budget=120.0):
    """Engineered supports as a grid of thin vertical walls, each designed in its own plane
    (2D offsets are robust where 3D offsets fail): one-layer gap to the part all round in the wall
    plane, line contact on top, optional grip fins at bed level."""
    layer = prof["layer"]
    nozzle = prof["nozzle"]
    z_gap = layer if z_gap is None else z_gap
    xy_gap = max(0.4, nozzle) if xy_gap is None else xy_gap
    wall = 2 * nozzle if wall is None else wall
    prisms, bridges = _steep_regions(part, thr, max_bridge, min_area, layer)
    info = {"bridges_skipped": bridges, "z_gap": z_gap, "xy_gap": z_gap, "wall": wall, "spacing": spacing,
            "islands": 0, "tabs": 0, "solid": 0, "too_close": 0, "dropped": 0}
    if not prisms:
        return None, info
    need = prisms[0].fuse(prisms[1:]) if len(prisms) > 1 else prisms[0]
    b = part.BoundBox
    need = need.common(Part.makeBox(b.XLength + 40, b.YLength + 40, b.ZLength + 20, V(b.XMin - 20, b.YMin - 20, 0)))
    if not need.Solids:
        return None, info
    nb = need.BoundBox
    t_start = time.time()
    walls = []
    for axis, lo, hi, c in (("y", nb.YMin, nb.YMax, nb.Center.y), ("x", nb.XMin, nb.XMax, nb.Center.x)):
        k0 = int(math.floor((lo - c) / spacing))
        k1 = int(math.ceil((hi - c) / spacing))
        for k in range(k0, k1 + 1):
            if time.time() - t_start > budget:
                info["timeout"] = True
                break
            pos = c + k * spacing + 1e-4  # avoid slicing exactly through model faces
            if not (lo - wall / 2 <= pos <= hi + wall / 2):
                continue
            faces = _wall_profiles(part, need, axis, pos, wall, z_gap)
            if not faces:
                continue
            n = V(0, 1, 0) if axis == "y" else V(1, 0, 0)
            for f in faces:
                g = f.copy()
                g.translate(n * (-wall / 2))
                w = g.extrude(n * wall)
                if part.distToShape(w)[0] < 0.5 * z_gap:  # safety net
                    info["too_close"] += 1
                    continue
                walls.append((axis, w))
    if not walls:
        return None, info
    pieces = [w for _, w in walls]
    if tabs:
        for axis, w in walls:
            wb = w.BoundBox
            if axis != "y" or wb.ZMin > 0.01:
                continue
            for x0 in (wb.XMin - 4, wb.XMax):  # a small fin on the bed at either end of the wall
                fin = Part.makeBox(4, wall, min(2.0, wb.ZLength), V(x0, wb.YMin, 0))
                if part.distToShape(fin)[0] >= z_gap:
                    pieces.append(fin)
                    info["tabs"] += 1
                    break
    info["islands"] = len(walls)
    return Part.makeCompound(pieces), info


# ------------------------------------------------------------------ print prep tool

def print_prep(object, up="auto", doc=None, chamfer=None, teardrops=True, supports=True, max_overhang=50.0,
               max_bridge=10.0, spacing=3.0, load=None, name=None, tabs=True):
    d = _doc(doc)
    o = _obj(d, object)
    s = global_shape(o)
    if not s.Solids:
        raise UserError("%s has no solid" % o.Name)
    prof = _prof()
    t0 = time.time()
    lines = []
    if str(up).lower() == "auto":
        res = _rank(s, max_overhang, max_bridge, _parse_vec(load) if load is not None else None, prof)
        upv = res[0]["up"]
        lines.append("orientation: auto -> up=%s (support %s cm3, height %s)" % (
            _vstr(upv), fmt(res[0]["support_vol"] / 1000.0), fmt(round(res[0]["height"], 1))))
    else:
        upv = _parse_vec(up)
        lines.append("orientation: up=%s" % _vstr(upv))
    b0 = s.BoundBox
    p = s.copy()
    p.transformShape(FreeCAD.Placement(V(), _rot_up(upv)).toMatrix())
    bb = p.BoundBox
    p.translate(V(b0.Center.x - bb.Center.x, b0.Center.y - bb.Center.y, -bb.ZMin))
    if teardrops:
        p, rep = _teardrops(p)
        lines.append(rep)
    # supports are built against the part BEFORE the bed chamfer: chamfering only removes material,
    # so it can only widen the gaps (building after it leaves slivers against the chamfer)
    sup = info = None
    if supports:
        sup, info = build_supports(p, prof, max_overhang, max_bridge, spacing, tabs=tabs)
    ch = prof["nozzle"] if chamfer is None else float(chamfer)
    p, rep = _bottom_chamfer(p, ch)
    lines.append(rep)
    nf = _fillet_suggestions(p, max_overhang)
    if nf:
        lines.append("suggestion: %d downward-facing fillet face(s) print as overhangs; freecad_print_fix turns them into 45-deg chamfers" % nf)
    base = name or (o.Name + "_print")
    if d.getObject(base) is not None:
        from .output import auto_checkpoint
        ck = auto_checkpoint(d, "before print_prep " + o.Name)
        if ck:
            lines.append(ck)
    po = cl.show(p, base, doc=d)
    if FreeCAD.GuiUp:
        try:
            po.ViewObject.ShapeColor = o.ViewObject.ShapeColor
        except Exception:
            pass
    lines.append("print copy: %s (original %s untouched), size %s" % (
        po.Name, o.Name, "x".join(fmt(round(v, 2)) for v in (p.BoundBox.XLength, p.BoundBox.YLength, p.BoundBox.ZLength))))
    old_sup = d.getObject(base + "_supports")
    if supports:
        if sup is None:
            if old_sup is not None:
                d.removeObject(old_sup.Name)
            if info.get("timeout"):
                lines.append("supports: NOT generated - over the time budget (very fragmented overhangs); "
                             "try another orientation (freecad_orient) or a larger spacing")
            elif info.get("too_close"):
                lines.append("supports: NOT generated - all %d piece(s) ended up too close to the part" % info["too_close"])
            else:
                lines.append("supports: none needed%s" % (
                    " (%d flat span(s) <= %s mm will bridge)" % (info["bridges_skipped"], fmt(max_bridge)) if info["bridges_skipped"] else ""))
        else:
            so = cl.show(sup, base + "_supports", doc=d)
            if FreeCAD.GuiUp:
                so.ViewObject.ShapeColor = (0.75, 0.75, 0.75)
            # supports were cut away from the part, so they can't sit inside it: the boundary
            # distance alone proves clearance (a common() against many islands is very slow)
            gap = p.distToShape(sup)[0]
            cv = 0.0 if gap > 1e-6 else -1.0
            lines.append("supports: %s, %d wall piece(s), %s cm3 (~%s g PETG), %d grip tab(s); walls %s mm every %s mm; "
                         "gaps: %s mm vertical (1 layer), %s mm sideways%s" % (
                             so.Name, info["islands"], fmt(sup.Volume / 1000.0), fmt(round(sup.Volume / 1000.0 * 1.27, 1)),
                             info["tabs"], fmt(info["wall"]), fmt(info["spacing"]), fmt(info["z_gap"]), fmt(info["xy_gap"]),
                             ("; %d flat span(s) left to bridge" % info["bridges_skipped"]) if info["bridges_skipped"] else ""))
            if info.get("solid"):
                lines.append("note: %d island(s) too small for a grid were left solid (harder to remove)" % info["solid"])
            ok = gap > 0.5 * min(info["z_gap"], info["xy_gap"])
            lines.append("check: min part-support distance %s -> %s" % (
                fmt(gap), "OK" if ok else "PROBLEM (supports touch the part)"))
            if info.get("too_close"):
                lines.append("WARNING: %d support piece(s) came out closer than half the gap and were left out - "
                             "check those overhangs (freecad_section)" % info["too_close"])
            if info.get("dropped"):
                lines.append("note: %d tiny support fragment(s) under %s mm3 dropped" % (info["dropped"], fmt(max(5.0, info["wall"] ** 2 * 4))))
    elif old_sup is not None:
        d.removeObject(old_sup.Name)
    d.recompute()
    lines.append("(%.1fs) export both with freecad_export(objects=[%r%s], single_file=true)" % (
        time.time() - t0, po.Name, (", %r" % (base + "_supports")) if d.getObject(base + "_supports") else ""))
    return "\n".join(lines)


METHODS = {"orient": orient, "print_prep": print_prep}
