"""Phase 3: exports, CAD-vs-mesh deviation, and document checkpoints."""
import base64
import glob
import math
import os
import random
import re
import shutil
import time

import FreeCAD

from . import snapshot as snap
from .analysis import global_shape, _resolve_objects, _vec, _clusters, _defl
from .server import UserError
from .snapshot import fmt

V = FreeCAD.Vector


def _doc(name=None):
    from .handlers import _doc as d
    return d(name)


def _obj(doc, key):
    from .handlers import _obj as o
    return o(doc, key)


class _KeepActive:
    """Closing a (hidden) temp document can leave no active document; restore the previous one."""

    def __enter__(self):
        self.name = FreeCAD.ActiveDocument.Name if FreeCAD.ActiveDocument else None
        return self

    def __exit__(self, *exc):
        if self.name and self.name in FreeCAD.listDocuments():
            FreeCAD.setActiveDocument(self.name)
            if FreeCAD.GuiUp:
                try:
                    import FreeCADGui
                    FreeCADGui.ActiveDocument = FreeCADGui.getDocument(self.name)
                except Exception:
                    pass
        return False


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_").lower() or "part"


def _kb(path):
    n = os.path.getsize(path)
    return "%.0f KB" % (n / 1024.0) if n < 10 * 1024 * 1024 else "%.1f MB" % (n / 1048576.0)


# ------------------------------------------------------------------ export

_MESH_FMT = {"stl", "3mf", "obj", "amf", "ply", "off"}


def export(objects=None, format="stl", dir=None, path=None, tolerance=0.01, angular=0.1, single_file=False,
           doc=None, names=None, colors=False):
    d = _doc(doc)
    fmt_ = format.lower().lstrip(".")
    if colors:
        if fmt_ != "3mf":
            raise UserError("colors=true needs format 3mf")
        return _export_color_3mf(d, objects, dir, path, tolerance, angular)
    if fmt_ == "stp":
        fmt_ = "step"
    if fmt_ not in _MESH_FMT | {"step", "brep", "iges"}:
        raise UserError("format must be stl, 3mf, obj, amf, ply, step, iges or brep")
    objs = _resolve_objects(d, objects)
    if not objs:
        raise UserError("nothing to export")
    if path is None and dir is None:
        if not d.FileName:
            raise UserError("document is unsaved: give dir or path")
        dir = os.path.join(os.path.dirname(d.FileName), "stl" if fmt_ in _MESH_FMT else "step")
    lines = []
    t0 = time.time()
    if single_file or path:
        target = path or os.path.join(dir, _safe(d.Name) + "." + fmt_)
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        jobs = [(target, objs)]
    else:
        os.makedirs(dir, exist_ok=True)
        jobs = []
        for i, o in enumerate(objs):
            base = (names or {}).get(o.Name) or _safe(o.Label)
            jobs.append((os.path.join(dir, base + "." + fmt_), [o]))
    for target, group in jobs:
        existed = os.path.exists(target)
        if fmt_ in _MESH_FMT:
            import Mesh
            import MeshPart
            meshes = [MeshPart.meshFromShape(Shape=global_shape(o), LinearDeflection=tolerance,
                                             AngularDeflection=angular, Relative=False) for o in group]
            m = meshes[0]
            for extra in meshes[1:]:
                m.addMesh(extra)
            if fmt_ == "stl":
                m.write(target)
            else:
                with _KeepActive():
                    tmp = FreeCAD.newDocument("ClaudeExport", hidden=True)
                    try:
                        mf = tmp.addObject("Mesh::Feature", "M")
                        mf.Mesh = m
                        Mesh.export([mf], target, tolerance)
                    finally:
                        FreeCAD.closeDocument(tmp.Name)
            info = "%d facets, %s" % (m.CountFacets, "watertight" if m.isSolid() else "NOT watertight")
            if m.hasNonManifolds():
                info += ", non-manifold"
        else:
            if fmt_ == "brep":
                shapes = [global_shape(o) for o in group]
                import Part
                (shapes[0] if len(shapes) == 1 else Part.makeCompound(shapes)).exportBrep(target)
            else:
                import Import
                Import.export(group, target)
            info = "%d object(s)" % len(group)
        lines.append("%s %s (%s, %s)" % ("replaced" if existed else "wrote", target, _kb(target), info))
    head = "exported %d file(s) as %s in %.1fs%s" % (len(jobs), fmt_.upper(), time.time() - t0,
                                                    (", mesh tolerance %s mm" % fmt(tolerance)) if fmt_ in _MESH_FMT else "")
    return "\n".join([head] + lines)


