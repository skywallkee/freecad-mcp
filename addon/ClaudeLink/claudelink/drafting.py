"""Phase 4: sketch diagnostics and TechDraw drawings."""
import base64
import math
import os
import time

import FreeCAD

from .server import UserError
from .snapshot import fmt

V = FreeCAD.Vector


def _doc(name=None):
    from .handlers import _doc as d
    return d(name)


def _obj(doc, key):
    from .handlers import _obj as o
    return o(doc, key)


# ------------------------------------------------------------------ sketches

def _geo_str(i, g, construction):
    t = type(g).__name__
    c = " (constr)" if construction else ""
    try:
        if t == "LineSegment":
            return "g%d line (%s,%s)->(%s,%s) len %s%s" % (i, fmt(g.StartPoint.x), fmt(g.StartPoint.y), fmt(g.EndPoint.x),
                                                         fmt(g.EndPoint.y), fmt(g.length()), c)
        if t == "Circle":
            return "g%d circle c(%s,%s) r %s%s" % (i, fmt(g.Center.x), fmt(g.Center.y), fmt(g.Radius), c)
        if t == "ArcOfCircle":
            return "g%d arc c(%s,%s) r %s %s..%s deg%s" % (i, fmt(g.Center.x), fmt(g.Center.y), fmt(g.Radius),
                                                         fmt(math.degrees(g.FirstParameter)), fmt(math.degrees(g.LastParameter)), c)
        if t == "Point":
            return "g%d point (%s,%s)%s" % (i, fmt(g.X), fmt(g.Y), c)
    except Exception:
        pass
    return "g%d %s%s" % (i, t, c)


def _con_str(i, k):
    s = "c%d %s" % (i + 1, k.Type)
    refs = []
    for a, b in (("First", "FirstPos"), ("Second", "SecondPos"), ("Third", "ThirdPos")):
        g = getattr(k, a, -2000)
        if g is not None and g > -2000:
            refs.append("g%d%s" % (g, ("." + str(getattr(k, b))) if getattr(k, b, 0) else ""))
    if refs:
        s += " " + ",".join(refs)
    if k.Type in ("Distance", "DistanceX", "DistanceY", "Radius", "Diameter", "Angle"):
        v = k.Value
        s += " = %s" % (fmt(math.degrees(v)) + "deg" if k.Type == "Angle" else fmt(v))
        if k.Name:
            s += " '%s'" % k.Name
    if not getattr(k, "Driving", True):
        s += " (reference)"
    return s


