"""Phase 2 checks against a headless ClaudeLink, with geometry whose answers are known.
Run:  uv run --directory server python ../tests/phase2_headless.py"""
import os
import subprocess
import sys
import time

os.environ["CLAUDELINK_TARGET"] = "headless"
from freecad_mcp.client import call, LinkError  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FC = r"C:\Program Files\FreeCAD 1.0\bin\freecadcmd.exe"
proc = subprocess.Popen([FC, os.path.join(HERE, "..", "addon", "ClaudeLink", "headless_serve.py")],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
BUILD = """
App.newDocument('P2'); App.ActiveDocument.UndoMode = 1
plate = cl.cut(Part.makeBox(40, 40, 10), Part.makeCylinder(5.1, 10, V(20, 20, 0)))
cl.show(plate, 'Plate')
cl.show(Part.makeCylinder(5.0, 30, V(20, 20, -5)), 'Pin')
cl.show(Part.makeBox(10, 10, 10, V(35, 0, 5)), 'Block')          # overlaps plate: 5x10x5 = 250
cup = cl.cut(Part.makeCylinder(20, 30, V(100, 0, 0)), Part.makeCylinder(19.4, 29, V(100, 0, 1)))  # 0.6 wall, 1.0 floor
cl.show(cup, 'Cup')
r = cl.revolve([(0, 0), (8, 0), (8, 2), (4, 6), (0, 6)])
cl.show(r, 'Rev', placement_=cl.placement((0, 80, 0)))
len(cl.sel(plate, 'faces %Cylinder')), len(cl.sel(plate, 'faces |Z')), round(cl.sel1(plate, 'faces >Z').CenterOfMass.z, 3)
"""
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

    step("build with cl", "exec", ["+ Plate", "+ Rev", "=> (1, 2, 10.0)"], code=BUILD)
    step("cl.help", "exec", ["cl.fillet", "Selectors"], code="print(cl.help())")
    step("fillet fallback", "exec", ["ok"], code="s, rep = cl.fillet(Part.makeBox(10,10,10), 'edges |Z', 1); rep")
    step("measure hole vs pin", "measure", ["radial clearance 0.1", "diametral 0.2", "axis offset 0"],
         a="Plate:faces %Cylinder", b="Pin:faces %Cylinder")
    step("measure top face", "measure", ["Plane", "area"], a="Plate:faces >Z")
    step("measure planes", "measure", ["parallel planes, separation 10"], a="Plate:faces >Z", b="Plate:faces <Z")
    step("check_geometry", "check_geometry", ["Plate: ok", "5 checked, 0 with problems"])
    step("fit", "fit", ["INTERFERENCE vol 250", "TIGHT gap 0.1"], min_clearance=0.2)
    step("section through pin", "section", ["Plate:", "Pin:", "gap 0.1"], objects=["Plate", "Pin"], plane="z", offset=5)
    step("wall thickness cup", "wall_thickness", ["below 0.8", "min 0.59"], object="Cup", min_wall=0.8)
    step("printability cup upright", "printability", ["fits", "walls: min 0.59", "bed contact area 125"], object="Cup")
    step("printability cup upside down", "printability", ["overhang area"], object="Cup", up="-z")
    step("selector error", "measure", ["matched nothing"], a="Plate:faces %Sphere")
    print("FAILS:", fails)
finally:
    proc.kill()
sys.exit(1 if fails else 0)
