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
    step("restore the checkpoint taken before the cbore fix", "checkpoint", ["restored"], action="restore", name="before_print_fix_cbore")
    step("counterbore back after restore", "ready", ["face-down counterbore"], objects=["Cbore"], up="+z")
    # labels
    call("exec", {"code": "cl.show(Part.makeBox(80, 40, 6, V(0, 300, 0)), 'Plate')"})
    step("embossed text on top", "label", ["emboss text 'Hello'", "valid"], ["strokes under"],
         object="Plate", text="Hello", at="faces >Z", size=8, offset=[15, 0])
    step("engraved text on the front", "label", ["engrave text", "normal (0, -1, 0)"], object="Plate",
         text="FRONT", at="faces <Y", mode="engrave", size=4)
    step("tiny embossed text warns", "label", ["strokes under 0.8", "cap height"], object="Plate", text="tiny",
         at="faces z=6", size=2, offset=[25, 12])
    step("date stamp under the part", "label", ["engrave text '20", "normal (0, 0, -1)"], object="Plate",
         kind="stamp", up="+z")
    step("stamp default size is small", "label", ["4 mm cap height"], object="Plate", kind="stamp", up="+z",
         text="v2", offset=[25, 0])
    step("QR code", "label", ["QR code (25x25 modules", "valid"], object="Plate", kind="qr",
         text="Hello from FreeCAD", at="faces z=6", module=1.0, offset=[-22, 0])
    step("label past the edge is refused", "label", ["loose piece"], object="Plate", text="WAY TOO LONG TEXT",
         at="faces z=6", size=12)
    step("plate still one valid solid", "exec", ["valid, 1 solids"], code="cl.check(App.ActiveDocument.Plate)")
    print("FAILS: %d" % fails)
finally:
    proc.kill()