def sketch(name, doc=None, detail="summary", image=False, size=480):
    """Solver status of a sketch: DoF, conflicting/redundant/malformed constraints, profile closure."""
    d = _doc(doc)
    sk = _obj(d, name)
    if not sk.isDerivedFrom("Sketcher::SketchObject"):
        raise UserError("%s is not a sketch (%s)" % (sk.Name, sk.TypeId))
    rc = sk.solve()
    geos = sk.Geometry
    cons = sk.Constraints
    constr_flags = [sk.getConstruction(i) for i in range(len(geos))]
    lines = ["%s: %d geometries (%d construction), %d constraints, solve rc %d -> %s" % (
        sk.Name, len(geos), sum(constr_flags), len(cons), rc,
        {0: "ok", -1: "solver failed", -2: "redundant constraints", -3: "conflicting constraints",
         -4: "over-constrained", -5: "malformed constraints"}.get(rc, "rc %d" % rc))]
    dof = getattr(sk, "DoF", None)
    if dof is not None:
        lines.append("degrees of freedom: %d%s" % (dof, " (fully constrained)" if dof == 0 else ""))
    for label, attr in (("CONFLICTING", "ConflictingConstraints"), ("REDUNDANT", "RedundantConstraints"),
                        ("partially redundant", "PartiallyRedundantConstraints"), ("MALFORMED", "MalformedConstraints")):
        v = list(getattr(sk, attr, []) or [])
        if v:
            lines.append("%s constraints (1-based): %s" % (label, ", ".join(
                "c%d %s" % (i, cons[i - 1].Type) if 0 < i <= len(cons) else str(i) for i in v)))
    try:
        sh = sk.Shape
        if sh.isNull() or not sh.Edges:
            raise ValueError("empty - the sketch failed to recompute; fix the constraints above first"
                             if "Invalid" in sk.State or rc != 0 else "empty")
        wires = sh.Wires
        closed = sum(1 for w in wires if w.isClosed())
        lines.append("profile: %d wire(s), %d closed, %d open%s" % (
            len(wires), closed, len(wires) - closed, "" if len(wires) == closed else "  <- open wires can't pad/pocket"))
        loose = len(sh.Edges) - sum(len(w.Edges) for w in wires)
        if loose > 0:
            lines.append("  %d edges not in any wire" % loose)
    except Exception as e:
        lines.append("profile: (no shape: %s)" % e)
    try:
        p = sk.Placement
        n = p.Rotation.multVec(V(0, 0, 1))
        lines.append("plane: origin (%s, %s, %s), normal (%s, %s, %s)%s" % (
            fmt(p.Base.x), fmt(p.Base.y), fmt(p.Base.z), fmt(n.x), fmt(n.y), fmt(n.z),
            ", attached to " + ", ".join("%s%s" % (o.Name, (":" + ",".join(s)) if s and s != ("",) else "")
                                        for o, s in sk.AttachmentSupport) if getattr(sk, "AttachmentSupport", None) else ""))
    except Exception:
        pass
    if detail in ("geometry", "full"):
        lines.append("geometry:")
        lines += ["  " + _geo_str(i, g, constr_flags[i]) for i, g in enumerate(geos)]
    if detail in ("constraints", "full"):
        lines.append("constraints:")
        lines += ["  " + _con_str(i, k) for i, k in enumerate(cons)]
    text = "\n".join(lines)
    if not image or not FreeCAD.GuiUp:
        return text
    return {"text": text, "image_b64": base64.b64encode(_draw_sketch(sk, size)).decode("ascii"), "mime": "image/png"}


