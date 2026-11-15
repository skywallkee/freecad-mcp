"""Bring a downloaded or scanned mesh (STL/OBJ/3MF/PLY) into a usable shape: report its defects, repair it,
optionally decimate it, find its flat faces and holes/bosses (with fitted size and axis) and convert it to a
solid that booleans, measuring and the other tools accept."""
import math
import os
import time

import FreeCAD
import Part

from . import cl
from .server import UserError
from .snapshot import fmt

V = FreeCAD.Vector


def _doc(name=None):
    from .handlers import _doc as d
    return d(name)


def _f(x, n=2):
    return fmt(round(float(x), n))


def _stats(m):
    out = {"facets": m.CountFacets, "points": m.CountPoints, "solid": m.isSolid(),
           "nonmanifold": m.hasNonManifolds(), "selfint": False, "flipped": m.countNonUniformOrientedFacets(),
           "components": m.countComponents(), "degenerate": m.hasPointsOnEdge() or m.hasInvalidPoints()}
    try:
        out["selfint"] = m.hasSelfIntersections()
    except Exception:
        pass
    return out


def _stat_line(s):
    probs = []
    if not s["solid"]:
        probs.append("open (holes in the surface)")
    if s["nonmanifold"]:
        probs.append("non-manifold edges")
    if s["selfint"]:
        probs.append("self-intersections")
    if s["flipped"]:
        probs.append("%d flipped facets" % s["flipped"])
    if s["components"] > 1:
        probs.append("%d separate pieces" % s["components"])
    if s["degenerate"]:
        probs.append("degenerate facets")
    return "%d facets, %d points: %s" % (s["facets"], s["points"], ", ".join(probs) if probs else "clean, watertight")


def _fit_cylinder(pts, nrm):
    """Axis from the normals (all perpendicular to it), then a least-squares circle in the plane across it."""
    import numpy as np
    N = np.array(nrm)
    P = np.array(pts)
    w, vecs = np.linalg.eigh(N.T @ N)
    a = vecs[:, 0]
    a = a / np.linalg.norm(a)
    u = np.cross(a, [1, 0, 0] if abs(a[0]) < 0.9 else [0, 1, 0])
    u /= np.linalg.norm(u)
    v = np.cross(a, u)
    x, y = P @ u, P @ v
    A = np.c_[2 * x, 2 * y, np.ones(len(x))]
    b = x * x + y * y
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy = sol[0], sol[1]
    r = math.sqrt(max(sol[2] + cx * cx + cy * cy, 0.0))
    t = P @ a
    centre = cx * u + cy * v + a * t.mean()
    resid = np.sqrt((x - cx) ** 2 + (y - cy) ** 2) - r
    return V(*centre), V(*a), r, float(t.max() - t.min()), float(np.abs(resid).mean())


