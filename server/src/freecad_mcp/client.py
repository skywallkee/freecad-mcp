"""Tiny client for the ClaudeLink JSON-lines protocol, plus the optional headless worker."""
import atexit
import json
import os
import socket
import subprocess
import sys
import time

CONF_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "FreeCAD", "ClaudeLink")
FREECADCMD = os.environ.get("FREECADCMD", r"C:\Program Files\FreeCAD 1.0\bin\freecadcmd.exe")
HEADLESS_SCRIPT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "addon", "ClaudeLink",
                                               "headless_serve.py"))
HEADLESS_PORT = 47824

# Default: a windowless FreeCAD worker, so nothing can freeze the user's FreeCAD window. The window is used
# for the few things that need it (freecad_show, illustrations, drawings) or with freecad_target('gui').
_state = {"target": os.environ.get("CLAUDELINK_TARGET", "headless").lower(), "worker": None, "starting": False}
AUTOSTART = os.environ.get("CLAUDELINK_AUTOSTART", "1") != "0"


class LinkError(Exception):
    pass


def target():
    return _state["target"]


def set_target(t):
    _state["target"] = t


def _endpoint(t=None):
    t = t or _state["target"]
    conn = "connection-headless.json" if t == "headless" else "connection.json"
    port = os.environ.get("CLAUDELINK_PORT")
    if not port:
        try:
            with open(os.path.join(CONF_DIR, conn), encoding="utf-8") as f:
                port = json.load(f)["port"]
        except (OSError, ValueError, KeyError):
            port = HEADLESS_PORT if t == "headless" else 47823
    try:
        with open(os.path.join(CONF_DIR, "token.txt"), encoding="utf-8") as f:
            token = f.read().strip()
    except OSError:
        raise LinkError("ClaudeLink token not found in %s - has FreeCAD (with the ClaudeLink addon) "
                        "ever been started?" % CONF_DIR)
    return int(port), token


def call(method, params=None, timeout=300.0, t=None, autostart=True):
    t = t or _state["target"]
    port, token = _endpoint(t)
    req = {"token": token, "method": method, "params": {k: v for k, v in (params or {}).items() if v is not None},
           "timeout": timeout}
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
    except OSError:
        if t == "headless":
            if autostart and AUTOSTART and not _state["starting"]:
                _state["starting"] = True
                try:
                    start_worker()
                finally:
                    _state["starting"] = False
                return call(method, params, timeout, t)
            raise LinkError("windowless worker not running on port %d; call freecad_target('headless') to start it" % port)
        raise LinkError("The FreeCAD window is not reachable on 127.0.0.1:%d. Ask the user to start FreeCAD "
                        "(the ClaudeLink addon starts with it). Do not launch the FreeCAD GUI yourself; "
                        "windowless work (freecad_target('headless'), the default) does not need it." % port)
    with s:
        s.settimeout(timeout + 10)
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        buf = bytearray()
        while not buf.endswith(b"\n"):
            chunk = s.recv(1 << 20)
            if not chunk:
                break
            buf += chunk
    if not buf:
        raise LinkError("empty response from ClaudeLink")
    resp = json.loads(buf.decode("utf-8"))
    if not resp.get("ok"):
        raise LinkError(resp.get("error", "unknown error"))
    return resp["result"]


def reachable(t):
    try:
        call("status", timeout=5, t=t, autostart=False)
        return True
    except LinkError:
        return False


def start_worker(wait=30.0):
    """Start a windowless freecadcmd running the ClaudeLink server (no console window)."""
    if reachable("headless"):
        return "headless worker already running"
    if not os.path.isfile(FREECADCMD):
        raise LinkError("freecadcmd not found at %s (set FREECADCMD)" % FREECADCMD)
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    env = dict(os.environ, CLAUDELINK_PORT=str(HEADLESS_PORT))
    p = subprocess.Popen([FREECADCMD, HEADLESS_SCRIPT], env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    _state["worker"] = p
    t0 = time.time()
    while time.time() - t0 < wait:
        if p.poll() is not None:
            raise LinkError("headless worker exited with code %s" % p.returncode)
        if reachable("headless"):
            return "headless worker started (pid %d) in %.1fs" % (p.pid, time.time() - t0)
        time.sleep(0.3)
    raise LinkError("headless worker did not come up within %ss" % wait)


def stop_worker():
    p = _state.get("worker")
    if p is None or p.poll() is not None:
        _state["worker"] = None
        return "no headless worker started by this server"
    try:        # keep recovery copies of whatever was open (FreeCAD/ClaudeLink/handoff/<Doc>.FCStd)
        call("handoff_all", timeout=60, t="headless")
    except Exception:
        pass
    p.terminate()
    try:
        p.wait(5)
    except subprocess.TimeoutExpired:
        p.kill()
    _state["worker"] = None
    return "headless worker stopped"


atexit.register(stop_worker)
