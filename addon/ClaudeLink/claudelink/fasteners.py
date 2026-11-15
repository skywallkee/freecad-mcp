"""Printable threads and fasteners: ISO metric threads, screw holes (counterbore / countersink),
nut traps, heat-set insert holes and bosses, self-tap holes, cantilever snap hooks.

Every builder works in a local frame (+Z = into the material from the surface point `at`) and is then
placed with (at, axis). Clearances come from the print profile, so holes fit on the user's printer."""
import math

import FreeCAD
import Part

from .server import UserError

V = FreeCAD.Vector

# size: (major diameter, coarse pitch)
METRIC = {"M2": (2.0, 0.4), "M2.5": (2.5, 0.45), "M3": (3.0, 0.5), "M4": (4.0, 0.7), "M5": (5.0, 0.8),
          "M6": (6.0, 1.0), "M8": (8.0, 1.25), "M10": (10.0, 1.5), "M12": (12.0, 1.75), "M16": (16.0, 2.0),
          "M20": (20.0, 2.5), "M24": (24.0, 3.0)}
NUT = {"M2": (4.0, 1.6), "M2.5": (5.0, 2.0), "M3": (5.5, 2.4), "M4": (7.0, 3.2), "M5": (8.0, 4.0),
       "M6": (10.0, 5.0), "M8": (13.0, 6.5), "M10": (16.0, 8.0), "M12": (18.0, 10.0)}   # across flats, height
SOCKET_HEAD = {"M2": (3.8, 2.0), "M2.5": (4.5, 2.5), "M3": (5.5, 3.0), "M4": (7.0, 4.0), "M5": (8.5, 5.0),
               "M6": (10.0, 6.0), "M8": (13.0, 8.0), "M10": (16.0, 10.0), "M12": (18.0, 12.0)}
CSK_HEAD = {"M2": 4.4, "M2.5": 5.5, "M3": 6.72, "M4": 8.96, "M5": 11.2, "M6": 13.44, "M8": 17.92, "M10": 22.4,
            "M12": 26.88}
CLEARANCE = {"M2": 2.4, "M2.5": 2.9, "M3": 3.4, "M4": 4.5, "M5": 5.5, "M6": 6.6, "M8": 9.0, "M10": 11.0,
             "M12": 13.5, "M16": 17.5, "M20": 22.0, "M24": 26.0}
INSERT = {"M2": (3.2, 3.0), "M2.5": (3.6, 4.0), "M3": (4.0, 5.7), "M4": (5.6, 8.1), "M5": (6.4, 9.5),
          "M6": (8.0, 12.7), "M8": (9.6, 12.7)}      # hole diameter, insert length (common short inserts)
SELFTAP = {"M2": 1.7, "M2.5": 2.1, "M3": 2.5, "M4": 3.4, "M5": 4.3, "M6": 5.2}
# permissible strain for a snap fit that is assembled a few times (typical values for printed parts)
SNAP_STRAIN = {"PLA": 0.015, "PETG": 0.025, "ABS": 0.025, "ASA": 0.025, "PA": 0.04, "NYLON": 0.04, "PC": 0.03,
               "PLA-CF": 0.01, "PETG-CF": 0.012, "TPU": 0.3}


def _size(size):
    s = str(size).upper().replace(" ", "")
    if not s.startswith("M"):
        s = "M" + s
    if s not in METRIC:
        raise UserError("unknown size %r; use %s" % (size, ", ".join(METRIC)))
    return s


def _prof_fit(cls, material=None):
    from . import profile
    return profile.fit(cls, material)


def _axis(axis):
    if axis is None:
        return V(0, 0, -1)
    if isinstance(axis, str):
        a = axis.strip().lower()
        sign = -1.0 if a.startswith("-") else 1.0
        a = a.lstrip("+-")
        if a not in ("x", "y", "z"):
            raise UserError("axis must be x/y/z with optional sign or [x,y,z]")
        return V(*[sign if c == a else 0.0 for c in "xyz"])
    v = V(*axis)
    if v.Length < 1e-9:
        raise UserError("zero axis")
    return v.normalize()


