"""Windowless-by-default flow over the real MCP protocol: the worker starts on demand, views render without a
window, colours survive, freecad_show / drawings go through the FreeCAD window when it is open, and a call that
overruns its timeout is interrupted.
Run:  uv run --directory server python ../tests/windowless_mcp.py"""
import asyncio
import os
import shutil
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="claudelink_wl_")
fails = 0


def check(name, ok, detail=""):
    global fails
    print(("ok   " if ok else "FAIL ") + name + ((" - " + str(detail)[:300]) if detail and not ok else ""))
    fails += 0 if ok else 1


def text(res):
    return "\n".join(c.text for c in res.content if c.type == "text")


async def main():
    env = dict(os.environ)
    env.pop("CLAUDELINK_TARGET", None)          # the default must be windowless
    params = StdioServerParameters(command="uv", args=["run", "--directory", os.path.join(HERE, "..", "server"),
                                                       "freecad-mcp"], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            t = text(await s.call_tool("freecad_status", {}))
            check("worker starts on demand, status is windowless", "[windowless]" in t and "FreeCAD" in t, t)
            t = text(await s.call_tool("freecad_doc", {"action": "new", "name": "WLTest"}))
            check("new document", "created" in t, t)
            code = ("cl.show(Part.makeBox(40, 30, 10), 'Plate', color=(0.9, 0.1, 0.1))\n"
                    "cl.show(Part.makeCylinder(6, 25, V(20, 15, 10)), 'Post', color=(0.1, 0.3, 0.9))\n"
                    "App.ActiveDocument.recompute()\ntuple(round(c, 2) for c in cl.color_of(App.ActiveDocument.Plate))")
            t = text(await s.call_tool("freecad_exec", {"code": code}))
            check("colours are remembered without a window", "0.9" in t and "Plate" in t, t)
            res = await s.call_tool("freecad_view", {"views": ["iso", "top"], "size": 200})
            check("freecad_view renders without a window", any(c.type == "image" for c in res.content)
                  and "windowless render" in text(res), text(res))
            path = os.path.join(TMP, "WLTest.FCStd").replace("\\", "/")
            t = text(await s.call_tool("freecad_doc", {"action": "saveas", "path": path}))
            check("saved", "saved" in t, t)
            t = text(await s.call_tool("freecad_exec", {"code": "import time\nt = time.time()\nwhile time.time() - t < 60:\n    pass\n",
                                                        "timeout": 3}))
            check("a call past its timeout is interrupted", "interrupted" in t or "KeyboardInterrupt" in t, t)
            t = text(await s.call_tool("freecad_exec", {"code": "2 + 3"}))
            check("the worker answers again afterwards", "5" in t, t)
            tg = text(await s.call_tool("freecad_target", {}))
            window = "GUI up" in tg
            t = text(await s.call_tool("freecad_show", {}))
            if window:
                check("freecad_show opens it in the window", "open in the FreeCAD window" in t and "view restored" in t, t)
                res = await s.call_tool("freecad_drawing", {"objects": ["Plate"], "preview": False})
                t = text(res)
                pdf = os.path.join(TMP, "images", "plate_drawing.pdf")
                check("drawing goes through the window, file lands next to the document",
                      os.path.isfile(pdf) and "scale" in t, t)
                check("drawing dimensions are right", "width 40" in t and "height 10" in t, t)
                await s.call_tool("freecad_target", {"target": "gui"})
                t = text(await s.call_tool("freecad_exec", {"code": "d = App.getDocument('WLTest')\n"
                                                            "c = tuple(round(x, 2) for x in d.Plate.ViewObject.ShapeColor[:3]) + (d.Plate.ViewObject.Visibility,)\n"
                                                            "App.closeDocument('WLTest')\nc"}))
                check("the window shows the part, in its remembered colour", "0.9" in t and "True" in t, t)
                await s.call_tool("freecad_target", {"target": "headless"})
            else:
                check("freecad_show without a window says where the file is", "not running" in t and "saved at" in t, t)
                print("     (FreeCAD window not open: window checks skipped)")
            await s.call_tool("freecad_doc", {"action": "close", "name": "WLTest", "force": True})
            await s.call_tool("freecad_target", {"stop_worker": True})


try:
    asyncio.run(main())
finally:
    shutil.rmtree(TMP, ignore_errors=True)
print("FAILS: %d" % fails)
sys.exit(1 if fails else 0)
