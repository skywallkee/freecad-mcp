"""cl - modelling helpers preloaded in the ClaudeLink exec namespace as `cl`.

Bakes in known FreeCAD gotchas (whole-Placement assignment, Shape already includes Placement,
fillets that fail on some edges) and adds CadQuery-style selectors so code doesn't depend on
brittle names like Face12.

Selectors (cl.sel / measure refs "Obj:<selector>"):
    "Face12" / "Edge3" / "Vertex2"     exact sub-element
    "faces" / "edges" / "vertices"     element kind (default faces)
    ">Z" "<X" ...                      extreme along an axis (by centre)  e.g. "faces >Z"
    "|Z"                               faces with normal || Z (planar) / edges parallel to Z
    "#Z"                               faces with normal perpendicular to Z / edges perpendicular
    "%Cylinder" "%Plane" "%Circle"...  surface / curve type
    "r=5" "r<3" "r>10"                 radius (cylinders, spheres, cones, circles; tol 0.01)
    "z=41" "x>0" "y<-2"                centre coordinate filter (tol 0.01)
    "near(10,0,41)"                    sort by distance of centre to a point
    "[0]" "[-1]" "[0:3]"               index into the (sorted) result
  Filters combine left to right: "edges %Circle r=29.77 z>40 [0]".
"""
import math
import re

import FreeCAD as App
import Part

V = App.Vector
_AX = {"X": V(1, 0, 0), "Y": V(0, 1, 0), "Z": V(0, 0, 1)}
TOL = 0.01


# ------------------------------------------------------------------ selectors

def _shape(x):
    if hasattr(x, "Shape") and hasattr(x, "TypeId"):
        return x.Shape
    return x


def _centre(s):
    try:
        if s.ShapeType == "Vertex":
            return s.Point
        if s.ShapeType == "Face":
            return s.CenterOfMass
        if s.ShapeType == "Edge":
            return s.CenterOfMass
        return s.BoundBox.Center
    except Exception:
        return s.BoundBox.Center


def _normal(f):
    try:
        u0, u1, v0, v1 = f.ParameterRange
        return f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
    except Exception:
        return None


def _radius(s):
    g = getattr(s, "Surface", None) if s.ShapeType == "Face" else getattr(s, "Curve", None)
    r = getattr(g, "Radius", None)
    if r is None and g is not None and hasattr(g, "MajorRadius"):
        r = g.MajorRadius
    return r


def _geom_type(s):
    g = s.Surface if s.ShapeType == "Face" else getattr(s, "Curve", None)
    return type(g).__name__ if g is not None else s.ShapeType


def _edge_dir(e):
    c = getattr(e, "Curve", None)
    if isinstance(c, (Part.Line, Part.LineSegment)):
        d = e.Vertexes[-1].Point - e.Vertexes[0].Point
        return d.normalize() if d.Length > 1e-12 else None
    if isinstance(c, (Part.Circle, Part.Ellipse)):
        return None
    return None


def _axis_vec(tok):
    tok = tok.strip().upper()
    if tok in _AX:
        return _AX[tok]
    x, y, z = (float(t) for t in tok.split(","))
    return V(x, y, z).normalize()


def _cmp(op, a, b):
    return abs(a - b) <= TOL if op == "=" else (a < b - TOL if op == "<" else a > b + TOL)


