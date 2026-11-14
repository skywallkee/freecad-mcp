"""ClaudeLink request handlers. Every handler runs on FreeCAD's main thread and returns
either a compact text string or a dict {"text": ..., "image_b64": ..., "mime": ...}."""
import ast
import contextlib
import io
import linecache
import math
import os
import sys
import time
import traceback
import types

import FreeCAD

from . import snapshot as snap
from .server import UserError

fmt = snap.fmt
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_started = time.time()
_counter = [0]


def _gui():
    if not FreeCAD.GuiUp:
        return None
    import FreeCADGui
    return FreeCADGui


# ---------------------------------------------------------------- namespace

def _step(pause=0.4):
    """Recompute, redraw and pause so each change is visible while building."""
    doc = FreeCAD.ActiveDocument
    if doc:
        doc.recompute()
    G = _gui()
    if G:
        G.updateGui()
        try:
            G.SendMsgToActiveView("ViewFit")
        except Exception:
            pass
        G.updateGui()
    time.sleep(pause)


def _base_ns():
    ns = {"__name__": "__claude__", "App": FreeCAD, "FreeCAD": FreeCAD, "math": math,
          "V": FreeCAD.Vector, "step": _step}
    G = _gui()
    ns["Gui"] = ns["FreeCADGui"] = G
    try:
        import Part
        ns["Part"] = Part
        from . import cl
        ns["cl"] = cl
    except ImportError:
        pass
    return ns


def _shared_ns(fresh=False):
    ns = getattr(FreeCAD, "_claudelink_ns", None)
    if ns is None or fresh:
        ns = _base_ns()
        FreeCAD._claudelink_ns = ns
    return ns


# ---------------------------------------------------------------- helpers

def _doc(name=None):
    if name:
        d = FreeCAD.listDocuments().get(name)
        if d is None:
            for d2 in FreeCAD.listDocuments().values():
                if d2.Label == name:
                    return d2
            raise UserError("no document %r (open: %s)" % (name, ", ".join(FreeCAD.listDocuments()) or "none"))
        return d
    d = FreeCAD.ActiveDocument
    if d is None:
        docs = list(FreeCAD.listDocuments().values())
        if len(docs) == 1:  # unambiguous: use it and make it active again
            FreeCAD.setActiveDocument(docs[0].Name)
            return docs[0]
        raise UserError("no active document (open: %s)" % (", ".join(FreeCAD.listDocuments()) or "none"))
    return d


def _obj(doc, key):
    o = doc.getObject(key)
    if o is None:
        objs = doc.getObjectsByLabel(key)
        if objs:
            return objs[0]
        raise UserError("no object %r in %s" % (key, doc.Name))
    return o


def _clip(text, limit):
    if limit and len(text) > limit:
        head = text[: int(limit * 0.6)]
        tail = text[-int(limit * 0.35):]
        return head + "\n... [%d chars cut] ...\n" % (len(text) - len(head) - len(tail)) + tail
    return text


def _report_widget():
    G = _gui()
    if not G:
        return None
    try:
        from PySide import QtGui
        mw = G.getMainWindow()
        dock = mw.findChild(QtGui.QDockWidget, "Report view")
        if dock is not None:
            return dock.widget()
    except Exception:
        pass
    return None


def _report_text():
    w = _report_widget()
    if w is None:
        return None
    try:
        return w.toPlainText()
    except Exception:
        return None


def _trim_tb(exc, filenames):
    """Traceback restricted to frames from user code; ends with the exception line."""
    tb = traceback.extract_tb(exc.__traceback__)
    keep = [f for f in tb if not f.filename.startswith(_PKG_DIR)]
    lines = []
    for f in keep[-8:]:
        where = os.path.basename(f.filename) if f.filename not in filenames else f.filename
        lines.append("  %s:%s in %s" % (where, f.lineno, f.name))
        if f.line:
            lines.append("    " + f.line.strip())
    lines += traceback.format_exception_only(type(exc), exc)
    return "\n".join(l.rstrip() for l in lines)


