"""Print readiness: one verdict per part (geometry, bed fit, supports, walls, small holes, warping,
tipping, overlaps, optional strength) and automatic fixes on the model itself (downward fillets ->
45-deg chamfers, one-layer membranes over face-down counterbores, teardrop holes, corner pads)."""
import math
import time

import FreeCAD
import Part

from . import cl
from .analysis import global_shape, _resolve_objects, _vec, _mesh, _facets, _thickness_samples, _clusters
from .server import UserError
from .snapshot import fmt

V = FreeCAD.Vector
Z = V(0, 0, 1)

# relative warping tendency (shrinkage x stiffness); large flat parts in these lift at the corners
WARP = {"ABS": 1.0, "ASA": 0.9, "PC": 1.0, "PA": 1.0, "NYLON": 1.0, "PP": 0.9, "PETG": 0.35, "PLA": 0.2,
        "TPU": 0.05, "PLA-CF": 0.12, "PETG-CF": 0.2, "PA-CF": 0.5, "HIPS": 0.8}


def _doc(name=None):
    from .handlers import _doc as d
    return d(name)


def _obj(doc, key):
    from .handlers import _obj as o
    return o(doc, key)


def _r(x, n=1):
    return fmt(round(x, n))


def _baked(s):
    t = s.copy()
    if not t.Placement.isIdentity():
        m = t.Placement.toMatrix()
        t.Placement = FreeCAD.Placement()
        t.transformShape(m, True)
    return t


def _rotate(s, rot):
    t = s.copy()
    t.transformShape(FreeCAD.Placement(V(), rot).toMatrix(), True)
    return t


def _orient(s, up, prof):
    from .printprep import _rank, _parse_vec
    if str(up).lower() == "auto":
        best = _rank(s, 50.0, 10.0, None, prof)[0]
        return best["up"], best
    return _parse_vec(up), None


# ------------------------------------------------------------------ warping

def _bed_faces(s, tol=1e-3):
    zmin = s.BoundBox.ZMin
    return [f for f in s.Faces if isinstance(f.Surface, Part.Plane) and f.BoundBox.ZMax - zmin < tol
            and abs(abs(f.Surface.Axis.z) - 1) < 1e-6]


def _sharp_corners(faces, max_deg=120.0):
    """Convex corners of the bed footprint without rounding: (point, interior angle)."""
    out = []
    for f in faces:
        w = f.OuterWire
        es = w.OrderedEdges
        if not es or not all(isinstance(e.Curve, (Part.Line, Part.LineSegment)) for e in es):
            # mixed edges: only check line-line joints
            pass
        n = len(es)
        for i in range(n):
            a, b = es[i], es[(i + 1) % n]
            if not (isinstance(a.Curve, (Part.Line, Part.LineSegment)) and isinstance(b.Curve, (Part.Line, Part.LineSegment))):
                continue
            va = [v.Point for v in a.Vertexes]
            vb = [v.Point for v in b.Vertexes]
            common = [p for p in va for q in vb if (p - q).Length < 1e-6]
            if not common:
                continue
            c = common[0]
            pa = va[0] if (va[1] - c).Length < 1e-6 else va[1]
            pb = vb[0] if (vb[1] - c).Length < 1e-6 else vb[1]
            u, v = pa - c, pb - c
            if u.Length < 1e-9 or v.Length < 1e-9:
                continue
            ang = math.degrees(u.getAngle(v))
            mid = c + (u.normalize() + v.normalize()) * 0.05
            if ang < max_deg and f.isInside(mid, 1e-6, True):      # convex (interior inside the face)
                out.append((c, ang))
    return out


