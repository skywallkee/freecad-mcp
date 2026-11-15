"""Standard parts for printed designs (bearings, magnets, servos, boards, connectors) as simplified models
or as seats cut into a part, plus an enclosure generator for a board (standoffs with inserts, port cut-outs,
screwed or snap-fit lid, optional vents).

Dimensions are typical datasheet values - check them against the actual part before printing."""
import math
import re
import time

import FreeCAD
import Part

from . import cl
from .server import UserError
from .snapshot import fmt

V = FreeCAD.Vector

# widths are the shielded (ZZ/2RS) versions where they differ (688, MR85, MR105)
BEARINGS = {"623": (3, 10, 4), "624": (4, 13, 5), "625": (5, 16, 5), "626": (6, 19, 6), "608": (8, 22, 7),
            "688": (8, 16, 5), "6000": (10, 26, 8), "6001": (12, 28, 8), "6200": (10, 30, 9), "6201": (12, 32, 10),
            "6800": (10, 19, 5), "6804": (20, 32, 7), "MR85": (5, 8, 2.5), "MR105": (5, 10, 4), "LM8UU": (8, 15, 24)}
# boards: size (x, y, thickness), mounting holes (x, y, d), keep-out height above the board, connectors:
# (edge, position along the edge from the board corner, width, height, z above the board top)
BOARDS = {
    "rpi4": {"size": (85, 56, 1.4), "holes": [(3.5, 3.5, 2.7), (61.5, 3.5, 2.7), (3.5, 52.5, 2.7), (61.5, 52.5, 2.7)],
             "height": 16.5, "insert": "M2.5", "conn": [      # official mechanical drawing (heights = Z above board)
                 ("front", 11.2, 9.0, 3.2, 0.0, "USB-C power"), ("front", 26.0, 7.0, 3.0, 0.0, "micro-HDMI 0"),
                 ("front", 39.5, 7.0, 3.0, 0.0, "micro-HDMI 1"), ("front", 54.0, 6.5, 6.0, 0.0, "audio"),
                 ("right", 9.0, 13.5, 16.0, 0.0, "USB 2"), ("right", 27.0, 13.5, 16.0, 0.0, "USB 3"),
                 ("right", 45.75, 16.0, 13.5, 0.0, "Ethernet")]},
    "rpi5": {"size": (85, 56, 1.4), "holes": [(3.5, 3.5, 2.7), (61.5, 3.5, 2.7), (3.5, 52.5, 2.7), (61.5, 52.5, 2.7)],
             "height": 16.5, "insert": "M2.5", "conn": [
                 ("front", 11.2, 9.0, 3.3, 0.0, "USB-C power"), ("front", 25.8, 7.0, 3.5, 0.0, "micro-HDMI 0"),
                 ("front", 39.2, 7.0, 3.5, 0.0, "micro-HDMI 1"),
                 ("right", 10.2, 16.0, 13.5, 0.0, "Ethernet"), ("right", 29.1, 13.5, 15.6, 0.0, "USB 3"),
                 ("right", 47.0, 13.5, 15.6, 0.0, "USB 2")]},
    "rpi-zero": {"size": (65, 30, 1.4), "holes": [(3.5, 3.5, 2.75), (61.5, 3.5, 2.75), (3.5, 26.5, 2.75),
                                                  (61.5, 26.5, 2.75)],
                 "height": 5.0, "insert": "M2.5", "conn": [
                     ("front", 12.4, 11.2, 3.4, 0.0, "mini-HDMI"), ("front", 41.4, 7.5, 2.8, 0.0, "micro-USB data"),
                     ("front", 54.0, 7.5, 2.8, 0.0, "micro-USB power")]},
    "arduino-uno": {"size": (68.58, 53.34, 1.6), "holes": [(13.97, 2.54, 3.2), (15.24, 50.8, 3.2), (66.04, 7.62, 3.2),
                                                           (66.04, 35.56, 3.2)],     # A000066 datasheet (mil grid)
                    "height": 15.0, "insert": "M3", "conn": [
                        ("left", 38.1, 12.0, 11.0, 0.0, "USB-B"), ("left", 7.6, 9.0, 11.0, 0.0, "DC jack")]},
    "arduino-nano": {"size": (43.2, 18.0, 1.6), "holes": [(1.28, 1.38, 1.8), (41.92, 1.38, 1.8), (1.28, 16.62, 1.8),
                                                          (41.92, 16.62, 1.8)],      # A000005 datasheet
                     "height": 6.0, "insert": "M2", "conn": [("left", 9.0, 8.0, 4.0, 0.0, "mini/USB-C")]},
    # PCB 48.26 long + the module antenna overhanging 6.04 at x=max: 54.3 envelope (Espressif dimension drawing)
    "esp32-devkitc": {"size": (54.3, 27.94, 1.6), "holes": [], "height": 5.0, "insert": None,
                      "conn": [("left", 13.97, 8.0, 3.2, 0.0, "micro-USB / USB-C")]},
}
SERVOS = {  # sg90 body is a measured clone size (datasheet says 22.2 x 11.8); body (x, y, z), tab length total, tab thickness, tab top height, hole spacing, hole d, shaft offset
    "sg90": {"body": (22.5, 12.2, 22.7), "tab": 32.2, "tab_t": 2.5, "tab_z": 18.5, "holes": 27.8, "hole_d": 2.0,
             "shaft": 5.9, "boss": (11.8, 4.0)},
    "mg996r": {"body": (40.7, 19.7, 36.6), "tab": 53.6, "tab_t": 2.5, "tab_z": 29.1, "holes": 49.5, "hole_d": 4.2,
               "shaft": 10.0, "boss": (20.0, 5.9)},
}


