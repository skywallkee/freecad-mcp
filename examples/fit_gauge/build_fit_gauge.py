# FitGauge - parametric FreeCAD 1.0 model.
# Printer clearance calibration: round + square hole rows at stepped clearances, Ø8 pin, 8 mm square peg,
# M3 heat-insert and self-tap holes. Results feed the ClaudeLink print profile.
# Edit the parameters below, or column B of the "Params" sheet and run Macro > Rebuild_FitGauge.
#
# Layout (top view, plate printed flat, pin + peg printed standing next to it):
#   row 1 (back)   round holes  for the Ø8 pin,       radial clearance 0 .. 0.40 (labels in 1/100 mm)
#   row 2          square holes for the 8 mm peg,     same clearances
#   row 3 (front)  3 blind M3 heat-insert holes (Ø3.8 / 4.0 / 4.2) + 3 M3 self-tap holes (Ø2.5 / 2.7 / 2.9)
import FreeCAD as App, Part, math, os
V = App.Vector

# ---------------- PARAMETERS ----------------
# --- fit test ---
NOM         = 8.0     # pin diameter / peg side (nominal)
CLEARANCES  = (0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40)   # radial clearance per side, one hole each
PEG_H       = 14.0    # pin / peg height
LEAD_IN     = 0.8     # top chamfer on pin and peg (easy start)
# --- hardware holes ---
INSERT_DIAS = (3.8, 4.0, 4.2)   # M3 heat-insert hole diameters
INSERT_DEPTH = 5.0    # blind depth of the insert holes
SCREW_DIAS  = (2.5, 2.7, 2.9)   # M3 self-tapping screw hole diameters (through)
# --- plate ---
PLATE_T     = 6.0     # plate thickness
PITCH       = 12.0    # hole spacing along a row
ROW_PITCH   = 17.0    # spacing between rows
MARGIN      = 5.0     # plate border
EDGE_CH     = 0.4     # chamfer on hole edges and part bottoms (hides elephant's foot)
# --- labels ---
LABEL_H     = 3.2     # engraved digit height
LABEL_DEPTH = 0.6     # engraving depth
FONT        = r"C:\Windows\Fonts\arialbd.ttf"   # bold sans font for the digits
# --------------------------------------------
import json as _json
_ov = os.path.join(os.path.dirname(os.path.abspath(__file__)), "params_override.json")
if os.path.exists(_ov):
    with open(_ov) as _f: globals().update({k.lstrip("'"): v for k, v in _json.load(_f).items()})

SHEET_PARAMS = [
    ("NOM", "pin diameter / peg side (nominal)"),
    ("PEG_H", "pin / peg height"),
    ("LEAD_IN", "top chamfer on pin and peg"),
    ("INSERT_DEPTH", "blind depth of the insert holes"),
    ("PLATE_T", "plate thickness"),
    ("PITCH", "hole spacing along a row"),
    ("ROW_PITCH", "spacing between rows"),
    ("MARGIN", "plate border"),
    ("EDGE_CH", "chamfer on hole edges and part bottoms"),
    ("LABEL_H", "engraved digit height"),
    ("LABEL_DEPTH", "engraving depth"),
]


# ---------------- geometry helpers ----------------
def hole_max():
    return NOM + 2 * max(CLEARANCES)


def layout():
    """Plate size and row y positions (origin = plate front-left-bottom corner)."""
    n = max(len(CLEARANCES), len(INSERT_DIAS) + len(SCREW_DIAS))
    w = 2 * MARGIN + (n - 1) * PITCH + hole_max()
    y3 = MARGIN + LABEL_H + 1.5 + max(INSERT_DIAS + SCREW_DIAS) / 2
    y2 = y3 + ROW_PITCH
    y1 = y2 + ROW_PITCH
    d = y1 + hole_max() / 2 + MARGIN
    return w, d, (y1, y2, y3)


def col_x(i):
    return MARGIN + hole_max() / 2 + i * PITCH


def round_hole(x, y, r, depth=None):
    """Vertical hole from the top, chamfered at the top (and the bottom if it goes through)."""
    depth = PLATE_T if depth is None else depth
    z0 = PLATE_T - depth
    tool = Part.makeCylinder(r, depth + 0.2, V(x, y, z0 - (0.1 if depth >= PLATE_T else 0)))
    tool = tool.fuse(Part.makeCone(r, r + EDGE_CH, EDGE_CH, V(x, y, PLATE_T - EDGE_CH)))
    if depth >= PLATE_T:
        tool = tool.fuse(Part.makeCone(r + EDGE_CH, r, EDGE_CH, V(x, y, 0)))
    return tool


