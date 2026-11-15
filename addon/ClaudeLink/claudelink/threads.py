"""Find screw threads (helical ridges/grooves around a cylinder) in a solid.

Used to simulate threaded parts (the helix detail defeats the tet mesher, so it is replaced by a plain rod at the
root diameter / a plain bore at the major diameter - both on the safe side) and to keep the print checks from
asking for supports under the flanks of a thread that is printed upright."""
import math

import FreeCAD
import Part

V = FreeCAD.Vector
_ANALYTIC = (Part.Plane, Part.Cylinder, Part.Cone, Part.Sphere, Part.Toroid)
_CACHE = {}


def _key(shape):
    try:
        b = shape.BoundBox
        return (shape.hashCode(), len(shape.Faces), len(shape.Edges), round(b.DiagonalLength, 4), round(b.ZMin, 4))
    except Exception:
        return None


def find(shape):
    """[{p, a, rc, rmin, rmax, t0, t1, external, faces}] - one entry per thread. p/a = axis point and unit
    direction, rc = radius of the cylinder the thread sits on, rmin..rmax = radial band of the helical faces,
    t0..t1 = their span along the axis from p, external = a bolt (True) or a tapped hole (False)."""
    k = _key(shape)
    if k is not None and _CACHE.get("key") == k:
        return _CACHE["val"]
    out = _find(shape)
    _CACHE["key"], _CACHE["val"] = k, out
    return out


def _find(shape):
    faces = shape.Faces
    hel = [i for i, f in enumerate(faces) if not isinstance(f.Surface, _ANALYTIC)]
    if len(hel) < 3:
        return []
    edge_faces = {}
    for i, f in enumerate(faces):
        for e in f.Edges:
            edge_faces.setdefault(e.hashCode(), set()).add(i)
    nb = {}
    for fs in edge_faces.values():
        for i in fs:
            nb.setdefault(i, set()).update(fs - {i})
    helset = set(hel)
    # helical faces connected through shared edges form one thread (flank - crest - flank, turn after turn)
    groups, seen = [], set()
    for i in hel:
        if i in seen:
            continue
        g, stack = [], [i]
        seen.add(i)
        while stack:
            j = stack.pop()
            g.append(j)
            for m in nb.get(j, ()):
                if m in helset and m not in seen:
                    seen.add(m)
                    stack.append(m)
        groups.append(g)
    out = []
    for g in groups:
        if len(g) < 3:
            continue
        cyls = {m for j in g for m in nb.get(j, ()) if isinstance(faces[m].Surface, Part.Cylinder)}
        if not cyls:
            continue
        # the cylinder most of the group's neighbours share = the thread's root (bolt) or minor bore (hole)
        best = {}
        for m in cyls:
            s = faces[m].Surface
            a = V(s.Axis)
            a.normalize()
            key = None
            for kk in best:
                a2, p2, r2 = kk
                if abs(abs(a.dot(V(*a2))) - 1) < 1e-4 and abs(r2 - s.Radius) < 0.02:
                    d = V(s.Center) - V(*p2)
                    if (d - V(*a2) * d.dot(V(*a2))).Length < 0.05:
                        key = kk
                        break
            if key is None:
                key = ((a.x, a.y, a.z), (s.Center.x, s.Center.y, s.Center.z), s.Radius)
                best[key] = []
            best[key].append(m)
        (ax, pc, rc), members = max(best.items(), key=lambda kv: len(kv[1]))
        a, p = V(*ax), V(*pc)
        rs, ts = [], []
        for j in g:
            try:
                pts = faces[j].tessellate(0.2)[0]
            except Exception:
                continue
            for q in pts[::max(1, len(pts) // 60)]:
                d = q - p
                t = d.dot(a)
                rs.append((d - a * t).Length)
                ts.append(t)
        if len(rs) < 12:
            continue
        rmin, rmax, t0, t1 = min(rs), max(rs), min(ts), max(ts)
        depth = rmax - rmin
        if not (0.15 <= depth <= 0.3 * rmax + 0.5) or t1 - t0 < 1.0 or abs(rmin - rc) > 0.15 * depth + 0.1 \
                and abs(rmax - rc) > 0.15 * depth + 0.1:
            continue
        f0 = faces[members[0]]
        try:
            u0, u1, v0, v1 = f0.ParameterRange
            um, vm = (u0 + u1) / 2, (v0 + v1) / 2
            pm = f0.valueAt(um, vm)
            radial = pm - p
            radial = radial - a * radial.dot(a)
            external = f0.normalAt(um, vm).dot(radial) > 0
        except Exception:
            external = abs(rmin - rc) < abs(rmax - rc)
        out.append({"p": p, "a": a, "rc": rc, "rmin": rmin, "rmax": rmax, "t0": t0, "t1": t1,
                    "external": external, "faces": len(g)})
    return out


def describe(th):
    d = 2 * th["rmax"]
    return "%s thread ~M%s x %s mm" % ("external" if th["external"] else "internal", ("%.1f" % d).rstrip("0").rstrip("."),
                                        ("%.1f" % (th["t1"] - th["t0"])).rstrip("0").rstrip("."))


def plain(shape):
    """(shape with every thread replaced by a plain cylinder, [descriptions]). A bolt becomes a rod at its root
    diameter, a tapped hole a bore at its major diameter - less material than the real thread, so a strength
    check on the result is on the safe side. Returns the original shape and [] when nothing was replaced."""
    ths = find(shape)
    if not ths:
        return shape, []
    res, notes = shape, []
    for th in ths:
        p, a = th["p"], th["a"]
        base = p + a * (th["t0"] - 0.02)
        length = th["t1"] - th["t0"] + 0.04
        try:
            if th["external"]:
                r_in = min(th["rc"], th["rmin"]) - 0.05
                tool = Part.makeCylinder(th["rmax"] + 0.3, length, base, a).cut(Part.makeCylinder(r_in, length, base, a))
            else:
                tool = Part.makeCylinder(th["rmax"] + 0.05, length, base, a)
            new = res.cut(tool)
            if new.isValid() and len(new.Solids) == len(res.Solids) and new.Volume > 0.5 * res.Volume:
                res = new
                notes.append(describe(th))
        except Exception:
            continue
    if not notes:
        return shape, []
    try:
        res = res.removeSplitter()
    except Exception:
        pass
    return res, notes


def upright_tester(shape, up, min_cos=0.85):
    """Function(point) -> True when the point lies on a thread whose axis is within ~30 degrees of the print
    direction `up`: such flanks step out a fraction of a line per layer and print without supports."""
    ths = [t for t in find(shape) if abs(t["a"].dot(up)) >= min_cos and t["rmax"] - t["rmin"] <= 3.0]
    if not ths:
        return None

    def on_thread(q):
        for t in ths:
            d = q - t["p"]
            s = d.dot(t["a"])
            if t["t0"] - 0.1 <= s <= t["t1"] + 0.1:
                r = math.sqrt(max(0.0, d.dot(d) - s * s))
                if t["rmin"] - 0.1 <= r <= t["rmax"] + 0.1:
                    return True
        return False
    return on_thread