def _doc(name=None):
    from .handlers import _doc as d
    return d(name)


def _obj(doc, key):
    from .handlers import _obj as o
    return o(doc, key)


def _f(x, n=2):
    return fmt(round(float(x), n))


def _fit(cls):
    from . import profile
    return profile.fit(cls)


def board_shape(key):
    b = BOARDS[key]
    L, W, T = b["size"]
    s = Part.makeBox(L, W, T)
    for x, y, d in b["holes"]:
        s = s.cut(Part.makeCylinder(d / 2, T + 2, V(x, y, -1)))
    parts = [s]
    for c in b["conn"]:
        parts.append(_conn_box(key, c, protrude=1.5))
    parts.append(Part.makeBox(L * 0.5, W * 0.4, min(b["height"], 3.0), V(L * 0.25, W * 0.3, T)))   # chips block
    return Part.makeCompound(parts)


def _conn_box(key, c, protrude=1.5, grow=0.0, through=0.0):
    """Connector body (or its cut-out when grow/through > 0) in board coordinates."""
    L, W, T = BOARDS[key]["size"]
    edge, pos, w, h, z, _ = c
    depth = 6.0 + through
    z0 = T + z - grow
    if edge == "front":
        return Part.makeBox(w + 2 * grow, depth, h + 2 * grow, V(pos - w / 2 - grow, -protrude - through, z0))
    if edge == "back":
        return Part.makeBox(w + 2 * grow, depth, h + 2 * grow, V(pos - w / 2 - grow, W - depth + protrude + through, z0))
    if edge == "left":
        return Part.makeBox(depth, w + 2 * grow, h + 2 * grow, V(-protrude - through, pos - w / 2 - grow, z0))
    return Part.makeBox(depth, w + 2 * grow, h + 2 * grow, V(L - depth + protrude + through, pos - w / 2 - grow, z0))


def bearing_shape(code):
    d, D, B = BEARINGS[code]
    return cl.ring(d / 2.0, D / 2.0, B)


