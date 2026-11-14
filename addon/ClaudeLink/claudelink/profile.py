"""Print profile: the user's printer and measured per-material fit clearances.

Stored in %APPDATA%/FreeCAD/ClaudeLink/print_profile.json. Values that come from defaults (not from a
printed FitGauge) are reported as guesses. Clearances are RADIAL (per side) in mm."""
import copy
import json
import os
import time

import FreeCAD

from .server import UserError
from .snapshot import fmt

PATH = os.environ.get("CLAUDELINK_PROFILE") or os.path.join(FreeCAD.getUserAppDataDir(), "ClaudeLink", "print_profile.json")
FIT_CLASSES = ("press", "snug", "slide", "loose")
FIT_HELP = {"press": "pushed in with force, stays put",
            "snug": "goes in by hand with light force, no wobble",
            "slide": "moves freely, barely any play",
            "loose": "clear play (hinges, rotating parts)"}
# nominal sizes of the FitGauge print (see ~/Documents/FitGauge/build_fit_gauge.py)
GAUGE = {"pin": 8.0, "plate": (90.8, 55.2, 6.0)}

DEFAULT_MATERIAL = {
    "measured": False,
    "fits": {"press": 0.05, "snug": 0.10, "slide": 0.15, "loose": 0.25},
    "square_fits": None,          # if square holes behave differently: same keys; None = use "fits"
    "insert_m3": 4.0,             # heat-set insert hole diameter
    "selftap_m3": 2.7,            # self-tapping screw hole diameter
    "xy_scale": 1.0,              # measured / nominal (outer dimensions)
    "z_scale": 1.0,
    "pin_error": 0.0,             # printed pin diameter - nominal (outer feature size error)
    "notes": "",
}
DEFAULT = {
    "printer": None, "slicer": None, "bed": [256, 256, 256], "nozzle": 0.4, "layer": 0.2,
    "default_material": "PETG",
    "materials": {"PETG": copy.deepcopy(DEFAULT_MATERIAL)},
    "history": [],
}


def load():
    if os.path.exists(PATH):
        try:
            with open(PATH, encoding="utf-8") as f:
                p = json.load(f)
            out = copy.deepcopy(DEFAULT)
            out.update(p)
            for m, v in out["materials"].items():
                mm = copy.deepcopy(DEFAULT_MATERIAL)
                mm.update(v)
                out["materials"][m] = mm
            return out
        except (OSError, ValueError):
            pass
    return copy.deepcopy(DEFAULT)


def save(p):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(p, f, indent=1)


def material(name=None, p=None):
    p = p or load()
    name = name or p.get("default_material") or "PETG"
    key = next((k for k in p["materials"] if k.lower() == name.lower()), None)
    if key is None:
        return name, copy.deepcopy(DEFAULT_MATERIAL), False
    return key, p["materials"][key], True


def fit(cls="slide", mat=None, square=False):
    """Radial clearance (mm) for a fit class from the profile."""
    if cls not in FIT_CLASSES:
        raise ValueError("fit class must be one of %s" % ", ".join(FIT_CLASSES))
    _, m, _ = material(mat)
    fits = (m.get("square_fits") if square and m.get("square_fits") else m["fits"])
    return float(fits[cls])


def classify(gap, mat=None):
    """Name the fit a radial gap gives on this printer, e.g. 'snug fit (PETG, measured)'."""
    name, m, _ = material(mat)
    f = m["fits"]
    tag = "%s, %s" % (name, "measured" if m.get("measured") else "guess")
    if gap < 1e-6:
        return None
    if gap < f["press"] * 0.75:
        return "tighter than press fit - won't assemble (%s)" % tag
    if gap < (f["press"] + f["snug"]) / 2:
        return "press fit (%s)" % tag
    if gap < (f["snug"] + f["slide"]) / 2:
        return "snug fit (%s)" % tag
    if gap < (f["slide"] + f["loose"]) / 2:
        return "sliding fit (%s)" % tag
    return "loose fit (%s)" % tag


