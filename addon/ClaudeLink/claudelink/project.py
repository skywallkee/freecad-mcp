"""Phase 3: parametric project handlers.

A project is a folder with <Name>.FCStd and a build_*.py whose parameters are top-level literal
constants, optionally overridden by params_override.json (keys may carry a leading ') and mirrored
in a 'Params' spreadsheet (column A name, column B value)."""
import ast
import glob
import json
import os
import re

import FreeCAD

from .server import UserError
from .snapshot import fmt

DOCS_DIR = os.path.join(os.path.expanduser("~"), "Documents")


# ------------------------------------------------------------------ resolution

class Project:
    def __init__(self, folder):
        self.folder = os.path.abspath(folder)
        self.name = os.path.basename(self.folder.rstrip("\\/"))
        scripts = sorted(glob.glob(os.path.join(self.folder, "build_*.py")))
        self.script = scripts[0] if scripts else None
        fc = os.path.join(self.folder, self.name + ".FCStd")
        if not os.path.exists(fc):
            cands = [f for f in glob.glob(os.path.join(self.folder, "*.FCStd"))]
            fc = cands[0] if len(cands) == 1 else fc
        self.fcstd = fc
        self.doc_name = os.path.splitext(os.path.basename(fc))[0]
        self.override = os.path.join(self.folder, "params_override.json")

    def doc(self):
        for d in FreeCAD.listDocuments().values():
            if d.FileName and os.path.normcase(os.path.abspath(d.FileName)) == os.path.normcase(self.fcstd):
                return d
        return None


def resolve(project=None):
    """Folder path, a folder name under Documents, an open document name, or None (active doc)."""
    if project:
        if os.path.isdir(project):
            return Project(project)
        p = os.path.join(DOCS_DIR, project)
        if os.path.isdir(p):
            return Project(p)
        d = FreeCAD.listDocuments().get(project)
        if d is not None and d.FileName:
            return Project(os.path.dirname(d.FileName))
        raise UserError("project %r not found (folder path, name under %s, or open document)" % (project, DOCS_DIR))
    d = FreeCAD.ActiveDocument
    if d is None or not d.FileName:
        raise UserError("no project given and the active document is not saved in a project folder")
    return Project(os.path.dirname(d.FileName))


def _need_script(pr):
    if not pr.script:
        raise UserError("no build_*.py in %s" % pr.folder)
    return pr.script


# ------------------------------------------------------------------ parameters

def parse_params(script):
    """Top-level literal constants before the first def/class, with trailing comments and group headings.
    Returns [(name, value, comment, group, node_value)] and the source text."""
    with open(script, encoding="utf-8-sig") as f:
        src = f.read()
    tree = ast.parse(src)
    lines = src.splitlines()
    first_def = min([n.lineno for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))] or [len(lines) + 1])
    heads = {}
    for i, l in enumerate(lines[: first_def - 1], 1):
        m = re.match(r"\s*#\s*-{2,}\s*(.*?)\s*-*\s*$", l)
        if m and m.group(1):
            heads[i] = m.group(1)
    out = []
    for node in tree.body:
        if node.lineno >= first_def:
            break
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        t = node.targets[0]
        if isinstance(t, ast.Name):
            pairs = [(t.id, node.value)]
        elif isinstance(t, ast.Tuple) and isinstance(node.value, ast.Tuple) and len(t.elts) == len(node.value.elts):
            pairs = [(e.id, v) for e, v in zip(t.elts, node.value.elts) if isinstance(e, ast.Name)]
        else:
            continue
        line = lines[node.lineno - 1]
        comment = line.split("#", 1)[1].strip() if "#" in line else ""
        group = ""
        for ln in sorted(heads):
            if ln < node.lineno:
                group = heads[ln]
        for name, vnode in pairs:
            if name.startswith("_") or not name.isupper():
                continue
            try:
                val = ast.literal_eval(vnode)
            except Exception:
                continue
            if isinstance(val, (int, float, bool, str, tuple)):
                out.append((name, val, comment, group, vnode))
    return out, src


def read_override(pr):
    if not os.path.exists(pr.override):
        return {}
    with open(pr.override, encoding="utf-8") as f:
        raw = json.load(f)
    return {k.lstrip("'"): v for k, v in raw.items()}


def write_override(pr, values):
    with open(pr.override, "w", encoding="utf-8") as f:
        json.dump(values, f, indent=1)


def read_sheet(doc):
    sh = doc.getObject("Params") if doc else None
    if sh is None or sh.TypeId != "Spreadsheet::Sheet":
        return {}, None
    vals, row = {}, 2
    while sh.getContents("A%d" % row):
        k = sh.getContents("A%d" % row).lstrip("'")
        try:
            vals[k] = (row, sh.get("B%d" % row))
        except Exception:
            vals[k] = (row, sh.getContents("B%d" % row))
        row += 1
    return vals, sh


