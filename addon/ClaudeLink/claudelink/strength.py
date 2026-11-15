"""Quick linear-static strength check of a printed part (FreeCAD FEM: Gmsh + CalculiX).

Works on a copy in a scratch document; the original is never touched. Layer-aware: besides the
von Mises stress (compared with the in-plane strength) it reports the tension ACROSS the layers
(compared with the much lower layer-adhesion strength) for the given print `up` direction."""
import math
import os
import shutil
import time

import FreeCAD
import Part

from . import cl
from .analysis import global_shape, _vec
from .server import UserError
from .snapshot import fmt

V = FreeCAD.Vector

# Typical datasheet values for printed parts (solid, 100 % infill / enough perimeters).
# E, strength in MPa; z = layer-adhesion (tension across layers). Override per material in the print
# profile: materials.<NAME>.mech = {"E":..., "nu":..., "xy":..., "z":..., "density":...}
MECH = {
    "PLA": {"E": 3500, "nu": 0.36, "xy": 50, "z": 25, "density": 1.24},
    "PETG": {"E": 2100, "nu": 0.38, "xy": 45, "z": 25, "density": 1.27},
    "ABS": {"E": 2200, "nu": 0.35, "xy": 40, "z": 18, "density": 1.04},
    "ASA": {"E": 2200, "nu": 0.35, "xy": 42, "z": 20, "density": 1.07},
    "PA": {"E": 1600, "nu": 0.39, "xy": 55, "z": 35, "density": 1.14},
    "NYLON": {"E": 1600, "nu": 0.39, "xy": 55, "z": 35, "density": 1.14},
    "PC": {"E": 2300, "nu": 0.37, "xy": 60, "z": 35, "density": 1.20},
    "PLA-CF": {"E": 5000, "nu": 0.35, "xy": 45, "z": 18, "density": 1.29},
    "PETG-CF": {"E": 3500, "nu": 0.36, "xy": 45, "z": 20, "density": 1.30},
}


def _doc(name=None):
    from .handlers import _doc as d
    return d(name)


def _obj(doc, key):
    from .handlers import _obj as o
    return o(doc, key)


def mech(material=None):
    from . import profile
    name, m, _ = profile.material(material)
    base = MECH.get(name.upper()) or MECH.get(name.upper().split("-")[0])
    src = "typical %s" % name
    if base is None:
        base, src = MECH["PETG"], "typical PETG (no data for %s)" % name
    out = dict(base)
    if isinstance(m.get("mech"), dict):
        out.update({k: float(v) for k, v in m["mech"].items() if k in out})
        src = "%s from print profile" % name
    return name, out, src


def _vec_of(s):
    from .printprep import _parse_vec
    return _parse_vec(s)


def _faces_for(shape, ref, owner):
    """Face indices (1-based) of `shape` matched by a selector string. Accepts 'Sel' or 'Obj:Sel'."""
    sel = ref.split(":", 1)[1] if ":" in ref else ref
    if ":" in ref and ref.split(":", 1)[0] not in (owner.Name, owner.Label):
        raise UserError("ref %r names another object (the load case is for %s)" % (ref, owner.Name))
    try:
        subs = cl.sel(shape, sel)
    except ValueError as e:
        raise UserError(str(e))
    out = []
    for sub in subs:
        if sub.ShapeType != "Face":
            continue
        n = cl.name_of(shape, sub)
        if n:
            out.append(n)
    if not out:
        raise UserError("ref %r matched no faces of %s" % (ref, owner.Name))
    return out


def _face_desc(shape, p):
    best = None
    for i, f in enumerate(shape.Faces, 1):
        d = f.distToShape(Part.Vertex(p))[0]
        if best is None or d < best[0]:
            best = (d, i, f)
    _, i, f = best
    t = type(f.Surface).__name__
    r = getattr(f.Surface, "Radius", None)
    return "Face%d (%s%s)" % (i, t.replace("Surface", "").lower() or t, (" r=%s" % fmt(round(r, 2))) if r else "")