def summary(p=None):
    p = p or load()
    lines = ["print profile (%s)" % PATH]
    lines.append("printer: %s; slicer: %s; bed %s; nozzle %s; layer %s; default material %s" % (
        p.get("printer") or "?", p.get("slicer") or "?", "x".join(str(v) for v in p["bed"]), fmt(p["nozzle"]),
        fmt(p["layer"]), p["default_material"]))
    for name, m in p["materials"].items():
        st = "MEASURED" if m.get("measured") else "GUESS (print FitGauge to measure)"
        lines.append("[%s] %s" % (name, st))
        lines.append("  radial fits: " + ", ".join("%s %s" % (k, fmt(m["fits"][k])) for k in FIT_CLASSES))
        if m.get("square_fits"):
            lines.append("  square fits: " + ", ".join("%s %s" % (k, fmt(m["square_fits"][k])) for k in FIT_CLASSES))
        lines.append("  M3 insert hole %s, M3 self-tap hole %s; xy scale %s, z scale %s, pin error %s" % (
            fmt(m["insert_m3"]), fmt(m["selftap_m3"]), fmt(m["xy_scale"]), fmt(m["z_scale"]), fmt(m["pin_error"])))
        if m.get("notes"):
            lines.append("  notes: " + m["notes"])
    if p.get("history"):
        h = p["history"][-1]
        lines.append("last update: %s %s" % (h.get("date"), h.get("what")))
    return "\n".join(lines)


def _merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v


def _monotonic(fits):
    vals = [fits[k] for k in FIT_CLASSES]
    return all(a <= b for a, b in zip(vals, vals[1:]))


def print_profile(action="show", material_name=None, values=None, round=None, square=None, pin_dia=None,
                  plate=None, insert_m3=None, selftap_m3=None, notes=None):
    """show | set (merge `values` into the profile, or into a material if material_name is given) |
    record_gauge (FitGauge results: round/square = {fit class: hole label in 1/100 mm})."""
    p = load()
    if action == "show":
        return summary(p)
    if action == "set":
        if not values:
            raise UserError("set needs values")
        if material_name:
            key, m, exists = material(material_name, p)
            _merge(m, values)
            p["materials"][key] = m
            what = "set %s: %s" % (key, json.dumps(values))
        else:
            unknown = [k for k in values if k not in DEFAULT]
            if unknown:
                raise UserError("unknown profile keys %s (top level: %s)" % (unknown, ", ".join(DEFAULT)))
            _merge(p, values)
            what = "set " + json.dumps(values)
        p["history"].append({"date": time.strftime("%Y-%m-%d %H:%M"), "what": what})
        save(p)
        return "saved.\n" + summary(p)
    if action != "record_gauge":
        raise UserError("action must be show, set or record_gauge")
    key, m, exists = material(material_name, p)
    notes_out = []
    for kind, res in (("fits", round), ("square_fits", square)):
        if not res:
            continue
        bad = [k for k in res if k not in FIT_CLASSES]
        if bad:
            raise UserError("fit classes are %s (got %s)" % (", ".join(FIT_CLASSES), bad))
        fits = dict(m["fits"]) if kind == "fits" or not m.get("square_fits") else dict(m["square_fits"])
        for k, lab in res.items():
            fits[k] = float(lab) / 100.0  # labels are 1/100 mm radial
        # fill classes the user didn't report by keeping the spacing of the defaults
        if not _monotonic(fits):
            notes_out.append("warning: %s not increasing press<snug<slide<loose: %s" % (kind, fits))
        m[kind] = fits
    if pin_dia is not None:
        m["pin_error"] = round_(float(pin_dia) - GAUGE["pin"])
    if plate:
        nx, ny, nz = GAUGE["plate"]
        m["xy_scale"] = round_((float(plate[0]) / nx + float(plate[1]) / ny) / 2, 4)
        m["z_scale"] = round_(float(plate[2]) / nz, 4)
    if insert_m3 is not None:
        m["insert_m3"] = float(insert_m3)
    if selftap_m3 is not None:
        m["selftap_m3"] = float(selftap_m3)
    if notes:
        m["notes"] = notes
    m["measured"] = True
    p["materials"][key] = m
    p["history"].append({"date": time.strftime("%Y-%m-%d %H:%M"), "what": "FitGauge results for %s" % key})
    save(p)
    return "\n".join(["recorded FitGauge results for %s." % key] + notes_out + [summary(p)])


def round_(v, n=3):
    return float("%.*f" % (n, v))


METHODS = {"print_profile": print_profile}