def place(shape, at=(0, 0, 0), axis="-z", xdir=None):
    """Move a shape built in the local frame (+Z into the material) to point `at`, +Z -> axis."""
    z = _axis(axis)
    if xdir is not None:
        x = _axis(xdir)
        x = (x - z * x.dot(z))
    else:
        x = V(1, 0, 0) if abs(z.x) < 0.9 else V(0, 1, 0)
        x = x - z * x.dot(z)
    x.normalize()
    y = z.cross(x)
    m = FreeCAD.Matrix(x.x, y.x, z.x, 0, x.y, y.y, z.y, 0, x.z, y.z, z.z, 0, 0, 0, 0, 1)
    s = shape.copy()
    s.transformShape(m, True)
    s.translate(V(*at))
    return s


def _cyl(r, z0, z1):
    return Part.makeCylinder(r, z1 - z0, V(0, 0, z0))


def _cone(r0, r1, z0, z1):
    return Part.makeCone(r0, r1, z1 - z0, V(0, 0, z0))


# ------------------------------------------------------------------ threads

def _helix_thread(r_root, r_crest, pitch, length):
    """Thread ridge (truncated 60-deg profile) swept along a right-hand helix, z from -pitch to length+pitch."""
    h = r_crest - r_root
    wc = pitch / 8.0
    wr = min(wc + 2 * h * math.tan(math.radians(30)), pitch * 0.92)
    r0 = r_root - 0.1 * pitch        # overlap the core so the fuse merges cleanly
    pts = [V(r0, 0, -wr / 2), V(r_crest, 0, -wc / 2), V(r_crest, 0, wc / 2), V(r0, 0, wr / 2)]
    prof = Part.makePolygon(pts + [pts[0]])
    # one BSpline per turn (makeLongHelix) + fixed binormal along the axis: makeHelix's single long
    # approximation and Frenet sweeps give solids that OCC booleans silently fail on
    spine = Part.Wire(Part.makeLongHelix(pitch, length + 2 * pitch, r0))
    ps = Part.BRepOffsetAPI.MakePipeShell(spine)
    ps.setBiNormalMode(V(0, 0, 1))
    ps.add(prof, False, False)
    ps.build()
    ps.makeSolid()
    sweep = ps.shape()
    sweep = sweep.Solids[0] if sweep.Solids else sweep
    if not sweep.isValid() or sweep.Volume <= 0:
        raise UserError("thread sweep failed")
    sweep.translate(V(0, 0, -pitch))
    return sweep


def thread_rod(size="M8", length=10.0, fit="slide", material=None, pitch=None, chamfer=True):
    """External ISO metric thread (local frame, z 0..length), major diameter reduced by the fit clearance."""
    s = _size(size)
    D, P = METRIC[s]
    P = float(pitch or P)
    c = _prof_fit(fit, material) if fit else 0.0
    H = 0.8660254 * P
    r_crest = D / 2 - c
    r_root = D / 2 - 0.625 * H - c                 # ISO minor of the bolt (5/8 H below major)
    rod = _cyl(r_root + 0.01, 0, length).fuse(_helix_thread(r_root, r_crest, P, length))
    env_pts = [(0, 0), (r_crest - 0.6 * H, 0), (r_crest + 0.01, 0.6 * H), (r_crest + 0.01, length - 0.6 * H),
               (r_crest - 0.6 * H, length), (0, length)] if chamfer else \
        [(0, 0), (r_crest + 0.01, 0), (r_crest + 0.01, length), (0, length)]
    from . import cl
    out = rod.common(cl.revolve(env_pts))
    try:
        out = out.removeSplitter()
    except Exception:
        pass
    return out


def tapped_cutter(size="M8", depth=10.0, fit="slide", material=None, pitch=None, through=False):
    """Cutter for an internal ISO thread (local frame: surface at z=0, into +z), grown by the fit clearance,
    with a 90-deg entry countersink so the first turn prints cleanly."""
    s = _size(size)
    D, P = METRIC[s]
    P = float(pitch or P)
    c = _prof_fit(fit, material) if fit else 0.0
    H = 0.8660254 * P
    r_crest = D / 2 + c + 0.125 * H * 0.5          # nut major slightly open (root clearance)
    r_root = D / 2 - 0.625 * H + c                 # nut minor (= bolt minor + clearance + 1/4 H truncation)
    r_root = max(r_root, D / 2 - 0.541 * P + c)
    L = depth + (1.0 if through else 0.0)
    core = _cyl(r_root, -1.0, L)
    th = _helix_thread(r_root - 0.02, r_crest, P, L)
    th = th.common(_cyl(r_crest + 1, -0.5, L))
    cut = core.fuse(th)
    cut = cut.fuse(_cone(r_crest + 0.4, 0.0, -0.4, r_crest))   # entry countersink
    try:
        cut = cut.removeSplitter()
    except Exception:
        pass
    return cut