def _hotspots(pts, vals, frac, radius, n=4):
    top = max(vals) if vals else 0
    if top <= 0:
        return []
    cand = sorted((v, i) for i, v in enumerate(vals) if v >= frac * top)[::-1]
    out = []
    for v, i in cand:
        p = pts[i]
        if all((p - q).Length > radius for _, q in out):
            out.append((v, p))
            if len(out) >= n:
                break
    return out


def strength(object, fixed, loads, up="+z", material=None, mesh_size=None, doc=None, image=True, keep=False,
             max_nodes=250000):
    """fixed: list of face refs (selectors) held rigidly. loads: list of {"ref": selector, "force": N,
    "dir": "-z" | [x,y,z]} (total force spread over the matched faces). up: print direction (model coords)."""
    import ObjectsFem
    from femtools import ccxtools
    from femmesh.gmshtools import GmshTools
    from .output import _KeepActive
    d = _doc(doc)
    o = _obj(d, object)
    shape = global_shape(o)
    if not shape.Solids:
        raise UserError("%s has no solid" % o.Name)
    if isinstance(fixed, str):
        fixed = [fixed]
    if isinstance(loads, dict):
        loads = [loads]
    if not fixed or not loads:
        raise UserError("need at least one fixed face ref and one load")
    t0 = time.time()
    mname, mp, msrc = mech(material)
    upv = _vec_of(up)
    fix_names = sorted({n for r in fixed for n in _faces_for(shape, r, o)}, key=lambda s: int(s[4:]))
    load_specs = []
    for ld in loads:
        if "ref" not in ld or "force" not in ld:
            raise UserError("each load needs 'ref' and 'force' (N), optional 'dir' (default -z)")
        names = _faces_for(shape, ld["ref"], o)
        dv = _vec_of(ld.get("dir", "-z"))
        load_specs.append((names, float(ld["force"]), dv, ld["ref"]))
    if set(fix_names) & {n for names, _, _, _ in load_specs for n in names}:
        raise UserError("a face is both fixed and loaded")
    bb = shape.BoundBox
    diag = bb.DiagonalLength
    h = float(mesh_size) if mesh_size else max(0.4, min(diag / 25.0, 6.0))
    show_img = bool(image) and FreeCAD.GuiUp
    lines = []
    img = None
    with _KeepActive():
        tmp = FreeCAD.newDocument("Strength_" + o.Name, hidden=not (show_img or keep))
        work = None
        try:
            part = tmp.addObject("Part::Feature", "Part")
            part.Shape = shape
            an = ObjectsFem.makeAnalysis(tmp, "Analysis")
            solver = ObjectsFem.makeSolverCalculiXCcxTools(tmp)
            an.addObject(solver)
            mat = ObjectsFem.makeMaterialSolid(tmp)
            mm = mat.Material
            mm.update({"Name": mname, "YoungsModulus": "%s MPa" % mp["E"], "PoissonRatio": str(mp["nu"]),
                       "Density": "%s kg/m^3" % (mp["density"] * 1000)})
            mat.Material = mm
            an.addObject(mat)
            fx = ObjectsFem.makeConstraintFixed(tmp)
            fx.References = [(part, tuple(fix_names))]
            an.addObject(fx)
            for k, (names, force, dv, _) in enumerate(load_specs):
                ln = tmp.addObject("Part::Feature", "Dir%d" % k)
                ln.Shape = Part.makeLine(V(0, 0, 0), dv * 10)
                if FreeCAD.GuiUp:
                    ln.ViewObject.Visibility = False
                f = ObjectsFem.makeConstraintForce(tmp)
                f.References = [(part, tuple(names))]
                f.Force = "%s N" % force
                f.Direction = (ln, ["Edge1"])
                f.Reversed = False
                an.addObject(f)
            mesh = ObjectsFem.makeMeshGmsh(tmp, "Mesh")
            mesh.Shape = part
            mesh.ElementOrder = "2nd"
            an.addObject(mesh)
            tmp.recompute()
            for attempt in range(4):
                mesh.CharacteristicLengthMax = h
                err = GmshTools(mesh).create_mesh()
                nn = mesh.FemMesh.NodeCount
                if nn == 0:
                    raise UserError("meshing failed (%s); check the geometry (freecad_check_geometry)" % (err or "no nodes"))
                if nn <= max_nodes:
                    break
                h *= (nn / float(max_nodes)) ** (1 / 3.0) * 1.1
            t_mesh = time.time() - t0
            fea = ccxtools.FemToolsCcx(an, solver)
            fea.purge_results()
            work = getattr(fea, "working_dir", None)
            import contextlib
            import io
            refined = 0
            while True:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):   # femtools prints its internals
                    ok = fea.run()
                work = getattr(fea, "working_dir", work)
                res = [x for x in an.Group if x.isDerivedFrom("Fem::FemResultObject")]
                if ok and res:
                    break
                log = buf.getvalue() + str(getattr(fea, "ccx_stdout", "") or "")
                # curved/helical parts: 2nd-order elements can come out inverted on a coarse mesh -> refine
                if "jacobian" in log.lower() and refined < 2:
                    refined += 1
                    h *= 0.6
                    mesh.CharacteristicLengthMax = h
                    GmshTools(mesh).create_mesh()
                    fea = ccxtools.FemToolsCcx(an, solver)
                    fea.purge_results()
                    continue
                if "jacobian" in log.lower():
                    raise UserError("CalculiX: distorted mesh elements even at %s mm - try mesh_size smaller" % fmt(h))
                raise UserError("CalculiX failed (is every load path connected to a fixed face?)")
            r = res[0]
            nodes = r.Mesh.FemMesh.Nodes
            pts = [nodes[i] for i in r.NodeNumbers]
            vm = list(r.vonMises)
            disp = list(r.DisplacementLengths)
            n = upv
            sxx, syy, szz = r.NodeStressXX, r.NodeStressYY, r.NodeStressZZ
            sxy, sxz, syz = r.NodeStressXY, r.NodeStressXZ, r.NodeStressYZ
            layer = [n.x * n.x * sxx[i] + n.y * n.y * syy[i] + n.z * n.z * szz[i]
                     + 2 * (n.x * n.y * sxy[i] + n.x * n.z * sxz[i] + n.y * n.z * syz[i]) for i in range(len(pts))]
            i_vm = max(range(len(vm)), key=vm.__getitem__)
            i_d = max(range(len(disp)), key=disp.__getitem__)
            fixed_faces = [shape.getElement(nm) for nm in fix_names]

            def near_fixed(p, tol=1.5 * h):
                return min(f.distToShape(Part.Vertex(p))[0] for f in fixed_faces) < tol

            # a rigid clamp blocks Poisson contraction and creates artificial stress right at the fixed
            # faces; judge layer tension away from them (the clamp peak is reported separately)
            band = 0.75 * h
            boxes = []
            for f in fixed_faces:
                b = f.BoundBox
                b.enlarge(band)
                boxes.append(b)
            clamp = [any(b.isInside(p) for b in boxes) and near_fixed(p, band) for p in pts]
            free = [i for i in range(len(pts)) if not clamp[i]] or list(range(len(pts)))
            i_l = max(free, key=layer.__getitem__)
            i_lc = max(range(len(layer)), key=layer.__getitem__)

            sf_xy = mp["xy"] / vm[i_vm] if vm[i_vm] > 0 else float("inf")
            sf_z = mp["z"] / layer[i_l] if layer[i_l] > 0 else float("inf")
            total = sum(f for _, f, _, _ in load_specs)
            lines.append("%s: %s N total on %d load(s), fixed %s; material %s (E %s MPa, strength %s in-layer / %s across "
                         "layers, %s)" % (o.Name, fmt(total), len(load_specs), ",".join(fix_names), mname, fmt(mp["E"]),
                                          fmt(mp["xy"]), fmt(mp["z"]), msrc))
            lines.append("mesh: %d nodes, element size %s mm (2nd order); solve %.1fs" % (
                len(pts), fmt(round(h, 2)), time.time() - t0 - t_mesh))
            lines.append("max von Mises %s MPa at %s on %s -> safety factor %s (in-layer)" % (
                fmt(round(vm[i_vm], 2)), _vec(pts[i_vm]), _face_desc(shape, pts[i_vm]), fmt(round(sf_xy, 2))))
            if layer[i_l] > 0:
                lines.append("max tension across layers (print up=%s) %s MPa at %s on %s -> safety factor %s (layer adhesion)" % (
                    up, fmt(round(layer[i_l], 2)), _vec(pts[i_l]), _face_desc(shape, pts[i_l]), fmt(round(sf_z, 2))))
            else:
                lines.append("layers (print up=%s): only compression across layers - layer adhesion not loaded" % up)
            if clamp[i_lc] and layer[i_lc] > max(layer[i_l], 0) * 1.3 and layer[i_lc] > 0.2 * mp["z"]:
                lines.append("  (%s MPa across layers right at the fixed face - mostly an artefact of the perfectly rigid "
                             "clamp; real if the part is glued/bonded there)" % fmt(round(layer[i_lc], 1)))
            rel = disp[i_d] / max(bb.XLength, bb.YLength, bb.ZLength)
            lines.append("max deflection %s mm at %s%s" % (
                fmt(round(disp[i_d], 3)), _vec(pts[i_d]),
                " - LARGE (>3% of the part size): linear result is only indicative" if rel > 0.03 else ""))
            worst = min(sf_xy, sf_z)
            gov = "layer adhesion" if sf_z < sf_xy else "in-layer strength"
            verdict = ("OK" if worst >= 3 else "MARGINAL (printed parts vary a lot; aim for >= 3)" if worst >= 1.5
                       else "LIKELY TO FAIL")
            lines.append("verdict: %s - weakest safety factor %s (%s)" % (verdict, fmt(round(worst, 2)), gov))
            if sf_z < sf_xy and sf_z < 3:
                lines.append("tip: tension runs across the layers; a print orientation with the load in the layer "
                             "plane is stronger (freecad_orient(load=...)) than a thicker part")
            hs = _hotspots(pts, vm, 0.6, max(diag / 8.0, 3 * h))
            if hs:
                lines.append("stress hot spots (>= 60% of max), thicken or round these:")
                for v, p in hs:
                    lines.append("  %s MPa at %s on %s%s" % (
                        fmt(round(v, 1)), _vec(p), _face_desc(shape, p),
                        " - next to a fixed face: the peak there depends on the mesh; a fillet at that corner helps" if near_fixed(p) else ""))
            lines.append("assumes solid material (100% infill or walls thick enough to be all perimeters) and the load "
                         "as given; a quick linear check, not a certification")
            if show_img:
                try:
                    import FreeCADGui
                    rm = r.Mesh
                    for x in tmp.Objects:
                        if x.Name != rm.Name and getattr(x, "ViewObject", None) is not None:
                            x.ViewObject.Visibility = False
                    rm.ViewObject.Visibility = True
                    try:
                        rm.ViewObject.DisplayMode = "Faces"
                    except Exception:
                        pass
                    rm.ViewObject.setNodeColorByScalars(list(r.NodeNumbers), vm)
                    from .handlers import view
                    img = view(doc=tmp.Name, views=["iso", "dir:-1,1,-1"], size=320, objects=[rm.Name])
                except Exception as e:
                    lines.append("(no stress image: %s)" % e)
        finally:
            if work and os.path.isdir(work) and not keep:
                shutil.rmtree(work, ignore_errors=True)
            if not keep:
                FreeCAD.closeDocument(tmp.Name)
    if keep:
        lines.append("kept the analysis in document %s (FEM workbench: double-click CCX_Results)" % tmp.Name)
    lines.append("(%.1fs)" % (time.time() - t0))
    text = "\n".join(lines)
    if img:
        img["text"] = text + "\nimage: von Mises stress, green low -> red high"
        return img
    return text


METHODS = {"strength": strength}
