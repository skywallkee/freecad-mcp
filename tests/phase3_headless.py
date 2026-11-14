"""Phase 3 checks (projects, export, mesh compare, checkpoints) against a headless ClaudeLink.
Run:  uv run --directory server python ../tests/phase3_headless.py"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

os.environ["CLAUDELINK_TARGET"] = "headless"
from freecad_mcp.client import call, LinkError  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FC = r"C:\Program Files\FreeCAD 1.0\bin\freecadcmd.exe"
TMP = tempfile.mkdtemp(prefix="claudelink_p3_")
PROJ = os.path.join(TMP, "TestWidget")
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

    def step(title, method, expects=(), **p):
        global fails
        try:
            r = call(method, p, timeout=600)
        except LinkError as e:
            r = "LinkError: %s" % e
        miss = [e for e in expects if e not in str(r)]
        fails += bool(miss)
        print("=== %s %s%s\n%s\n" % ("FAIL" if miss else "PASS", title, (" missing %s" % miss) if miss else "", r))
        return str(r)

    step("project_new", "project_new", ["created", "+ Body", "40.0 x 30.0 x 10.0", "saved"], name="TestWidget", folder=PROJ,
         description="test part")
    step("project_info", "project_info", ["WIDTH = 40", "# overall width", "[body]", "build(doc, export_dir)"], project=PROJ)
    step("params override + rebuild", "project_params", ["params_override.json: WIDTH=50", "(+3000)", "bb 40x30x10->50x30x10"],
         project=PROJ, values={"WIDTH": 50}, rebuild=True)
    step("info shows override", "project_info", ["WIDTH = 50  [override; source 40]"], project=PROJ, filter="width")
    step("params source edit", "project_params", ["wrote 1 value(s) into build_test_widget.py", "removed from params_override.json: WIDTH"],
         project=PROJ, values={"WIDTH": 44.5}, persist="source")
    src = open(os.path.join(PROJ, "build_test_widget.py")).read()
    ok = "WIDTH   = 44.5   # overall width (X)" in src
    fails += not ok
    print("=== %s source literal rewritten with comment kept\n" % ("PASS" if ok else "FAIL"))
    step("rebuild temp override + export", "project_rebuild", ["bb 50x30x10->44.5x30x10", "exported to stl/ + step/"],
         project=PROJ, export=True, save=True)
    ok = os.path.exists(os.path.join(PROJ, "stl", "body.stl")) and os.path.exists(os.path.join(PROJ, "step", "body.step"))
    fails += not ok
    print("=== %s build() wrote stl/body.stl + step/body.step\n" % ("PASS" if ok else "FAIL"))
    step("export 3mf", "export", ["3MF", "watertight"], format="3mf")
    step("export step single", "export", ["STEP"], format="step", path=os.path.join(TMP, "all.step"))
    step("checkpoint save", "checkpoint", ["saved"], label="before change")
    step("rebuild with temp override", "project_rebuild", ["HEIGHT=20", "bb 44.5x30x10->44.5x30x20"], project=PROJ,
         overrides={"HEIGHT": 20})
    step("compare to old STL (height +10, so top face 'missing'/'extra')", "compare_mesh", ["reference -> CAD", "NO reference surface"],
         object="Body", reference=os.path.join(PROJ, "stl", "body.stl"), search=3.0)
    step("checkpoint list", "checkpoint", ["001_before_change"], action="list")
    step("checkpoint restore", "checkpoint", ["restored 001_before_change", "bb 44.5x30x20->44.5x30x10"], action="restore")
    step("width +0.2 vs STL", "project_rebuild", ["44.7x30x10"], project=PROJ, overrides={"WIDTH": 44.7})
    step("compare small deviation", "compare_mesh", ["+0.1", "within"], object="Body",
         reference=os.path.join(PROJ, "stl", "body.stl"), tolerance=0.05, search=1.0)
    step("undo restore-free", "undo", ["undid"])
    print("FAILS:", fails)
finally:
    proc.kill()
    time.sleep(0.5)
    shutil.rmtree(TMP, ignore_errors=True)
    import glob
    for d in glob.glob(os.path.join(os.environ["LOCALAPPDATA"], "cache", "FreeCAD", "Cache", "ClaudeLink", "checkpoints", "testwidget*")):
        shutil.rmtree(d, ignore_errors=True)
sys.exit(1 if fails else 0)