def servo_shape(key):
    s = SERVOS[key]
    x, y, z = s["body"]
    body = Part.makeBox(x, y, z, V(-x / 2, -y / 2, 0))
    tab = Part.makeBox(s["tab"], y, s["tab_t"], V(-s["tab"] / 2, -y / 2, s["tab_z"] - s["tab_t"]))
    for sx in (-1, 1):
        tab = tab.cut(Part.makeCylinder(s["hole_d"] / 2, 10, V(sx * s["holes"] / 2, 0, s["tab_z"] - 5)))
    bd, bh = s["boss"]
    boss = Part.makeCylinder(bd / 2, bh, V(x / 2 - s["shaft"], 0, z))
    shaft = Part.makeCylinder(2.4, 3.0, V(x / 2 - s["shaft"], 0, z + bh))
    return cl.union([body, tab, boss, shaft])


def _parse(kind, size):
    k = (kind or "").lower()
    if k == "bearing":
        code = str(size or "608").upper()
        if code not in BEARINGS:
            raise UserError("bearing %r unknown; known: %s" % (size, ", ".join(BEARINGS)))
        return k, code
    if k == "magnet":
        m = re.match(r"^\s*([0-9.]+)\s*[x*]\s*([0-9.]+)\s*$", str(size or "6x3"))
        if not m:
            raise UserError("magnet size as 'DxH', e.g. '6x3'")
        return k, (float(m.group(1)), float(m.group(2)))
    if k == "servo":
        key = str(size or "sg90").lower()
        if key not in SERVOS:
            raise UserError("servo %r unknown; known: %s" % (size, ", ".join(SERVOS)))
        return k, key
    if k == "board":
        key = str(size or "rpi4").lower()
        if key not in BOARDS:
            raise UserError("board %r unknown; known: %s" % (size, ", ".join(BOARDS)))
        return k, key
    raise UserError("kind must be bearing, magnet, servo or board")


