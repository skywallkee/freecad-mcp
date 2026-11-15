"""Clearance through a range of motion (hinges, lids, sliders, print-in-place parts)."""
import math
import time

import FreeCAD
import Part

from . import cl
from .analysis import global_shape, _vec, _resolve_objects
from .server import UserError
from .snapshot import fmt

V = FreeCAD.Vector


def _doc(name=None):
    from .handlers import _doc as d
    return d(name)


def _obj(doc, key):
    from .handlers import _obj as o
    return o(doc, key)


def _v(x):
    from .printprep import _parse_vec
    return _parse_vec(x) if not isinstance(x, FreeCAD.Vector) else x


def _axis_from_ref(d, ref):
    """Axis (point, direction) of a cylinder/circle picked by 'Obj:selector'."""
    if ":" not in ref:
        raise UserError("axis_ref must be 'Object:selector', e.g. 'Base:faces %Cylinder r<4 [0]'")
    on, sel = ref.split(":", 1)
    o = _obj(d, on)
    try:
        subs = cl.sel(global_shape(o), sel)
    except ValueError as e:
        raise UserError(str(e))
    for s in subs:
        surf = getattr(s, "Surface", None) or getattr(s, "Curve", None)
        if surf is not None and hasattr(surf, "Axis") and hasattr(surf, "Center"):
            return V(surf.Center), V(surf.Axis).normalize()
        if surf is not None and hasattr(surf, "Axis") and hasattr(surf, "Location"):
            return V(surf.Location), V(surf.Axis).normalize()
    raise UserError("axis_ref %r matched no cylinder/circle" % ref)


