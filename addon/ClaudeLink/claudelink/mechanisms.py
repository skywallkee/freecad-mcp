"""Printable mechanisms sized from the print profile: involute spur gears and meshed pairs, print-in-place
pin hinges, living hinges, parallelogram flexures and compression springs - each with the engineering check
that matters for a printed part (backlash/undercut, clearances, strain, stiffness, travel)."""
import math
import time

import FreeCAD
import Part

from . import cl
from .server import UserError
from .snapshot import fmt

V = FreeCAD.Vector


def _doc(name=None):
    from .handlers import _doc as d
    return d(name)


def _f(x, n=2):
    return fmt(round(float(x), n))


def _mat():
    from . import profile
    from .strength import mech
    name = profile.material(None)[0]
    return name, mech(name)[1]


def _strain_limit(name):
    from .fasteners import SNAP_STRAIN
    return SNAP_STRAIN.get(name.upper(), 0.02)


def _show(shape, name, at, d, color=None):
    s = shape.copy()
    if at:
        s.translate(V(*at))
    o = cl.show(s, name, doc=d)
    if color and FreeCAD.GuiUp:
        try:
            o.ViewObject.ShapeColor = color
        except Exception:
            pass
    return o


# ------------------------------------------------------------------ gears

def _involute_pts(rb, r_from, r_to, n=12):
    t0 = math.sqrt(max(r_from * r_from / (rb * rb) - 1, 0.0))
    t1 = math.sqrt(max(r_to * r_to / (rb * rb) - 1, 0.0))
    out = []
    for k in range(n + 1):
        t = t0 + (t1 - t0) * k / n
        out.append((rb * (math.cos(t) + t * math.sin(t)), rb * (math.sin(t) - t * math.cos(t))))
    return out


def gear_profile(module, teeth, pressure_angle=20.0, backlash=0.0):
    """Closed 2D outline (list of (x, y), counter-clockwise) of an involute spur gear, tooth 0 centred on +X.
    backlash = circumferential play at the pitch circle (taken off each tooth)."""
    m, z = float(module), int(teeth)
    if z < 6:
        raise UserError("at least 6 teeth")
    a = math.radians(pressure_angle)
    r = m * z / 2.0
    rb = r * math.cos(a)
    ra = r + m
    rf = r - 1.25 * m
    inv_a = math.tan(a) - a
    half = math.pi / (2 * z) - backlash / (2.0 * r)        # half tooth angle at the pitch circle
    th0 = half + inv_a                                      # angle of the upper flank at the base circle
    phi_a = math.acos(rb / ra)
    if th0 - (math.tan(phi_a) - phi_a) <= 0:
        raise UserError("teeth come to a point - fewer teeth per module or less backlash")
    inv_pts = _involute_pts(rb, max(rb, rf), ra)           # angle increases with radius
    # upper flank: mirrored involute rotated to th0 (angle decreases with radius); listed tip -> root
    upper = []
    for x, y in reversed(inv_pts):
        x, y = x, -y
        upper.append((x * math.cos(th0) - y * math.sin(th0), x * math.sin(th0) + y * math.cos(th0)))
    lower = [(x, -y) for x, y in reversed(upper)]           # root -> tip
    pitch = 2 * math.pi / z
    tooth = []
    if rf < rb:
        tooth.append((rf * math.cos(-th0), rf * math.sin(-th0)))
    tooth += lower
    a_lo, a_hi = math.atan2(lower[-1][1], lower[-1][0]), math.atan2(upper[0][1], upper[0][0])
    tooth += [(ra * math.cos(a_lo + (a_hi - a_lo) * j / 4.0), ra * math.sin(a_lo + (a_hi - a_lo) * j / 4.0))
              for j in range(1, 4)]
    tooth += upper
    if rf < rb:
        tooth.append((rf * math.cos(th0), rf * math.sin(th0)))
    a_end, a_next = th0, pitch - th0
    tooth += [(rf * math.cos(a_end + (a_next - a_end) * j / 4.0), rf * math.sin(a_end + (a_next - a_end) * j / 4.0))
              for j in range(1, 4)]
    pts = []
    for k in range(z):
        c, s_ = math.cos(k * pitch), math.sin(k * pitch)
        pts += [(x * c - y * s_, x * s_ + y * c) for x, y in tooth]
    return pts, {"r": r, "ra": ra, "rf": rf, "rb": rb}