def warp_risk(srot, material, prof):
    """srot = part in print orientation. Returns dict(level, score, corners, footprint, notes)."""
    name = (material or "PETG").upper()
    k = WARP.get(name, WARP.get(name.split("-")[0], 0.4))
    faces = _bed_faces(srot)
    b = srot.BoundBox
    area = sum(f.Area for f in faces)
    if faces:
        fb = faces[0].BoundBox
        for f in faces[1:]:
            fb.add(f.BoundBox)
        L = math.hypot(fb.XLength, fb.YLength)
        w = min(fb.XLength, fb.YLength)
    else:
        L, w = 0.0, 0.0
    corners = _sharp_corners(faces)
    score = k * (L / 100.0) * (1 + 0.08 * min(len(corners), 8))
    level = "high" if score >= 0.7 else "medium" if score >= 0.3 else "low"
    notes = []
    if level == "high":
        notes.append("brim 6-10 mm, enclosure/draft shield, bed adhesive; corner pads at %d sharp corner(s) "
                     "(freecad_print_fix ears=true) or round the footprint corners r>=5" % len(corners))
    elif level == "medium":
        notes.append("brim 4-6 mm or round the footprint corners r>=3%s" % (
            "; corner pads at %d sharp corner(s)" % len(corners) if corners else ""))
    tip = None
    if w > 0 and b.ZLength / w > 4:
        tip = "tall and narrow (height %s on a %s mm wide footprint): add a brim or a temporary stabiliser, " \
              "print slower near the top" % (_r(b.ZLength), _r(w))
    elif faces and area < 0.08 * b.XLength * b.YLength:
        tip = "small bed contact (%s mm2 of a %sx%s footprint): may detach - brim or a flat on the bottom" % (
            _r(area, 0), _r(b.XLength), _r(b.YLength))
    return {"level": level, "score": score, "corners": corners, "diag": L, "material": name, "notes": notes,
            "tip": tip, "area": area}


# ------------------------------------------------------------------ readiness check

def _small_holes(srot, nozzle):
    out = []
    for f in srot.Faces:
        s = f.Surface
        if isinstance(s, Part.Cylinder) and s.Radius < 1.0:
            u0, u1, v0, v1 = f.ParameterRange
            p, n = f.valueAt((u0 + u1) / 2, (v0 + v1) / 2), f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
            foot = s.Center + s.Axis * (p - s.Center).dot(s.Axis)
            if (foot - p).dot(n) > 0:     # hole, not a pin
                out.append((2 * s.Radius, f.CenterOfMass))
    return out