def _round_features(m, min_facets=6):
    """Holes and bosses parallel to X, Y or Z: connected curved wall regions (neighbouring facets bend a little,
    normals perpendicular to the axis) with a least-squares circle fit."""
    import numpy as np
    pts, tris = m.Topology
    P = np.array([(p.x, p.y, p.z) for p in pts])
    T = np.array(tris)
    N = np.array([f.Normal for f in m.Facets])
    nb = {}
    for i, (a, b, c) in enumerate(tris):
        for e in ((a, b), (b, c), (c, a)):
            nb.setdefault((min(e), max(e)), []).append(i)
    adj = [[] for _ in range(len(tris))]
    for fs in nb.values():
        if len(fs) == 2:
            adj[fs[0]].append(fs[1])
            adj[fs[1]].append(fs[0])
    found = []
    for ax in (np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0])):
        cand = set(np.where(np.abs(N @ ax) < 0.03)[0].tolist())
        curved = set()
        for i in cand:
            for j in adj[i]:
                if j in cand and 0.0003 < 1 - float(N[i] @ N[j]) < 0.3:
                    curved.add(i)
                    break
        seen = set()
        for i0 in curved:
            if i0 in seen:
                continue
            comp, stack = [], [i0]
            seen.add(i0)
            while stack:
                i = stack.pop()
                comp.append(i)
                for j in adj[i]:
                    if j in curved and j not in seen:
                        seen.add(j)
                        stack.append(j)
            if len(comp) < min_facets:
                continue
            idx = np.unique(T[comp].ravel())
            c, a, r, L, err = _fit_cylinder(P[idx].tolist(), N[comp].tolist())
            ang = np.degrees(np.ptp(np.unwrap(np.arctan2(N[comp] @ np.cross(ax, [0, 0, 1] if ax[2] < 0.9 else [1, 0, 0]),
                                                          N[comp] @ (np.cross(ax, np.cross(ax, [0, 0, 1] if ax[2] < 0.9 else [1, 0, 0])))))))                 if len(comp) > 2 else 0
            if r <= 0 or err > max(0.05, 0.03 * r) or ang < 60:
                continue
            f0 = comp[len(comp) // 2]
            p0 = V(*P[T[f0][0]])
            foot = c + a * (p0 - c).dot(a)
            hole = (foot - p0).dot(V(*N[f0])) > 0
            found.append(("hole" if hole else "boss", r, c, V(*ax), L, err))
    out = []
    for f in found:      # one entry per feature: same radius on the same axis line = one cylinder in pieces
        dup = None
        for k, g in enumerate(out):
            if f[0] == g[0] and abs(f[1] - g[1]) < 0.05 and abs(abs(f[3].dot(g[3])) - 1) < 1e-3:
                dv = f[2] - g[2]
                if (dv - g[3] * dv.dot(g[3])).Length < 0.5:
                    dup = k
                    break
        if dup is None:
            out.append(f)
        else:
            g = out[dup]
            out[dup] = (g[0], g[1], g[2], g[3], g[4] + f[4], max(g[5], f[5]))
    return out


class _Quiet:
    """Mute FreeCAD's console while OCC/TNP spams 'unresolved duplicate element mapping' per triangle."""
    def __enter__(self):
        self.saved = []
        for obs in ("Console", "Report", "ReportOutput", "File"):
            for typ in ("Log", "Wrn"):
                try:
                    self.saved.append((obs, typ, FreeCAD.Console.GetStatus(obs, typ)))
                    FreeCAD.Console.SetStatus(obs, typ, False)
                except Exception:
                    pass
        return self

    def __exit__(self, *a):
        for obs, typ, st in self.saved:
            try:
                FreeCAD.Console.SetStatus(obs, typ, bool(st))
            except Exception:
                pass
        return False


def mesh_import(path=None, mesh=None, name=None, repair=True, solid=True, features=True, max_facets=60000,
                scale=1.0, doc=None, solid_facets=8000):
    """See the MCP tool."""
    import Mesh
    d = _doc(doc)
    t0 = time.time()
    if path:
        if not os.path.isfile(path):
            raise UserError("no file %s" % path)
        m = Mesh.Mesh(path)
        base = name or os.path.splitext(os.path.basename(path))[0]
    elif mesh:
        o = d.getObject(mesh)
        if o is None or not hasattr(o, "Mesh"):
            raise UserError("no mesh object %r" % mesh)
        m = o.Mesh.copy()
        base = name or o.Name
    else:
        raise UserError("give path (stl/obj/3mf/ply) or mesh (a Mesh object name)")
    base = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in base) or "Imported"
    if scale != 1.0:
        mat = FreeCAD.Matrix()
        mat.scale(scale, scale, scale)
        m.transform(mat)
    b = m.BoundBox
    out = ["%s: %sx%sx%s mm" % (os.path.basename(path) if path else mesh, _f(b.XLength), _f(b.YLength), _f(b.ZLength))]
    if max(b.XLength, b.YLength, b.ZLength) < 2.0:
        out.append("note: tiny - the file is probably in metres or inches; try scale=1000 or 25.4")
    s0 = _stats(m)
    out.append("as loaded: " + _stat_line(s0))
    if repair:
        def run(mesh, steps):
            for fn, args in steps:
                try:
                    getattr(mesh, fn)(*args)
                except Exception:
                    pass
        gentle = (("removeDuplicatedPoints", ()), ("removeDuplicatedFacets", ()), ("removeInvalidPoints", ()),
                  ("fixDegenerations", (0.0,)), ("removeNeedles", (0.0001,)), ("fixSelfIntersections", ()),
                  ("removeNonManifolds", ()), ("removeNonManifoldPoints", ()), ("harmonizeNormals", ()),
                  ("fillupHoles", (1000,)), ("fixIndices", ()))
        strong = (("removeFoldsOnSurface", ()), ("fixSelfIntersections", ()), ("removeNonManifolds", ()),
                  ("fillupHoles", (1000,)), ("harmonizeNormals", ()), ("fixIndices", ()))
        orig = m.copy()
        run(m, gentle)
        if m.countComponents() > 1:
            small = max(20, int(0.002 * m.CountFacets))
            try:
                m.removeComponents(small)        # loose debris
            except Exception:
                pass
        # removing self-intersecting facets opens holes that hole-filling closes cleanly on some meshes and
        # not on others: try each level and keep the best (watertight > no self-intersections > no
        # degenerate facets > fewest facets changed) - never worse than the file as loaded
        cands = [("as loaded", orig), ("gentle", m)]
        m2 = m.copy()
        run(m2, strong)
        cands.append(("strong", m2))
        m3 = orig.copy()            # one pass: drop intersecting/folded facets first, fill holes only at the end
        run(m3, [x for x in gentle if x[0] != "fillupHoles"] + list(strong))
        cands.append(("full", m3))

        def score(mm):
            st = _stats(mm)
            return (not st["solid"], st["selfint"], st["degenerate"], st["nonmanifold"],
                    abs(mm.CountFacets - orig.CountFacets))
        best_name, m = min(cands, key=lambda c: score(c[1]))
        repair_level = best_name
        try:
            if m.Volume < 0:
                m.flipNormals()
        except Exception:
            pass
        s1 = _stats(m)
        b1 = m.BoundBox
        left = [k for k in ("selfint", "degenerate", "nonmanifold") if s1[k]]
        out.append("repaired (%s pass kept): " % repair_level + _stat_line(s1) + " - %sx%sx%s mm" % (_f(b1.XLength), _f(b1.YLength), _f(b1.ZLength)) +
                   ("; the remaining %s could not be removed automatically - the solid below is checked separately"
                    % "/".join({"selfint": "self-intersections", "degenerate": "degenerate facets",
                                "nonmanifold": "non-manifold edges"}[k] for k in left) if left else ""))
    if m.CountFacets > max_facets:
        n0 = m.CountFacets
        try:
            m.decimate(0.05, 1.0 - float(max_facets) / n0)
            out.append("decimated %d -> %d facets (tolerance 0.05 mm) to keep the solid manageable" % (n0, m.CountFacets))
        except Exception:
            out.append("note: %d facets - conversion to a solid will be slow" % n0)
    mo = d.getObject(base + "_mesh") or d.addObject("Mesh::Feature", base + "_mesh")
    mo.Mesh = m
    if FreeCAD.GuiUp:
        try:
            mo.ViewObject.Visibility = not solid
        except Exception:
            pass
    feats = []
    if features:
        try:
            for seg in m.getSegmentsOfType("Plane", 0.02, max(30, m.CountFacets // 2000)):
                sub = m.meshFromSegment(seg)
                if sub.Area < 0.01 * m.Area:
                    continue
                fac = [m.Facets[i] for i in seg[:50]]
                n = V()
                for f in fac:
                    n += V(*f.Normal)
                n.normalize()
                feats.append(("plane", sub.Area, sub.BoundBox.Center, n))
        except Exception:
            pass
        try:
            feats += _round_features(m)
        except Exception as e:
            out.append("note: round-feature search failed (%s)" % e)
    if feats:
        planes = sorted([x for x in feats if x[0] == "plane"], key=lambda x: -x[1])[:6]
        cyl = [x for x in feats if x[0] in ("hole", "boss")]
        out.append("flat faces (largest first): " + "; ".join(
            "%s mm2 normal %s at %s" % (_f(a, 0), _vn(n), _vp(c)) for _, a, c, n in planes))
        if cyl:
            out.append("round features (fitted):")
            for kind, r, c, a, L, err in sorted(cyl, key=lambda x: (x[0], -x[1]))[:12]:
                out.append("  %s d%s, %s long, axis %s through %s (fit +-%s)" % (
                    kind, _f(2 * r), _f(L, 1), _vn(a), _vp(c), _f(err, 3)))
    if solid:
        t1 = time.time()
        try:
            ms = m.copy()
            note = ""
            if ms.CountFacets > solid_facets:      # B-rep with one face per triangle grows superlinearly
                n0 = ms.CountFacets
                ms.decimate(0.05, 1.0 - float(solid_facets) / n0)
                note = " (from a %d-facet copy reduced to %d, 0.05 mm tolerance; the full mesh stays in %s)" % (
                    n0, ms.CountFacets, mo.Name)
            with _Quiet():
                sh = Part.Shape()
                sh.makeShapeFromMesh(ms.Topology, 0.05)
                try:
                    sh.sewShape()
                except Exception:
                    pass
                so = Part.Solid(Part.Shell(sh.Faces))
                if so.Volume < 0:
                    so.reverse()
                try:
                    so = so.removeSplitter()          # merges coplanar triangles into real planar faces
                except Exception:
                    pass
            ok = so.isValid()
            po = cl.show(so, base, doc=d)
            out.append("solid %s: %s, %d faces after merging flat areas, volume %s cm3 (%.1fs)%s%s" % (
                po.Name, "valid" if ok else "INVALID - booleans may fail; freecad_check_geometry", len(so.Faces),
                _f(so.Volume / 1000.0), time.time() - t1, note,
                "" if s0["solid"] or repair else " - mesh was open, solid may be broken"))
            out.append("curved areas stay triangulated: model new features as separate solids and fuse/cut them")
        except Exception as e:
            out.append("conversion to a solid failed (%s) - the mesh object %s is still usable for measuring and "
                       "freecad_compare_mesh" % (e, mo.Name))
    out.append("mesh object: %s%s (%.1fs)" % (mo.Name, " (hidden)" if solid else "", time.time() - t0))
    return "\n".join(out)


def _vn(v):
    return "(%s)" % ", ".join(_f(c, 3) for c in (v.x, v.y, v.z))


def _vp(v):
    return "(%s)" % ", ".join(_f(c, 2) for c in (v.x, v.y, v.z))


METHODS = {"mesh_import": mesh_import}