def gear_solid(module, teeth, thickness, pressure_angle=20.0, backlash=0.0, bore=0.0, dflat=False):
    pts, g = gear_profile(module, teeth, pressure_angle, backlash)
    w = Part.makePolygon([V(x, y, 0) for x, y in pts] + [V(pts[0][0], pts[0][1], 0)])
    s = Part.Face(w).extrude(V(0, 0, thickness))
    if bore:
        hole = Part.makeCylinder(bore / 2.0, thickness + 2, V(0, 0, -1))
        if dflat:
            hole = hole.cut(Part.makeBox(bore, bore, thickness + 2, V(bore / 2.0 - 0.1 * bore, -bore / 2.0, -1)))
        s = s.cut(hole)
    return s.removeSplitter(), g


# ------------------------------------------------------------------ hinge

def hinge_shapes(length=40.0, diameter=8.0, width=20.0, thickness=3.0, knuckles=5, clearance=None):
    from . import profile
    c = profile.fit("loose") if clearance is None else float(clearance)
    n = max(3, int(knuckles) | 1)              # odd: A has the outer knuckles
    R = diameter / 2.0
    rp = max(R - max(1.2, 0.25 * diameter), 1.0)
    gap = max(2 * c, 0.4)
    seg = (length - (n - 1) * gap) / n
    if seg < 2.0:
        raise UserError("knuckles too short - fewer knuckles or a longer hinge")
    ax = V(1, 0, 0)
    zc = R

    def cyl(r, x0, x1):
        return Part.makeCylinder(r, x1 - x0, V(x0, 0, zc), ax)
    a_parts, b_parts, b_env, a_env = [], [], [], []
    for k in range(n):
        x0 = k * (seg + gap)
        x1 = x0 + seg
        if k % 2 == 0:
            a_parts.append(cyl(R, x0, x1))
            a_env.append(cyl(R + c, x0 - gap / 2 - c, x1 + gap / 2 + c))
        else:
            b_parts.append(cyl(R, x0, x1).cut(cyl(rp + c, x0 - 1, x1 + 1)))
            b_env.append(cyl(R + c, x0 - gap / 2 - c, x1 + gap / 2 + c))
    pin = cyl(rp, 0, length)
    plate_a = Part.makeBox(length, width, thickness, V(0, 0, 0)).cut(Part.makeCompound(b_env))
    plate_b = Part.makeBox(length, width, thickness, V(0, -width, 0)).cut(Part.makeCompound(a_env))
    A = cl.union([plate_a] + a_parts + [pin])
    B = cl.union([plate_b] + b_parts).cut(cyl(rp + c, -1, length + 1))     # plate B must clear the pin too
    try:
        B = B.removeSplitter()
    except Exception:
        pass
    return A, B, {"clearance": c, "gap": gap, "pin_r": rp, "R": R, "knuckles": n, "seg": seg}


# ------------------------------------------------------------------ main tool

