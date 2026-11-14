"""Print-profile checks against a headless ClaudeLink using a throwaway profile file.
Run:  uv run --directory server python ../tests/profile_headless.py"""
import os
import subprocess
import sys
import tempfile
import time

os.environ["CLAUDELINK_TARGET"] = "headless"
from freecad_mcp.client import call, LinkError  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FC = r"C:\Program Files\FreeCAD 1.0\bin\freecadcmd.exe"
PROFILE = os.path.join(tempfile.mkdtemp(prefix="claudelink_prof_"), "print_profile.json")
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

    def step(title, method, expects=(), **p):
        global fails
        try:
            r = call(method, p, timeout=300)
        except LinkError as e:
            r = "LinkError: %s" % e
        miss = [e for e in expects if e not in str(r)]
        fails += bool(miss)
        print("=== %s %s%s\n%s\n" % ("FAIL" if miss else "PASS", title, (" missing %s" % miss) if miss else "", r))

    step("defaults are guesses", "print_profile", ["GUESS", "slide 0.15", "bed 256x256x256"])
    step("set printer", "print_profile", ["printer: Test MK1", "bed 220x220x250", "nozzle 0.6"], action="set",
         values={"printer": "Test MK1", "bed": [220, 220, 250], "nozzle": 0.6})
    step("record gauge", "print_profile", ["MEASURED", "press 0.1", "slide 0.2", "loose 0.3", "insert hole 4.2",
                                            "xy scale 0.998", "pin error -0.08"],
         action="record_gauge", material_name="PETG", round={"press": 10, "snug": 15, "slide": 20, "loose": 30},
         pin_dia=7.92, plate=[90.6, 55.1, 6.02], insert_m3=4.2)
    step("bad order warns", "print_profile", ["warning: square_fits not increasing"], action="record_gauge",
         material_name="TPU", square={"press": 30, "snug": 10})
    step("cl.fit uses profile", "exec", ["=> (0.2, 4.2"], code="cl.fit('slide'), cl.hole('insert_m3'), cl.fit('slide', 'TPU')")
    step("printability uses profile nozzle/bed", "exec", ["+ Thin"],
         code="App.newDocument('PP'); cl.show(cl.cut(Part.makeCylinder(10, 10), Part.makeCylinder(9.5, 10)), 'Thin')")
    step("printability", "printability", ["nozzle 0.6", "bed 220x220x250", "< nozzle 0.6"], object="Thin")
    step("measure classifies", "exec", ["+ Pin", "+ Hole"],
         code="cl.show(Part.makeCylinder(4, 10, V(50, 0, 0)), 'Pin')\n"
              "cl.show(cl.cut(Part.makeBox(20, 20, 10, V(40, -10, 0)), Part.makeCylinder(4.15, 10, V(50, 0, 0))), 'Hole')")
    step("measure fit class", "measure", ["radial clearance 0.15", "snug fit (PETG, measured)"],
         a="Pin:faces %Cylinder", b="Hole:faces %Cylinder")
    step("fit fit class", "fit", ["gap 0.15", "snug fit (PETG, measured)"], objects=["Pin", "Hole"])
    print("FAILS:", fails)
finally:
    proc.kill()
sys.exit(1 if fails else 0)
