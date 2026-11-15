"""Every printing/docs tool over the real MCP stdio protocol, against the RUNNING FreeCAD GUI.
Works in a throwaway document (MCPLive) that it closes at the end; other open documents are not touched.
Run (FreeCAD must be open):  uv run --directory server python ../tests/gui_live_mcp.py"""
import asyncio
import os
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = tempfile.mkdtemp(prefix="claudelink_live_")
DOC = "MCPLive"

BUILD = """
App.newDocument('MCPLive')
cl.show(cl.cut(Part.makeBox(60, 40, 20), Part.makeBox(52, 32, 17, V(4, 4, 3))), 'Base')
cl.show(Part.makeBox(60, 40, 4, V(0, 0, 20)), 'Lid')
cl.show(Part.makeCylinder(6, 8, V(30, 20, 24)), 'Knob')
cl.show(Part.makeBox(300, 30, 20, V(0, 100, 0)), 'Beam')
App.ActiveDocument.Beam.ViewObject.Visibility = False
cl.show(cl.union([Part.makeBox(40, 30, 4, V(0, 200, 0)), Part.makeCylinder(3, 30, V(40, 200, 7), V(0, 1, 0))]), 'HBase')
cl.show(Part.makeBox(40, 30, 3, V(0, 200, 11)), 'HLid')
for n in ('HBase', 'HLid'):
    App.ActiveDocument.getObject(n).ViewObject.Visibility = False
"""

CASES = [
    # (tool, args, expected substrings, wants_image)
    ("freecad_print_profile", {}, ["nozzle"], False),
    ("freecad_orient", {"object": "Lid", "doc": DOC}, ["best: up="], False),
    ("freecad_print_prep", {"object": "Knob", "doc": DOC}, ["Knob_print"], False),
    ("freecad_strength", {"object": "Lid", "fixed": ["<X"], "loads": [{"ref": ">X", "force": 20, "dir": "-z"}],
                          "doc": DOC}, ["safety factor", "von Mises"], True),
    ("freecad_split", {"object": "Beam", "axis": "x", "positions": [150], "doc": DOC}, ["2 piece(s)", "pin"], False),
    ("freecad_fastener", {"object": "Lid", "kind": "screw_hole", "at": [6, 6, 24], "axis": "-z", "doc": DOC},
     ["M3 clearance hole", "valid"], False),
    ("freecad_fastener", {"object": "Base", "kind": "insert_boss", "at": [6, 6, 3], "axis": "-z", "height": 17,
                          "doc": DOC}, ["insert boss 17 mm tall", "valid"], False),
    ("freecad_motion", {"moving": "HLid", "fixed": ["HBase"],
                        "rotate": {"axis": "y", "point": [40, 0, 7], "from": 0, "to": 90}, "doc": DOC},
     ["gap along the motion"], False),
    ("freecad_illustrate", {"kind": "manual", "objects": ["Base", "Lid", "Knob"], "title": "Live test",
                            "tools": ["soldering iron"], "hardware": ["1x M3 heat-set insert"], "out_dir": OUT,
                            "doc": DOC, "preview_width": 600},
     ["steps: 2", "manual: live_test_manual.pdf"], True),
    ("freecad_export", {"objects": ["Base", "Lid", "Knob"], "format": "3mf", "colors": True,
                        "path": os.path.join(OUT, "live.3mf"), "doc": DOC}, ["3 parts", "extruder 1"], False),
]


async def main():
    params = StdioServerParameters(command="uv", args=["run", "--directory", os.path.join(HERE, "..", "server"),
                                                       "freecad-mcp"], env=dict(os.environ, CLAUDELINK_TARGET="gui"))
    fails = 0
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            print("tools: %d" % len(names))
            await s.call_tool("freecad_exec", {"code": BUILD})
            try:
                for tool, args, expects, img in CASES:
                    if tool not in names:
                        print("FAIL %s: not registered" % tool)
                        fails += 1
                        continue
                    res = await s.call_tool(tool, args)
                    text = "\n".join(c.text for c in res.content if c.type == "text")
                    has_img = any(c.type == "image" for c in res.content)
                    bad = [e for e in expects if e not in text] + (["image"] if img and not has_img else [])
                    if res.isError:
                        bad.append("isError")
                    print(("ok   " if not bad else "FAIL ") + tool + ("  [image]" if has_img else ""))
                    print("     " + text.strip().replace("\n", "\n     ")[:500])
                    if bad:
                        fails += 1
                        print("     missing:", bad)
            finally:
                await s.call_tool("freecad_doc", {"action": "close", "name": DOC, "force": True})
    print("outputs in", OUT)
    print("FAILS: %d" % fails)


asyncio.run(main())
