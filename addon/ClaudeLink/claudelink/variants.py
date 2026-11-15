"""Size variants of a parametric project: rebuild with each set of parameter overrides, export every part to
stl/<variant>/, check print readiness and summarise sizes and weights; the project is rebuilt with its
original values afterwards."""
import os
import re
import time

import FreeCAD

from .server import UserError
from .snapshot import fmt


def _f(x, n=1):
    return fmt(round(float(x), n))


def _ok(txt):
    parts = txt.split("\n", 1)
    return len(parts) > 1 and parts[1].lstrip().startswith("OK")


def variants(project=None, variants=None, objects=None, format="stl", check=True, up="auto", doc=None):
    """See the MCP tool."""
    from . import project as P
    from .output import export, _safe, auto_checkpoint
    from .analysis import _resolve_objects, global_shape
    from .strength import mech
    from . import profile
    if not variants or not isinstance(variants, dict):
        raise UserError('variants = {"S": {"WIDTH": 30}, "M": {"WIDTH": 40}, ...}')
    pr = P.resolve(project)
    params = {p[0] for p in P.parse_params(P._need_script(pr))[0]}
    unknown = sorted({k for v in variants.values() for k in v} - params)
    if unknown:
        raise UserError("unknown parameter(s) %s; the project has %s" % (", ".join(unknown), ", ".join(sorted(params))))
    t0 = time.time()
    d0 = pr.doc()
    if d0 is not None:
        auto_checkpoint(d0, "before variants")
    dens = mech(profile.material(None)[0])[1].get("density", 1.25)
    rows, lines = [], []
    for vname, ov in variants.items():
        txt = P.rebuild_(pr, overrides=ov, export=False, max_output=1500)
        if not _ok(txt):
            lines.append("%s: BUILD FAILED - %s" % (vname, txt.strip().splitlines()[-1][:200]))
            continue
        d = pr.doc() or FreeCAD.ActiveDocument
        objs = _resolve_objects(d, objects)
        folder = os.path.join(pr.folder, format if format in ("stl", "step") else "stl", _safe(vname))
        try:
            ex = export(objects=[o.Name for o in objs], format=format, dir=folder, doc=d.Name)
            nfiles = len([l for l in ex.splitlines() if l.startswith(("wrote", "replaced"))])
        except Exception as e:
            ex, nfiles = "export failed: %s" % e, 0
        verdicts = {}
        if check:
            from .ready import ready
            try:
                rr = ready(objects=[o.Name for o in objs], up=up, doc=d.Name)
                for l in rr.splitlines():
                    m = re.match(r"^(\S+): (READY.*?|FIX FIRST.*?|NOT PRINTABLE.*?)\s+\[", l)
                    if m:
                        verdicts[m.group(1)] = m.group(2)
            except Exception as e:
                verdicts["*"] = "check failed: %s" % e
        for o in objs:
            s = global_shape(o)
            b = s.BoundBox
            rows.append((vname, o.Name, "%sx%sx%s" % (_f(b.XLength), _f(b.YLength), _f(b.ZLength)),
                         s.Volume / 1000.0 * dens, verdicts.get(o.Name, verdicts.get("*", "-"))))
        lines.append("%s %s: %d file(s) in %s" % (vname, ", ".join("%s=%s" % kv for kv in ov.items()), nfiles,
                                                  os.path.relpath(folder, pr.folder)))
    # back to the original values
    restore = P.rebuild_(pr, overrides=None, export=False, max_output=500)
    out = ["%s: %d variant(s) (%.1fs)" % (pr.name, len(variants), time.time() - t0)] + ["  " + l for l in lines]
    if rows:
        out.append("  variant  part                 size (mm)              grams  print check")
        for v, o, sz, g, ver in rows:
            out.append("  %-8s %-20s %-22s %-6s %s" % (v, o[:20], sz, _f(g, 0), ver))
    out.append("  project rebuilt with its original values%s" % ("" if _ok(restore) else " - REBUILD FAILED: " +
                                                              restore.splitlines()[-1][:200]))
    return "\n".join(out)


METHODS = {"variants": variants}
