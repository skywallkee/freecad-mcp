"""Strength (FEM) checks against hand calculations (headless, throwaway profile).
Run:  uv run --directory server python ../tests/strength_headless.py"""
import os
import subprocess
import sys
import tempfile
import time

os.environ["CLAUDELINK_TARGET"] = "headless"
from freecad_mcp.client import call, LinkError  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FC = r"C:\Program Files\FreeCAD 1.0\bin\freecadcmd.exe"
PROFILE = os.path.join(tempfile.mkdtemp(prefix="claudelink_st_"), "print_profile.json")
proc = subprocess.Popen([FC, os.path.join(HERE, "..", "addon", "ClaudeLink", "headless_serve.py")],
                        env=dict(os.environ, CLAUDELINK_PROFILE=PROFILE),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
BUILD = """
App.newDocument('ST'); App.ActiveDocument.UndoMode = 1
cl.show(Part.makeBox(100, 10, 10), 'Beam')
br = cl.union([Part.makeBox(40, 20, 4), Part.makeBox(4, 20, 40)])
cl.show(br, 'Bracket', placement_=cl.placement((0, 50, 0)))
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
        r = r.get("text", r) if isinstance(r, dict) else r
        miss = [e for e in expects if e not in str(r)] + ["NOT " + a for a in absent if a in str(r)]
        fails += bool(miss)
        print("=== %s %s%s\n%s\n" % ("FAIL" if miss else "PASS", title, (" missing %s" % miss) if miss else "", r))
        return str(r)

    step("build", "exec", ["+ Beam", "+ Bracket"], code=BUILD)
    # cantilever 100 x 10 x 10, 50 N at the tip: sigma = 30 MPa, deflection (PETG E 2100) = 9.52 mm
    r = step("cantilever beam vs hand calc", "strength", ["max von Mises 3", "max deflection 9."],
             object="Beam", fixed=["<X"], loads=[{"ref": ">X", "force": 50, "dir": "-z"}], mesh_size=3)
    # printed upright (up=+x), bending stress runs across the layers -> layer adhesion governs
    step("beam printed upright: layer adhesion governs", "strength", ["tension across layers", "(layer adhesion)"],
         object="Beam", fixed=["<X"], loads=[{"ref": ">X", "force": 50, "dir": "-z"}], up="+x", mesh_size=3)
    step("beam printed flat: compression/zero across layers is fine", "strength", ["(in-layer strength)"],
         object="Beam", fixed=["<X"], loads=[{"ref": ">X", "force": 50, "dir": "-z"}], up="+z", mesh_size=3)
    step("bracket: hot spot at the inside corner", "strength", ["hot spots", "verdict"],
         object="Bracket", fixed=["<Z"], loads=[{"ref": ">Z", "force": 100, "dir": "+x"}])
    step("bad ref is a clear error", "strength", ["matched no faces"], object="Beam", fixed=["%Cylinder"],
         loads=[{"ref": ">X", "force": 5}])
    step("doc unchanged", "tree", ["Beam", "Bracket"], absent=["Strength_"], depth=1)
    print("FAILS:", fails)
finally:
    proc.kill()
sys.exit(1 if fails else 0)