def ready(objects=None, up="auto", material=None, strength=None, doc=None):
    """See the MCP tool."""
    from . import profile
    from .printprep import _rot_up, _vstr
    d = _doc(doc)
    objs = _resolve_objects(d, objects)
    if not objs:
        raise UserError("no solids to check")
    prof = profile.load()
    mat = material or profile.material(None)[0]
    nozzle, layer, bed = prof["nozzle"], prof["layer"], prof["bed"]
    t0 = time.time()
    out = ["print readiness (%s, nozzle %s, layer %s, bed %s):" % (mat, fmt(nozzle), fmt(layer),
                                                                   "x".join(fmt(x) for x in bed))]
    shapes = {}
    worst = 0
    for o in objs:
        s = _baked(global_shape(o))
        shapes[o.Name] = s
        fixes, notes = [], []
        bad = 0
        if not s.isValid():
            fixes.append("shape is INVALID - freecad_check_geometry, rebuild the failing boolean")
            bad = 2
        if len(s.Solids) != 1:
            fixes.append("%d separate solids - fuse them or export separately" % len(s.Solids))
            bad = max(bad, 1)
        opens = [sh for sh in s.Shells if not sh.isClosed()]
        if opens:
            fixes.append("%d open shell(s) - not watertight" % len(opens))
            bad = 2
        upv, rank = _orient(s, up, prof)
        rot = _rot_up(upv)
        sr = _rotate(s, rot)
        b = sr.BoundBox
        fits = ((b.XLength <= bed[0] and b.YLength <= bed[1]) or (b.XLength <= bed[1] and b.YLength <= bed[0])) \
            and b.ZLength <= bed[2]
        if not fits:
            fixes.append("%sx%sx%s doesn't fit the bed - freecad_split" % (_r(b.XLength), _r(b.YLength), _r(b.ZLength)))
            bad = 2
        if rank is None:
            from .printprep import _rank
            try:
                rank = [r for r in _rank(s, 50.0, 10.0, None, prof) if (r["up"] - upv).Length < 1e-6][0]
            except Exception:
                rank = None
        sup = rank["support_vol"] / 1000.0 if rank else 0.0
        if sup >= 0.05:
            notes.append("needs ~%s cm3 of support" % _r(sup, 2))
        # downward fillets that print as overhangs
        nfil = len(_fillets_down(sr, layer))
        if nfil:
            fixes.append("%d downward-facing fillet(s) print as overhangs - freecad_print_fix (-> 45-deg chamfers)" % nfil)
            bad = max(bad, 1)
        # counterbores opening downwards (ceiling with a hole through it)
        nmem = len(_membranes(sr, layer))
        if nmem:
            fixes.append("%d face-down counterbore/step ceiling(s) with a hole through - freecad_print_fix adds a "
                         "one-layer membrane to bridge" % nmem)
            bad = max(bad, 1)
        from .printprep import _holes_horizontal
        nh = 0
        for c0, a, r, t0, t1 in _holes_horizontal(sr):
            probe = c0 + a * ((t0 + t1) / 2) + Z * (r * 1.2)
            if r >= 1.0 and sr.isInside(probe, 1e-6, True):     # round top: no teardrop yet
                nh += 1
        if nh:
            notes.append("%d horizontal hole(s) - teardrops print rounder (freecad_print_fix)" % nh)
        # walls
        try:
            mesh = _mesh(sr)
            res, _ = _thickness_samples(mesh, _facets(mesh), 1500)
        except Exception:
            res = []
        if res:
            discs = _membrane_discs(sr, layer)

            def in_membrane(c):
                return any(math.hypot(c.x - m.x, c.y - m.y) <= rr + 0.05 and z0 - 0.01 <= c.z <= z0 + layer + 0.01
                           for m, rr, z0 in discs)
            if discs:
                res = [r for r in res if not in_membrane(r[1])]
                notes.append("%d one-layer membrane(s) over holes (intended - poke through after printing)" % len(discs))
            thin = [r for r in res if r[0] < nozzle]
            weak = [r for r in res if nozzle <= r[0] < 2 * nozzle]
            back = rot.inverted()
            if thin:
                loc = ", ".join("%s near %s" % (_r(v, 2), _vec(back.multVec(c))) for v, c, n in _clusters(thin, 2.0, 3))
                fixes.append("walls thinner than the nozzle (won't print): %s - thicken to >= %s" % (loc, fmt(2 * nozzle)))
                bad = 2
            elif weak:
                notes.append("%d%% of wall samples are single-line (< %s mm)" % (round(100.0 * len(weak) / len(res)), fmt(2 * nozzle)))
        sh = _small_holes(sr, nozzle)
        if sh:
            notes.append("%d hole(s) under 2 mm diameter print undersized/closed - drill out or enlarge" % len(sh))
        w = warp_risk(sr, mat, prof)
        if w["level"] != "low":
            (fixes if w["level"] == "high" else notes).append("warping risk %s (%s, %s mm footprint diagonal): %s" % (
                w["level"], w["material"], _r(w["diag"], 0), "; ".join(w["notes"])))
            if w["level"] == "high":
                bad = max(bad, 1)
        if w["tip"]:
            notes.append(w["tip"])
        verdict = ["READY", "FIX FIRST (minor)", "NOT PRINTABLE AS IS"][bad]
        if bad == 0 and notes:
            verdict = "READY (notes)"
        worst = max(worst, bad)
        out.append("%s: %s  [up=%s, %sx%sx%s, %s]" % (o.Name, verdict, _vstr(upv), _r(b.XLength), _r(b.YLength),
                                                     _r(b.ZLength), "no supports" if sup < 0.05 else "supports"))
        out += ["  fix: " + f for f in fixes]
        out += ["  note: " + n for n in notes]
    # overlaps between parts of an assembly
    names = list(shapes)
    ov = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, c = shapes[names[i]], shapes[names[j]]
            if not a.BoundBox.intersect(c.BoundBox):
                continue
            try:
                v = a.common(c).Volume
            except Exception:
                v = 0.0
            if v > 0.01:
                ov.append("%s & %s overlap %s mm3" % (names[i], names[j], _r(v, 2)))
    if ov:
        out.append("assembly: parts overlap - they won't fit together: " + "; ".join(ov[:6]))
        worst = max(worst, 1)
    if strength:
        from .strength import strength as _st
        try:
            r = _st(doc=d.Name, image=False, **strength)
            txt = r if isinstance(r, str) else r.get("text", "")
            vl = [l for l in txt.splitlines() if l.lower().startswith("verdict")]
            out.append("strength %s: %s" % (strength.get("object"), vl[0] if vl else txt.splitlines()[-1]))
        except Exception as e:
            out.append("strength check failed: %s" % e)
    prof_m = profile.material(mat)[1]
    if not prof_m.get("measured"):
        out.append("fits/clearances use profile guesses until the FitGauge is printed")
    out.append("overall: %s (%.1fs)" % (["READY", "fix the items above first", "NOT printable as is"][worst],
                                       time.time() - t0))
    return "\n".join(out)