def square_frustum(x, y, s0, s1, h, z0):
    w0 = Part.makePolygon([V(x - s0 / 2, y - s0 / 2, z0), V(x + s0 / 2, y - s0 / 2, z0), V(x + s0 / 2, y + s0 / 2, z0),
                           V(x - s0 / 2, y + s0 / 2, z0), V(x - s0 / 2, y - s0 / 2, z0)])
    z1 = z0 + h
    w1 = Part.makePolygon([V(x - s1 / 2, y - s1 / 2, z1), V(x + s1 / 2, y - s1 / 2, z1), V(x + s1 / 2, y + s1 / 2, z1),
                           V(x - s1 / 2, y + s1 / 2, z1), V(x - s1 / 2, y - s1 / 2, z1)])
    return Part.makeLoft([w0, w1], True)


def square_hole(x, y, s):
    tool = Part.makeBox(s, s, PLATE_T + 0.2, V(x - s / 2, y - s / 2, -0.1))
    tool = tool.fuse(square_frustum(x, y, s, s + 2 * EDGE_CH, EDGE_CH, PLATE_T - EDGE_CH))
    tool = tool.fuse(square_frustum(x, y, s + 2 * EDGE_CH, s, EDGE_CH, 0))
    return tool


def text_tool(txt, cx, ytop):
    """Engraving tool for `txt`, centred on cx, cap height LABEL_H, top of the text at ytop."""
    if not os.path.exists(FONT):
        return None
    chars = Part.makeWireString(txt, os.path.dirname(FONT) + os.sep, os.path.basename(FONT), LABEL_H, 0)
    faces = [Part.makeFace(ch, "Part::FaceMakerBullseye") for ch in chars if ch]
    if not faces:
        return None
    t = Part.makeCompound(faces)
    b = t.BoundBox
    t.translate(V(cx - (b.XMin + b.XMax) / 2, ytop - b.YMax, PLATE_T - LABEL_DEPTH))
    return t.extrude(V(0, 0, LABEL_DEPTH + 0.1))


def label(v):
    """Clearance in 1/100 mm ('0', '5', '10' ...)."""
    return "%d" % round(v * 100)


# ---------------- parts ----------------
def make_gauge():
    w, d, (y1, y2, y3) = layout()
    plate = Part.makeBox(w, d, PLATE_T)
    plate = plate.makeChamfer(EDGE_CH, [e for e in plate.Edges if abs(e.BoundBox.ZMax) < 1e-6])  # bottom edges
    tools, labels = [], []
    lab_gap = 1.2
    for i, c in enumerate(CLEARANCES):
        x = col_x(i)
        tools.append(round_hole(x, y1, NOM / 2 + c))
        tools.append(square_hole(x, y2, NOM + 2 * c))
        labels.append((label(c), x, y1 - hole_max() / 2 - lab_gap))
        labels.append((label(c), x, y2 - hole_max() / 2 - lab_gap))
    for i, dia in enumerate(INSERT_DIAS):
        x = col_x(i)
        tools.append(round_hole(x, y3, dia / 2, INSERT_DEPTH))
        labels.append(("%.1f" % dia, x, y3 - max(INSERT_DIAS) / 2 - lab_gap))
    for j, dia in enumerate(SCREW_DIAS):
        x = col_x(len(INSERT_DIAS) + j)
        tools.append(round_hole(x, y3, dia / 2))
        labels.append(("%.1f" % dia, x, y3 - max(INSERT_DIAS) / 2 - lab_gap))
    for txt, x, ytop in labels:
        t = text_tool(txt, x, ytop)
        if t is not None:
            tools.append(t)
    g = plate.cut(Part.makeCompound(tools))
    try:
        g = g.removeSplitter()
    except Exception:
        pass
    return g


def _chamfer_ends(s, z_bottom, z_top):
    bot = [e for e in s.Edges if abs(e.BoundBox.ZMax - z_bottom) < 1e-6 and abs(e.BoundBox.ZMin - z_bottom) < 1e-6]
    s = s.makeChamfer(EDGE_CH, bot)
    top = [e for e in s.Edges if abs(e.BoundBox.ZMin - z_top) < 1e-6 and abs(e.BoundBox.ZMax - z_top) < 1e-6]
    return s.makeChamfer(LEAD_IN, top)


