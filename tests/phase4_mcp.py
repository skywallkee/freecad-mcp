"""Phase 4 over the real MCP protocol: headless worker targeting, sketch diagnostics (drawings through the window: windowless_mcp.py).
Run:  uv run --directory server python ../tests/phase4_mcp.py"""
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
SKETCH = """
import Sketcher
App.newDocument('P4'); App.ActiveDocument.UndoMode = 1
sk = App.ActiveDocument.addObject('Sketcher::SketchObject', 'Sk')
pts = [V(0,0,0), V(20,0,0), V(20,10,0), V(0,10,0)]
for i in range(4):
    sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
for i in range(4):
    sk.addConstraint(Sketcher.Constraint('Coincident', i, 2, (i + 1) % 4, 1))
sk.addConstraint(Sketcher.Constraint('Horizontal', 0)); sk.addConstraint(Sketcher.Constraint('Horizontal', 2))
sk.addConstraint(Sketcher.Constraint('Vertical', 1)); sk.addConstraint(Sketcher.Constraint('Vertical', 3))
App.ActiveDocument.recompute()
"""


async def main():
    env = dict(os.environ)
    env.pop("CLAUDELINK_TARGET", None)
    params = StdioServerParameters(command="uv", args=["run", "--directory", os.path.join(HERE, "..", "server"),
                                                       "freecad-mcp"], env=env)
    fails = 0
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = [t.name for t in (await s.list_tools()).tools]

            async def step(title, tool, args, expects):
                nonlocal fails
                res = await s.call_tool(tool, args)
                text = "\n".join(c.text for c in res.content if c.type == "text")
                miss = [e for e in expects if e not in text]
                fails += bool(miss)
                print("=== %s %s%s\n%s\n" % ("FAIL" if miss else "PASS", title, (" missing %s" % miss) if miss else "", text))

            ok = all(n in names for n in ("freecad_sketch", "freecad_drawing", "freecad_target"))
            fails += not ok
            print("=== %s %d tools incl. sketch/drawing/target\n" % ("PASS" if ok else "FAIL", len(names)))
            await step("start headless worker", "freecad_target", {"target": "headless"}, ["target: headless", "headless worker up"])
            await step("status is tagged", "freecad_status", {}, ["[windowless]", "(headless)"])
            await step("build sketch", "freecad_exec", {"code": SKETCH}, ["+ Sk"])
            await step("sketch status", "freecad_sketch", {"name": "Sk", "detail": "constraints"},
                       ["degrees of freedom: 4", "1 closed, 0 open", "c5 Horizontal g0"])
            await step("back to gui + stop worker", "freecad_target", {"target": "gui", "stop_worker": True},
                       ["headless worker stopped", "target: gui", "headless worker not running"])
    print("FAILS:", fails)
    return fails


sys.exit(1 if asyncio.run(main()) else 0)