# ------------------------------------------------------------------ multi-colour 3MF

def _obj_rgb(o, k):
    from .splitting import _COLOURS
    from . import cl
    c = cl.color_of(o)
    if c is not None:
        return c
    for name, rgb in _COLOURS.items():          # headless: colour-split names end in _<colour>
        if re.search(r"(^|_)%s($|_)" % name, o.Name.lower()):
            return rgb
    from .illustrate import PALETTE
    return PALETTE[k % len(PALETTE)]


def _export_color_3mf(d, objects, dir, path, tolerance, angular):
    """One 3MF with every part in its own colour: a single object whose triangles carry a base-material
    colour per part (core 3MF), plus PrusaSlicer/Orca volume metadata (one volume per part, extruder per
    colour) so the parts stay assembled and land on the right filament."""
    import zipfile
    import MeshPart
    from .splitting import _colour_name
    from xml.sax.saxutils import quoteattr
    objs = _resolve_objects(d, objects)
    if not objs:
        raise UserError("nothing to export")
    if path is None:
        if dir is None:
            if not d.FileName:
                raise UserError("document is unsaved: give dir or path")
            dir = os.path.join(os.path.dirname(d.FileName), "stl")
        path = os.path.join(dir, _safe(d.Name) + "_color.3mf")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    t0 = time.time()
    colours = []        # distinct rgb in first-seen order -> extruder index
    parts = []
    for k, o in enumerate(objs):
        rgb = tuple(round(c, 3) for c in _obj_rgb(o, k))
        if rgb not in colours:
            colours.append(rgb)
        m = MeshPart.meshFromShape(Shape=global_shape(o), LinearDeflection=tolerance, AngularDeflection=angular,
                                   Relative=False)
        pts, facets = m.Topology
        parts.append((o, rgb, pts, facets, m.isSolid()))
    vert, tris, vols = [], [], []
    for o, rgb, pts, facets, solid in parts:
        off = len(vert)
        first = len(tris)
        vert += [(p.x, p.y, p.z) for p in pts]
        ci = colours.index(rgb)
        tris += [(a + off, b + off, c + off, ci) for a, b, c in facets]
        vols.append((o, first, len(tris) - 1, ci + 1, solid))
    hexc = ["#%02X%02X%02XFF" % tuple(int(round(c * 255)) for c in rgb) for rgb in colours]
    model = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
             'xmlns:slic3rpe="http://schemas.slic3r.org/3mf/2017/06">',
             ' <metadata name="Application">FreeCAD ClaudeLink</metadata>',
             ' <resources>', '  <basematerials id="1">']
    model += ['   <base name=%s displaycolor="%s"/>' % (quoteattr(_colour_name(rgb) or "colour %d" % (i + 1)), hexc[i])
              for i, rgb in enumerate(colours)]
    model += ['  </basematerials>', '  <object id="2" type="model" pid="1" pindex="0" name=%s>' % quoteattr(d.Label),
              '   <mesh>', '    <vertices>']
    model += ['     <vertex x="%.5f" y="%.5f" z="%.5f"/>' % v for v in vert]
    model += ['    </vertices>', '    <triangles>']
    model += ['     <triangle v1="%d" v2="%d" v3="%d" pid="1" p1="%d"/>' % t for t in tris]
    model += ['    </triangles>', '   </mesh>', '  </object>', ' </resources>',
              ' <build>', '  <item objectid="2"/>', ' </build>', '</model>']
    cfg = ['<?xml version="1.0" encoding="UTF-8"?>', '<config>', ' <object id="2" instances_count="1">',
           '  <metadata type="object" key="name" value=%s/>' % quoteattr(d.Label)]
    for o, a, b, ext, solid in vols:
        cfg += ['  <volume firstid="%d" lastid="%d">' % (a, b),
                '   <metadata type="volume" key="name" value=%s/>' % quoteattr(o.Label),
                '   <metadata type="volume" key="volume_type" value="ModelPart"/>',
                '   <metadata type="volume" key="extruder" value="%d"/>' % ext,
                '  </volume>']
    cfg += [' </object>', '</config>']
    ctypes = ('<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
              'content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships'
              '+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
              '<Default Extension="config" ContentType="text/xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/'
            '3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    existed = os.path.exists(path)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ctypes)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", "\n".join(model))
        z.writestr("Metadata/Slic3r_PE_model.config", "\n".join(cfg))
    lines = ["exported 1 multi-colour 3MF in %.1fs, mesh tolerance %s mm" % (time.time() - t0, fmt(tolerance)),
             "%s %s (%s, %d parts, %d triangles)" % ("replaced" if existed else "wrote", path, _kb(path), len(vols),
                                                    len(tris))]
    for i, rgb in enumerate(colours):
        members = [o.Label for o, a, b, ext, solid in vols if ext == i + 1]
        lines.append("  extruder %d = %s %s: %s" % (i + 1, _colour_name(rgb) or "colour", hexc[i][:7], ", ".join(members)))
    bad = [o.Label for o, a, b, ext, solid in vols if not solid]
    if bad:
        lines.append("NOT watertight: " + ", ".join(bad))
    lines.append("open in PrusaSlicer/Orca: loads as one object, one part per piece, each on its colour's extruder")
    return "\n".join(lines)


# ------------------------------------------------------------------ CAD vs mesh deviation

def _ref_mesh(d, reference):
    import Mesh
    import MeshPart
    if os.path.isfile(reference):
        return Mesh.Mesh(reference), os.path.basename(reference)
    o = _obj(d, reference)
    if hasattr(o, "Mesh"):
        return o.Mesh.copy(), o.Name  # includes the object's placement
    s = global_shape(o)
    return MeshPart.meshFromShape(Shape=s, LinearDeflection=_defl(s), AngularDeflection=0.1), o.Name


def _samples(mesh, n, seed):
    pts, tris = mesh.Topology
    fac = []
    for a, b, c in tris:
        pa, pb, pc = pts[a], pts[b], pts[c]
        cr = (pb - pa).cross(pc - pa)
        ar = cr.Length / 2
        if ar > 1e-12:
            fac.append((pa, pb, pc, cr * (1.0 / (2 * ar)), ar))
    rnd = random.Random(seed)
    out = []
    for pa, pb, pc, nrm, _ in rnd.choices(fac, weights=[f[4] for f in fac], k=n):
        r1, r2 = rnd.random(), rnd.random()
        if r1 + r2 > 1:
            r1, r2 = 1 - r1, 1 - r2
        out.append((pa + (pb - pa) * r1 + (pc - pa) * r2, nrm))
    return out


def _deviation(src_pts, target_mesh, search):
    res, miss = [], []
    for p, n in src_pts:
        hits = target_mesh.foraminate((p.x, p.y, p.z), (n.x, n.y, n.z))
        best = None
        for h in hits.values():
            dv = (V(*h) - p).dot(n)
            if abs(dv) <= search and (best is None or abs(dv) < abs(best)):
                best = dv
        if best is None:
            miss.append(p)
        else:
            res.append((best, p))
    return res, miss


def _stats(res, tol):
    vals = [r[0] for r in res]
    a = sorted(abs(v) for v in vals)
    n = len(vals)
    rms = math.sqrt(sum(v * v for v in vals) / n)
    return ("mean %s, mean|d| %s, RMS %s, p95|d| %s, max +%s / %s; within %s: %.1f%%, within %s: %.1f%%" % (
        fmt(sum(vals) / n), fmt(sum(a) / n), fmt(rms), fmt(a[int(0.95 * (n - 1))]), fmt(max(vals)), fmt(min(vals)),
        fmt(tol), 100.0 * sum(1 for x in a if x <= tol) / n, fmt(2 * tol), 100.0 * sum(1 for x in a if x <= 2 * tol) / n))


def compare_mesh(object, reference, doc=None, samples=4000, tolerance=0.1, search=3.0, image=True, views=None):
    import MeshPart
    d = _doc(doc)
    o = _obj(d, object)
    t0 = time.time()
    s = global_shape(o)
    cad = MeshPart.meshFromShape(Shape=s, LinearDeflection=_defl(s), AngularDeflection=0.1)
    ref, rname = _ref_mesh(d, reference)
    half = max(200, int(samples) // 2)
    fwd, fmiss = _deviation(_samples(ref, half, 1), cad, search)
    bwd, bmiss = _deviation(_samples(cad, half, 2), ref, search)
    lines = ["%s vs %s (%d+%d normal-ray samples, search +-%s, %.1fs). Sign: + = CAD outside the reference (oversize)" % (
        o.Name, rname, half, half, fmt(search), time.time() - t0)]
    rb, cb = ref.BoundBox, s.BoundBox
    lines.append("bbox ref %sx%sx%s vs CAD %sx%sx%s; centre offset %s" % (
        fmt(rb.XLength), fmt(rb.YLength), fmt(rb.ZLength), fmt(cb.XLength), fmt(cb.YLength), fmt(cb.ZLength),
        _vec(cb.Center - rb.Center)))
    if fwd:
        lines.append("reference -> CAD: " + _stats(fwd, tolerance))
    def miss_lines(miss, what):
        cl_ = [c for c in _clusters([(0, p) for p in miss], 4.0, 50) if c[2] >= 3]
        out = ["  %d %s samples (%.1f%%) have NO %s surface within %s%s" % (
            len(miss), what[0], 100.0 * len(miss) / half, what[1], fmt(search),
            ":" if cl_ else " (scattered single points, likely sharp edges)")]
        for _, c, k in sorted(cl_, key=lambda c: -c[2])[:4]:
            out.append("    near %s (%d samples) - %s" % (_vec(c), k, what[2]))
        return out

    if fmiss:
        lines += miss_lines(fmiss, ("reference", "CAD", "missing in CAD?"))
    if bwd:
        # CAD sample against reference: + means reference is outside CAD -> flip so + still = CAD oversize
        bwd = [(-v, p) for v, p in bwd]
        lines.append("CAD -> reference: " + _stats(bwd, tolerance))
    if bmiss:
        lines += miss_lines(bmiss, ("CAD", "reference", "extra in CAD?"))
    worst = [r for r in fwd + bwd if abs(r[0]) > tolerance]
    if worst:
        groups = []  # [signed value of the most extreme sample, location, count]
        rad = max(3.0, s.BoundBox.DiagonalLength / 8.0)
        for v, p in sorted(worst, key=lambda r: -abs(r[0])):
            for g in groups:
                if (g[1] - p).Length <= rad:
                    g[2] += 1
                    break
            else:
                groups.append([v, p, 1])
        lines.append("worst deviations beyond %s (%d regions):" % (fmt(tolerance), len(groups)))
        for v, c, k in groups[:6]:
            lines.append("  %s%s near %s (%d samples)" % ("+" if v > 0 else "", fmt(v), _vec(c), k))
    text = "\n".join(lines)
    if not image or not FreeCAD.GuiUp or not (fwd or bwd):
        return text
    data = _draw_deviation(fwd + bwd, fmiss + bmiss, tolerance, views or ["top", "front"])
    return {"text": text, "image_b64": base64.b64encode(data).decode("ascii"), "mime": "image/png"}


_PROJ = {"top": (V(1, 0, 0), V(0, 1, 0), V(0, 0, 1), "X", "Y"), "bottom": (V(1, 0, 0), V(0, -1, 0), V(0, 0, -1), "X", "-Y"),
         "front": (V(1, 0, 0), V(0, 0, 1), V(0, -1, 0), "X", "Z"), "back": (V(-1, 0, 0), V(0, 0, 1), V(0, 1, 0), "-X", "Z"),
         "right": (V(0, 1, 0), V(0, 0, 1), V(1, 0, 0), "Y", "Z"), "left": (V(0, -1, 0), V(0, 0, 1), V(-1, 0, 0), "-Y", "Z")}


def _colour(t):
    """t in [-1, 1] -> blue (inside/undersize) .. green .. red (oversize)."""
    t = max(-1.0, min(1.0, t))
    if t < 0:
        return (int(40 + 0 * t), int(200 + 160 * t), int(80 - 170 * t))
    return (int(40 + 215 * t), int(200 - 160 * t), int(80 - 60 * t))


def _draw_deviation(res, miss, tol, views, tile=380):
    from PySide import QtCore, QtGui
    rng = 3 * tol
    W = tile * len(views)
    H = tile + 34
    img = QtGui.QImage(W, H, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor(255, 255, 255))
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    f = QtGui.QFont()
    f.setPixelSize(11)
    p.setFont(f)
    allp = [r[1] for r in res] + list(miss)
    for i, vname in enumerate(views):
        u, v, w, ul, vl = _PROJ.get(vname, _PROJ["top"])
        us = [q.dot(u) for q in allp]
        vs = [q.dot(v) for q in allp]
        u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
        span = max(u1 - u0, v1 - v0, 1e-6) * 1.08
        cu, cv = (u0 + u1) / 2, (v0 + v1) / 2
        sc = (tile - 20) / span
        ox = i * tile

        def X(a):
            return ox + tile / 2 + (a - cu) * sc

        def Y(b):
            return tile / 2 + 8 - (b - cv) * sc
        # draw far points first so the near side is on top
        order = sorted(range(len(res)), key=lambda k: res[k][1].dot(w))
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(170, 170, 170))
        for q in miss:
            p.drawEllipse(QtCore.QPointF(X(q.dot(u)), Y(q.dot(v))), 1.6, 1.6)
        for k in order:
            dv, q = res[k]
            r, g, b = _colour(dv / rng)
            p.setBrush(QtGui.QColor(r, g, b))
            p.drawEllipse(QtCore.QPointF(X(q.dot(u)), Y(q.dot(v))), 1.6, 1.6)
        p.setPen(QtGui.QColor(60, 60, 60))
        p.drawText(QtCore.QPointF(ox + 6, 16), "%s (%s right, %s up)" % (vname, ul, vl))
        p.setPen(QtGui.QColor(210, 210, 210))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawRect(ox, 0, tile - 1, tile + 16)
    # legend
    y = H - 14
    for k in range(200):
        t = -1 + 2 * k / 199.0
        r, g, b = _colour(t)
        p.fillRect(QtCore.QRectF(10 + k, y - 8, 1, 10), QtGui.QColor(r, g, b))
    p.setPen(QtGui.QColor(40, 40, 40))
    p.drawText(QtCore.QPointF(215, y + 1), "-%s .. 0 .. +%s mm   (+ = CAD oversize, grey = no match)" % (fmt(rng), fmt(rng)))
    p.end()
    buf = QtCore.QBuffer()
    buf.open(QtCore.QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


# ------------------------------------------------------------------ checkpoints

def _ckpt_dir(d):
    import hashlib
    base = FreeCAD.getUserCachePath() if hasattr(FreeCAD, "getUserCachePath") else os.environ.get("TEMP", ".")
    key = _safe(d.Name)
    if d.FileName:  # same-named documents from different files must not share checkpoints
        key += "_" + hashlib.sha1(os.path.normcase(os.path.abspath(d.FileName)).encode("utf-8")).hexdigest()[:8]
    p = os.path.join(base, "ClaudeLink", "checkpoints", key)
    os.makedirs(p, exist_ok=True)
    return p


def _list(d):
    return sorted(glob.glob(os.path.join(_ckpt_dir(d), "*.FCStd")))


def auto_checkpoint(d, label):
    """Snapshot before a tool changes or replaces objects; returns a one-line note ('' if disabled/failed).
    Turn off with freecad_print_profile set {"auto_checkpoint": false}."""
    try:
        from . import profile
        if not profile.load().get("auto_checkpoint", True) or not d.Objects:
            return ""
        r = checkpoint("save", label=label, doc=d.Name)
        n = r.split()[1].split("_", 1)[0]
        return "checkpoint %s saved first (freecad_checkpoint action=restore name=%r undoes this)" % (n, n)
    except Exception:
        return ""


def checkpoint(action="save", label=None, name=None, doc=None, keep=30):
    d = _doc(doc)
    files = _list(d)
    if action == "list":
        if not files:
            return "no checkpoints for %s" % d.Name
        return "\n".join(["checkpoints for %s (newest last):" % d.Name] + [
            "  %s  %s  %s" % (os.path.splitext(os.path.basename(f))[0], time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(f))), _kb(f))
            for f in files])
    if action == "save":
        n = 1 + max([int(os.path.basename(f).split("_", 1)[0]) for f in files] or [0])
        fn = os.path.join(_ckpt_dir(d), "%03d_%s.FCStd" % (n, _safe(label or "checkpoint")))
        d.saveCopy(fn)
        files.append(fn)
        for old in files[:-keep]:
            os.remove(old)
        return "checkpoint %s saved (%d objects, %s); restore with name=%r" % (
            os.path.splitext(os.path.basename(fn))[0], len(d.Objects), _kb(fn), "%03d" % n)
    if action not in ("restore", "open"):
        raise UserError("action must be save, list, restore or open")
    if not files:
        raise UserError("no checkpoints for %s" % d.Name)
    if name is None:
        f = files[-1]
    else:
        m = [x for x in files if os.path.basename(x).startswith(str(name)) or str(name) in os.path.basename(x)]
        if not m:
            raise UserError("no checkpoint matching %r; use action=list" % name)
        f = m[-1]
    cname = os.path.splitext(os.path.basename(f))[0]
    if action == "open":
        o = FreeCAD.openDocument(f)
        return "opened checkpoint %s as separate document %s (read it, then close it with freecad_doc)" % (cname, o.Name)
    # restore in place: keep the document identity/file name, replace its objects; one undo step
    before = snap.snapshot()
    keep = _KeepActive().__enter__()
    keep.name = d.Name
    src = FreeCAD.openDocument(f, hidden=True)
    try:
        FreeCAD.setActiveTransaction("Claude: restore checkpoint %s" % cname)
        try:
            for o in list(d.Objects):
                try:
                    d.removeObject(o.Name)
                except Exception:
                    pass
            d.copyObject(src.Objects, True)
            d.recompute()
            FreeCAD.closeActiveTransaction(False)
        except Exception:
            FreeCAD.closeActiveTransaction(True)
            raise
    finally:
        FreeCAD.closeDocument(src.Name)
        keep.__exit__(None, None, None)
    after = snap.snapshot()
    renamed = [n for n in before.get(d.Name, {}) if n not in after.get(d.Name, {})]
    lines = ["restored %s into %s (%d objects); undo with freecad_undo" % (cname, d.Name, len(d.Objects))]
    lines += snap.diff(before, after) or ["no geometry differences"]
    if renamed and len(after.get(d.Name, {})) >= len(before.get(d.Name, {})):
        lines.append("note: some objects came back under new names")
    return "\n".join(lines)


METHODS = {
    "export": export,
    "compare_mesh": compare_mesh,
    "checkpoint": checkpoint,
}