def motion(moving, fixed=None, rotate=None, translate=None, steps=36, doc=None, show_worst=True):
    """Move `moving` objects through a rotation or translation and track clearance to `fixed`.
    rotate = {"axis": "y" | [x,y,z], "point": [x,y,z], "from": deg, "to": deg} or {"axis_ref": "Obj:sel", ...}
    translate = {"dir": "x" | [x,y,z], "from": mm, "to": mm}"""
    from . import profile
    d = _doc(doc)
    if isinstance(moving, str):
        moving = [moving]
    mov = [_obj(d, k) for k in moving]
    if fixed:
        fix = [_obj(d, k) for k in ([fixed] if isinstance(fixed, str) else fixed)]
    else:
        fix = [o for o in _resolve_objects(d, None) if o.Name not in {m.Name for m in mov}]
    fix = [o for o in fix if o.Name not in {m.Name for m in mov}]
    if not fix:
        raise UserError("nothing to check against (no other visible solids)")
    if (rotate is None) == (translate is None):
        raise UserError("give exactly one of rotate={axis, point, from, to} or translate={dir, from, to}")
    t0 = time.time()
    if rotate is not None:
        if rotate.get("axis_ref"):
            pt, ax = _axis_from_ref(d, rotate["axis_ref"])
        else:
            ax = _v(rotate.get("axis", "z"))
            pt = V(*rotate.get("point", (0, 0, 0)))
        a0, a1 = float(rotate.get("from", 0)), float(rotate.get("to", 90))
        unit = "deg"

        def plc(t):
            return FreeCAD.Placement(V(), FreeCAD.Rotation(ax, t), pt)
        what = "rotate about %s through %s" % (_vec(ax), _vec(pt))
    else:
        ax = _v(translate.get("dir", "x"))
        a0, a1 = float(translate.get("from", 0)), float(translate.get("to", 10))
        unit = "mm"

        def plc(t):
            return FreeCAD.Placement(ax * t, FreeCAD.Rotation())
        what = "slide along %s" % _vec(ax)
    steps = max(2, min(int(steps), 720))
    ms = [global_shape(o) for o in mov]
    mshape = ms[0] if len(ms) == 1 else Part.makeCompound(ms)
    fshapes = [(o.Name, global_shape(o)) for o in fix]
    reach = mshape.BoundBox.DiagonalLength
    rows = []   # (t, min_gap, against, point, interference_volume)
    for i in range(steps + 1):
        t = a0 + (a1 - a0) * i / float(steps)
        m = mshape.copy()
        m.transformShape(plc(t).toMatrix())
        mb = m.BoundBox
        best = (float("inf"), None, None, 0.0)
        for nm, fs in fshapes:
            fb = fs.BoundBox
            # cheap reject: boxes farther apart than the current best gap
            gx = max(fb.XMin - mb.XMax, mb.XMin - fb.XMax, 0)
            gy = max(fb.YMin - mb.YMax, mb.YMin - fb.YMax, 0)
            gz = max(fb.ZMin - mb.ZMax, mb.ZMin - fb.ZMax, 0)
            if math.sqrt(gx * gx + gy * gy + gz * gz) > min(best[0], reach):
                continue
            dist, pts, _ = m.distToShape(fs)
            if dist < 1e-6:
                try:
                    vol = m.common(fs).Volume
                except Exception:
                    vol = -1.0
                if vol > 1e-4 or vol < 0:
                    if best[0] > 0 or vol > best[3]:
                        best = (0.0, nm, pts[0][0] if pts else None, vol)
                    continue
                dist = 0.0
            if dist < best[0]:
                best = (dist, nm, pts[0][0] if pts else None, 0.0)
        rows.append((t,) + best)
    hits = [r for r in rows if r[4] != 0.0]
    clear = [r for r in rows if r[4] == 0.0]
    lines = ["%s: %s from %s to %s %s, %d steps; checked against %s" % (
        "+".join(m.Name for m in mov), what, fmt(a0), fmt(a1), unit, steps, ", ".join(n for n, _ in fshapes))]
    if hits:
        # contiguous colliding ranges
        ranges, cur = [], None
        for r in rows:
            if r[4] != 0.0:
                cur = [r[0], r[0], r] if cur is None else [cur[0], r[0], cur[2] if cur[2][4] >= r[4] else r]
            elif cur is not None:
                ranges.append(cur)
                cur = None
        if cur is not None:
            ranges.append(cur)
        for lo, hi, worst in ranges:
            lines.append("COLLISION from %s to %s %s with %s; worst %s mm3 at %s %s near %s" % (
                fmt(round(lo, 2)), fmt(round(hi, 2)), unit, worst[2], fmt(round(worst[4], 2)) if worst[4] > 0 else "?",
                fmt(round(worst[0], 2)), unit, _vec(worst[3]) if worst[3] else "?"))
        if clear:
            lines.append("free over %s of the range (%s..%s %s at the ends: %s)" % (
                "%d%%" % round(100.0 * len(clear) / len(rows)), fmt(round(clear[0][0], 2)), fmt(round(clear[-1][0], 2)),
                unit, "start clear" if rows[0][4] == 0 else "start blocked"))
    if clear and not hits:
        mn = min(clear, key=lambda r: r[1])
        cls = profile.classify(mn[1]) if mn[1] > 1e-6 else "touching"
        lines.append("min clearance %s mm at %s %s against %s near %s%s" % (
            fmt(round(mn[1], 3)), fmt(round(mn[0], 2)), unit, mn[2], _vec(mn[3]) if mn[3] else "?",
            (" -> " + cls) if cls else ""))
        loose = profile.fit("loose")
        if 0 < mn[1] < loose:
            lines.append("print-in-place: %s mm is under the loose-fit clearance %s mm - the parts may fuse when printed "
                         "assembled; open the gap to >= %s mm or print them separately" % (
                             fmt(round(mn[1], 3)), fmt(loose), fmt(loose)))
        elif mn[1] >= loose:
            lines.append("print-in-place: OK (>= loose-fit clearance %s mm everywhere along the motion)" % fmt(loose))
    # compact profile of the gap along the motion (8 samples)
    samp = [rows[int(round(k * (len(rows) - 1) / 7.0))] for k in range(8)]
    lines.append("gap along the motion (%s: mm): " % unit + ", ".join(
        "%s:%s" % (fmt(round(r[0], 1)), "HIT" if r[4] != 0 else fmt(round(r[1], 2))) for r in samp))
    ghost = d.getObject("_".join(m.Name for m in mov) + "_collision")
    if hits and show_worst:
        worst = max(hits, key=lambda r: r[4])
        m = mshape.copy()
        m.transformShape(plc(worst[0]).toMatrix())
        go = cl.show(m, "_".join(x.Name for x in mov) + "_collision", doc=d)
        if True:
            try:
                cl.paint(go, (1.0, 0.2, 0.2), 60)
            except Exception:
                pass
        lines.append("ghost of the worst position: %s (delete when done)" % go.Name)
    elif ghost is not None:
        d.removeObject(ghost.Name)
    lines.append("(%.1fs)" % (time.time() - t0))
    return "\n".join(lines)


METHODS = {"motion": motion}