def make_pin():
    w, d, (y1, y2, y3) = layout()
    return _chamfer_ends(Part.makeCylinder(NOM / 2, PEG_H, V(w + 6 + NOM / 2, y1, 0)), 0, PEG_H)


def make_peg():
    w, d, (y1, y2, y3) = layout()
    x = w + 6 + NOM / 2
    return _chamfer_ends(Part.makeBox(NOM, NOM, PEG_H, V(x - NOM / 2, y2 - NOM / 2, 0)), 0, PEG_H)


# (object name, shape function, colour)
PARTS = [("Gauge", make_gauge, (0.85, 0.55, 0.20)),
         ("Pin", make_pin, (0.25, 0.45, 0.80)),
         ("Peg", make_peg, (0.25, 0.65, 0.40))]


def make_param_sheet(doc):
    sh = doc.getObject("Params") or doc.addObject("Spreadsheet::Sheet", "Params")
    sh.set("A1", "Parameter"); sh.set("B1", "Value"); sh.set("C1", "What it is   ->  edit column B, then run Macro > Rebuild_FitGauge")
    for i, (k, note) in enumerate(SHEET_PARAMS, start=2):
        sh.set("A%d" % i, k); sh.set("B%d" % i, str(globals()[k])); sh.set("C%d" % i, note)
    sh.recompute()


MACRO = """# Rebuild FitGauge from the Params spreadsheet.
# Edit column B of "Params", then run this macro (Macro > Macros... > Rebuild_FitGauge).
import FreeCAD as App, importlib.util, json, os
doc = App.getDocument("FitGauge")
HERE = os.path.dirname(doc.FileName)
sh = doc.getObject("Params")
vals, row = {}, 2
while sh.getContents("A%d" % row):
    vals[sh.getContents("A%d" % row).lstrip("'")] = float(sh.get("B%d" % row)); row += 1
with open(os.path.join(HERE, "params_override.json"), "w") as f: json.dump(vals, f, indent=1)
spec = importlib.util.spec_from_file_location("b", os.path.join(HERE, "build_fit_gauge.py"))
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)
for l in b.build(doc, os.path.join(HERE, "stl")): print(l)
doc.save(); print("Rebuilt and re-exported STL + STEP.")
"""


def write_macro(folder):
    with open(os.path.join(folder, "Rebuild_FitGauge.FCMacro"), "w") as f:
        f.write(MACRO)


def build(doc=None, export_dir=None):
    doc = doc or App.ActiveDocument or App.newDocument("FitGauge")
    if doc.getObject("Body") is not None:  # placeholder from the project template
        doc.removeObject("Body")
    info, shapes = [], []
    for name, fn, col in PARTS:
        s = fn()
        shapes.append((name, s))
        o = doc.getObject(name) or doc.addObject("Part::Feature", name)
        o.Shape = s
        if App.GuiUp:
            o.ViewObject.ShapeColor = col
        b = s.BoundBox
        info.append("%-6s %.1f x %.1f x %.1f mm, %.2f cm3, valid %s" % (
            name, b.XLength, b.YLength, b.ZLength, s.Volume / 1000.0, s.isValid()))
    w, d, _ = layout()
    info.append("plate nominal %.2f x %.2f x %.2f mm (measure it: X/Y/Z scale check)" % (w, d, PLATE_T))
    info.append("round holes  dia: " + ", ".join("%s=%.2f" % (label(c), NOM + 2 * c) for c in CLEARANCES))
    info.append("square holes side: " + ", ".join("%s=%.2f" % (label(c), NOM + 2 * c) for c in CLEARANCES))
    info.append("inserts %s (blind %.1f), self-tap %s" % (", ".join("%.1f" % x for x in INSERT_DIAS), INSERT_DEPTH,
                                                         ", ".join("%.1f" % x for x in SCREW_DIAS)))
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
            s.exportStep(os.path.join(step_dir, "fit_gauge_" + fn + ".step"))
            MeshPart.meshFromShape(Shape=s, LinearDeflection=0.01, AngularDeflection=0.1).write(
                os.path.join(export_dir, "fit_gauge_" + fn + ".stl"))
        allm = None
        for name, s in shapes:  # one plate-ready file with all three parts
            m = MeshPart.meshFromShape(Shape=s, LinearDeflection=0.01, AngularDeflection=0.1)
            if allm is None:
                allm = m
            else:
                allm.addMesh(m)
        allm.write(os.path.join(export_dir, "fit_gauge_all.stl"))
    return info


if __name__ == "__main__":
    for l in build():
        print(l)