def _same(a, b):
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return a == b


def _fmtv(v):
    if isinstance(v, float):
        return fmt(v)
    if isinstance(v, tuple):
        return "(" + ", ".join(_fmtv(x) for x in v) + ")"
    return repr(v) if isinstance(v, str) else str(v)


def info(project=None, filter=None, limit=120):
    pr = resolve(project)
    d = pr.doc()
    lines = ["project %s: %s" % (pr.name, pr.folder)]
    lines.append("document: %s (%s)" % (os.path.basename(pr.fcstd) if os.path.exists(pr.fcstd) else "none",
                                         "open as " + d.Name if d else "not open"))
    if not pr.script:
        lines.append("no build_*.py script")
        return "\n".join(lines)
    params, src = parse_params(pr.script)
    tree = ast.parse(src)
    build = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build"), None)
    sig = "build(%s)" % ", ".join(a.arg for a in build.args.args) if build else "no build() (runs at top level)"
    lines.append("script: %s, %s, %d parameters" % (os.path.basename(pr.script), sig, len(params)))
    extras = [x for x in ("stl", "step", "images", "ref") if os.path.isdir(os.path.join(pr.folder, x))]
    macros = [os.path.basename(m) for m in glob.glob(os.path.join(pr.folder, "*.FCMacro"))]
    lines.append("folders: %s%s" % (", ".join(extras) or "-", ("; macros: " + ", ".join(macros)) if macros else ""))
    ov = read_override(pr)
    sheet, _ = read_sheet(d)
    lines.append("overrides: %s; Params sheet: %s" % ("%d in params_override.json" % len(ov) if ov else "none",
                                                     "%d rows" % len(sheet) if sheet else ("n/a" if not d else "none")))
    f = filter.lower() if filter else None
    group, n = None, 0
    for name, val, comment, grp, _ in params:
        if f and f not in name.lower() and f not in comment.lower() and f not in grp.lower():
            continue
        if grp != group:
            lines.append("[%s]" % (grp or "params"))
            group = grp
        eff, marks = val, []
        if name in ov and not _same(ov[name], val):
            eff = ov[name]
            marks.append("override; source %s" % _fmtv(val))
        if name in sheet and not _same(sheet[name][1], eff):
            marks.append("sheet says %s" % _fmtv(sheet[name][1]))
        s = "  %s = %s" % (name, _fmtv(eff))
        if marks:
            s += "  [" + "; ".join(marks) + "]"
        if comment:
            s += "  # " + comment
        lines.append(s)
        n += 1
        if n >= limit:
            lines.append("  ... more (use filter)")
            break
    unknown = [k for k in ov if k not in {p[0] for p in params}]
    if unknown:
        lines.append("overrides with no matching constant: " + ", ".join(unknown))
    return "\n".join(lines)


def set_params(project=None, values=None, persist="auto", rebuild=False, export=False, save=False):
    pr = resolve(project)
    script = _need_script(pr)
    if not values:
        raise UserError("values is empty")
    params, src = parse_params(script)
    if persist == "auto":  # override file only works if the script reads it
        persist = "override" if "params_override.json" in src else "source"
    known = {p[0]: p for p in params}
    unknown = [k for k in values if k not in known]
    if unknown:
        raise UserError("unknown parameter(s): %s" % ", ".join(unknown))
    lines = []
    ov = read_override(pr)
    if persist == "source":
        # replace literal text in place (bottom-up so offsets stay valid); comments are preserved
        src_lines = src.splitlines(True)
        edits = sorted(((known[k][4], v) for k, v in values.items()),
                       key=lambda e: (e[0].lineno, e[0].col_offset), reverse=True)
        for node, v in edits:
            if node.lineno != node.end_lineno:
                raise UserError("value of a parameter spans several lines; edit it with freecad_exec")
            l = src_lines[node.lineno - 1]
            b = l.encode("utf-8")
            new = repr(tuple(v)) if isinstance(v, list) else repr(v)
            b = b[:node.col_offset] + new.encode("utf-8") + b[node.end_col_offset:]
            src_lines[node.lineno - 1] = b.decode("utf-8")
        with open(script, "w", encoding="utf-8", newline="") as f:
            f.write("".join(src_lines))
        dropped = [k for k in values if k in ov]
        for k in dropped:
            ov.pop(k)
        if dropped:
            write_override(pr, ov)
        lines.append("wrote %d value(s) into %s%s" % (len(values), os.path.basename(script),
                                                    ("; removed from params_override.json: " + ", ".join(dropped)) if dropped else ""))
    elif persist == "override":
        ov.update(values)
        write_override(pr, ov)
        lines.append("params_override.json: %s" % ", ".join("%s=%s" % (k, _fmtv(v)) for k, v in values.items()))
    elif persist != "none":
        raise UserError("persist must be override, source or none")
    d = pr.doc()
    sheet, sh = read_sheet(d)
    synced = []
    if sh is not None and persist != "none":
        for k, v in values.items():
            if k in sheet:
                sh.set("B%d" % sheet[k][0], str(v))
                synced.append(k)
        if synced:
            sh.recompute()
            lines.append("Params sheet updated: " + ", ".join(synced))
    if rebuild:
        lines.append(rebuild_(pr, overrides=values if persist == "none" else None, export=export, save=save))
    else:
        lines.append("(not rebuilt; pass rebuild=true or call freecad_project_rebuild)")
    return "\n".join(lines)


