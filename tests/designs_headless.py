"""Design helpers: mechanisms, lightweighting, standard parts, enclosures, mesh import, variants (headless).
Run:  uv run --directory server python ../tests/designs_headless.py"""
import os
import subprocess
import tempfile
import time

os.environ["CLAUDELINK_TARGET"] = "headless"
from freecad_mcp.client import call, LinkError  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FC = r"C:\Program Files\FreeCAD 1.0\bin\freecadcmd.exe"
TMP = tempfile.mkdtemp(prefix="claudelink_ds_")
PROFILE = os.path.join(TMP, "print_profile.json")
proc = subprocess.Popen([FC, os.path.join(HERE, "..", "addon", "ClaudeLink", "headless_serve.py")],
                        env=dict(os.environ, CLAUDELINK_PROFILE=PROFILE),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(100):
        try:
            call("status", timeout=5)
            break
        except LinkError:
            time.sleep(0.3)
    fails = 0

    def step(title, method, expects=(), absent=(), **p):
        global fails
        try:
            r = call(method, p, timeout=900)
        except Exception as e:  # noqa: BLE001
            r = "ERROR %s" % e
        r = r if isinstance(r, str) else str(r)
        bad = [e for e in expects if e not in r] + [a for a in absent if a in r]
        print(("ok   " if not bad else "FAIL ") + title + "\n     " + r.strip().replace("\n", "\n     ")[:1000])
        if bad:
            fails += 1
            print("     missing/unexpected:", bad)
        return r

    call("exec", {"code": "App.newDocument('DS'); App.ActiveDocument.UndoMode = 1"})
    # --- mechanisms
    step("gear pair meshes", "mechanism", ["ratio 2:1", "interference 0 mm3", "contact ratio"],
         kind="gear_pair", params={"module": 1.5, "teeth": 18, "teeth2": 36, "bore": 5})
    step("small gear undercut note", "mechanism", ["undercut"], kind="gear", params={"teeth": 12}, name="G12")
    step("print-in-place hinge clears", "mechanism", ["interference 0 mm3", "OK, >= loose fit"], kind="hinge",
         at=[0, 100, 0])
    step("living hinge strain", "mechanism", ["web strain", "will crack"], kind="living_hinge", at=[0, 150, 0])
    step("flexure stiffness", "mechanism", ["stiffness", "N/mm", "travel up to"], kind="flexure", at=[0, 200, 0])
    step("spring rate", "mechanism", ["rate ~", "N/mm"], kind="spring", at=[0, 250, 0])
    step("all mechanism solids valid", "exec", ["True"],
         code="all(o.Shape.isValid() and len(o.Shape.Solids) == 1 for o in App.ActiveDocument.Objects)")
    # --- lightweighting
    call("exec", {"code": "; ".join([
        "p = Part.makeBox(120, 60, 4, V(0, 400, 0)).cut(Part.makeCylinder(4, 4, V(20, 430, 0)))",
        "cl.show(p, 'Panel')", "cl.show(Part.makeBox(120, 60, 4, V(0, 500, 0)), 'Panel2')",
        "cl.show(Part.makeBox(80, 40, 2, V(0, 600, 0)), 'Lid')"])})
    step("hex pattern through a panel, away from the hole", "lighten", ["hex cut-outs", "-> Panel", "saved"],
         object="Panel", at="faces >Z", pattern="hex", cell=12)
    step("panel still one valid solid", "exec", ["valid, 1 solids"], code="cl.check(App.ActiveDocument.Panel)")
    step("vent slots in a lid", "lighten", ["slots cut-outs"], object="Lid", at="faces >Z", pattern="slots",
         cell=4, wall=2, slot_len=20)
    step("grid with strength before/after", "lighten", ["grid cut-outs", "strength: safety factor"],
         object="Panel2", at="faces >Z", pattern="grid", cell=15,
         check={"fixed": ["<X"], "loads": [{"ref": ">X", "force": 20, "dir": "-z"}]})
    # --- standard parts + enclosures
    step("bearing reference part", "stdpart", ["bearing 608 (d8 x D22 x 7)"], kind="bearing", size="608",
         at=[0, 700, 0])
    call("exec", {"code": "cl.show(Part.makeBox(40, 40, 10, V(100, 700, 0)), 'Block')"})
    step("bearing seat", "stdpart", ["bearing 608 seat d22.1", "through-relief"], kind="bearing", size="608",
         mode="seat", target="Block", at=[120, 720, 10], axis="-z")
    step("magnet pocket", "stdpart", ["magnet pocket d6.1"], kind="magnet", size="6x3", mode="seat", target="Block",
         at=[105, 705, 10], axis="-z")
    step("servo pocket", "stdpart", ["servo SG90 pocket"], kind="servo", size="sg90", mode="seat", target="Block",
         at=[120, 720, 0], axis="z")
    step("unknown bearing", "stdpart", ["unknown; known:"], kind="bearing", size="9999")
    step("Pi 4 enclosure, screw lid", "enclosure", ["7 port cut-outs", "4 standoffs", "board/base overlap 0 mm3",
                                                     "lid/base overlap 0 mm3", "M2.5"], board="rpi4", name="Pi")
    step("Uno enclosure, snap lid", "enclosure", ["snap hooks", "-> OK", "board/base overlap 0 mm3"],
         board="arduino-uno", lid="snap", name="Uno")
    step("custom box with vents", "enclosure", ["custom enclosure", "vent slots"], size=[60, 40, 20], vents=True,
         name="Box")
    step("enclosure parts valid", "exec", ["True"], code="all(App.ActiveDocument.getObject(n).Shape.isValid() and "
         "len(App.ActiveDocument.getObject(n).Shape.Solids) == 1 for n in ['Pi_base', 'Pi_lid', 'Uno_base', 'Uno_lid', "
         "'Box_base', 'Box_lid', 'Block'])")
    # --- mesh import / repair
    stl = os.path.join(TMP, "broken.stl").replace("\\", "/")
    stl_m = os.path.join(TMP, "metres.stl").replace("\\", "/")
    call("exec", {"code": chr(10).join([
        "import Mesh, MeshPart",
        "s = Part.makeBox(40, 30, 5).cut(Part.makeCylinder(3, 5, V(10, 15, 0)))",
        "s = s.fuse(Part.makeCylinder(4, 8, V(30, 15, 5)))",
        "m = MeshPart.meshFromShape(Shape=s, LinearDeflection=0.02, AngularDeflection=0.1)",
        "m.removeFacets([0, 1, 2])",
        "m.addFacet(V(100, 100, 100), V(101, 100, 100), V(100, 101, 100))",
        "m.write(%r)" % stl,
        "m2 = MeshPart.meshFromShape(Shape=Part.makeBox(0.04, 0.03, 0.005), LinearDeflection=0.0001)",
        "m2.write(%r)" % stl_m])})
    step("broken STL repaired and converted", "mesh_import",
         ["as loaded:", "open (holes in the surface)", "repaired: ", "clean, watertight", "solid broken: valid",
          "hole d6", "boss d8", "flat faces"], path=stl)
    step("solid usable for booleans", "exec", ["valid, 1 solids"],
         code="cl.check(App.ActiveDocument.broken.Shape.cut(Part.makeBox(5, 5, 50, V(-1, -1, -1))))")
    step("metre-scale file flagged", "mesh_import", ["probably in metres"], path=stl_m, solid=False, features=False)
    # --- size variants of a project
    pdir = os.path.join(TMP, "VarBox")
    step("scaffold a project", "project_new", ["VarBox"], name="VarBox", folder=pdir)
    step("S/M/L variants", "variants", ["3 variant(s)", "S WIDTH=30", "L WIDTH=60", "30x30x10", "60x30x10",
                                        "stl", "READY", "original values"],
         project=pdir, variants={"S": {"WIDTH": 30}, "M": {"WIDTH": 45}, "L": {"WIDTH": 60}})
    step("files per variant", "exec", ["[1, 1, 1]"], code="import os; [len(os.listdir(os.path.join(%r, 'stl', v))) for v in ('s', 'm', 'l')]" % pdir)
    step("unknown parameter refused", "variants", ["unknown parameter"], project=pdir, variants={"X": {"NOPE": 1}})
    print("FAILS: %d" % fails)
finally:
    proc.kill()