def sel(target, selector="faces"):
    """List of sub-shapes of an object/shape matching a selector (see module doc)."""
    s = _shape(target)
    toks = re.findall(r"near\([^)]*\)|\[[^\]]*\]|\S+", selector.strip())
    kind = "faces"
    if toks and re.fullmatch(r"(Face|Edge|Vertex)\d+", toks[0]):
        items = [s.getElement(toks.pop(0))]
    else:
        if toks and toks[0].lower() in ("faces", "edges", "vertices", "solids", "shells", "wires"):
            kind = toks.pop(0).lower()
        items = list(getattr(s, {"faces": "Faces", "edges": "Edges", "vertices": "Vertexes",
                                 "solids": "Solids", "shells": "Shells", "wires": "Wires"}[kind]))
    for t in toks:
        if not items:
            break
        if t[0] in "<>" and len(t) > 1 and not re.match(r"[<>]-?\d", t):
            ax = _axis_vec(t[1:])
            key = lambda e: _centre(e).dot(ax)
            m = (max if t[0] == ">" else min)(key(e) for e in items)
            items = [e for e in items if abs(key(e) - m) <= TOL]
        elif t[0] in "|#":
            ax = _axis_vec(t[1:])
            out = []
            for e in items:
                if e.ShapeType == "Face":
                    if not isinstance(e.Surface, Part.Plane):
                        if t[0] == "#" and hasattr(e.Surface, "Axis"):  # cylinder etc: axis || ax -> side faces
                            if abs(abs(e.Surface.Axis.dot(ax)) - 1) < 1e-6:
                                out.append(e)
                        continue
                    n = _normal(e)
                    if n is None:
                        continue
                    d = abs(n.dot(ax))
                    if (t[0] == "|" and d > 1 - 1e-6) or (t[0] == "#" and d < 1e-6):
                        out.append(e)
                elif e.ShapeType == "Edge":
                    dvec = _edge_dir(e)
                    if dvec is None:
                        c = getattr(e, "Curve", None)
                        if t[0] == "#" and isinstance(c, Part.Circle) and abs(abs(c.Axis.dot(ax)) - 1) < 1e-6:
                            out.append(e)  # circle lying in a plane perpendicular to ax
                        continue
                    d = abs(dvec.dot(ax))
                    if (t[0] == "|" and d > 1 - 1e-6) or (t[0] == "#" and d < 1e-6):
                        out.append(e)
            items = out
        elif t.startswith("%"):
            name = t[1:].lower()
            items = [e for e in items if name in _geom_type(e).lower()]
        elif re.fullmatch(r"[rxyz][=<>]-?[\d.]+", t):
            k, op, val = t[0], t[1], float(t[2:])
            out = []
            for e in items:
                if k == "r":
                    r = _radius(e)
                    if r is not None and _cmp(op, r, val):
                        out.append(e)
                else:
                    c = _centre(e)
                    if _cmp(op, getattr(c, k), val):
                        out.append(e)
            items = out
        elif t.startswith("near("):
            p = V(*(float(x) for x in t[5:-1].split(",")))
            items = sorted(items, key=lambda e: (_centre(e) - p).Length)
        elif t.startswith("["):
            inner = t[1:-1]
            if ":" in inner:
                a, b = (int(x) if x.strip() else None for x in inner.split(":"))
                items = items[a:b]
            else:
                i = int(inner)
                items = [items[i]] if -len(items) <= i < len(items) else []
        else:
            raise ValueError("bad selector token %r in %r" % (t, selector))
    return items


def sel1(target, selector):
    """Exactly one sub-shape; raises with the match count otherwise."""
    r = sel(target, selector)
    if len(r) != 1:
        raise ValueError("selector %r matched %d elements (need 1; add [0] or more filters)" % (selector, len(r)))
    return r[0]


def name_of(shape, sub):
    """'Face12' style name of a sub-shape within shape (or None)."""
    kind = {"Face": "Faces", "Edge": "Edges", "Vertex": "Vertexes"}.get(sub.ShapeType)
    if not kind:
        return None
    for i, e in enumerate(getattr(shape, kind), 1):
        if e.isSame(sub):
            return "%s%d" % (sub.ShapeType, i)
    return None


# ------------------------------------------------------------------ placement / objects

def placement(pos=(0, 0, 0), axis=(0, 0, 1), angle=0.0, ypr=None):
    """Build a Placement. angle in degrees about axis, or ypr=(yaw, pitch, roll) in degrees."""
    rot = App.Rotation(*ypr) if ypr is not None else App.Rotation(V(*axis), angle)
    return App.Placement(V(*pos), rot)


def place(obj, pos=None, axis=(0, 0, 1), angle=None, ypr=None):
    """Assign a whole new Placement (editing obj.Placement.Base.z only edits a copy)."""
    p = obj.Placement
    base = V(*pos) if pos is not None else p.Base
    if ypr is not None:
        rot = App.Rotation(*ypr)
    elif angle is not None:
        rot = App.Rotation(V(*axis), angle)
    else:
        rot = p.Rotation
    obj.Placement = App.Placement(base, rot)
    return obj


def show(shape, name, color=None, transparency=None, doc=None, replace=True, placement_=None):
    """Put a shape in the document as Part::Feature `name`. replace=True updates the existing
    object in place (keeps the name stable and dependents intact) instead of adding Name001."""
    doc = doc or App.ActiveDocument or App.newDocument()
    o = doc.getObject(name) if replace else None
    if o is None or o.TypeId != "Part::Feature":
        if o is not None:
            doc.removeObject(name)
        o = doc.addObject("Part::Feature", name)
    o.Shape = shape
    if placement_ is not None:
        o.Placement = placement_
    if App.GuiUp:
        if color is not None:
            o.ViewObject.ShapeColor = tuple(color)
        if transparency is not None:
            o.ViewObject.Transparency = int(transparency)
    o.purgeTouched()
    return o


# ------------------------------------------------------------------ primitives

def polygon(points, z=0.0, close=True):
    """Wire through 2D (x, y) or 3D points."""
    pts = [V(p[0], p[1], p[2] if len(p) > 2 else z) for p in points]
    if close and (pts[0] - pts[-1]).Length > 1e-9:
        pts.append(pts[0])
    return Part.makePolygon(pts)


def extrude(points, h, z0=0.0):
    """Prism from a closed 2D (x, y) polygon, from z0 up by h."""
    f = Part.Face(polygon(points, z0))
    return f.extrude(V(0, 0, h))


