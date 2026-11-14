# Run a headless ClaudeLink server (no window):
#   freecadcmd.exe headless_serve.py            -> port 47824
# Set CLAUDELINK_PORT to change the port.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claudelink  # noqa: E402
from claudelink import server  # noqa: E402

srv = claudelink.start(port=int(os.environ.get("CLAUDELINK_PORT", "47824")), headless=True)
if srv is None:
    sys.exit(1)
print("ClaudeLink headless ready", flush=True)
server.serve_blocking(srv)
