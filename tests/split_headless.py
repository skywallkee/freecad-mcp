"""Bed split / colour split / motion checks with known shapes (headless, throwaway profile).
Run:  uv run --directory server python ../tests/split_headless.py [only]"""
import os
import subprocess
import sys
import tempfile
import time

os.environ["CLAUDELINK_TARGET"] = "headless"
from freecad_mcp.client import call, LinkError  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FC = r"C:\Program Files\FreeCAD 1.0\bin\freecadcmd.exe"
PROFILE = os.path.join(tempfile.mkdtemp(prefix="claudelink_sp_"), "print_profile.json")
ONLY = sys.argv[1] if len(sys.argv) > 1 else None
proc = subprocess.Popen([FC, os.path.join(HERE, "..", "addon", "ClaudeLink", "headless_serve.py")],
                        env=dict(os.environ, CLAUDELINK_PROFILE=PROFILE),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
BUILD = """
App.newDocument('SP'); App.ActiveDocument.UndoMode = 1
cl.show(Part.makeBox(400, 30, 20), 'Bar')
outer = Part.makeBox(300, 300, 60); inner = Part.makeBox(290, 290, 60, V(5, 5, 5))
cl.show(cl.cut(outer, inner), 'Tray', placement_=cl.placement((0, 100, 0)))
# colour test: a plaque with a raised logo, an inlaid star and a two-tone body
plaque = Part.makeBox(60, 40, 6)
logo = Part.makeCylinder(8, 4, V(15, 20, 4))           # sinks 2 mm into the plaque, sticks out 2 mm
star = cl.extrude([(45 + 9 * math.cos(math.radians(a)) * (1 if i % 2 == 0 else 0.45),
                    20 + 9 * math.sin(math.radians(a)) * (1 if i % 2 == 0 else 0.45)) for i, a in
                   enumerate(range(90, 450, 36))], 3, 3)  # flush inlay, 3 mm deep
cl.show(plaque, 'Plaque', placement_=cl.placement((0, 500, 0)))
cl.show(logo, 'Logo', placement_=cl.placement((0, 500, 0)))
cl.show(star, 'Star', placement_=cl.placement((0, 500, 0)))
cl.show(Part.makeBox(60, 40, 10), 'Lower', placement_=cl.placement((100, 500, 0)))
cl.show(Part.makeBox(60, 40, 10), 'Upper', placement_=cl.placement((100, 500, 10)))
# hinge test: a base with a knuckle and a lid rotating about y
base = cl.union([Part.makeBox(40, 30, 4), Part.makeCylinder(3, 30, V(40, 0, 7), V(0, 1, 0))])
lid = Part.makeBox(40, 30, 3, V(0, 0, 11))
cl.show(base, 'HBase', placement_=cl.placement((0, 700, 0)))
cl.show(lid, 'HLid', placement_=cl.placement((0, 700, 0)))
# slider: a block in a channel with 0.3 mm side gaps and a stop at x=50
chan = cl.cut(Part.makeBox(80, 20.6, 10), Part.makeBox(80, 10.6, 10, V(0, 5, 3)))
chan = cl.union([chan, Part.makeBox(4, 20.6, 10, V(60, 0, 0))])
cl.show(chan, 'Channel', placement_=cl.placement((0, 800, 0)))
cl.show(Part.makeBox(10, 10, 6, V(5, 5.3, 3.3)), 'Slider', placement_=cl.placement((0, 800, 0)))
# colour edge cases: dome half sunk into a block (not a prism), ball fully enclosed (locked)
cl.show(Part.makeBox(30, 30, 10), 'DomeBase', placement_=cl.placement((200, 500, 0)))
cl.show(Part.makeSphere(6, V(15, 15, 10)), 'Dome', placement_=cl.placement((200, 500, 0)))
cl.show(Part.makeBox(30, 30, 30), 'Cage', placement_=cl.placement((300, 500, 0)))
cl.show(Part.makeSphere(5, V(15, 15, 15)), 'Ball', placement_=cl.placement((300, 500, 0)))
"""
try:
    for _ in range(100):
        try:
            call("status", timeout=5)
            break
        except LinkError:
            time.sleep(0.3)
    fails = 0

    def step(title, method, expects=(), absent=(), group=None, **p):
        global fails
        if ONLY and group and group != ONLY:
            return ""
        t = time.time()
        try:
            r = call(method, p, timeout=900)
        except LinkError as e:
            r = "LinkError: %s" % e
        r = r.get("text", r) if isinstance(r, dict) else r
        miss = [e for e in expects if e not in str(r)] + ["NOT " + a for a in absent if a in str(r)]
        fails += bool(miss)
        print("=== %s %s (%.1fs)%s\n%s\n" % ("FAIL" if miss else "PASS", title, time.time() - t,
                                           (" missing %s" % miss) if miss else "", r), flush=True)
        return str(r)

    step("build", "exec", ["+ Bar", "+ Tray", "+ Plaque", "+ HLid"], code=BUILD)
    # ---- bed split
    step("bar 400 long -> 2 pieces with pins", "split", ["2 piece(s)", "cut x =", "pin(s) d=", "press fit", "Bar_split_pins"],
         absent=["TOO BIG", "INVALID"], group="split", object="Bar")
    step("pieces + pins valid", "check_geometry", ["Bar_split1: ok", "Bar_split2: ok"], group="split",
         objects=["Bar_split1", "Bar_split2", "Bar_split_pins"])
    step("tray 300x300 -> split in x and y", "split", ["4 piece(s)", "cut x =", "cut y ="], absent=["TOO BIG", "INVALID"],
         group="split", object="Tray")
    step("small part: nothing to split", "split", ["nothing to split"], group="split", object="Plaque")
    step("pegs instead of pins", "split", ["peg"], absent=["_pins:"], group="split", object="Bar", joint="peg",
         name="BarPeg")
    step("tray pieces only touch (pins/bosses don't collide)", "fit", ["contact"], absent=["INTERFERENCE"], group="split",
         objects=["Tray_split1", "Tray_split2", "Tray_split3", "Tray_split4"])
    # ---- colour split
    step("plaque + raised logo + flush star", "color_split", ["3 single-colour pieces", "Logo into Plaque: inlay",
         "Star into Plaque: inlay", "push in along -z", "holds without glue", "exact (prism)"], absent=["INVALID", "LOCKED"],
         group="color", objects=["Plaque", "Logo", "Star"], colors=["white", "red", "yellow"])
    step("inlays have press clearance, no overlap", "fit", ["gap 0.05"], absent=["INTERFERENCE"], group="color",
         objects=["Plaque_color1_white", "Plaque_color2_red", "Plaque_color3_yellow"])
    step("two-tone stacked body -> pins", "color_split", ["flat joint", "pin(s) d=", "no glue needed"],
         absent=["GLUE", "INVALID"], group="color", objects=["Lower", "Upper"], colors=["blue", "orange"])
    step("stacked halves valid", "check_geometry", ["Lower_color1_blue: ok", "Lower_color2_orange: ok"], group="color",
         objects=["Lower_color1_blue", "Lower_color2_orange"])
    step("dome: not a prism -> 3D offset pocket", "color_split", ["Dome into DomeBase: inlay", "3D offset"],
         absent=["LOCKED", "INVALID"], group="color", objects=["DomeBase", "Dome"])
    step("enclosed ball is reported as locked", "color_split", ["LOCKED"], group="color", objects=["Cage", "Ball"])
    # ---- motion
    step("hinge opens 0..120 clear, 1 mm min at the knuckle", "motion", ["min clearance 1 mm", "print-in-place: OK"],
         absent=["COLLISION"], group="motion", moving="HLid", fixed=["HBase"],
         rotate={"axis_ref": "HBase:faces %Cylinder [0]", "from": 0, "to": 120}, steps=24)
    step("closing past flat hits the base (~-10 deg)", "motion", ["COLLISION from -", "HLid_collision"], absent=["print-in-place"], group="motion",
         moving="HLid", fixed=["HBase"], rotate={"axis_ref": "HBase:faces %Cylinder [0]", "from": 0, "to": -20}, steps=20)
    step("slider runs into the stop", "motion", ["COLLISION from 46", "Channel", "0:0.3"], group="motion",
         moving="Slider", fixed=["Channel"], translate={"dir": "x", "from": 0, "to": 50}, steps=50)
    print("FAILS:", fails)
finally:
    proc.kill()
sys.exit(1 if fails else 0)
