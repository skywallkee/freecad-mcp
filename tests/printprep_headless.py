"""Orientation / print prep / engineered support checks with known shapes (headless, throwaway profile).
Run:  uv run --directory server python ../tests/printprep_headless.py"""
import os
import subprocess
import sys
import tempfile
import time

os.environ["CLAUDELINK_TARGET"] = "headless"
from freecad_mcp.client import call, LinkError  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FC = r"C:\Program Files\FreeCAD 1.0\bin\freecadcmd.exe"
PROFILE = os.path.join(tempfile.mkdtemp(prefix="claudelink_pp_"), "print_profile.json")
proc = subprocess.Popen([FC, os.path.join(HERE, "..", "addon", "ClaudeLink", "headless_serve.py")],
                        env=dict(os.environ, CLAUDELINK_PROFILE=PROFILE),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
BUILD = """
App.newDocument('PP'); App.ActiveDocument.UndoMode = 1
tee = cl.union([Part.makeBox(10, 10, 20, V(15, 0, 0)), Part.makeBox(40, 10, 5, V(0, 0, 20))])
cl.show(tee, 'Tee')
tunnel = cl.cut(Part.makeBox(30, 20, 20), Part.makeBox(30, 8, 6, V(0, 6, 5)))
cl.show(tunnel, 'Tunnel', placement_=cl.placement((0, 60, 0)))
holed = cl.cut(Part.makeBox(30, 20, 20), Part.makeCylinder(4, 30, V(0, 10, 10), V(1, 0, 0)))
cl.show(holed, 'Holed', placement_=cl.placement((0, 120, 0)))
"""
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
        except LinkError as e:
            r = "LinkError: %s" % e
        miss = [e for e in expects if e not in str(r)] + ["NOT " + a for a in absent if a in str(r)]
        fails += bool(miss)
        print("=== %s %s%s\n%s\n" % ("FAIL" if miss else "PASS", title, (" missing %s" % miss) if miss else "", r))

    step("build", "exec", ["+ Tee", "+ Tunnel", "+ Holed"], code=BUILD)
    step("orient tee: lying on its side wins, upright needs support", "orient",
         ["best: up='+y'", "no supports"], object="Tee")
    step("orient with load along x prefers keeping x in-plane", "orient", ["load"], object="Tee", load="+x")
    step("prep tee upright -> support walls with tabs, 1-layer gap", "print_prep",
         ["wall piece(s)", "grip tab(s)", "0.2 mm vertical", "-> OK", "Tee_print", "Tee_print_supports"],
         object="Tee", up="+z")
    step("fit confirms gap", "fit", ["Tee_print - Tee_print_supports: gap 0.2"], objects=["Tee_print", "Tee_print_supports"])
    step("prep tee auto -> no supports", "print_prep", ["supports: none needed", "up=+y"], object="Tee")
    step("tunnel 8 mm bridges", "print_prep", ["supports: none needed", "will bridge"], object="Tunnel", up="+z")
    step("tunnel with max_bridge 5 gets supports", "print_prep", ["wall piece(s)", "-> OK"], object="Tunnel", up="+z",
         max_bridge=5)
    step("holed: teardrop", "print_prep", ["teardrops on 1 horizontal hole", "supports: none needed"], object="Holed",
         up="+z")
    step("holed print copy valid", "check_geometry", ["Holed_print: ok"], objects=["Holed_print"])
    step("original untouched", "inspect", ["bb", "solids 1"], name="Tee")
    print("FAILS:", fails)
finally:
    proc.kill()
sys.exit(1 if fails else 0)
