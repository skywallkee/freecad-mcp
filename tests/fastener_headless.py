"""Threads and fasteners (headless, throwaway profile).
Run:  uv run --directory server python ../tests/fastener_headless.py"""
import os
import subprocess
import tempfile
import time

os.environ["CLAUDELINK_TARGET"] = "headless"
from freecad_mcp.client import call, LinkError  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FC = r"C:\Program Files\FreeCAD 1.0\bin\freecadcmd.exe"
PROFILE = os.path.join(tempfile.mkdtemp(prefix="claudelink_fa_"), "print_profile.json")
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

    def step(title, method, expects=(), absent=(), **p):
        global fails
        try:
            r = call(method, p, timeout=300)
        except Exception as e:  # noqa: BLE001
            r = "ERROR %s" % e
        r = r if isinstance(r, str) else str(r)
        bad = [e for e in expects if e not in r] + [a for a in absent if a in r]
        print(("ok   " if not bad else "FAIL ") + title + "\n     " + r.strip().replace("\n", "\n     ")[:600])
        if bad:
            fails += 1
            print("     missing/unexpected:", bad)

    call("exec", {"code": "App.newDocument('FA'); App.ActiveDocument.UndoMode = 1\n"
                          "cl.show(Part.makeBox(60, 40, 12), 'Plate')\n"
                          "cl.show(Part.makeBox(30, 30, 10), 'Moved', placement_=cl.placement((100, 0, 0)))"})
    step("through screw hole, counterbore, from the top face", "fastener", ["M3 clearance hole d3.4 + counterbore, through", "valid"], ["note:"], object="Plate", kind="screw_hole", at=[8, 8, 12], axis="-z")
    step("countersunk M4 via selector", "fastener", ["countersink", "axis (0, 0, -1)"], ["note:"],
         object="Plate", kind="screw_hole", at="faces >Z", size="M4", head="countersunk")
    step("tapped M8, 10 deep", "fastener", ["M8 internal thread 10 mm deep", "valid"], ["nothing was removed"],
         object="Plate", kind="tapped_hole", at=[45, 25, 12], axis="-z", size="M8", depth=10)
    step("tapped M3 warns", "fastener", ["below M5"], object="Plate", kind="tapped_hole", at=[52, 8, 12], axis="-z",
         size="M3", depth=6)
    step("nut trap with side slot from the bottom", "fastener", ["M3 nut trap 5.5 across flats", "side slot 10"],
         ["nothing was removed"], object="Plate", kind="nut_trap", at=[8, 8, 0], axis="z", slot=10, slot_dir="-x")
    step("buried nut", "fastener", ["buried 4"], object="Plate", kind="nut_trap", at=[20, 30, 12], axis="-z",
         buried=4)
    step("insert boss on the top", "fastener", ["M3 insert boss 6 mm tall", "valid"], ["does not touch"],
         object="Plate", kind="insert_boss", at=[30, 30, 12], axis="-z", height=6)
    step("external M10 thread out of the side face", "fastener", ["M10 external thread 15 mm long", "axis (1, 0, 0)",
         "valid"], ["does not touch"], object="Plate", kind="thread", at=[60, 20, 6], axis="x", size="M10", length=15)
    step("snap hook OK", "fastener", ["snap hook 14 long", "-> OK"], ["does not touch"],
         object="Plate", kind="snap", at=[30, 0, 6], axis="-y", hook="z", length=14, thickness=1.6, deflection=1)
    step("snap hook too stiff", "fastener", ["TOO STIFF"], object="Plate", kind="snap", at=[45, 0, 6], axis="-y",
         hook="z", length=5, thickness=2.5, deflection=1.2, name="PlateSnapTest")
    step("placed object keeps its placement", "exec", ["(100.0, 0.0, 0.0) BoundBox (100, 0, 0, 130, 30, 10) True"],
         code="from claudelink.fasteners import fastener\n"
              "print(fastener('Moved', 'selftap', [115, 15, 10], '-z'))\n"
              "o = App.ActiveDocument.Moved\nprint(tuple(o.Placement.Base), o.Shape.BoundBox, o.Shape.isValid())")
    step("plate valid, one solid", "exec", ["valid, 1 solids"], code="cl.check(App.ActiveDocument.Plate)")
    step("miss is reported", "fastener", ["nothing was removed"], object="Plate", kind="selftap", at=[200, 200, 12],
         axis="-z")
    step("bad selector", "fastener", ["matched nothing"], object="Plate", kind="insert", at="faces %Sphere")
    step("cl helpers listed", "exec", ["True"], code="h = cl.help(); 'cl.thread(' in h and 'cl.snap_hook(' in h and 'cl.nut_trap(' in h")
    # multi-colour 3MF
    out3 = os.path.join(os.path.dirname(PROFILE), "plaque_color.3mf").replace("\\", "/")
    call("exec", {"code": "\n".join([
        "cl.show(Part.makeBox(40, 20, 3, V(0, 100, 0)), 'Tag_white')",
        "cl.show(Part.makeCylinder(5, 2, V(10, 110, 3)), 'Dot_red')",
        "cl.show(Part.makeCylinder(5, 2, V(30, 110, 3)), 'Dot2_red')"])})
    step("colour 3MF", "export", ["3 parts", "extruder 1 = white", "extruder 2 = red", "Dot_red, Dot2_red"],
         ["NOT watertight"], objects=["Tag_white", "Dot_red", "Dot2_red"], format="3mf", path=out3, colors=True)
    check_code = "\n".join([
        "import zipfile, re, Mesh",
        "z = zipfile.ZipFile(%r)" % out3,
        "m = z.read('3D/3dmodel.model').decode()",
        "c = z.read('Metadata/Slic3r_PE_model.config').decode()",
        "print('mats', m.count('<base '), 'vols', c.count('<volume'), 'ext', re.findall('extruder\" value=\"([0-9])', c))",
        "mm = Mesh.Mesh(%r); print('reread', mm.CountFacets > 0)" % out3])
    step("colour 3MF content", "exec", ["mats 2", "vols 3", "ext ['1', '2', '2']", "reread True"], code=check_code)
    step("colors needs 3mf", "export", ["needs format 3mf"], objects=["Tag_white"], format="stl", path=out3,
         colors=True)
    print("FAILS: %d" % fails)
finally:
    proc.kill()