def _draw_sketch(sk, size):
    from PySide import QtCore, QtGui
    geos = sk.Geometry
    pts = []
    polys = []
    for i, g in enumerate(geos):
        try:
            e = g.toShape()
            ps = [V(p.x, p.y, 0) for p in e.discretize(Deflection=0.01 * max(e.BoundBox.DiagonalLength, 1e-3))] \
                if e.Length > 0 else [e.Vertexes[0].Point]
            mid = e.valueAt((e.FirstParameter + e.LastParameter) / 2) if e.Length > 0 else ps[0]
        except Exception:
            continue
        polys.append((i, ps, sk.getConstruction(i), mid))
        pts += ps
    if not pts:
        pts = [V(0, 0, 0)]
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    span = max(x1 - x0, y1 - y0, 1e-6) * 1.15
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    sc = size / span
    img = QtGui.QImage(size, size, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor(255, 255, 255))
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    f = QtGui.QFont()
    f.setPixelSize(10)
    p.setFont(f)

    def X(x):
        return size / 2 + (x - cx) * sc

    def Y(y):
        return size / 2 - (y - cy) * sc
    for i, ps, constr, m in polys:
        pen = QtGui.QPen(QtGui.QColor(30, 110, 220) if constr else QtGui.QColor(20, 20, 20), 1.4)
        if constr:
            pen.setStyle(QtCore.Qt.DashLine)
        p.setPen(pen)
        for a, b in zip(ps, ps[1:]):
            p.drawLine(QtCore.QPointF(X(a.x), Y(a.y)), QtCore.QPointF(X(b.x), Y(b.y)))
        if len(ps) == 1:
            p.drawEllipse(QtCore.QPointF(X(ps[0].x), Y(ps[0].y)), 2, 2)
        p.setPen(QtGui.QColor(200, 60, 0))
        p.drawText(QtCore.QPointF(X(m.x) + 3, Y(m.y) - 3), "g%d" % i)
    p.setPen(QtGui.QColor(150, 150, 150))
    p.drawText(QtCore.QPointF(6, size - 6), "sketch coords, span %s mm; dashed blue = construction" % fmt(span))
    p.end()
    buf = QtCore.QBuffer()
    buf.open(QtCore.QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


# ------------------------------------------------------------------ TechDraw

_PROJ = {"front": "Front", "top": "Top", "right": "Right", "left": "Left", "rear": "Rear", "back": "Rear",
         "bottom": "Bottom", "iso": "FrontTopRight", "fronttopright": "FrontTopRight", "fronttopleft": "FrontTopLeft"}


def _templates_dir():
    return os.path.join(FreeCAD.getResourceDir(), "Mod", "TechDraw", "Templates")


def _template_path(name):
    if os.path.isfile(name):
        return name
    tdir = _templates_dir()
    for cand in (name, name + ".svg"):
        p = os.path.join(tdir, cand)
        if os.path.isfile(p):
            return p
    opts = sorted(f[:-4] for f in os.listdir(tdir) if f.endswith(".svg") and f.startswith(("A4", "A3", "A2")))
    raise UserError("template %r not found; e.g. %s" % (name, ", ".join(opts[:12])))


def _wait_hlr(views, timeout=30.0):
    from PySide import QtCore
    t0 = time.time()
    while time.time() - t0 < timeout:
        QtCore.QCoreApplication.processEvents()
        try:
            if all(len(v.getVisibleEdges()) > 0 for v in views):
                return True
        except Exception:
            pass
        time.sleep(0.03)
    return False


def _bbox2d(edges):
    xs, ys = [], []
    for e in edges:
        b = e.BoundBox
        xs += [b.XMin, b.XMax]
        ys += [b.YMin, b.YMax]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else (0, 0, 0, 0)


_STD_SCALES = [10, 5, 4, 2.5, 2, 1, 0.5, 0.4, 0.25, 0.2, 0.1, 0.05, 0.04, 0.025, 0.02, 0.01]


def _f(q):
    """Quantity or number -> float (mm)."""
    return float(q.Value) if hasattr(q, "Value") else float(q)


def _group_extents(pg, items):
    """(xmin, ymin, xmax, ymax) of all projection items, relative to the group origin, page mm."""
    xs, ys = [], []
    for it in items:
        x0, y0, x1, y1 = _bbox2d(it.getVisibleEdges())
        hw, hh = (x1 - x0) / 2, (y1 - y0) / 2
        ix, iy = _f(it.X), _f(it.Y)
        xs += [ix - hw, ix + hw]
        ys += [iy - hh, iy + hh]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else (0, 0, 0, 0)


def _sizes(items):
    out = []
    for it in items:
        x0, y0, x1, y1 = _bbox2d(it.getVisibleEdges())
        out.append((x1 - x0, y1 - y0))
    return out


_ORTHO = {"Front": (0, 2), "Rear": (0, 2), "Top": (0, 1), "Bottom": (0, 1), "Right": (1, 2), "Left": (1, 2)}


def _wait_views(items, dims, scale, timeout):
    """Hidden-line removal runs in the background: after a (re)compute the views keep their OLD edges until it
    finishes, so "has edges" proves nothing. Wait until every orthographic view has the size the bounding box
    (dims = X, Y, Z in mm) and the scale must give, and the other views have stopped changing."""
    from PySide import QtCore
    t0 = time.time()
    last, stable_since = None, None
    while time.time() - t0 < timeout:
        QtCore.QCoreApplication.processEvents()
        try:
            got = _sizes(items)
            ok = all(g[0] > 0 or g[1] > 0 for g in got)
            for it, g in zip(items, got):
                ax = _ORTHO.get(getattr(it, "Type", ""))
                if ax is None:
                    continue
                w, h = dims[ax[0]] * scale, dims[ax[1]] * scale
                if abs(g[0] - w) > 0.04 * w + 0.3 or abs(g[1] - h) > 0.04 * h + 0.3:
                    ok = False
            if ok:
                if got != last:
                    last, stable_since = got, time.time()
                elif time.time() - stable_since >= 0.4:
                    return True
            else:
                last = None
        except Exception:
            pass
        time.sleep(0.05)
    return False


def _fit_group(d, pg, items, area, dims, fixed=None, timeout=60.0, grow=True):
    """Pick the largest standard scale whose layout fits `area`, then centre the group in it.
    The scale is computed once from the views' real sizes (never from a half-updated page)."""
    ax0, ay0, ax1, ay1 = area
    aw, ah = (ax1 - ax0) * 0.92, (ay1 - ay0) * 0.85  # leave room for dimensions
    cur = pg.getScale() if hasattr(pg, "getScale") else pg.Scale
    x0, y0, x1, y1 = _group_extents(pg, items)
    if fixed is None and cur > 0:
        f = min(aw / max(x1 - x0, 1e-6), ah / max(y1 - y0, 1e-6))
        want = next((s for s in _STD_SCALES if s <= cur * f + 1e-9), _STD_SCALES[-1])
        if want > cur and not grow:
            want = cur
        if abs(want - cur) > 1e-9:
            pg.ScaleType = "Custom"
            pg.Scale = want
            d.recompute()
            if not _wait_views(items, dims, want, timeout):
                raise UserError("the drawing views did not finish updating within %d s (hidden-line removal on a very "
                                "detailed part) - try hidden=false, fewer views, or a simpler copy of the part"
                                % timeout)
    x0, y0, x1, y1 = _group_extents(pg, items)
    pg.X = (ax0 + ax1) / 2 - (x0 + x1) / 2
    pg.Y = (ay0 + ay1) / 2 - (y0 + y1) / 2
    d.recompute()
    return pg.getScale() if hasattr(pg, "getScale") else pg.Scale


def _open_page(page):
    """Show the page in its own tab (the template frame is only drawn by the page view)."""
    import FreeCADGui
    try:
        page.ViewObject.doubleClicked()
    except Exception:
        try:
            page.ViewObject.show()
        except Exception:
            pass
    FreeCADGui.updateGui()


def _close_page_tabs(page):
    import FreeCADGui
    from PySide import QtGui
    try:
        mdi = FreeCADGui.getMainWindow().findChild(QtGui.QMdiArea)
        for sw in mdi.subWindowList():
            if page.Label in sw.windowTitle() and page.Document.Label in sw.windowTitle():
                sw.close()
    except Exception:
        pass


def drawing(objects, doc=None, views=None, template="A4_Landscape_TD", scale=None, hidden=True, dims=True,
            title=None, path=None, keep=False, preview=True, size=1000):
    if not FreeCAD.GuiUp:
        raise UserError("drawing export needs the FreeCAD GUI")
    import FreeCADGui
    import TechDraw
    import TechDrawGui
    from PySide import QtGui, QtSvg, QtCore
    d = _doc(doc)
    objs = [_obj(d, k) for k in objects]
    views = views or ["front", "top", "right", "iso"]
    projs = []
    for v in views:
        p = _PROJ.get(v.lower())
        if p is None:
            raise UserError("unknown view %r (front top right left rear bottom iso)" % v)
        projs.append(p)
    if "Front" not in projs:
        projs.insert(0, "Front")
    nfaces = sum(len(o.Shape.Faces) for o in objs if hasattr(o, "Shape"))
    lines_pre = []
    if hidden and nfaces > 600:
        hidden = False       # hundreds of hidden edges are unreadable and take minutes to compute
        lines_pre.append("hidden lines left out (%d faces: unreadable and very slow) - pass a simpler copy for them" % nfaces)
    tpath = _template_path(template)
    if path is None:
        if not d.FileName:
            raise UserError("document unsaved: give path (.pdf or .svg)")
        path = os.path.join(os.path.dirname(d.FileName), "images", "%s_drawing.pdf" % "_".join(o.Name.lower() for o in objs))
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    prev = FreeCAD.ActiveDocument.Name if FreeCAD.ActiveDocument else None
    FreeCAD.setActiveDocument(d.Name)  # TechDraw.make*Dim create objects in the active document
    FreeCAD.setActiveTransaction("Claude: drawing")
    t0 = time.time()
    lines = list(lines_pre)
    page = None
    ok = False
    try:
        page = d.addObject("TechDraw::DrawPage", "ClaudeDrawing")
        tpl = d.addObject("TechDraw::DrawSVGTemplate", "ClaudeTemplate")
        tpl.Template = tpath
        page.Template = tpl
        pg = d.addObject("TechDraw::DrawProjGroup", "ClaudeProjGroup")
        page.addView(pg)
        try:
            W, H = _f(tpl.Width), _f(tpl.Height)
        except Exception:
            W, H = 297.0, 210.0
        bb = FreeCAD.BoundBox()
        for o in objs:
            try:                      # the plain BoundBox is loose around spline surfaces (threads, text)
                o.Shape.tessellate(0.05)
                bb.add(o.Shape.optimalBoundingBox(True, False))
            except Exception:
                bb.add(o.Shape.BoundBox)
        dims = (max(bb.XLength, 1e-3), max(bb.YLength, 1e-3), max(bb.ZLength, 1e-3))
        pg.ScaleType = "Custom"
        if scale:
            pg.Scale = float(scale)
        else:
            # start at the scale the bounding box predicts, so the (slow, background) hidden-line pass usually
            # runs once instead of once per scale change
            X, Y, Z = dims
            iso = 0.71 * (X + Y) if any(p.startswith("FrontTop") or p.startswith("FrontBottom") for p in projs) else 0.0
            side = Y if any(p in ("Right", "Left") for p in projs) else 0.0
            below = Y if any(p in ("Top", "Bottom") for p in projs) else 0.0
            aw, ah = (W - 30.0) * 0.92, (H * 0.70 - 15.0) * 0.85
            est = min((aw - 25.0) / (X + side + iso), (ah - 12.0) / (Z + below))
            pg.Scale = next((sv for sv in _STD_SCALES if sv <= est + 1e-9), _STD_SCALES[-1])
        pg.Source = objs
        for p in projs:
            it = pg.addProjection(p)
            if hidden and it is not None and hasattr(it, "HardHidden"):
                it.HardHidden = True  # before the first compute, so the page graphics include them
        # centre the group in the free area above the title block (bottom ~28% of the sheet)
        pg.X, pg.Y = W * 0.45, H * 0.62
        d.recompute()
        items = list(pg.Views)
        for it in items:
            if hidden and hasattr(it, "HardHidden"):
                it.HardHidden = True
        d.recompute()
        budget = min(150.0, 30.0 + 0.1 * nfaces)         # hidden-line removal time grows with the face count
        if not _wait_views(items, dims, pg.getScale() if hasattr(pg, "getScale") else pg.Scale, budget):
            raise UserError("the drawing views were not ready after %d s (hidden-line removal on %d faces) - try "
                            "hidden=false, fewer views, or a simpler copy of the part" % (budget, nfaces))
        # fit: free area above the title block, standard scales only
        area = (15.0, H * 0.30, W - 15.0, H - 15.0)
        sc = _fit_group(d, pg, items, area, dims, None if not scale else float(scale), budget, grow=nfaces <= 600)
        sc = pg.getScale() if hasattr(pg, "getScale") else pg.Scale
        texts = dict(tpl.EditableTexts)
        upd = {"FC-Title": title or d.Label, "FC-SC": ("1:%s" % fmt(1 / sc)) if sc < 1 else ("%s:1" % fmt(sc)),
               "FC-Date": time.strftime("%Y-%m-%d"), "Drawing_number": d.Label,
               "Subtitle": ", ".join(o.Label for o in objs)}
        for k, v in upd.items():
            if k in texts:
                texts[k] = v
        tpl.EditableTexts = texts
        dim_vals = []
        if dims:
            by = {it.Type if hasattr(it, "Type") else it.Label: it for it in items}
            for it in items:
                kind = getattr(it, "Type", it.Label)
                if kind in ("FrontTopRight", "FrontTopLeft"):
                    continue
                edges = it.getVisibleEdges()
                x0, y0, x1, y1 = _bbox2d(edges)
                wants = {"Front": (0, 1), "Top": (1,), "Right": (), "Left": (), "Rear": (), "Bottom": ()}.get(kind, ())
                if kind == "Right" and "Top" not in by:
                    wants = (0,)
                for direction in wants:
                    dm = TechDraw.makeExtentDim(it, [], direction)
                    if direction == 0:
                        dm.X, dm.Y = (x0 + x1) / 2 * sc, (y0 - 6 / sc) * sc if y0 <= 0 else -((y1 - y0) / 2 + 6 / sc) * sc
                    else:
                        dm.X, dm.Y = ((x1 - x0) / 2 + 6 / sc) * sc, (y0 + y1) / 2 * sc
                    dim_vals.append((kind, "width" if direction == 0 else "height", dm))
            # hole/boss diameters: unique radii, prefer the Top view then Front
            seen = set()
            centres = {}
            for kind in ("Top", "Front", "Right"):
                it = by.get(kind)
                if it is None:
                    continue
                for i, e in enumerate(it.getVisibleEdges()):
                    c = getattr(e, "Curve", None)
                    if type(c).__name__ != "Circle":
                        continue
                    r = round(c.Radius / sc, 3) if sc else c.Radius
                    full = abs(e.Length - 2 * math.pi * c.Radius) < 1e-3 * max(1, e.Length)
                    key = round(r, 2)
                    if key in seen or len(seen) >= 8:
                        continue
                    ck = (kind, round(c.Center.x), round(c.Center.y))
                    if ck in centres and (not full or centres[ck] >= 2):
                        continue          # concentric arcs (thread turns, chamfers): labels would pile up
                    centres[ck] = centres.get(ck, 0) + 1
                    seen.add(key)
                    dm = d.addObject("TechDraw::DrawViewDimension", "ClaudeDim")
                    dm.Type = "Diameter" if full else "Radius"
                    dm.MeasureType = "Projected"
                    dm.References2D = [(it, "Edge%d" % i)]
                    page.addView(dm)
                    off = c.Radius + 7.0  # label just outside the circle, up-right
                    dm.X = c.Center.x + off * 0.7
                    dm.Y = -c.Center.y + off * 0.7  # projected edges are y-down, dimension positions y-up
                    dim_vals.append((kind, "diameter" if full else "radius", dm))
            d.recompute()
            _wait_hlr(items, 5)
        _open_page(page)
        _wait_hlr(items, 10)
        for it in items:  # make sure every view's graphics (incl. hidden lines) are current
            try:
                it.requestPaint()
            except Exception:
                pass
        for _ in range(20):
            QtCore.QCoreApplication.processEvents()
            time.sleep(0.03)
        svg_path = os.path.splitext(path)[0] + ".svg"
        if path.lower().endswith(".pdf"):
            TechDrawGui.exportPageAsPdf(page, path)
        TechDrawGui.exportPageAsSvg(page, svg_path)
        files = [path] if path.lower().endswith(".pdf") else []
        files.append(svg_path)
        lines.insert(0, "drawing of %s: %s, scale %s, views %s (%.1fs)" % (
            ", ".join(o.Name for o in objs), os.path.basename(tpath), upd["FC-SC"], ", ".join(projs), time.time() - t0))
        lines.append("files: " + "; ".join("%s (%d KB)" % (f, os.path.getsize(f) // 1024) for f in files))
        if dim_vals:
            vals = []
            for kind, what, dm in dim_vals:
                try:
                    vals.append("%s %s %s" % (kind, what, fmt(dm.getRawValue())))
                except Exception:
                    vals.append("%s %s ?" % (kind, what))
            lines.append("dimensions: " + "; ".join(vals))
        png = None
        if preview:
            r = QtSvg.QSvgRenderer(svg_path)
            sz = r.defaultSize()
            w = int(size)
            h = max(1, int(w * sz.height() / max(1, sz.width())))
            img = QtGui.QImage(w, h, QtGui.QImage.Format_RGB32)
            img.fill(QtGui.QColor(255, 255, 255))
            p = QtGui.QPainter(img)
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            r.render(p)
            p.end()
            buf = QtCore.QBuffer()
            buf.open(QtCore.QIODevice.WriteOnly)
            img.save(buf, "PNG")
            png = bytes(buf.data())
        ok = True
    finally:
        if page is not None and not keep:
            _close_page_tabs(page)
        FreeCAD.closeActiveTransaction(not (ok and keep))  # abort = remove the page again
        if prev and prev in FreeCAD.listDocuments():
            FreeCAD.setActiveDocument(prev)
        FreeCADGui.updateGui()
    lines.append("page kept in %s as %s" % (d.Name, "ClaudeDrawing") if keep else "page removed again (keep=false)")
    text = "\n".join(lines)
    if png is None:
        return text
    return {"text": text, "image_b64": base64.b64encode(png).decode("ascii"), "mime": "image/png"}


METHODS = {"sketch": sketch, "drawing": drawing}
