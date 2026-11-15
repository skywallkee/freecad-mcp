"""freecad_cancel interrupts a running job from outside (headless).
Run:  uv run --directory server python ../tests/cancel_headless.py"""
import os
import subprocess
import sys
import threading
import time

os.environ["CLAUDELINK_TARGET"] = "headless"
from freecad_mcp.client import call, LinkError  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FC = r"C:\Program Files\FreeCAD 1.0\bin\freecadcmd.exe"
proc = subprocess.Popen([FC, os.path.join(HERE, "..", "addon", "ClaudeLink", "headless_serve.py")],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
fails = 0


def check(name, ok, detail=""):
    global fails
    print(("ok   " if ok else "FAIL ") + name + (" - " + str(detail)[:200] if detail and not ok else ""))
    fails += 0 if ok else 1


SLOW = """
import time
t = time.time()
n = 0
while time.time() - t < 60:      # a long pure-Python job
    n += 1
'finished'
"""
try:
    for _ in range(100):
        try:
            call("status", timeout=5)
            break
        except LinkError:
            time.sleep(0.3)
    r = call("cancel", {}, timeout=10)
    check("cancel with nothing running is harmless", "nothing" in str(r), r)
    box = {}

    def slow():
        try:
            box["r"] = call("exec", {"code": SLOW}, timeout=120)
        except Exception as e:
            box["r"] = "EXC " + str(e)
    th = threading.Thread(target=slow)
    t0 = time.time()
    th.start()
    time.sleep(2.0)
    r = call("cancel", {}, timeout=10)
    check("cancel reports the running job", "interrupt sent to exec" in str(r), r)
    th.join(30)
    took = time.time() - t0
    check("the job stopped early (%.1fs)" % took, took < 15 and ("cancel" in str(box.get("r", "")).lower() or "KeyboardInterrupt" in str(box.get("r", ""))), box.get("r"))
    r = call("exec", {"code": "1 + 1"}, timeout=20)
    check("FreeCAD answers normally afterwards", "2" in str(r), r)
finally:
    proc.kill()
print("FAILS: %d" % fails)
sys.exit(1 if fails else 0)
