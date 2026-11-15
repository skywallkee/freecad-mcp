"""Assembly-order planner checks for freecad_illustrate (headless; rendering itself needs the GUI).
Run:  uv run --directory server python ../tests/illustrate_headless.py"""
import os
import subprocess
import sys
import tempfile
import time

os.environ["CLAUDELINK_TARGET"] = "headless"
from freecad_mcp.client import call, LinkError  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FC = r"C:\Program Files\FreeCAD 1.0\bin\freecadcmd.exe"
PROFILE = os.path.join(tempfile.mkdtemp(prefix="claudelink_il_"), "print_profile.json")
proc = subprocess.Popen([FC, os.path.join(HERE, "..", "addon", "ClaudeLink", "headless_serve.py")],
                        env=dict(os.environ, CLAUDELINK_PROFILE=PROFILE),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

BUILD = """
App.newDocument('IL'); App.ActiveDocument.UndoMode = 1
corners = [(6, 6), (54, 6), (6, 34), (54, 34)]
base = cl.cut(Part.makeBox(60, 40, 20), [Part.makeCylinder(1.6, 15, V(x, y, 5)) for x, y in corners])
lid = cl.cut(Part.makeBox(60, 40, 4, V(0, 0, 20)), [Part.makeCylinder(1.7, 4, V(x, y, 20)) for x, y in corners])
cl.show(base, 'Base'); cl.show(lid, 'Lid')
for i, (x, y) in enumerate(corners):
    cl.show(cl.union([Part.makeCylinder(1.5, 16, V(x, y, 8)), Part.makeCylinder(2.8, 2.5, V(x, y, 24))]), 'Screw%d' % i)
cl.show(Part.makeCylinder(6, 8, V(30, 20, 24)), 'Knob')
"""

PLAN = """
from claudelink import illustrate as IL
from claudelink.analysis import global_shape
def run(names, base=None):
    d = App.ActiveDocument
    insts = []
    for n in names:
        o = d.getObject(n); s = IL._baked(global_shape(o))
        for so in s.Solids:
            insts.append({"obj": n, "label": n, "shape": so, "rgb": None, "key": (IL._key(so), None)})
    steps, warn = IL._Planner(insts).plan(base=base)
    out = []
    for st in steps:
        out.append(" + ".join(sorted("%s%s" % (insts[i]["obj"], "" if st["dirs"][i] is None else "_" + IL._from_words(st["dirs"][i]).replace(" ", "_")) for i in st["add"])))
    return " | ".join(out) + ("  WARN " + "; ".join(warn) if warn else "")
"""

try:
    for _ in range(100):
        try:
            call("status", timeout=5)
            break
        except LinkError:
            time.sleep(0.3)
    fails = 0

    def check(title, code, expects=(), absent=()):
        global fails
        try:
            r = call("exec", {"code": code}, timeout=600)
        except Exception as e:  # noqa: BLE001
            r = "ERROR %s" % e
        r = str(r)
        bad = [e for e in expects if e not in r] + [a for a in absent if a in r]
        print(("ok   " if not bad else "FAIL ") + title)
        if bad:
            fails += 1
            print("     missing/unexpected:", bad)
            print("     " + r[-1500:].replace("\n", "\n     "))
        return r

    call("exec", {"code": BUILD}, timeout=120)
    call("exec", {"code": PLAN}, timeout=60)
    names = "['Base', 'Lid', 'Screw0', 'Screw1', 'Screw2', 'Screw3', 'Knob']"
    r = check("box: base, then lid before screws, screws together from above",
              "run(%s)" % names,
              ["Base | Lid_from_above |", "Screw0_from_above + Screw1_from_above + Screw2_from_above + Screw3_from_above"],
              ["WARN"])
    print("     " + r.strip().splitlines()[-1][:300])
    # lid given as base: base then comes from below
    r = check("box with Lid as base", "run(%s, base=['Lid'])" % names, ["Lid | Base_from_below"])
    print("     " + r.strip().splitlines()[-1][:300])
    # a bar split with pins: pins must go in before the second piece
    check("split bar", "cl.show(Part.makeBox(400, 30, 20, V(0, 100, 0)), 'Bar'); "
          "from claudelink.splitting import split; split('Bar')", ["Bar_split_pins"])
    r = check("split bar order: piece, pins, piece",
              "run(['Bar_split1', 'Bar_split2', 'Bar_split_pins_placed'])",
              ["| Bar_split_pins_placed_from_the_", "_from_the_", "Bar_split1", "Bar_split2"], ["WARN", "from_above", "from_below"])
    print("     " + r.strip().splitlines()[-1][:300])
    check("headless refuses to render", "from claudelink.illustrate import illustrate\n"
          "try:\n    illustrate()\nexcept Exception as e:\n    print(e)", ["needs the FreeCAD GUI"])
    print("FAILS: %d" % fails)
finally:
    proc.kill()
