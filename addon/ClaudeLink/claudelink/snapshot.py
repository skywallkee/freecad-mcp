"""Cheap per-object geometry fingerprints and before/after diffs.

Metrics are cached by the shape's hashCode, so unchanged objects cost a dict lookup."""
import FreeCAD

_cache = {}  # shape hash -> (valid, bbox6, volume, nsolids)
_CACHE_MAX = 5000


def fmt(x):
    """Short number: 3 decimals max, trailing zeros stripped."""
    if x is None:
        return "-"
    if isinstance(x, float):
        if abs(x) >= 1e5:
            return "%.4g" % x
        s = "%.3f" % x
        s = s.rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    return str(x)


def shape_of(obj):
    try:
        s = obj.Shape
    except Exception:
        return None
    try:
        if s is None or s.isNull():
            return None
    except Exception:
        return None
    return s


def metrics(shape):
    """(valid, bbox6, volume, nsolids) for a shape, cached."""
    try:
        h = shape.hashCode()
    except Exception:
        h = None
    if h is not None and h in _cache:
        return h, _cache[h]
    try:
        valid = bool(shape.isValid())
    except Exception:
        valid = False
    try:
        b = shape.BoundBox
        bb = tuple(round(v, 3) for v in (b.XMin, b.YMin, b.ZMin, b.XMax, b.YMax, b.ZMax)) if b.isValid() else None
    except Exception:
        bb = None
    try:
        ns = len(shape.Solids)
        vol = round(shape.Volume, 3) if ns else None
    except Exception:
        ns, vol = 0, None
    m = (valid, bb, vol, ns)
    if h is not None:
        if len(_cache) > _CACHE_MAX:
            _cache.clear()
        _cache[h] = m
    return h, m


def bb_size(bb):
    if not bb:
        return None
    return "%sx%sx%s" % (fmt(bb[3] - bb[0]), fmt(bb[4] - bb[1]), fmt(bb[5] - bb[2]))


def obj_record(obj):
    try:
        state = tuple(s for s in obj.State if s not in ("Up-to-date", "Expanded"))
    except Exception:
        state = ()
    rec = {"type": obj.TypeId, "label": obj.Label, "state": state, "hash": None, "m": None}
    s = shape_of(obj)
    if s is not None:
        rec["hash"], rec["m"] = metrics(s)
    return rec


def snapshot():
    """{doc_name: {obj_name: record}} for every open document."""
    out = {}
    for dname, doc in FreeCAD.listDocuments().items():
        try:
            out[dname] = {o.Name: obj_record(o) for o in doc.Objects}
        except Exception:
            out[dname] = {}
    return out


def describe(name, rec):
    parts = [name, "[%s]" % rec["type"]]
    if rec["label"] != name:
        parts.append(repr(rec["label"]))
    m = rec["m"]
    if m:
        valid, bb, vol, ns = m
        if bb:
            parts.append("bb " + bb_size(bb))
        if vol is not None:
            parts.append("vol " + fmt(vol))
        if ns > 1:
            parts.append("%d solids" % ns)
        if not valid:
            parts.append("INVALID-SHAPE")
    if rec["state"]:
        parts.append("state=" + ",".join(rec["state"]))
    return " ".join(parts)


def _delta(a, b):
    d = b - a
    sign = "+" if d >= 0 else ""
    return "%s->%s (%s%s)" % (fmt(a), fmt(b), sign, fmt(d))


def diff(before, after, max_lines=40):
    """Compact text diff between two snapshots."""
    lines = []
    multi = len(set(before) | set(after)) > 1
    for dname in after:
        if dname not in before:
            lines.append("+doc %s (%d objects)" % (dname, len(after[dname])))
            for n, rec in after[dname].items():
                lines.append("+ " + (dname + "." if multi else "") + describe(n, rec))
    for dname in before:
        if dname not in after:
            lines.append("-doc %s" % dname)
    for dname, objs in after.items():
        if dname not in before:
            continue
        old = before[dname]
        pre = (dname + ".") if multi else ""
        for n, rec in objs.items():
            if n not in old:
                lines.append("+ " + pre + describe(n, rec))
                continue
            o = old[n]
            if o == rec:
                continue
            bits = []
            if o["label"] != rec["label"]:
                bits.append("label %r->%r" % (o["label"], rec["label"]))
            if o["type"] != rec["type"]:
                bits.append("type %s->%s" % (o["type"], rec["type"]))
            om, nm = o["m"], rec["m"]
            if nm and om:
                if om[2] is not None and nm[2] is not None and om[2] != nm[2]:
                    bits.append("vol " + _delta(om[2], nm[2]))
                if om[1] != nm[1] and nm[1]:
                    if bb_size(om[1]) != bb_size(nm[1]):
                        bits.append("bb %s->%s" % (bb_size(om[1]), bb_size(nm[1])))
                    else:
                        bits.append("moved to min(%s,%s,%s)" % tuple(fmt(v) for v in nm[1][:3]))
                if om[3] != nm[3]:
                    bits.append("solids %d->%d" % (om[3], nm[3]))
                if om[0] and not nm[0]:
                    bits.append("NOW INVALID-SHAPE")
                elif nm[0] and not om[0]:
                    bits.append("now valid")
                if not bits and om != nm:
                    bits.append("shape changed")
            elif nm and not om:
                bits.append("shape: " + describe(n, rec).split("]", 1)[-1].strip())
            elif om and not nm:
                bits.append("shape now empty")
            elif o["hash"] != rec["hash"]:
                bits.append("shape changed")
            if o["state"] != rec["state"]:
                bits.append("state=" + (",".join(rec["state"]) or "ok"))
            if bits:
                lines.append("~ " + pre + n + " " + "; ".join(bits))
        for n in old:
            if n not in objs:
                lines.append("- " + pre + n)
    if len(lines) > max_lines:
        extra = len(lines) - max_lines
        lines = lines[:max_lines] + ["... %d more changes" % extra]
    return lines


def problems(snap, max_lines=15):
    """Objects currently in an error/invalid/touched state."""
    bad, touched = [], []
    for dname, objs in snap.items():
        for n, rec in objs.items():
            st = rec["state"]
            if any(s in ("Invalid", "Error") for s in st) or (rec["m"] and not rec["m"][0]):
                bad.append("%s.%s(%s)" % (dname, n, ",".join(st) or "invalid shape"))
            elif "Touched" in st:
                touched.append(n)
    out = []
    if bad:
        out.append("! problems: " + ", ".join(bad[:max_lines]) + (" ..." if len(bad) > max_lines else ""))
    if touched:
        out.append("! %d object(s) need recompute: %s" % (len(touched), ", ".join(touched[:8]) + (" ..." if len(touched) > 8 else "")))
    return out