# ------------------------------------------------------------------ holes

def screw_hole_cutter(size="M3", depth=10.0, head="socket", head_depth=None, fit="slide", material=None):
    """Clearance hole with counterbore (head='socket'), 90-deg countersink ('countersunk') or none."""
    s = _size(size)
    c = _prof_fit(fit, material) if fit else 0.0
    rd = CLEARANCE[s] / 2 + c * 0.5
    cut = _cyl(rd, -1.0, depth + 0.01)
    if head in ("socket", "counterbore", "cap"):
        dk, k = SOCKET_HEAD.get(s, (METRIC[s][0] * 1.7, METRIC[s][0]))
        hd = k + 0.2 if head_depth is None else float(head_depth)
        cut = cut.fuse(_cyl(dk / 2 + 0.3 + c, -1.0, hd))
    elif head in ("countersunk", "csk", "flat"):
        dk = CSK_HEAD.get(s, METRIC[s][0] * 2.24) + 2 * c
        hd = 0.0 if head_depth is None else float(head_depth)
        cut = cut.fuse(_cone(dk / 2, 0.0, hd, hd + dk / 2)).fuse(_cyl(dk / 2, -1.0, hd + 0.001))
    elif head not in (None, "none"):
        raise UserError("head must be socket, countersunk or none")
    return cut.removeSplitter()


def nut_trap_cutter(size="M3", depth=None, fit="slide", material=None, slot=0.0, at_depth=0.0):
    """Hex pocket for a nut (flats +clearance), optional side slot of length `slot` along local +X so the nut
    slides in from the side; at_depth > 0 buries it below the surface (captive, insert during the print)."""
    s = _size(size)
    if s not in NUT:
        raise UserError("no nut size for %s" % s)
    sf, m = NUT[s]
    c = _prof_fit(fit, material) if fit else 0.0
    af = sf + 2 * c
    ro = af / math.sqrt(3)            # circumradius from across-flats
    h = (m + 0.3) if depth is None else float(depth)
    z0 = float(at_depth)
    pts = [V(ro * math.cos(math.radians(60 * k)), ro * math.sin(math.radians(60 * k)), 0) for k in range(6)]
    hexf = Part.Face(Part.makePolygon(pts + [pts[0]]))
    hexf.translate(V(0, 0, z0 if z0 > 0 else -1.0))
    cut = hexf.extrude(V(0, 0, h + (0 if z0 > 0 else 1.0)))
    if slot:
        cut = cut.fuse(Part.makeBox(float(slot), af, h, V(0, -af / 2, z0 if z0 > 0 else 0)))
    return cut.removeSplitter()


def insert_hole_cutter(size="M3", depth=None, material=None):
    """Hole for a heat-set insert (diameter from the profile for M3, else typical values), insert length +1 mm
    deep, with a small lead-in chamfer."""
    s = _size(size)
    if s not in INSERT:
        raise UserError("no insert size for %s" % s)
    d, L = INSERT[s]
    if s == "M3":
        from . import profile
        _, m, _ = profile.material(material)
        d = float(m.get("insert_m3", d))
    h = (L + 1.0) if depth is None else float(depth)
    r = d / 2
    return _cyl(r, -1.0, h).fuse(_cone(r + 0.4, r, -0.001, 0.4)).removeSplitter(), d, L


def insert_boss(size="M3", height=8.0, material=None):
    """Solid boss standing out of the surface (local frame z from -height to 0, i.e. against the axis) with a
    >= 1.6 mm wall round the insert hole; fuse it, then cut insert_hole_cutter at its top."""
    _, d, L = insert_hole_cutter(size, None, material)
    ro = d / 2 + max(1.6, d * 0.45)
    return _cyl(ro, -float(height), 0.0)