# ------------------------------------------------------------------ automatic fixes

def _membranes(sr, layer):
    """Downward-facing planar ceilings (above the bed) whose inner boundary is a circle: the hole through a
    face-down counterbore or stepped hole. Returns [(circle centre, radius, z)]."""
    zmin = sr.BoundBox.ZMin
    out = []
    for f in sr.Faces:
        if not isinstance(f.Surface, Part.Plane):
            continue
        n = cl._normal(f)
        if n is None or n.z > -0.999 or f.BoundBox.ZMin < zmin + layer:
            continue
        for w in f.Wires:
            if w.isSame(f.OuterWire):
                continue
            es = w.Edges
            if len(es) >= 1 and all(hasattr(e.Curve, "Radius") and hasattr(e.Curve, "Center") for e in es):
                c = es[0].Curve
                if abs(abs(V(c.Axis).z) - 1) < 1e-6:
                    out.append((V(c.Center), c.Radius, f.BoundBox.ZMin))
    return out


def _membrane_discs(sr, layer):
    """Sacrificial one-layer discs (as added by print_fix): a downward disc face with an upward disc face of the
    same circle exactly one layer above. Returns [(centre, radius, z)]."""
    downs, ups = [], []
    for f in sr.Faces:
        if not isinstance(f.Surface, Part.Plane) or len(f.Wires) != 1 or len(f.Edges) != 1:
            continue
        e = f.Edges[0]
        if not isinstance(e.Curve, Part.Circle):
            continue
        n = cl._normal(f)
        if n is None or abs(abs(n.z) - 1) > 1e-6:
            continue
        (downs if n.z < 0 else ups).append((V(e.Curve.Center), e.Curve.Radius))
    out = []
    for c, r in downs:
        for c2, r2 in ups:
            # after fusing, the disc's underside merges with the counterbore ceiling: below may be wider
            if r2 <= r + 1e-3 and math.hypot(c.x - c2.x, c.y - c2.y) < 1e-3 and abs(c2.z - c.z - layer) < 1e-3:
                out.append((c2, r2 + 0.02, c.z))
    return out


def _fillets_down(sr, layer, thr=50.0):
    lim = -math.sin(math.radians(thr))
    zmin = sr.BoundBox.ZMin
    size = sr.BoundBox.DiagonalLength
    out = []
    for f in sr.Faces:
        s = f.Surface
        if not isinstance(s, (Part.Cylinder, Part.Toroid)):
            continue
        r = s.Radius if isinstance(s, Part.Cylinder) else s.MinorRadius
        if r > 0.25 * size:
            continue
        if isinstance(s, Part.Cylinder):
            if abs(s.Axis.z) > 1e-3:           # only horizontal blends overhang
                continue
            u0, u1, v0, v1 = f.ParameterRange
            if abs(u1 - u0) > math.pi + 1e-3:   # a full hole/boss, not a blend
                continue
        nrm = cl._normal(f)
        lowest = min((f.normalAt(u, v).z for u in (f.ParameterRange[0], (f.ParameterRange[0] + f.ParameterRange[1]) / 2,
                                                    f.ParameterRange[1]) for v in (f.ParameterRange[2], f.ParameterRange[3])),
                     default=0)
        if (nrm is not None and nrm.z < lim) or lowest < -0.95:
            out.append((f, r))
    return out


def _fillets_to_chamfers(sr, layer):
    found = _fillets_down(sr, layer)
    if not found:
        return sr, 0, ""
    try:
        sharp = sr.defeaturing([f for f, r in found])
    except Exception as e:
        return sr, 0, "fillet removal failed (%s)" % e
    if not sharp.isValid() or not sharp.Solids:
        return sr, 0, "fillet removal gave an invalid shape - left as is"
    done = 0
    out = sharp
    for f, r in found:
        # the recovered sharp edge lies within ~r of where the fillet face was
        cand = [e for e in out.Edges if e.distToShape(f)[0] < r * 0.75 and isinstance(e.Curve, (Part.Line, Part.LineSegment, Part.Circle))]
        if not cand:
            continue
        e = min(cand, key=lambda e: e.distToShape(f)[0])
        try:
            nxt = out.makeChamfer(r, [e])
            if nxt.isValid():
                out = nxt
                done += 1
        except Exception:
            continue
    if done == 0:
        return sr, 0, "fillets found but chamfering failed"
    return out, done, ""


