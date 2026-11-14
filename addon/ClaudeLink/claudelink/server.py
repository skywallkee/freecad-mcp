"""ClaudeLink transport: a localhost JSON-lines server that runs every request on
FreeCAD's main thread (FreeCAD's API is not thread-safe).

Protocol: client connects, sends ONE line of JSON
    {"token": "...", "method": "exec", "params": {...}, "timeout": 120}
and receives ONE line back:
    {"ok": true, "result": ...}   or   {"ok": false, "error": "..."}
"""
import hmac
import json
import os
import queue
import secrets
import socketserver
import threading
import time
import traceback

import FreeCAD

DEFAULT_PORT = 47823
CONF_DIR = os.path.join(FreeCAD.getUserAppDataDir(), "ClaudeLink")


def _load_token():
    os.makedirs(CONF_DIR, exist_ok=True)
    path = os.path.join(CONF_DIR, "token.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            tok = f.read().strip()
        if tok:
            return tok
    tok = secrets.token_hex(24)
    with open(path, "w", encoding="utf-8") as f:
        f.write(tok)
    return tok


class Executor:
    """Queue of jobs drained on the main thread, by a Qt timer (GUI) or a loop (headless)."""

    def __init__(self):
        self.jobs = queue.Queue()
        self.busy = False
        self.current = None

    def submit(self, fn, timeout):
        box = {}
        done = threading.Event()
        self.jobs.put((fn, box, done))
        if not done.wait(timeout):
            return {"ok": False, "error": "timeout after %ss - the job is still running in FreeCAD "
                                          "(%s); later calls will queue behind it" % (timeout, self.current)}
        return box["resp"]

    def pump(self):
        if self.busy:  # re-entered via updateGui() inside a running job
            return
        self.busy = True
        try:
            while True:
                try:
                    fn, box, done = self.jobs.get_nowait()
                except queue.Empty:
                    return
                try:
                    box["resp"] = {"ok": True, "result": fn()}
                except Exception as e:  # handler bug or user-facing error
                    msg = str(e) if isinstance(e, UserError) else traceback.format_exc()
                    box["resp"] = {"ok": False, "error": msg}
                finally:
                    self.current = None
                    done.set()
        finally:
            self.busy = False


class UserError(Exception):
    """Error whose message is shown as-is (no traceback)."""


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        srv = self.server
        try:
            line = self.rfile.readline(64 * 1024 * 1024)
            req = json.loads(line.decode("utf-8"))
            if not hmac.compare_digest(str(req.get("token", "")), srv.token):
                resp = {"ok": False, "error": "bad token"}
            else:
                method = req.get("method")
                params = req.get("params") or {}
                fn = srv.methods.get(method)
                if fn is None:
                    resp = {"ok": False, "error": "unknown method %r" % method}
                else:
                    def job(fn=fn, params=params, method=method):
                        srv.executor.current = method
                        return fn(**params)
                    resp = srv.executor.submit(job, float(req.get("timeout") or 300))
        except Exception:
            resp = {"ok": False, "error": traceback.format_exc()}
        try:
            self.wfile.write((json.dumps(resp, default=str) + "\n").encode("utf-8"))
        except OSError:
            pass


class _TCP(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False


def start(methods, port=DEFAULT_PORT, conn_name="connection.json", gui=True):
    """Start the server. In GUI mode returns immediately (Qt timer pumps jobs).
    In headless mode call serve_blocking() instead."""
    ex = Executor()
    srv = _TCP(("127.0.0.1", port), _Handler)
    srv.token = _load_token()
    srv.methods = methods
    srv.executor = ex
    srv.started = time.time()
    threading.Thread(target=srv.serve_forever, daemon=True, name="ClaudeLink").start()
    with open(os.path.join(CONF_DIR, conn_name), "w", encoding="utf-8") as f:
        json.dump({"port": port, "pid": os.getpid(), "gui": gui}, f)
    if gui:
        from PySide import QtCore
        timer = QtCore.QTimer()
        timer.timeout.connect(ex.pump)
        timer.start(10)
        srv.timer = timer  # keep alive
    return srv


def stop(srv):
    try:
        if getattr(srv, "timer", None):
            srv.timer.stop()
        srv.shutdown()
        srv.server_close()
    except Exception:
        pass


def serve_blocking(srv):
    while True:
        srv.executor.pump()
        time.sleep(0.005)