def revolve(profile, angle=360.0, axis=(0, 0, 1), centre=(0, 0, 0)):
    """Solid of revolution from a closed (r, z) profile in the XZ plane, about Z by default."""
    w = polygon([(r, 0, z) for r, z in profile])
    return Part.Face(w).revolve(V(*centre), V(*axis), angle)


def ring(ri, ro, h, z0=0.0):
    """Tube/washer: inner radius ri, outer ro, height h from z0."""
    o = Part.makeCylinder(ro, h, V(0, 0, z0))
    return o.cut(Part.makeCylinder(ri, h, V(0, 0, z0))) if ri > 0 else o


def polar(shape, n, axis=(0, 0, 1), centre=(0, 0, 0), start=0.0, fuse=True):
    """n copies of shape rotated evenly about an axis (start angle in degrees)."""
    copies = []
    for i in range(n):
        c = shape.copy()
        c.rotate(V(*centre), V(*axis), start + 360.0 * i / n)
        copies.append(c)
    return union(copies) if fuse else Part.makeCompound(copies)


def union(shapes):
    """Fuse a list of shapes and clean coplanar seams."""
    shapes = [s for s in shapes if s is not None]
    if not shapes:
        return None
    r = shapes[0].fuse(shapes[1:]) if len(shapes) > 1 else shapes[0]
    try:
        r = r.removeSplitter()
    except Exception:
        pass
    return r


def cut(base, tools):
    """base minus one shape or a list of shapes, cleaned."""
    tools = tools if isinstance(tools, (list, tuple)) else [tools]
    r = base.cut(tools) if len(tools) > 1 else base.cut(tools[0])
    try:
        r = r.removeSplitter()
    except Exception:
        pass
    return r


def fillet(shape, edges, r, fallback=True):
    """Fillet edges (list of edges or a selector string). With fallback, tries all edges at once,
    then one by one, skipping failures. Returns (shape, report)."""
    return _round(shape, edges, r, "makeFillet", fallback)


def chamfer(shape, edges, d, fallback=True):
    """Chamfer like fillet(); returns (shape, report)."""
    return _round(shape, edges, d, "makeChamfer", fallback)


def _round(shape, edges, r, meth, fallback):
    if isinstance(edges, str):
        edges = sel(shape, edges if edges.split()[0] == "edges" else "edges " + edges)
    if not edges:
        return shape, "no edges matched"
    try:
        out = getattr(shape, meth)(r, edges)
        if out.isValid():
            return out, "ok: %d edges" % len(edges)
    except Exception:
        pass
    if not fallback:
        raise RuntimeError("%s failed on %d edges" % (meth, len(edges)))
    cur, ok, bad = shape, 0, 0
    for e in edges:
        try:
            # edge identity changes after each op; re-find it by centre
            c = _centre(e)
            cands = sorted(cur.Edges, key=lambda x: (_centre(x) - c).Length)
            nxt = getattr(cur, meth)(r, [cands[0]])
            if nxt.isValid():
                cur, ok = nxt, ok + 1
                continue
        except Exception:
            pass
        bad += 1
    return cur, "partial: %d ok, %d failed" % (ok, bad)


# ------------------------------------------------------------------ print profile

def fit(cls="slide", material=None, square=False):
    """Radial clearance (mm) for a fit class on the user's printer: press, snug, slide or loose.
    Measured with the FitGauge print when available (see profile())."""
    from . import profile as _p
    return _p.fit(cls, material, square)


def hole(kind, material=None):
    """Printed hole diameter for hardware: 'insert_m3' (heat-set insert) or 'selftap_m3'."""
    from . import profile as _p
    _, m, _ = _p.material(material)
    if kind not in ("insert_m3", "selftap_m3"):
        raise ValueError("kind must be insert_m3 or selftap_m3")
    return float(m[kind])


def profile():
    """Printer/material profile summary (bed, nozzle, measured fits)."""
    from . import profile as _p
    return _p.summary()


# ------------------------------------------------------------------ checks

def check(shape):
    """One-line validity summary for a shape or object."""
    s = _shape(shape)
    parts = ["valid" if s.isValid() else "INVALID", "%d solids" % len(s.Solids)]
    if s.Solids:
        parts.append("vol %.3f" % s.Volume)
    open_shells = [sh for sh in s.Shells if not sh.isClosed()]
    if open_shells:
        parts.append("%d open shells" % len(open_shells))
    b = s.BoundBox
    parts.append("bb %.2fx%.2fx%.2f" % (b.XLength, b.YLength, b.ZLength))
    return ", ".join(parts)


def help():
    """List the cl helpers."""
    import inspect as _i
    out = []
    for n, f in sorted(globals().items()):
        if callable(f) and not n.startswith("_") and getattr(f, "__module__", None) == __name__:
            doc = (f.__doc__ or "").strip().splitlines()
            out.append("cl.%s%s - %s" % (n, _i.signature(f), doc[0] if doc else ""))
    return "\n".join(out) + "\n\nSelectors" + __doc__.split("Selectors", 1)[1]