def selftap_cutter(size="M3", depth=10.0, material=None):
    """Pilot hole for a screw cut straight into the plastic (profile value for M3, scaled for other sizes)."""
    s = _size(size)
    d = SELFTAP.get(s, METRIC[s][0] * 0.85)
    from . import profile
    _, m, _ = profile.material(material)
    if m.get("selftap_m3"):
        d = d * float(m["selftap_m3"]) / SELFTAP["M3"]
    return _cyl(d / 2, -1.0, float(depth)).fuse(_cone(d / 2 + 0.4, d / 2, -0.001, 0.4)).removeSplitter(), d


# ------------------------------------------------------------------ snap fit

def snap_hook(length=12.0, thickness=1.6, width=6.0, deflection=1.0, material=None, retain_deg=90.0):
    """Cantilever snap hook in the local frame: beam along +Z from its root at z=0, thickness along +Y, width
    along X, hook at the tip protruding along +Y by `deflection` with a 30-deg lead-in. retain_deg 90 = permanent,
    ~45 = can be pulled apart. Returns (solid, info): strain vs the material's permissible strain, push-in force."""
    from .strength import mech
    from . import profile
    name, _, _ = profile.material(material)
    _, mm, _ = mech(name)
    L, t, w, y = float(length), float(thickness), float(width), float(deflection)
    hl = max(y / math.tan(math.radians(30)) * 1.15, 1.5 * t)
    # beam along +Z (into the mating part), thickness along Y, width along X
    beam = Part.makeBox(w, t, L, V(-w / 2, 0, 0))
    rd = max(20.0, min(90.0, float(retain_deg)))
    xr = y / math.tan(math.radians(rd)) if rd < 89.9 else 0.0
    # hook profile in the YZ plane at the tip: retaining face at z = L - hl, lead-in down to the tip
    pts = [V(0, t, L - hl), V(0, t + y, L - hl + xr), V(0, t + y, L - hl + xr + 0.25 * hl), V(0, t, L)]
    face = Part.Face(Part.makePolygon(pts + [pts[0]]))
    face.translate(V(-w / 2, 0, 0))
    hook = beam.fuse(face.extrude(V(w, 0, 0))).removeSplitter()
    E = mm["E"]
    strain = 1.5 * t * y / (L * L)
    allow = SNAP_STRAIN.get(name.upper(), 0.02)
    P = w * t * t * E * strain / (6.0 * L)                  # deflection force, N (E in MPa)
    mu, a = 0.3, math.radians(30)
    W = P * (mu + math.tan(a)) / (1 - mu * math.tan(a))     # push-in force
    Lmin = math.sqrt(1.5 * t * y / allow)
    info = {"strain": strain, "allow": allow, "material": name, "force": P, "push": W, "L_min": Lmin,
            "hook_len": hl}
    return hook, info


def snap_report(info):
    ok = info["strain"] <= info["allow"]
    s = "snap hook strain %.2f%% vs %.1f%% allowed for %s -> %s; push-in ~%.1f N" % (
        info["strain"] * 100, info["allow"] * 100, info["material"],
        "OK" if ok else "TOO STIFF - it will crack; make it >= %.1f mm long or thinner" % info["L_min"], info["push"])
    s += "; print with the beam lying flat (layers along its length) and add a ~0.5 mm root fillet"
    return s


# ------------------------------------------------------------------ tool: apply to an object

KINDS = ("screw_hole", "tapped_hole", "thread", "nut_trap", "insert", "insert_boss", "selftap", "snap")
OUTWARD = ("thread", "snap")      # these grow out of the surface along `axis`


