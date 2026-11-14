"""ClaudeLink: lets Claude (via the freecad MCP server) drive this FreeCAD session."""
import importlib
import sys

import FreeCAD

VERSION = "0.1.0"


def start(port=None, headless=False):
    from . import server, handlers
    if getattr(FreeCAD, "_claudelink_srv", None) is not None:
        return FreeCAD._claudelink_srv
    port = port or server.DEFAULT_PORT
    conn = "connection-headless.json" if headless else "connection.json"
    try:
        srv = server.start(handlers.METHODS, port=port, conn_name=conn, gui=not headless)
    except OSError as e:
        FreeCAD.Console.PrintWarning("ClaudeLink: could not listen on 127.0.0.1:%d (%s) - "
                                     "another FreeCAD probably has it\n" % (port, e))
        return None
    FreeCAD._claudelink_srv = srv
    FreeCAD.Console.PrintMessage("ClaudeLink %s listening on 127.0.0.1:%d\n" % (VERSION, port))
    return srv


def stop():
    from . import server
    srv = getattr(FreeCAD, "_claudelink_srv", None)
    if srv is not None:
        server.stop(srv)
        FreeCAD._claudelink_srv = None


def restart():
    """Reload the addon code and restart the server (deferred, so it is safe to call
    from inside a ClaudeLink request)."""
    def _do():
        port = getattr(getattr(FreeCAD, "_claudelink_srv", None), "server_address", (None, None))[1]
        stop()
        for name in [m for m in sys.modules if m == "claudelink" or m.startswith("claudelink.")]:
            del sys.modules[name]
        importlib.import_module("claudelink").start(port)
        ns = getattr(FreeCAD, "_claudelink_ns", None)
        if ns is not None:  # refresh preloaded helpers in the persistent namespace
            from claudelink import handlers
            for k, v in handlers._base_ns().items():
                if k != "__name__":
                    ns[k] = v
    if FreeCAD.GuiUp:
        from PySide import QtCore
        QtCore.QTimer.singleShot(200, _do)
        return "restart scheduled"
    _do()
    return "restarted"