# ------------------------------------------------------------------ rebuild

def rebuild(project=None, overrides=None, export=False, save=False, checks=False, max_output=4000):
    return rebuild_(resolve(project), overrides, export, save, checks, max_output)


def rebuild_(pr, overrides=None, export=False, save=False, checks=False, max_output=4000):
    from . import handlers
    script = _need_script(pr)
    d = pr.doc()
    opened = False
    if d is None and os.path.exists(pr.fcstd):
        d = FreeCAD.openDocument(pr.fcstd)
        d.UndoMode = 1
        opened = True
    if d is not None:
        FreeCAD.setActiveDocument(d.Name)
    with open(script, encoding="utf-8-sig") as f:
        tree = ast.parse(f.read())
    build = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build"), None)
    args = [a.arg for a in build.args.args] if build else []
    then = None
    if build is not None:
        kw = []
        if "doc" in args:
            kw.append("doc=App.getDocument(%r)" % d.Name if d is not None else "doc=None")
        if "export_dir" in args:
            kw.append("export_dir=%r" % (os.path.join(pr.folder, "stl") if export else None))
        then = ("_r = build(%s)\nif isinstance(_r, (list, tuple)) and all(isinstance(x, str) for x in _r):\n"
                "    print('\\n'.join(_r))\n_r if not isinstance(_r, (list, tuple)) else None" % ", ".join(kw))
    text = handlers.run_file(script, overrides=overrides, then=then, max_output=max_output, module_name="build_module")
    out = ["rebuilt %s%s%s" % (pr.name, " (opened %s)" % os.path.basename(pr.fcstd) if opened else "",
                               ", exported to stl/ + step/" if export and "export_dir" in args else "")]
    if export and "export_dir" not in args:
        out.append("note: build() has no export_dir; use freecad_export")
    out.append(text)
    if checks and text.startswith("OK"):
        from . import analysis
        try:
            out.append("checks:\n" + analysis.check_geometry(doc=d.Name if d else None, bop=False))
            out.append(analysis.fit(doc=d.Name if d else None, max_gap=0.5))
        except UserError as e:
            out.append("checks skipped: %s" % e)
    if save and text.startswith("OK"):
        d2 = pr.doc() or FreeCAD.ActiveDocument
        if d2 is not None:
            if d2.FileName:
                d2.save()
            else:
                d2.saveAs(pr.fcstd)
            out.append("saved %s" % d2.FileName)
    return "\n".join(out)


# ------------------------------------------------------------------ new project

