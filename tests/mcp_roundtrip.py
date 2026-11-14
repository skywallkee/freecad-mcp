"""End-to-end over the real MCP stdio protocol, against a headless ClaudeLink.
Run:  uv run --directory server python ../tests/mcp_roundtrip.py"""
import asyncio
import os
import subprocess
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
FC = r"C:\Program Files\FreeCAD 1.0\bin\freecadcmd.exe"


async def main():
    env = dict(os.environ, CLAUDELINK_TARGET="headless")
    params = StdioServerParameters(command="uv", args=["run", "--directory", os.path.join(HERE, "..", "server"),
                                                       "freecad-mcp"], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            print("server:", init.serverInfo.name, "| instructions:", len(init.instructions or ""), "chars")
            tools = (await s.list_tools()).tools
            print("tools:", ", ".join(t.name for t in tools))
            for name, args in [("freecad_doc", {"action": "new", "name": "RT"}),
                               ("freecad_exec", {"code": "s = Part.makeCylinder(5, 20)\nPart.show(s, 'Cyl')\nApp.ActiveDocument.recompute()\ns.Volume"}),
                               ("freecad_tree", {}),
                               ("freecad_doc", {"action": "close", "force": True})]:
                res = await s.call_tool(name, args)
                print("---", name, "\n" + "\n".join(c.text for c in res.content if c.type == "text"))


proc = subprocess.Popen([FC, os.path.join(HERE, "..", "addon", "ClaudeLink", "headless_serve.py")],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    time.sleep(4)
    asyncio.run(main())
finally:
    proc.kill()