def stdpart(kind, size=None, at=(0, 0, 0), axis="z", mode="part", target=None, fit=None, name=None, doc=None):
    """See the MCP tool."""
    from .fasteners import place
    from .output import auto_checkpoint
    d = _doc(doc)
    k, key = _parse(kind, size)
    out = []
    at = tuple(at or (0, 0, 0))
    if mode == "part":
        if k == "bearing":
            s, desc = bearing_shape(key), "bearing %s (d%s x D%s x %s)" % ((key,) + tuple(_f(v) for v in BEARINGS[key]))
        elif k == "magnet":
            D, H = key
            s, desc = Part.makeCylinder(D / 2, H), "magnet d%s x %s" % (_f(D), _f(H))
        elif k == "servo":
            s, desc = servo_shape(key), "servo %s (simplified)" % key.upper()
        else:
            s, desc = board_shape(key), "board %s (simplified, connectors as blocks)" % key
        s = place(s, at, axis) if k != "board" else s.translated(V(*at))
        o = cl.show(s, name or ("Std_" + re.sub(r"\W", "_", "%s_%s" % (k, key if isinstance(key, str) else "x".join(_f(v) for v in key)))), doc=d)
        if True:
            try:
                cl.paint(o, (0.75, 0.75, 0.78), 40)
            except Exception:
                pass
        out.append("%s: %s at %s - reference only, not for printing" % (o.Name, desc, at))
    elif mode == "seat":
        if not target:
            raise UserError("mode=seat needs target (the object to cut the seat into)")
        t = _obj(d, target)
        base = t.Shape.copy()
        if k == "bearing":
            dd, D, B = BEARINGS[key]
            c = _fit(fit or "press")
            seat = Part.makeCylinder(D / 2 + c, B + 0.1, V(0, 0, 0))
            lip = Part.makeCylinder(D / 2 - min(1.5, 0.15 * D), 200, V(0, 0, 0))        # shaft side relief
            cutter = seat.fuse(lip).fuse(Part.makeCone(D / 2 + c + 0.4, D / 2 + c, 0.4, V(0, 0, -0.001)))
            desc = "bearing %s seat d%s (+%s %s), %s deep, through-relief d%s" % (
                key, _f(D + 2 * c), _f(c), fit or "press", _f(B + 0.1), _f(D - 2 * min(1.5, 0.15 * D)))
        elif k == "magnet":
            D, H = key
            c = _fit(fit or "press")
            cutter = Part.makeCylinder(D / 2 + c, H + 1.2, V(0, 0, -1))
            desc = "magnet pocket d%s (+%s %s) x %s deep" % (_f(D + 2 * c), _f(c), fit or "press", _f(H + 0.2))
        elif k == "servo":
            s = SERVOS[key]
            x, y, z = s["body"]
            c = _fit(fit or "slide")
            cutter = Part.makeBox(x + 2 * c, y + 2 * c, z + 2, V(-x / 2 - c, -y / 2 - c, -1))
            for sx in (-1, 1):
                cutter = cutter.fuse(Part.makeCylinder(s["hole_d"] * 0.42, 12, V(sx * s["holes"] / 2, 0, -1)))
            desc = "servo %s pocket %sx%s (+%s %s) with self-tap holes at %s mm" % (
                key.upper(), _f(x + 2 * c), _f(y + 2 * c), _f(c), fit or "slide", _f(s["holes"]))
        else:
            raise UserError("board seats: use freecad_enclosure")
        cutter = place(cutter, at, axis)
        res = base.cut(cutter)
        try:
            res = res.removeSplitter()
        except Exception:
            pass
        if not res.isValid():
            raise UserError("seat cut failed")
        ck = auto_checkpoint(d, "before seat " + t.Name)
        d.openTransaction("seat")
        try:
            t.Shape = res
            d.recompute()
        finally:
            d.commitTransaction()
        out.append("%s: %s at %s, axis %s (vol %s -> %s mm3)" % (t.Name, desc, at, axis, _f(base.Volume, 1),
                                                                 _f(res.Volume, 1)))
        if ck:
            out.append(ck)
    else:
        raise UserError("mode must be part or seat")
    out.append("dimensions are typical datasheet values - measure your part")
    return "\n".join(out)


# ------------------------------------------------------------------ enclosure