def mechanism(kind, params=None, at=None, name=None, doc=None):
    """See the MCP tool."""
    from . import profile
    p = dict(params or {})
    d = _doc(doc)
    prof = profile.load()
    nozzle, layer = prof["nozzle"], prof["layer"]
    mat, mm = _mat()
    E = mm["E"]
    t0 = time.time()
    out = []
    if kind in ("gear", "gear_pair"):
        m = float(p.get("module", 1.5))
        z1 = int(p.get("teeth", p.get("teeth1", 20)))
        th = float(p.get("thickness", 6.0))
        pa = float(p.get("pressure_angle", 20.0))
        bl = float(p.get("backlash", 2 * profile.fit("slide")))
        bore = float(p.get("bore", 0.0))
        if m < 2.5 * nozzle:
            out.append("note: module %s is small for a %s nozzle - teeth print soft; module >= %s recommended" % (
                _f(m), _f(nozzle), _f(2.5 * nozzle)))
        g1, info1 = gear_solid(m, z1, th, pa, bl, bore, bool(p.get("dflat", False)))
        nm = name or ("Gear%d" % z1 if kind == "gear" else "GearA")
        o1 = _show(g1, nm, at, d, (0.22, 0.47, 0.85))
        out.append("%s: involute spur gear m%s z%d, pitch d%s, tip d%s, root d%s, %s mm thick, backlash %s mm%s" % (
            o1.Name, _f(m), z1, _f(2 * info1["r"]), _f(2 * info1["ra"]), _f(2 * info1["rf"]), _f(th), _f(bl),
            ", bore d%s" % _f(bore) if bore else ""))
        zmin = 2 / math.sin(math.radians(pa)) ** 2
        if z1 < zmin:
            out.append("note: %d teeth < %d - undercut (weaker roots); use more teeth or a profile shift" % (z1, math.ceil(zmin)))
        if kind == "gear_pair":
            z2 = int(p.get("teeth2", 40))
            g2, info2 = gear_solid(m, z2, th, pa, bl, float(p.get("bore2", bore)))
            a = m * (z1 + z2) / 2.0
            g2.rotate(V(), V(0, 0, 1), 180.0 + 180.0 / z2)
            g2.translate(V(a, 0, 0))
            o2 = _show(g2, (name + "_B") if name else "GearB", at, d, (0.96, 0.56, 0.12))
            try:
                inter = g1.common(g2).Volume
            except Exception:
                inter = -1
            try:
                gap = g1.distToShape(g2)[0]
            except Exception:
                gap = -1
            ra1, ra2, rb1, rb2 = info1["ra"], info2["ra"], info1["rb"], info2["rb"]
            alpha = math.radians(pa)
            eps = (math.sqrt(ra1 ** 2 - rb1 ** 2) + math.sqrt(ra2 ** 2 - rb2 ** 2) - a * math.sin(alpha)) / (
                math.pi * m * math.cos(alpha))
            out.append("%s: m%s z%d meshed at centre distance %s mm (ratio %s:1, contact ratio %s)" % (
                o2.Name, _f(m), z2, _f(a), _f(z2 / float(z1)), _f(eps)))
            out.append("mesh check: interference %s mm3, closest flank gap %s mm" % (
                _f(inter, 3) if inter >= 0 else "?", _f(gap, 3) if gap >= 0 else "?"))
            if eps < 1.2:
                out.append("note: contact ratio under 1.2 - noisy/jerky; more teeth or a smaller module")
            out.append("axles: %s at the origin, %s at x=%s; print flat (teeth vertical)" % (o1.Name, o2.Name, _f(a)))
    elif kind == "hinge":
        L = float(p.get("length", 40.0))
        A, B, info = hinge_shapes(L, float(p.get("diameter", 8.0)), float(p.get("width", 20.0)),
                                  float(p.get("thickness", 3.0)), int(p.get("knuckles", 5)), p.get("clearance"))
        nm = name or "Hinge"
        oa = _show(A, nm + "_A", at, d, (0.22, 0.47, 0.85))
        ob = _show(B, nm + "_B", at, d, (0.96, 0.56, 0.12))
        try:
            inter = A.common(B).Volume
            gap = A.distToShape(B)[0]
        except Exception:
            inter, gap = -1, -1
        out.append("%s + %s: print-in-place pin hinge %s mm long, knuckle d%s x %d, pin d%s, radial clearance %s, "
                   "axial gaps %s mm" % (oa.Name, ob.Name, _f(L), _f(2 * info["R"]), info["knuckles"],
                                         _f(2 * info["pin_r"]), _f(info["clearance"]), _f(info["gap"])))
        out.append("check: interference %s mm3, min gap %s mm (%s)" % (
            _f(inter, 3), _f(gap, 3), "OK, >= loose fit" if gap >= info["clearance"] - 1e-3 else "TOO TIGHT"))
        out.append("print flat as modelled (axis horizontal); break it free by flexing once. freecad_motion "
                   "rotate={'axis': 'x', 'point': [0, 0, %s], 'from': 0, 'to': 180} checks the swing" % _f(info["R"]))
    elif kind == "living_hinge":
        L = float(p.get("length", 40.0))
        t = float(p.get("thickness", 0.5 if mat.upper() == "PP" else 0.6))
        span = float(p.get("span", 4.0))
        W = float(p.get("width", 20.0))
        T = float(p.get("plate", 3.0))
        a = Part.makeBox(L, W, T, V(0, -W - span / 2, 0))
        b = Part.makeBox(L, W, T, V(0, span / 2, 0))
        web = Part.makeBox(L, span + 2, t, V(0, -span / 2 - 1, 0))
        s = cl.union([a, b, web])
        o = _show(s, name or "LivingHinge", at, d, (0.27, 0.66, 0.32))
        ang = math.radians(float(p.get("angle", 90.0)))
        strain = t / 2.0 * ang / span           # bending a length `span` of web through `ang`
        lim = _strain_limit(mat)
        out.append("%s: living hinge %s mm long, web %s mm thick over a %s mm span between %s mm plates" % (
            o.Name, _f(L), _f(t), _f(span), _f(T)))
        out.append("bend to %s deg: web strain %s%% vs %s%% for %s -> %s" % (
            _f(math.degrees(ang), 0), _f(100 * strain, 1), _f(100 * lim, 1), mat,
            "OK" if strain <= lim else "will crack - lengthen the span to >= %s mm or thin the web" % _f(t / 2.0 * ang / lim)))
        if mat.upper() not in ("PP", "TPU", "PA", "NYLON"):
            out.append("note: %s living hinges survive tens of cycles at best; PP or nylon last thousands" % mat)
        if t < 2 * layer:
            out.append("note: web thinner than two layers (%s mm)" % _f(2 * layer))
        out.append("print flat, web on the bed so the lines run across the hinge; flex it while still warm")
    elif kind == "flexure":
        L = float(p.get("length", 20.0))
        t = float(p.get("thickness", 1.0))
        w = float(p.get("width", 10.0))
        gap = float(p.get("gap", 20.0))
        base = Part.makeBox(8, gap + 2 * t, w, V(-8, 0, 0))
        stage = Part.makeBox(8, gap + 2 * t, w, V(L, 0, 0))
        b1 = Part.makeBox(L, t, w, V(0, 0, 0))
        b2 = Part.makeBox(L, t, w, V(0, gap + t, 0))
        s = cl.union([base, stage, b1, b2])
        o = _show(s, name or "Flexure", at, d, (0.56, 0.37, 0.76))
        I = w * t ** 3 / 12.0
        k = 2 * 12 * E * I / L ** 3                     # two guided blades
        lim = _strain_limit(mat)
        dmax = lim * L * L / (3 * t)
        out.append("%s: parallelogram flexure, two %sx%sx%s mm blades %s mm apart" % (o.Name, _f(L), _f(t), _f(w), _f(gap)))
        out.append("stiffness ~%s N/mm along Y (%s, E %s MPa; beam theory - FEM of this geometry is ~10%% softer); "
                   "travel up to +-%s mm before %s%% strain" % (
            _f(k, 2), mat, _f(E, 0), _f(dmax), _f(100 * lim, 1)))
        out.append("print with the blades vertical (Z = width) so each blade is solid perimeters, not layers")
    elif kind == "spring":
        D = float(p.get("diameter", 12.0))
        a = float(p.get("wire", 1.6))
        nco = float(p.get("coils", 6))
        L = float(p.get("length", 20.0))
        pitch = (L - 2 * a) / nco
        if pitch <= a * 1.2:
            raise UserError("coils too tight: pitch %s <= 1.2 x wire %s - fewer coils or longer" % (_f(pitch), _f(a)))
        rm = D / 2.0
        prof_ = Part.makePolygon([V(rm - a / 2, 0, -a / 2), V(rm + a / 2, 0, -a / 2), V(rm + a / 2, 0, a / 2),
                                  V(rm - a / 2, 0, a / 2), V(rm - a / 2, 0, -a / 2)])
        spine = Part.Wire(Part.makeLongHelix(pitch, nco * pitch, rm - a / 2))
        ps = Part.BRepOffsetAPI.MakePipeShell(spine)
        ps.setBiNormalMode(V(0, 0, 1))
        ps.add(prof_, False, False)
        ps.build()
        ps.makeSolid()
        coil = ps.shape()
        coil = coil.Solids[0] if coil.Solids else coil
        coil.translate(V(0, 0, a))
        e = 0.03     # rings slightly wider than the coil: coincident cylinders make the fuse fail
        ends = [cl.ring(rm - a / 2 - e, rm + a / 2 + e, a, 0.0),
                cl.ring(rm - a / 2 - e, rm + a / 2 + e, a, a + nco * pitch - a / 2)]
        for r_ in ends:      # keep the ring seams away from the coil's end faces (in the XZ plane)
            r_.rotate(V(), V(0, 0, 1), 90)
        s = cl.union([coil] + ends)
        o = _show(s, name or "Spring", at, d, (0.12, 0.70, 0.76))
        G = E / (2 * (1 + mm.get("nu", 0.38)))
        k = G * a ** 4 / (5.58 * D ** 3 * nco)          # square-section wire
        travel = L - (nco + 2) * a
        out.append("%s: compression spring d%s, %s mm square wire, %s coils, free length %s (closed flat ends)" % (
            o.Name, _f(D), _f(a), _f(nco, 1), _f(s.BoundBox.ZLength)))
        out.append("rate ~%s N/mm (%s, G %s MPa; estimate - FEM gives ~15%% stiffer); solid at %s mm travel - use "
                   "<= %s mm (40%%) for a long life" % (
            _f(k, 3), mat, _f(G, 0), _f(travel), _f(0.4 * travel)))
        if mat.upper() == "PLA":
            out.append("note: PLA springs creep and lose force under constant load - PETG, nylon or TPU last longer")
        out.append("print standing up; small coils may need a slower outer wall")
    else:
        raise UserError("kind must be gear, gear_pair, hinge, living_hinge, flexure or spring")
    out.append("(%.1fs)" % (time.time() - t0))
    return "\n".join(out)


METHODS = {"mechanism": mechanism}