TEMPLATE = r'''# {{NAME}} - parametric FreeCAD 1.0 model.
# {{DESC}}
# Edit the parameters below, or column B of the "Params" sheet and run Macro > Rebuild_{{NAME}}.
import FreeCAD as App, Part, math, os
V = App.Vector

# ---------------- PARAMETERS ----------------
# --- body ---
WIDTH   = 40.0   # overall width (X)
DEPTH   = 30.0   # overall depth (Y)
HEIGHT  = 10.0   # overall height (Z)
FILLET  = 2.0    # vertical edge fillet radius (0 = sharp)
# --------------------------------------------
import json as _json
_ov = os.path.join(os.path.dirname(os.path.abspath(__file__)), "params_override.json")
if os.path.exists(_ov):
    with open(_ov) as _f: globals().update({k.lstrip("'"): v for k, v in _json.load(_f).items()})

SHEET_PARAMS = [
    ("WIDTH", "overall width (X)"),
    ("DEPTH", "overall depth (Y)"),
    ("HEIGHT", "overall height (Z)"),
    ("FILLET", "vertical edge fillet radius"),
]


def make_body():
    b = Part.makeBox(WIDTH, DEPTH, HEIGHT, V(-WIDTH / 2, -DEPTH / 2, 0))
    if FILLET > 0:
        vert = [e for e in b.Edges if abs(e.Vertexes[0].Point.z - e.Vertexes[-1].Point.z) > 1e-6]
        b = b.makeFillet(FILLET, vert)
    return b


# (object name, shape function, colour)
PARTS = [("Body", make_body, (0.80, 0.80, 0.82))]


def make_param_sheet(doc):
    sh = doc.getObject("Params") or doc.addObject("Spreadsheet::Sheet", "Params")
    sh.set("A1", "Parameter"); sh.set("B1", "Value"); sh.set("C1", "What it is   ->  edit column B, then run Macro > Rebuild_{{NAME}}")
    for i, (k, note) in enumerate(SHEET_PARAMS, start=2):
        sh.set("A%d" % i, k); sh.set("B%d" % i, str(globals()[k])); sh.set("C%d" % i, note)
    sh.recompute()


MACRO = """# Rebuild {{NAME}} from the Params spreadsheet.
# Edit column B of "Params", then run this macro (Macro > Macros... > Rebuild_{{NAME}}).
import FreeCAD as App, importlib.util, json, os
doc = App.getDocument("{{NAME}}")
HERE = os.path.dirname(doc.FileName)
sh = doc.getObject("Params")
vals, row = {}, 2
while sh.getContents("A%d" % row):
    vals[sh.getContents("A%d" % row).lstrip("'")] = float(sh.get("B%d" % row)); row += 1
with open(os.path.join(HERE, "params_override.json"), "w") as f: json.dump(vals, f, indent=1)
spec = importlib.util.spec_from_file_location("b", os.path.join(HERE, "{{SCRIPT}}"))
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)
for l in b.build(doc, os.path.join(HERE, "stl")): print(l)
doc.save(); print("Rebuilt and re-exported STL + STEP.")
"""


def write_macro(folder):
    with open(os.path.join(folder, "Rebuild_{{NAME}}.FCMacro"), "w") as f:
        f.write(MACRO)


def build(doc=None, export_dir=None):
    doc = doc or App.ActiveDocument or App.newDocument("{{NAME}}")
    info, shapes = [], []
    for name, fn, col in PARTS:
        s = fn()
        shapes.append((name, s))
        o = doc.getObject(name) or doc.addObject("Part::Feature", name)
        o.Shape = s
        if App.GuiUp:
            o.ViewObject.ShapeColor = col
        b = s.BoundBox
        info.append("%-8s %.1f x %.1f x %.1f mm, %.2f cm3, valid %s" % (
            name, b.XLength, b.YLength, b.ZLength, s.Volume / 1000.0, s.isValid()))
    make_param_sheet(doc)
    if doc.FileName:
        write_macro(os.path.dirname(doc.FileName))
    doc.recompute()
    if export_dir:
        import MeshPart
        step_dir = os.path.join(os.path.dirname(export_dir), "step")
        for dd in (export_dir, step_dir):
            os.makedirs(dd, exist_ok=True)
        for name, s in shapes:
            fn = name.lower()
            s.exportStep(os.path.join(step_dir, fn + ".step"))
            MeshPart.meshFromShape(Shape=s, LinearDeflection=0.01, AngularDeflection=0.1).write(
                os.path.join(export_dir, fn + ".stl"))
    return info


if __name__ == "__main__":
    for l in build():
        print(l)
'''


def new(name, folder=None, description="", force=False):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
        raise UserError("name must be letters/digits/underscore, starting with a letter (it is the document name)")
    folder = os.path.abspath(folder or os.path.join(DOCS_DIR, name))
    if os.path.exists(folder) and os.listdir(folder) and not force:
        raise UserError("%s exists and is not empty (force=true to add files into it)" % folder)
    for sub in ("", "stl", "step", "images"):
        os.makedirs(os.path.join(folder, sub), exist_ok=True)
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    script = os.path.join(folder, "build_%s.py" % snake)
    if os.path.exists(script) and not force:
        raise UserError("%s exists" % script)
    body = TEMPLATE.replace("{{NAME}}", name).replace("{{DESC}}", description or "(describe the part here)") \
                   .replace("{{SCRIPT}}", os.path.basename(script))
    with open(script, "w", encoding="utf-8") as f:
        f.write(body)
    readme = os.path.join(folder, "README.md")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as f:
            f.write("# %s\n\n%s\n\n- Model: `%s.FCStd` (built by `%s`)\n- Parameters: top of the script, or the "
                    "`Params` sheet + `Rebuild_%s` macro\n- Prints: `stl/`, CAD exchange: `step/`\n"
                    % (name, description, name, os.path.basename(script), name))
    fc = os.path.join(folder, name + ".FCStd")
    if name in FreeCAD.listDocuments():
        raise UserError("a document named %s is already open; close it first" % name)
    d = FreeCAD.newDocument(name)
    d.UndoMode = 1
    d.saveAs(fc)
    out = ["created %s with %s, README.md, stl/ step/ images/, %s.FCStd" % (folder, os.path.basename(script), name)]
    out.append(rebuild_(Project(folder), save=True))
    return "\n".join(out)


METHODS = {
    "project_info": info,
    "project_params": set_params,
    "project_rebuild": rebuild,
    "project_new": new,
}