def enclosure(board=None, size=None, wall=2.0, floor=2.0, clearance=1.5, standoff=5.0, headroom=3.0,
              lid="screw", vents=False, corner_r=3.0, name="Enclosure", doc=None):
    """See the MCP tool."""
    from . import fasteners as F
    from . import profile
    d = _doc(doc)
    t0 = time.time()
    prof = profile.load()
    if board:
        key = str(board).lower()
        if key not in BOARDS:
            raise UserError("board %r unknown; known: %s (or give size=[x, y, h])" % (board, ", ".join(BOARDS)))
        bd = BOARDS[key]
        L, W, T = bd["size"]
        keep = bd["height"]
    elif size:
        key, bd = None, None
        L, W, keep = (float(v) for v in size)
        T = 0.0
    else:
        raise UserError("give board (%s) or size=[x, y, height]" % ", ".join(BOARDS))
    c = float(clearance)
    IL, IW = L + 2 * c, W + 2 * c
    so = float(standoff) if (bd and bd["holes"]) else 0.0
    IH = so + T + keep + float(headroom)
    OL, OW, OH = IL + 2 * wall, IW + 2 * wall, IH + floor
    r = min(float(corner_r), wall + c - 0.2) if corner_r else 0.0

    def rbox(l, w, h, x, y, z, rr):
        b = Part.makeBox(l, w, h, V(x, y, z))
        if rr > 0.05:
            try:
                b = b.makeFillet(rr, [e for e in b.Edges if abs(e.tangentAt(e.FirstParameter).z) > 0.99])
            except Exception:
                pass
        return b
    outer = rbox(OL, OW, OH, 0, 0, 0, r)
    inner = rbox(IL, IW, IH + 1, wall, wall, floor, max(r - wall, 0.0))
    base = outer.cut(inner)
    ox, oy, oz = wall + c, wall + c, floor + so          # board origin
    lines = []
    ins = (bd or {}).get("insert") or "M3"
    # standoffs with heat-set inserts (or self-tap if the board holes are tiny)
    nst = 0
    if bd and bd["holes"]:
        for hx, hy, hd in bd["holes"]:
            hole, dh, Lins = F.insert_hole_cutter(ins)
            ro = max(dh / 2 + 1.6, hd / 2 + 2.0)
            post = Part.makeCylinder(ro, so + 0.01, V(ox + hx, oy + hy, floor - 0.01))
            base = base.fuse(post)
            depth = min(Lins + 1.0, so + floor - 0.8)
            base = base.cut(F.place(F.insert_hole_cutter(ins, depth)[0], (ox + hx, oy + hy, floor + so), "-z"))
            nst += 1
        lines.append("%d standoffs %s mm tall with %s heat-set insert holes (screw the board down with %s x 6)" % (
            nst, _f(so), ins, ins))
    elif bd:
        lines.append("board has no mounting holes: it rests on the floor - add a foam pad or hot glue")
    # port cut-outs through the walls
    nports = 0
    if bd:
        g = max(0.6, _fit("loose") * 2)
        for cdef in bd["conn"]:
            cut = _conn_box(key, cdef, protrude=0.0, grow=g, through=wall + c + 2)
            cut.translate(V(ox, oy, oz))
            base = base.cut(cut)
            nports += 1
        lines.append("%d port cut-outs (%s): connector size + %s mm all round" % (
            nports, ", ".join(cd[5] for cd in bd["conn"]), _f(g)))
    # lid: plate + locating lip
    lt = wall
    slide = _fit("slide")
    lid_plate = rbox(OL, OW, lt, 0, 0, OH, r)
    lip_h = 3.0
    lip_out = rbox(IL - 2 * slide, IW - 2 * slide, lip_h, wall + slide, wall + slide, OH - lip_h,
                   max(r - wall - slide, 0.0))
    lip_in = rbox(IL - 2 * slide - 2 * 1.6, IW - 2 * slide - 2 * 1.6, lip_h + 1, wall + slide + 1.6,
                  wall + slide + 1.6, OH - lip_h - 0.5, max(r - wall - slide - 1.6, 0.0))
    lidS = lid_plate.fuse(lip_out.cut(lip_in))
    if lid == "screw":
        # screw ears outside the corners keep the inside free for the board
        r_ear = 4.5
        need = r_ear + 0.6 - (wall + c) * math.sqrt(2)          # clearance from the ear to the board corner
        e = max(0.0, need / math.sqrt(2))
        ears = [(-e, -e), (OL + e, -e), (-e, OW + e), (OL + e, OW + e)]
        for ex, ey in ears:
            tower = Part.makeCylinder(r_ear, OH, V(ex, ey, 0))
            base = base.fuse(tower.cut(inner))
            base = base.cut(F.place(F.insert_hole_cutter("M3", 6.7)[0], (ex, ey, OH), "-z"))
            lidS = lidS.fuse(Part.makeCylinder(r_ear, lt, V(ex, ey, OH)))
            lidS = lidS.cut(F.place(F.screw_hole_cutter("M3", lt + 1, "countersunk"), (ex, ey, OH + lt), "-z"))
        lines.append("lid: 4 x M3 countersunk screws through corner ears into heat-set inserts; locating lip %s mm" % (
            _f(lip_h)))
        hardware = ["4x M3 x 8 countersunk screw", "4x M3 heat-set insert"]
    elif lid == "snap":
        hooks = []
        Lh, th_, dfl = min(10.0, IH * 0.6), 1.4, 0.8
        info = None
        for side in (-1, 1):                     # -1 = front wall (y=0), +1 = back wall
            ya = (wall + slide + th_) if side < 0 else (OW - wall - slide - th_)
            xdir = V(1, 0, 0) if side < 0 else V(-1, 0, 0)      # local +Y (the catch) points into the wall
            for xf in (0.3, 0.7):
                px = OL * xf
                hk, info = F.snap_hook(length=Lh, thickness=th_, width=6.0, deflection=dfl)
                hooks.append(F.place(hk, (px, ya, OH - lip_h + 0.01), "-z", xdir))
                zc = OH - lip_h - Lh                       # tip of the hook
                wy = -1.0 if side < 0 else OW - wall - 1.0
                base = base.cut(Part.makeBox(7.0, wall + 2.0, info["hook_len"] + 0.6, V(px - 3.5, wy, zc - 0.3)))
        lidS = lidS.fuse(hooks)
        lines.append("lid: 4 snap hooks on the lip catching windows in the long walls (%s)" % (
            F.snap_report(info).split(";")[0]))
        hardware = []
    else:
        raise UserError("lid must be screw or snap")
    try:
        base = base.removeSplitter()
        lidS = lidS.removeSplitter()
    except Exception:
        pass
    if vents:
        lp = lidS
        try:
            from .lighten import _cell
            cells = []
            pitch = 3.5
            for k in range(int((OW - 2 * wall - 16) // pitch)):
                f, _ = _cell("slots", OL / 2, wall + 8 + k * pitch, 2.0, OL * 0.5)
                cells.append(f)
            vent = Part.makeCompound(cells)
            vent.translate(V(0, 0, OH - 1))
            lidS = lidS.cut(vent.extrude(V(0, 0, lt + 2)))
            lines.append("vent slots in the lid (%d x 2 mm)" % len(cells))
        except Exception:
            lidS = lp
    ob = cl.show(base, name + "_base", doc=d)
    ol = cl.show(lidS, name + "_lid", doc=d)
    objs = [ob.Name, ol.Name]
    if bd:
        bs = board_shape(key)
        bs.translate(V(ox, oy, oz))
        og = cl.show(bs, name + "_board", doc=d)
        objs.append(og.Name)
        if True:
            try:
                cl.paint(og, (0.1, 0.55, 0.25), 30)
            except Exception:
                pass
    if True:
        try:
            cl.paint(ob, (0.22, 0.47, 0.85))
            cl.paint(ol, (0.96, 0.56, 0.12))
        except Exception:
            pass
    # fit checks
    checks = []
    try:
        v = base.common(lidS).Volume
        checks.append("lid/base overlap %s mm3" % _f(v, 3))
    except Exception:
        pass
    if bd:
        try:
            bs2 = board_shape(key)
            bs2.translate(V(ox, oy, oz))
            body = Part.makeBox(L, W, T, V(ox, oy, oz))
            v = base.common(body).Volume
            checks.append("board/base overlap %s mm3" % _f(v, 3))
        except Exception:
            pass
    head = "%s: %s enclosure %sx%sx%s mm outside (inside %sx%sx%s), %s mm walls, %s mm floor" % (
        name, (key or "custom"), _f(OL), _f(OW), _f(OH + lt), _f(IL), _f(IW), _f(IH), _f(wall), _f(floor))
    out = [head] + ["  " + l for l in lines]
    out.append("  objects: %s (board = reference only)" % ", ".join(objs))
    if checks:
        out.append("  check: " + ", ".join(checks))
    if nst:
        hardware = ["%dx %s x 6 screw (board)" % (nst, ins), "%dx %s heat-set insert (board)" % (nst, ins)] + hardware
    out.append("  hardware: " + (", ".join(hardware) if hardware else "none"))
    out.append("  print the base open side up and the lid upside down (flat top on the bed); connector positions "
               "are typical - check them against your board")
    out.append("(%.1fs)" % (time.time() - t0))
    return "\n".join(out)


METHODS = {"stdpart": stdpart, "enclosure": enclosure}