def _resolve_at(shape, at, kind):
    """at = [x,y,z] or a selector on the object -> (point, default axis)."""
    from . import cl
    if isinstance(at, (list, tuple)) and len(at) == 3 and not isinstance(at[0], str):
        return V(*at), None
    if not isinstance(at, str):
        raise UserError("at must be [x,y,z] or a selector like 'faces >Z' / 'edges %Circle z=10 [0]'")
    try:
        subs = cl.sel(shape, at)
    except ValueError as e:
        raise UserError(str(e))
    if not subs:
        raise UserError("at %r matched nothing" % at)
    s = subs[0]
    if s.ShapeType == "Face":
        u0, u1, v0, v1 = s.ParameterRange
        p = s.CenterOfMass
        try:
            n = s.normalAt(*s.Surface.parameter(p))
        except Exception:
            n = s.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
        if hasattr(s.Surface, "Axis") and s.Surface.__class__.__name__ in ("Cylinder", "Cone"):
            raise UserError("pick a planar face or a circular edge for `at`, not a cylinder face")
        return p, -V(n).normalize()
    if s.ShapeType == "Edge" and hasattr(s.Curve, "Center"):
        c, ax = V(s.Curve.Center), V(s.Curve.Axis).normalize()
        # point the axis into the material
        if shape.isInside(c + ax * 0.3, 1e-6, True) or not shape.isInside(c - ax * 0.3, 1e-6, True):
            return c, ax
        return c, -ax
    if s.ShapeType == "Vertex":
        return s.Point, None
    return s.CenterOfMass, None


