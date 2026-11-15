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
    print("FAILS: %d" % fails)
finally:
    proc.kill()