def _compile_units(src, filename):
    """Compile source; if the last statement is an expression, compile it separately
    so its value can be returned (REPL style)."""
    tree = ast.parse(src, filename)
    last = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = ast.Expression(tree.body.pop().value)
    return tree, last


def _register_source(filename, src):
    linecache.cache[filename] = (len(src), None, src.splitlines(True), filename)


def _run(units, ns, filename, label, transaction=True, rollback_on_error=True,
         max_output=4000, recompute=False, extra=None, post=None):
    """Execute compiled units with change tracking. Returns compact text."""
    t0 = time.time()
    before = snap.snapshot()
    rep0 = _report_text()
    use_tx = transaction and FreeCAD.listDocuments()
    if use_tx:
        try:
            FreeCAD.setActiveTransaction("Claude: " + label[:50])
        except Exception:
            use_tx = False
    out = io.StringIO()
    value, status, err = None, "OK", None
    ns.pop("result", None)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            for code, is_expr in units:
                if is_expr:
                    value = eval(code, ns)
                else:
                    exec(code, ns)
            if post:
                v = post()
                if v is not None:
                    value = v
    except BaseException as e:  # incl. SystemExit from scripts
        status = "ERROR"
        err = _trim_tb(e, {filename})
    if use_tx:
        abort = status != "OK" and rollback_on_error
        try:
            FreeCAD.closeActiveTransaction(abort)
            if abort:
                status = "ERROR (all changes rolled back)"
        except Exception:
            pass
    if recompute and status == "OK" and FreeCAD.ActiveDocument:
        FreeCAD.ActiveDocument.recompute()
    G = _gui()
    if G:
        try:
            G.updateGui()
        except Exception:
            pass
    after = snap.snapshot()
    lines = ["%s %.2fs" % (status, time.time() - t0)]
    if extra:
        lines += extra
    changes = snap.diff(before, after)
    if changes:
        lines.append("changes:")
        lines += changes
    elif status == "OK":
        lines.append("no model changes")
    lines += snap.problems(after)
    printed = out.getvalue()
    rep1 = _report_text()
    if rep0 is not None and rep1 is not None and len(rep1) > len(rep0) and rep1.startswith(rep0[:200]):
        r = rep1[len(rep0):].strip()
        if r:
            printed = (printed + "\n[report view]\n" + r) if printed else "[report view]\n" + r
    if printed.strip():
        lines.append("output:")
        lines.append(_clip(printed.rstrip(), max_output))
    if err:
        lines.append("error:")
        lines.append(err)
    if "result" in ns and value is None:
        value = ns["result"]
    if value is not None:
        try:
            r = repr(value)
        except Exception as e:
            r = "<repr failed: %s>" % e
        lines.append("=> " + _clip(r, max_output // 2))
    return "\n".join(lines)


# ---------------------------------------------------------------- methods

def status():
    G = _gui()
    docs = FreeCAD.listDocuments()
    lines = ["FreeCAD %s (%s), ClaudeLink up %ds, pid %d" % (
        ".".join(FreeCAD.Version()[:3]), "GUI" if G else "headless", time.time() - _started, os.getpid())]
    ad = FreeCAD.ActiveDocument
    for n, d in docs.items():
        mark = "*" if ad is not None and d.Name == ad.Name else " "
        lines.append("%s %s: %d objects%s%s" % (mark, n, len(d.Objects),
                                                 (", " + d.FileName) if d.FileName else ", unsaved",
                                                 ", modified" if _modified(d) else ""))
    if not docs:
        lines.append("no documents open")
    ns = getattr(FreeCAD, "_claudelink_ns", None)
    if ns:
        user = [k for k in ns if not k.startswith("_") and k not in _base_ns()]
        lines.append("namespace: %d user names%s" % (len(user), (" (" + ", ".join(sorted(user)[:20]) + ")") if user else ""))
    return "\n".join(lines)


def _modified(d):
    G = _gui()
    if G:
        try:
            return bool(G.getDocument(d.Name).Modified)
        except Exception:
            pass
    return False


def exec_code(code, fresh=False, transaction=True, rollback_on_error=True, max_output=4000, recompute=False):
    ns = _shared_ns(fresh)
    _counter[0] += 1
    filename = "<claude-%d>" % _counter[0]
    _register_source(filename, code)
    try:
        tree, last = _compile_units(code, filename)
    except SyntaxError as e:
        return "SYNTAX ERROR line %s: %s\n    %s" % (e.lineno, e.msg, (e.text or "").rstrip())
    units = [(compile(tree, filename, "exec"), False)]
    if last is not None:
        units.append((compile(last, filename, "eval"), True))
    label = code.strip().splitlines()[0] if code.strip() else "exec"
    return _run(units, ns, filename, label, transaction, rollback_on_error, max_output, recompute)


def _override_ast(tree, overrides):
    """Replace literal values of top-level `NAME = ...` / `A, B = ...` assignments."""
    applied = set()

    def const(v, like):
        node = ast.parse(repr(tuple(v) if isinstance(v, list) and isinstance(like, ast.Tuple) else v),
                         mode="eval").body
        return node

    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        t = node.targets[0]
        if isinstance(t, ast.Name) and t.id in overrides:
            node.value = ast.copy_location(const(overrides[t.id], node.value), node.value)
            applied.add(t.id)
        elif isinstance(t, ast.Tuple) and isinstance(node.value, ast.Tuple) and len(t.elts) == len(node.value.elts):
            for i, el in enumerate(t.elts):
                if isinstance(el, ast.Name) and el.id in overrides:
                    node.value.elts[i] = ast.copy_location(const(overrides[el.id], node.value.elts[i]), node.value.elts[i])
                    applied.add(el.id)
    ast.fix_missing_locations(tree)
    return applied


def run_file(path, overrides=None, then=None, shared=False, transaction=True,
             rollback_on_error=True, max_output=4000, recompute=False, module_name="__main__"):
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise UserError("file not found: %s" % path)
    with open(path, encoding="utf-8-sig") as f:
        src = f.read()
    overrides = overrides or {}
    try:
        tree = ast.parse(src, path)
    except SyntaxError as e:
        return "SYNTAX ERROR %s:%s: %s" % (os.path.basename(path), e.lineno, e.msg)
    applied = _override_ast(tree, overrides)
    unknown = [k for k in overrides if k not in applied]
    units = [(compile(tree, path, "exec"), False)]
    base = _shared_ns()
    if shared:
        ns = base
        ns["__file__"] = path
    else:
        ns = dict(_base_ns())
        ns.update(__name__=module_name, __file__=path)
    then_file = None
    if then:
        _counter[0] += 1
        then_file = "<claude-then-%d>" % _counter[0]
        _register_source(then_file, then)
        ttree, tlast = _compile_units(then, then_file)

    def post():
        for k, v in overrides.items():  # beat any later override (e.g. params_override.json)
            ns[k] = tuple(v) if isinstance(v, list) and isinstance(ns.get(k), tuple) else v
        mod = types.ModuleType(os.path.splitext(os.path.basename(path))[0])
        mod.__dict__.update(ns)
        base["last_run"] = mod
        if then:
            exec(compile(ttree, then_file, "exec"), ns)
            if tlast is not None:
                return eval(compile(tlast, then_file, "eval"), ns)
        return None

    extra = []
    if overrides:
        extra.append("overrides applied: %s" % (", ".join("%s=%s" % (k, overrides[k]) for k in overrides if k in applied) or "none"))
        if unknown:
            extra.append("overrides set after load only (no top-level literal found): " + ", ".join(unknown))
    d = os.path.dirname(path)
    sys.path.insert(0, d)
    try:
        return _run(units, ns, path, os.path.basename(path), transaction, rollback_on_error, max_output,
                    recompute, extra=extra, post=post)
    finally:
        try:
            sys.path.remove(d)
        except ValueError:
            pass


def _children(o):
    G = _gui()
    kids = []
    if G:
        try:
            kids = list(o.ViewObject.claimChildren() or [])
        except Exception:
            kids = []
    if not kids:
        kids = list(getattr(o, "Group", None) or [])
    return [k for k in kids if hasattr(k, "Name")]


def _line(o):
    rec = snap.obj_record(o)
    s = snap.describe(o.Name, rec)
    try:
        if not o.Visibility:
            s += " (hidden)"
    except Exception:
        pass
    return s


def tree(doc=None, depth=2, filter=None, limit=150):
    d = _doc(doc)
    head = "%s: %d objects%s%s" % (d.Name, len(d.Objects), (", " + d.FileName) if d.FileName else ", unsaved",
                                   ", modified" if _modified(d) else "")
    lines = [head]
    if filter:
        f = filter.lower()
        hits = [o for o in d.Objects if f in o.Name.lower() or f in o.Label.lower() or f in o.TypeId.lower()]
        for o in hits[:limit]:
            lines.append(_line(o))
        if len(hits) > limit:
            lines.append("... %d more" % (len(hits) - limit))
        if not hits:
            lines.append("(no match)")
        return "\n".join(lines)
    claimed = set()
    kids_of = {}
    for o in d.Objects:
        k = _children(o)
        kids_of[o.Name] = k
        claimed.update(c.Name for c in k)
    roots = [o for o in d.Objects if o.Name not in claimed]
    seen = set()
    count = [0]

    def walk(o, lvl):
        if o.Name in seen or count[0] >= limit:
            return
        seen.add(o.Name)
        count[0] += 1
        kids = kids_of.get(o.Name, [])
        s = "  " * lvl + _line(o)
        if kids and lvl + 1 >= depth:
            s += " {%d children}" % len(kids)
            lines.append(s)
            return
        lines.append(s)
        for c in kids:
            walk(c, lvl + 1)

    for r in roots:
        walk(r, 0)
    if count[0] >= limit:
        lines.append("... truncated at %d lines (use filter or depth)" % limit)
    lines += snap.problems({d.Name: {o.Name: snap.obj_record(o) for o in d.Objects}})
    return "\n".join(lines)


def _placement_str(p):
    b = p.Base
    y, pi, r = p.Rotation.toEuler()
    s = "pos (%s, %s, %s)" % (fmt(b.x), fmt(b.y), fmt(b.z))
    if abs(y) > 1e-9 or abs(pi) > 1e-9 or abs(r) > 1e-9:
        ax = p.Rotation.Axis
        s += " rot %s deg about (%s, %s, %s) [ypr %s, %s, %s]" % (
            fmt(math.degrees(p.Rotation.Angle)), fmt(ax.x), fmt(ax.y), fmt(ax.z), fmt(y), fmt(pi), fmt(r))
    return s


_SKIP_PROPS = {"Shape", "ExpressionEngine", "Label2", "Proxy", "_LinkTouched", "_GroupTouched", "Visibility",
               "Label", "Placement"}


def inspect(name, doc=None, detail="summary", max_props=80):
    d = _doc(doc)
    o = _obj(d, name)
    lines = ["%s [%s] label %r in %s" % (o.Name, o.TypeId, o.Label, d.Name)]
    try:
        st = [s for s in o.State if s not in ("Up-to-date", "Expanded")]
        lines.append("state: " + (",".join(st) or "ok") + ("" if o.Visibility else ", hidden"))
    except Exception:
        pass
    if hasattr(o, "Placement"):
        lines.append("placement: " + _placement_str(o.Placement))
    s = snap.shape_of(o)
    if s is not None:
        _, (valid, bb, vol, ns_) = snap.metrics(s)
        if bb:
            lines.append("bbox: min (%s, %s, %s) max (%s, %s, %s) size %s" % tuple(
                [fmt(v) for v in bb] + [snap.bb_size(bb)]))
        topo = "solids %d, shells %d, faces %d, edges %d, vertices %d" % (
            len(s.Solids), len(s.Shells), len(s.Faces), len(s.Edges), len(s.Vertexes))
        lines.append("topology: " + topo + ("" if valid else "  ** INVALID SHAPE **"))
        try:
            g = "volume %s, area %s" % (fmt(s.Volume) if ns_ else "-", fmt(s.Area))
            if ns_:
                c = s.Solids[0].CenterOfMass if ns_ == 1 else s.CenterOfGravity if hasattr(s, "CenterOfGravity") else None
                if c is not None:
                    g += ", centre of mass (%s, %s, %s)" % (fmt(c.x), fmt(c.y), fmt(c.z))
            lines.append(g)
        except Exception:
            pass
    try:
        if o.ExpressionEngine:
            lines.append("expressions: " + "; ".join("%s = %s" % e for e in o.ExpressionEngine))
    except Exception:
        pass
    ins = [x.Name for x in getattr(o, "InList", [])]
    outs = [x.Name for x in getattr(o, "OutList", [])]
    if ins:
        lines.append("used by: " + ", ".join(ins[:20]) + (" ..." if len(ins) > 20 else ""))
    if outs:
        lines.append("uses: " + ", ".join(outs[:20]) + (" ..." if len(outs) > 20 else ""))
    if detail in ("props", "full"):
        lines.append("properties:")
        n = 0
        for p in o.PropertiesList:
            if p in _SKIP_PROPS and detail != "full":
                continue
            try:
                v = getattr(o, p)
            except Exception:
                continue
            if hasattr(v, "Name") and hasattr(v, "TypeId"):
                v = "<%s>" % v.Name
            r = repr(v)
            if len(r) > 100:
                r = r[:97] + "..."
            lines.append("  %s = %s" % (p, r))
            n += 1
            if n >= max_props:
                lines.append("  ... more properties")
                break
    if detail == "full" and _gui():
        try:
            vo = o.ViewObject
            lines.append("view: " + ", ".join("%s=%r" % (p, getattr(vo, p)) for p in (
                "ShapeColor", "Transparency", "DisplayMode", "LineWidth") if hasattr(vo, p)))
        except Exception:
            pass
    return "\n".join(lines)


def doc(action="list", name=None, path=None, force=False):
    docs = FreeCAD.listDocuments()
    if action == "list":
        return status()
    if action == "new":
        d = FreeCAD.newDocument(name or "Unnamed")
        d.UndoMode = 1
        return "created %s (active)" % d.Name
    if action == "open":
        if not path:
            raise UserError("open needs path")
        p = os.path.abspath(os.path.expanduser(path))
        for d in docs.values():
            if d.FileName and os.path.normcase(d.FileName) == os.path.normcase(p):
                FreeCAD.setActiveDocument(d.Name)
                return "already open as %s (made active)" % d.Name
        d = FreeCAD.openDocument(p)
        d.UndoMode = 1
        return "opened %s: %d objects" % (d.Name, len(d.Objects))
    d = _doc(name)
    if action == "save":
        if not d.FileName:
            raise UserError("%s has never been saved; use action=saveas with path" % d.Name)
        d.save()
        return "saved %s" % d.FileName
    if action == "saveas":
        if not path:
            raise UserError("saveas needs path")
        p = os.path.abspath(os.path.expanduser(path))
        if os.path.exists(p) and not force:
            raise UserError("%s exists; pass force=true to overwrite" % p)
        d.saveAs(p)
        return "saved %s as %s" % (d.Name, p)
    if action == "close":
        if _modified(d) and not force:
            raise UserError("%s has unsaved changes; save first or pass force=true to discard them" % d.Name)
        n = d.Name
        FreeCAD.closeDocument(n)
        return "closed %s" % n
    if action == "activate":
        FreeCAD.setActiveDocument(d.Name)
        return "active: %s" % d.Name
    if action == "recompute":
        before = snap.snapshot()
        t0 = time.time()
        n = d.recompute()
        lines = ["recomputed %s objects in %.2fs" % (n, time.time() - t0)]
        lines += snap.diff(before, snap.snapshot()) or ["no geometry changes"]
        lines += snap.problems({d.Name: {o.Name: snap.obj_record(o) for o in d.Objects}})
        return "\n".join(lines)
    raise UserError("unknown action %r (list/new/open/save/saveas/close/activate/recompute)" % action)


def undo(doc=None, steps=1, redo=False):
    d = _doc(doc)
    names = list(getattr(d, "RedoNames" if redo else "UndoNames", []) or [])
    if not names:
        return "nothing to %s in %s" % ("redo" if redo else "undo", d.Name)
    before = snap.snapshot()
    done = []
    for i in range(min(steps, len(names))):
        (d.redo if redo else d.undo)()
        done.append(names[i])
    G = _gui()
    if G:
        G.updateGui()
    lines = ["%s %d: %s" % ("redid" if redo else "undid", len(done), " | ".join(done))]
    lines += snap.diff(before, snap.snapshot()) or ["no geometry changes"]
    return "\n".join(lines)


def console(tail=40, grep=None):
    t = _report_text()
    if t is None:
        return "report view not available (headless)"
    lines = t.splitlines()
    if grep:
        g = grep.lower()
        lines = [l for l in lines if g in l.lower()]
    return "\n".join(lines[-tail:]) or "(empty)"


# ---------------------------------------------------------------- view

_VIEWS = {"iso": "viewIsometric", "isometric": "viewIsometric", "front": "viewFront", "back": "viewRear",
          "rear": "viewRear", "top": "viewTop", "bottom": "viewBottom", "left": "viewLeft",
          "right": "viewRight", "dimetric": "viewDimetric", "trimetric": "viewTrimetric"}


def _set_view(v, name):
    if name.startswith("dir:"):
        x, y, z = (float(c) for c in name[4:].split(","))
        v.setViewDirection((x, y, z))
        return
    m = _VIEWS.get(name.lower())
    if m is None:
        raise UserError("unknown view %r; use %s or dir:x,y,z" % (name, ", ".join(sorted(set(_VIEWS)))))
    getattr(v, m)()


def _restore_visibility(d, saved):
    """Restore containers first (they cascade to children), then settle every object."""
    def is_container(o):
        return bool(getattr(o, "Group", None))
    objs = [d.getObject(n) for n in saved]
    objs = [o for o in objs if o is not None]
    for o in sorted(objs, key=lambda o: not is_container(o)):
        if bool(o.ViewObject.Visibility) != saved[o.Name]:
            o.ViewObject.Visibility = saved[o.Name]
    for _ in range(3):
        wrong = [o for o in objs if bool(o.ViewObject.Visibility) != saved[o.Name]]
        if not wrong:
            break
        for o in wrong:
            o.ViewObject.Visibility = saved[o.Name]


def view(doc=None, views=None, size=380, objects=None, highlight=None, background="White",
         fit=True, format="png", quality=85, ortho=True):
    G = _gui()
    if not G:
        raise UserError("view needs the FreeCAD GUI (this is a headless instance)")
    from PySide import QtCore, QtGui
    d = _doc(doc)
    gd = G.getDocument(d.Name)
    v3 = gd.mdiViewsOfType("Gui::View3DInventor")
    if not v3:
        raise UserError("document %s has no 3D view" % d.Name)
    v = v3[0]
    views = views or ["iso", "front", "top", "right"]
    size = max(120, min(int(size), 1600))
    cam = v.getCamera()
    cam_type = v.getCameraType()
    try:
        anim = v.isAnimationEnabled()
        v.setAnimationEnabled(False)  # an unfinished view animation would move the camera after restore
    except Exception:
        anim = None
    vis_saved = {}
    sel_saved = [(s.DocumentName, s.ObjectName, s.SubElementNames) for s in G.Selection.getSelectionEx()]
    tmpdir = os.path.join(FreeCAD.getUserCachePath() if hasattr(FreeCAD, "getUserCachePath") else os.environ.get("TEMP", "."), "ClaudeLink")
    os.makedirs(tmpdir, exist_ok=True)
    try:
        v.setCameraType("Orthographic" if ortho else "Perspective")
        if objects:
            keep = set()
            for k in objects:
                o = _obj(d, k)
                keep.add(o.Name)
                for p in o.InListRecursive:
                    if p.TypeId in ("App::Part", "PartDesign::Body", "App::DocumentObjectGroup"):
                        keep.add(p.Name)
            # Save EVERY object's visibility: toggling a group/part also toggles its children.
            for o in d.Objects:
                vo = getattr(o, "ViewObject", None)
                if vo is not None and hasattr(vo, "Visibility"):
                    vis_saved[o.Name] = bool(vo.Visibility)
            for o in d.Objects:
                if o.Name in vis_saved:
                    want = o.Name in keep
                    if bool(o.ViewObject.Visibility) != want:
                        o.ViewObject.Visibility = want
        G.Selection.clearSelection()
        for k in highlight or []:
            G.Selection.addSelection(d.Name, _obj(d, k).Name)
        tiles = []
        for i, name in enumerate(views):
            _set_view(v, name)
            if fit:
                v.fitAll()
            G.updateGui()
            f = os.path.join(tmpdir, "tile%d.png" % i)
            v.saveImage(f, size, size, background)
            tiles.append((name, QtGui.QImage(f)))
        cols = 1 if len(tiles) == 1 else 2 if len(tiles) <= 4 else 3
        rows = (len(tiles) + cols - 1) // cols
        sheet = QtGui.QImage(cols * size, rows * size, QtGui.QImage.Format_RGB32)
        sheet.fill(QtGui.QColor(255, 255, 255))
        p = QtGui.QPainter(sheet)
        font = QtGui.QFont()
        font.setPixelSize(max(11, size // 28))
        p.setFont(font)
        for i, (name, img) in enumerate(tiles):
            x, y = (i % cols) * size, (i // cols) * size
            p.drawImage(x, y, img)
            p.setPen(QtGui.QColor(200, 200, 200))
            p.drawRect(x, y, size - 1, size - 1)
            p.setPen(QtGui.QColor(60, 60, 60))
            p.drawText(x + 6, y + 4 + font.pixelSize(), name)
        p.end()
        fmt_ = "JPG" if format.lower() in ("jpg", "jpeg") else "PNG"
        buf = QtCore.QBuffer()
        buf.open(QtCore.QIODevice.WriteOnly)
        sheet.save(buf, fmt_, int(quality) if fmt_ == "JPG" else -1)
        data = bytes(buf.data())
    finally:
        try:
            v.setCameraType(cam_type)
            v.setCamera(cam)
            G.updateGui()
            v.setCamera(cam)
            if anim is not None:
                v.setAnimationEnabled(anim)
        except Exception:
            pass
        _restore_visibility(d, vis_saved)
        G.Selection.clearSelection()
        for dn, on, subs in sel_saved:
            try:
                if subs:
                    for s in subs:
                        G.Selection.addSelection(dn, on, s)
                else:
                    G.Selection.addSelection(dn, on)
            except Exception:
                pass
        G.updateGui()
    import base64
    text = "%s: %s, %dx%d px%s" % (d.Name, ", ".join(views), sheet.width(), sheet.height(),
                                   (", isolated " + ", ".join(objects)) if objects else "")
    return {"text": text, "image_b64": base64.b64encode(data).decode("ascii"),
            "mime": "image/jpeg" if fmt_ == "JPG" else "image/png"}


METHODS = {
    "status": status,
    "exec": exec_code,
    "run_file": run_file,
    "tree": tree,
    "inspect": inspect,
    "doc": doc,
    "undo": undo,
    "console": console,
    "view": view,
}

from . import analysis as _analysis  # noqa: E402
from . import project as _project  # noqa: E402
from . import output as _output  # noqa: E402
from . import drafting as _drafting  # noqa: E402
from . import profile as _profile  # noqa: E402
from . import printprep as _printprep  # noqa: E402
from . import strength as _strength  # noqa: E402
from . import splitting as _splitting  # noqa: E402
from . import motion as _motion  # noqa: E402

METHODS.update(_analysis.METHODS)
METHODS.update(_project.METHODS)
METHODS.update(_output.METHODS)
METHODS.update(_drafting.METHODS)
METHODS.update(_profile.METHODS)
METHODS.update(_printprep.METHODS); METHODS.update(_strength.METHODS); METHODS.update(_splitting.METHODS); METHODS.update(_motion.METHODS)