def fastener(object, kind, at, axis=None, size="M3", depth=None, length=None, head="socket", fit=None,
             slot=0.0, slot_dir=None, buried=0.0, height=None, hook=None, thickness=1.6, width=6.0,
             deflection=1.0, retain=90.0, material=None, name=None, doc=None):
    """Add a fastener feature to an object (cut or fuse), in place for a Part::Feature (one undo step)
    or into a new object. See the MCP tool for parameters."""
    from .analysis import global_shape
    from .handlers import _doc as _d, _obj as _o
    from . import profile
    if kind not in KINDS:
        raise UserError("kind must be one of %s" % ", ".join(KINDS))
    d = _d(doc)
    o = _o(d, object)
    base = global_shape(o)
    if not base.Placement.isIdentity():
        m = base.Placement.toMatrix()
        base.Placement = FreeCAD.Placement()
        base.transformShape(m, True)
    p, ax0 = _resolve_at(base, at, kind)
    if axis is not None:
        axv = _axis(axis)
    elif ax0 is not None:
        axv = -ax0 if kind in OUTWARD else ax0
    else:
        raise UserError("give axis (direction into the material, or out of it for thread/snap) when at is a point")
    mat = material or profile.material(None)[0]
    fitc = fit or ("slide" if kind in ("thread", "tapped_hole", "nut_trap", "screw_hole") else None)
    notes = []
    op = "cut"
    tag = ""
    if kind == "screw_hole":
        dd = float(depth) if depth else max(base.BoundBox.DiagonalLength, 10)
        tool = place(screw_hole_cutter(size, dd, head, None, fitc, mat), p, axv)
        s_ = _size(size)
        tag = "%s clearance hole d%s%s, %s" % (s_, _f(CLEARANCE[s_]), {"socket": " + counterbore", "countersunk": " + 90-deg countersink"}.get(head, ""), "through" if not depth else _f(dd) + " mm deep")
    elif kind == "tapped_hole":
        dd = float(depth or 2 * METRIC[_size(size)][0])
        tool = place(tapped_cutter(size, dd, fitc, mat), p, axv)
        tag = "%s internal thread %s mm deep (+%s mm %s clearance)" % (_size(size), _f(dd), _f(profile.fit(fitc, mat)), fitc)
        if METRIC[_size(size)][0] < 5:
            notes.append("printed threads below M5 are weak and rough - a heat-set insert (kind='insert') or self-tap hole holds better")
    elif kind == "thread":
        L = float(length or depth or 2 * METRIC[_size(size)][0])
        tool = place(thread_rod(size, L, fitc, mat), p - axv * 0.01, axv)
        op = "fuse"
        tag = "%s external thread %s mm long (major -%s mm %s clearance)" % (_size(size), _f(L), _f(profile.fit(fitc, mat)), fitc)
        if METRIC[_size(size)][0] < 6:
            notes.append("external threads below M6 snap easily when printed; print the rod lying down only if it is >= M10")
    elif kind == "nut_trap":
        sd = slot_dir if slot_dir is not None else None
        tool = place(nut_trap_cutter(size, depth, fitc, mat, float(slot or 0), float(buried or 0)), p, axv, sd)
        sf, mh = NUT[_size(size)]
        tag = "%s nut trap %s across flats +%s, %s deep%s%s" % (
            _size(size), _f(sf), _f(2 * profile.fit(fitc, mat)), _f(float(depth) if depth else mh + 0.3),
            ", side slot %s mm" % _f(slot) if slot else "", ", buried %s mm (pause the print to drop the nut in)" % _f(buried) if buried else "")
    elif kind == "insert":
        cut_, dh, L = insert_hole_cutter(size, depth, mat)
        tool = place(cut_, p, axv)
        tag = "%s heat-set insert hole d%s, %s deep (insert %s mm long)" % (_size(size), _f(dh), _f(float(depth) if depth else L + 1), _f(L))
    elif kind == "insert_boss":
        h = float(height or 8.0)
        boss = place(insert_boss(size, h, mat), p, axv)
        base = base.fuse(boss)
        try:
            base = base.removeSplitter()
        except Exception:
            pass
        cut_, dh, L = insert_hole_cutter(size, depth, mat)
        tool = place(cut_, p - axv * h, axv)
        tag = "%s insert boss %s mm tall with a d%s insert hole" % (_size(size), _f(h), _f(dh))
    elif kind == "selftap":
        dd = float(depth or 2.5 * METRIC[_size(size)][0])
        cut_, dh = selftap_cutter(size, dd, mat)
        tool = place(cut_, p, axv)
        tag = "%s self-tap pilot hole d%s, %s deep" % (_size(size), _f(dh), _f(dd))
    else:  # snap
        L = float(length or 12.0)
        hk, info = snap_hook(L, thickness, width, deflection, mat, retain)
        if hook is None:
            hv = V(1, 0, 0) if abs(axv.x) < 0.9 else V(0, 1, 0)
        else:
            hv = _axis(hook)
        hv = hv - axv * hv.dot(axv)
        if hv.Length < 1e-6:
            raise UserError("hook direction must not be parallel to the beam axis")
        hv.normalize()
        tool = place(hk, p - axv * 0.01, axv, hv.cross(axv))
        op = "fuse"
        tag = "snap hook %s long, %s thick, %s wide, %s mm catch" % (_f(L), _f(thickness), _f(width), _f(deflection))
        notes.append(snap_report(info))
    v0 = base.Volume
    res = base.cut(tool) if op == "cut" else base.fuse(tool)
    try:
        res = res.removeSplitter()
    except Exception:
        pass
    if not res.isValid() or not res.Solids:
        raise UserError("the %s operation gave an invalid shape - try another position/size" % op)
    if op == "fuse" and len(res.Solids) > len(base.Solids):
        notes.append("the new feature does not touch the part (check at/axis)")
    if op == "cut" and abs(res.Volume - v0) < 1e-6:
        notes.append("nothing was removed - the feature misses the part (check at/axis)")
    # write back
    from .output import auto_checkpoint
    ck = auto_checkpoint(d, "before fastener " + o.Name)
    if ck:
        notes.append(ck)
    d.openTransaction("fastener")
    try:
        if name is None and o.TypeId == "Part::Feature":
            gp = o.getGlobalPlacement()
            r2 = res.copy()
            r2.transformShape(gp.inverse().toMatrix(), True)
            r2.Placement = o.Placement      # a Part::Feature takes its Placement from the shape
            o.Shape = r2
            target = o.Name
        else:
            from . import cl
            no = cl.show(res, name or (o.Name + "_f"), doc=d)
            target = no.Name
            if FreeCAD.GuiUp and no.Name != o.Name:
                try:
                    o.ViewObject.Visibility = False
                except Exception:
                    pass
        d.recompute()
    finally:
        d.commitTransaction()
    out = ["%s: %s at %s, axis %s -> %s (vol %s -> %s mm3, %s)" % (
        o.Name, tag, _vstr(p), _vstr(axv), target, _f(round(v0, 1)), _f(round(res.Volume, 1)),
        "valid" if res.isValid() else "INVALID")]
    out += ["note: " + n for n in notes]
    fit_m = profile.material(mat)[1]
    if fitc and not fit_m.get("measured"):
        out.append("clearances are profile guesses until the FitGauge is printed")
    return "\n".join(out)


def _f(x):
    from .snapshot import fmt
    return fmt(round(float(x), 2))


def _vstr(v):
    return "(%s)" % ", ".join(_f(round(c, 3)) for c in (v.x, v.y, v.z))


METHODS = {"fastener": fastener}