def print_fix(object, up="auto", fillets=True, membranes=True, teardrops=True, ears=False, ear_d=None,
              name=None, doc=None):
    """See the MCP tool."""
    from . import profile
    from .printprep import _rot_up, _vstr, _teardrops
    from .output import auto_checkpoint
    d = _doc(doc)
    o = _obj(d, object)
    s = _baked(global_shape(o))
    if not s.Solids:
        raise UserError("%s has no solid" % o.Name)
    prof = profile.load()
    layer = prof["layer"]
    t0 = time.time()
    upv, _ = _orient(s, up, prof)
    rot = _rot_up(upv)
    sr = _rotate(s, rot)
    lines = ["%s: print orientation up=%s" % (o.Name, _vstr(upv))]
    v0 = s.Volume
    if fillets:
        sr2, n, err = _fillets_to_chamfers(sr, layer)
        if n:
            sr = sr2
            lines.append("  %d downward fillet(s) -> 45-deg chamfers (print without support)" % n)
        elif err:
            lines.append("  " + err)
    if membranes:
        mem = _membranes(sr, layer)
        if mem:
            discs = [Part.makeCylinder(r + 0.01, layer, V(c.x, c.y, z)) for c, r, z in mem]
            nxt = sr.fuse(discs)
            try:
                nxt = nxt.removeSplitter()
            except Exception:
                pass
            if nxt.isValid():
                sr = nxt
                lines.append("  %d one-layer membrane(s) (%s mm) over face-down counterbores/steps - poke or drill "
                             "through them after printing" % (len(mem), fmt(layer)))
    if teardrops:
        sr2, rep = _teardrops(sr)
        if sr2 is not sr:
            sr = sr2
        lines.append("  " + rep)
    if ears:
        w = warp_risk(sr, profile.material(None)[0], prof)
        ed = float(ear_d or 12.0)
        h = 2 * layer
        zmin = sr.BoundBox.ZMin
        pads = [Part.makeCylinder(ed / 2, h, V(c.x, c.y, zmin)) for c, ang in w["corners"]]
        if pads:
            nxt = sr.fuse(pads)
            try:
                nxt = nxt.removeSplitter()
            except Exception:
                pass
            if nxt.isValid():
                sr = nxt
                lines.append("  %d corner pad(s) d%s x %s mm at sharp footprint corners - cut off after printing" % (
                    len(pads), fmt(ed), fmt(h)))
        else:
            lines.append("  no sharp footprint corners for pads")
    if len(lines) == 1 or all(("no horizontal holes" in l or "no sharp" in l) for l in lines[1:]):
        lines.append("  nothing to fix")
        return "\n".join(lines)
    res = _rotate(sr, rot.inverted())
    if not res.isValid():
        raise UserError("fix produced an invalid shape - nothing written")
    ck = auto_checkpoint(d, "before print_fix " + o.Name)
    d.openTransaction("print_fix")
    try:
        if name is None and o.TypeId == "Part::Feature":
            gp = o.getGlobalPlacement()
            r2 = res.copy()
            r2.transformShape(gp.inverse().toMatrix(), True)
            r2.Placement = o.Placement
            o.Shape = r2
            target = o.Name
        else:
            no = cl.show(res, name or (o.Name + "_fixed"), doc=d)
            target = no.Name
            if FreeCAD.GuiUp and no.Name != o.Name:
                try:
                    o.ViewObject.Visibility = False
                except Exception:
                    pass
        d.recompute()
    finally:
        d.commitTransaction()
    lines.append("-> %s (vol %s -> %s mm3, %s)%s (%.1fs)" % (target, _r(v0), _r(res.Volume), "valid" if res.isValid()
                                                             else "INVALID", "; " + ck if ck else "", time.time() - t0))
    return "\n".join(lines)


METHODS = {"ready": ready, "print_fix": print_fix}
