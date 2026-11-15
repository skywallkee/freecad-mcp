"""Smoke test against a headless ClaudeLink (freecadcmd, no window).
Run:  uv run --directory server python ../tests/smoke_headless.py"""
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
try:
    for _ in range(100):
        try:
            call("status", timeout=5)
            break
        except LinkError:
            time.sleep(0.3)
    fails = 0

    def step(title, method, expect=None, **p):
        global fails
        try:
            r = call(method, p, timeout=60)
        except LinkError as e:
            r = "LinkError: %s" % e
        ok = expect is None or expect in str(r)
        fails += not ok
        print("=== %s %s\n%s\n" % ("PASS" if ok else "FAIL", title, r))

    step("status", "status", "headless")
    step("new doc", "doc", "created", action="new", name="Smoke")
    step("exec box", "exec", "+ Box", code="b = App.ActiveDocument.addObject('Part::Box','Box')\nb.Length=10\nApp.ActiveDocument.recompute()\nb.Shape.Volume")
    step("exec change", "exec", "vol 1000->500", code="b.Length=5; App.ActiveDocument.recompute()")
    step("exec error rolls back", "exec", "ZeroDivisionError", code="c = App.ActiveDocument.addObject('Part::Cylinder','Cyl')\n1/0")
    step("syntax error", "exec", "SYNTAX ERROR", code="def (:")
    step("tree", "tree", "Box [Part::Box]")
    step("inspect", "inspect", "solids 1", name="Box", detail="props")
    tmp = os.path.join(os.environ["TEMP"], "claudelink_rf.py")
    with open(tmp, "w") as f:
        f.write("W = 1.0\nA, B = 2, 3\nimport FreeCAD as App, Part\n"
                "def build(doc):\n    o = doc.addObject('Part::Feature','RF'); o.Shape = Part.makeBox(W, A, B)\n")
    step("run_file overrides", "run_file", "=> 24", path=tmp, overrides={"W": 4, "B": 3, "ZZ": 1},
         then="build(App.ActiveDocument)\nW*A*B")
    step("undo", "undo", "undid 1", steps=1)
    step("view renders without a window", "view", "windowless render", size=160, views=["iso"])
    step("close unsaved", "doc", "closed", action="close", force=True)
    print("FAILS:", fails)
finally:
    proc.kill()
sys.exit(1 if fails else 0)
