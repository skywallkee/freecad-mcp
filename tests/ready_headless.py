"""Print readiness check + automatic print fixes + auto checkpoints (headless, throwaway profile).
Run:  uv run --directory server python ../tests/ready_headless.py"""
import os
import subprocess
import tempfile
import time

os.environ["CLAUDELINK_TARGET"] = "headless"
from freecad_mcp.client import call, LinkError  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FC = r"C:\Program Files\FreeCAD 1.0\bin\freecadcmd.exe"
PROFILE = os.path.join(tempfile.mkdtemp(prefix="claudelink_rd_"), "print_profile.json")
proc = subprocess.Popen([FC, os.path.join(HERE, "..", "addon", "ClaudeLink", "headless_serve.py")],
                        env=dict(os.environ, CLAUDELINK_PROFILE=PROFILE),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
BUILD = "\n".join([
    "App.newDocument('RD'); App.ActiveDocument.UndoMode = 1",
    "b = Part.makeBox(40, 40, 20)",
    "b = b.makeFillet(4, [e for e in b.Edges if e.BoundBox.ZMax < 1e-6])",
    "cl.show(b, 'Rounded')",
    "from claudelink import fasteners as F",
    "p = Part.makeBox(40, 40, 10, V(100, 0, 0)).cut(F.place(F.screw_hole_cutter('M4', 10, 'socket'), (120, 20, 0), 'z'))",
    "p = p.cut(Part.makeCylinder(3, 40, V(100, 30, 5), V(1, 0, 0)))",
    "cl.show(p, 'Cbore')",
    "cl.show(Part.makeBox(200, 150, 3, V(0, 100, 0)), 'Sheet')",
    "cl.show(cl.cut(Part.makeBox(30, 30, 20, V(300, 0, 0)), Part.makeBox(29.7, 29.7, 20, V(300.15, 0.15, 2))), 'Thin')",
    "cl.show(Part.makeBox(10, 10, 10, V(400, 0, 0)), 'A1'); cl.show(Part.makeBox(10, 10, 10, V(405, 0, 0)), 'A2')",
])
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
            r = call(method, p, timeout=600)
        except Exception as e:  # noqa: BLE001
            r = "ERROR %s" % e
        r = r if isinstance(r, str) else str(r)
        bad = [e for e in expects if e not in r] + [a for a in absent if a in r]
        print(("ok   " if not bad else "FAIL ") + title + "\n     " + r.strip().replace("\n", "\n     ")[:1200])
        if bad:
            fails += 1
            print("     missing/unexpected:", bad)
        return r

    call("exec", {"code": BUILD})
    step("auto orientation flips the rounded box (fillets on top)", "ready", ["Rounded: READY", "up=-z"], objects=["Rounded"])
    step("rounded bottom edges flagged when printed upright", "ready", ["Rounded: FIX FIRST", "downward-facing fillet"],
         objects=["Rounded"], up="+z")
    step("counterbore + horizontal hole flagged", "ready", ["face-down counterbore", "horizontal hole"],
         objects=["Cbore"], up="+z")
    step("ABS sheet: high warping", "ready", ["warping risk high", "corner pads at 4"], objects=["Sheet"],
         material="ABS")
    step("PLA sheet: lower risk", "ready", [], ["warping risk high"], objects=["Sheet"], material="PLA")
    step("thin wall not printable", "ready", ["NOT PRINTABLE", "thinner than the nozzle"], objects=["Thin"], up="+z")
    step("overlapping assembly", "ready", ["A1 & A2 overlap 500"], objects=["A1", "A2"])
    step("fix fillets -> chamfers", "print_fix", ["downward fillet(s) -> 45-deg chamfers", "valid", "checkpoint"],
         object="Rounded", up="+z")
    step("rounded now ready upright", "ready", ["Rounded: READY"], ["downward-facing fillet"], objects=["Rounded"],
         up="+z")
    step("fix counterbore membrane + teardrop", "print_fix", ["1 one-layer membrane", "teardrops on 1"],
         object="Cbore", up="+z")
    step("cbore now ready", "ready", ["Cbore: READY", "membrane(s) over holes (intended"],
         ["face-down counterbore", "horizontal hole", "thinner than the nozzle"],
         objects=["Cbore"], up="+z")
    step("corner pads", "print_fix", ["4 corner pad(s)"], object="Sheet", up="+z", ears=True, fillets=False,
         name="Sheet_pads")
    step("checkpoints exist", "checkpoint", ["before_print_fix"], action="list")
    step("restore first checkpoint brings fillets back", "checkpoint", ["restored"], action="restore", name="001")
    step("fillets back after restore", "ready", ["downward-facing fillet"], objects=["Rounded"], up="+z")
    print("FAILS: %d" % fails)
finally:
    proc.kill()
