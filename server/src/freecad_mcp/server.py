"""FreeCAD MCP server: thin, token-frugal tools over a live FreeCAD session (ClaudeLink addon)."""
import asyncio
import base64
from typing import Literal

from mcp.server.fastmcp import FastMCP, Image

from . import client
from .client import LinkError, call

INSTRUCTIONS = """Drives the user's live FreeCAD 1.0 GUI. Never launch FreeCAD yourself; if it is not
reachable, ask the user to start it.
Workflow: freecad_status -> build with freecad_exec / freecad_run_file -> read the change summary
(it lists added/changed objects with bbox, volume, validity and recompute problems) -> freecad_view
once to check visually -> fix -> save with freecad_doc.
- freecad_exec keeps one persistent namespace (App, Gui, Part, V=App.Vector, math, step() preloaded);
  the value of a trailing expression is returned. Each call is one undo step; on error the changes
  are rolled back unless rollback_on_error=false.
- Prefer freecad_run_file with overrides for parametric build scripts instead of pasting code.
- `cl` helpers are preloaded (cl.help() lists them): selectors cl.sel(obj, "edges %Circle z>40"),
  cl.show(shape, name) (replaces in place), cl.place, cl.revolve, cl.ring, cl.polar, cl.union,
  cl.cut, cl.fillet/chamfer with per-edge fallback, cl.check.
- Verify, don't eyeball: freecad_check_geometry after booleans, freecad_fit for assemblies,
  freecad_section through mating features for real clearances, freecad_measure for critical
  dimensions, freecad_printability before exporting parts to print.
- Printing: freecad_orient to pick the orientation, freecad_print_prep for the print copy with
  teardrops, bottom chamfer and engineered snap-off supports; export <obj>_print + supports together.
  freecad_strength for loaded parts (layer-aware FEM), freecad_split when a part is bigger than the
  bed, freecad_color_split for single-colour pieces that assemble without glue, freecad_motion for
  hinges/lids/sliders and print-in-place clearance.
- Fasteners: freecad_fastener (screw holes, printed threads, nut traps, heat-set inserts, snap hooks;
  cl.thread/tapped_hole/screw_hole/nut_trap/insert_hole/selftap_hole/snap_hook in code).
- Docs: freecad_illustrate for parts + tools sheets, LEGO-style assembly steps, product views and
  cutaways (PNG pages in the project's images/ folder).
- Projects (~/Documents/<Name> with build_*.py + params): freecad_project_info to read parameters,
  freecad_project_params / freecad_project_rebuild to change and rebuild, freecad_export for files.
  freecad_checkpoint save before risky multi-step changes.
- Clearances for printed fits come from the print profile (freecad_print_profile; cl.fit('slide'),
  cl.hole('insert_m3')) - never hard-code guesses when the profile has measured values.
- freecad_sketch to debug sketches (DoF, conflicts, open wires); freecad_drawing for a dimensioned
  PDF; freecad_target('headless') for windowless batch work (switch back with 'gui').
- obj.Shape of a Part::Feature already includes its Placement. Assign whole Placements
  (obj.Placement.Base.z = x edits a copy)."""

mcp = FastMCP("freecad", instructions=INSTRUCTIONS)


async def _call(method, timeout=300.0, **params):
    tag = "[headless] " if client.target() == "headless" else ""
    try:
        res = await asyncio.to_thread(call, method, params, timeout)
    except LinkError as e:
        return "%sERROR: %s" % (tag, e)
    if tag:
        if isinstance(res, dict):
            res["text"] = tag + res.get("text", "")
        elif isinstance(res, str):
            res = tag + res
    return res


def _out(res):
    if isinstance(res, dict) and "image_b64" in res:
        fmt = "jpeg" if res.get("mime") == "image/jpeg" else "png"
        return [res.get("text", ""), Image(data=base64.b64decode(res["image_b64"]), format=fmt)]
    return res


@mcp.tool()
async def freecad_status() -> str:
    """FreeCAD version, open documents (active marked *), saved/modified state, and user names
    defined in the persistent exec namespace."""
    return await _call("status", 15)


@mcp.tool()
async def freecad_exec(code: str, fresh: bool = False, rollback_on_error: bool = True,
                       recompute: bool = False, max_output: int = 4000, timeout: float = 300) -> str:
    """Run Python inside the FreeCAD GUI (main thread). Persistent namespace across calls
    (fresh=true resets it). Preloaded: App/FreeCAD, Gui/FreeCADGui, Part, V (App.Vector), math,
    step() (recompute+redraw), last_run (module of the last freecad_run_file).
    Returns: status, a compact diff of model changes (+ added, ~ changed with volume/bbox deltas,
    - removed), problem objects, printed output, and the value of a trailing expression.
    The whole call is one undo step; errors roll it back unless rollback_on_error=false."""
    return await _call("exec", timeout, code=code, fresh=fresh, rollback_on_error=rollback_on_error,
                       recompute=recompute, max_output=max_output)


@mcp.tool()
async def freecad_run_file(path: str, overrides: dict | None = None, then: str | None = None,
                           shared: bool = False, rollback_on_error: bool = True, recompute: bool = False,
                           max_output: int = 4000, timeout: float = 600) -> str:
    """Execute a Python file inside FreeCAD as __main__, without sending its source.
    overrides: {"NAME": value} replaces top-level literal assignments (NAME = 1.5, A, B = 1, 2)
    before running, and is re-applied after load so later JSON/param overrides can't clobber it.
    then: extra code run in the file's namespace afterwards, e.g.
    "build(App.getDocument('MyPart'), None)"; its trailing expression is returned.
    shared=true runs in the persistent exec namespace. Returns the same change summary as freecad_exec."""
    return await _call("run_file", timeout, path=path, overrides=overrides, then=then, shared=shared,
                       rollback_on_error=rollback_on_error, recompute=recompute, max_output=max_output)


@mcp.tool()
async def freecad_tree(doc: str | None = None, depth: int = 2, filter: str | None = None,
                       limit: int = 150) -> str:
    """Compact object tree of a document (default: active): name [type] 'label' bb WxDxH vol,
    hidden/invalid/error flags, children nested as in the GUI tree. filter = substring of
    name/label/type for a flat match list."""
    return await _call("tree", 60, doc=doc, depth=depth, filter=filter, limit=limit)


@mcp.tool()
async def freecad_inspect(name: str, doc: str | None = None,
                          detail: Literal["summary", "props", "full"] = "summary") -> str:
    """One object by name or label: placement, bbox, topology counts, validity, volume/area/centre
    of mass, expressions, dependencies. detail=props adds all properties, full adds view props."""
    return await _call("inspect", 60, name=name, doc=doc, detail=detail)


@mcp.tool()
async def freecad_view(doc: str | None = None, views: list[str] | None = None, size: int = 380,
                       objects: list[str] | None = None, highlight: list[str] | None = None,
                       background: str = "White", format: Literal["png", "jpg"] = "png", ortho: bool = True):
    """Render the 3D view to ONE contact-sheet image (default views iso, front, top, right).
    views: iso front back top bottom left right dimetric trimetric, or "dir:x,y,z" (view direction).
    objects: show only these (temporarily) and fit to them. highlight: select these (drawn in the
    selection colour). size = px per tile (120-1600). Orthographic unless ortho=false. The user's
    camera, visibility and selection are restored afterwards. Use one view / small size when enough."""
    return _out(await _call("view", 120, doc=doc, views=views, size=size, objects=objects,
                            highlight=highlight, background=background, format=format, ortho=ortho))


@mcp.tool()
async def freecad_doc(action: Literal["list", "new", "open", "save", "saveas", "close", "activate", "recompute"] = "list",
                      name: str | None = None, path: str | None = None, force: bool = False) -> str:
    """Document management. new(name), open(path), save(name?), saveas(path, force to overwrite),
    close(name?, force to discard unsaved changes), activate(name), recompute(name?) -> diff and
    problems. name defaults to the active document."""
    return await _call("doc", 300, action=action, name=name, path=path, force=force)


@mcp.tool()
async def freecad_undo(doc: str | None = None, steps: int = 1, redo: bool = False) -> str:
    """Undo (or redo) the last steps in a document; each freecad_exec/run_file call is one step.
    Returns what was undone and the resulting model diff."""
    return await _call("undo", 120, doc=doc, steps=steps, redo=redo)


@mcp.tool()
async def freecad_console(tail: int = 40, grep: str | None = None) -> str:
    """Last lines of FreeCAD's Report view (warnings, errors, recompute messages), optionally
    filtered by a case-insensitive substring."""
    return await _call("console", 15, tail=tail, grep=grep)


# ------------------------------------------------------------------ Phase 2: measure & verify

_REF_HELP = ("Refs are 'Obj' or 'Obj:<selector>' (name or label). Selectors: Face12/Edge3/Vertex2, "
             "faces|edges|vertices, >Z <X (extreme), |Z (normal/direction parallel), #Z (perpendicular), "
             "%Cylinder %Plane %Circle (type), r=5 r<3, z=41 x>0 (centre coord), near(x,y,z), [0] [-1] [0:3]. "
             "e.g. 'Housing:faces %Cylinder r>29 [0]', 'Bezel:>Z'.")


@mcp.tool(description="""Measure in global coordinates. One ref: describe it (type, length/area, radius,
centre, axis/normal, volume). Two refs: min distance with closest points, overlap check, angle between
planar faces or straight edges, parallel-plane separation, and for two cylinders/circles the axis offset
and radial/diametral clearance. """ + _REF_HELP)
async def freecad_measure(a: str, b: str | None = None, doc: str | None = None) -> str:
    return await _call("measure", 120, a=a, b=b, doc=doc)


@mcp.tool()
async def freecad_section(objects: list[str] | None = None, plane: str = "z", offset: float = 0.0,
                          region: list[float] | None = None, size: int = 560, image: bool = True,
                          max_gap: float = 5.0, angle: float | None = None, doc: str | None = None):
    """Cut objects (default: all visible solids) with a plane and return per-object loop count, area
    and extents, in-plane gaps/overlaps between objects (the real fit clearances), plus a 2D drawing
    with a mm grid, colour per object and red markers at the smallest gaps.
    plane: 'x'|'y'|'z' or a normal 'nx,ny,nz'; offset: plane position along the normal
    (e.g. plane='y', offset=0 is the XZ mid-section through a part centred on the axis).
    region: [u0, v0, u1, v1] zooms the drawing to a window in image coords (mm) for small features.
    angle rotates the in-plane axes. image=false for text only."""
    return _out(await _call("section", 180, objects=objects, plane=plane, offset=offset, region=region,
                            size=size, image=image, max_gap=max_gap, angle=angle, doc=doc))


@mcp.tool()
async def freecad_check_geometry(objects: list[str] | None = None, bop: bool = True, doc: str | None = None) -> str:
    """Validate shapes (default: all visible): isValid, OCC check (with boolean self-intersection check
    when bop=true), open shells, inside-out or multiple solids, large tolerances, tiny edges/faces.
    One line per object; 'ok' or the list of problems."""
    return await _call("check_geometry", 300, objects=objects, bop=bop, doc=doc)


@mcp.tool()
async def freecad_fit(objects: list[str] | None = None, min_clearance: float = 0.0, max_gap: float = 5.0,
                      doc: str | None = None) -> str:
    """Assembly fit check between every pair of objects (default: all visible solids): INTERFERENCE
    volume and region, contact, or minimum gap with location. Pairs with gap < min_clearance are
    flagged TIGHT; pairs farther than max_gap are only counted. Sorted worst first."""
    return await _call("fit", 600, objects=objects, min_clearance=min_clearance, max_gap=max_gap, doc=doc)


@mcp.tool()
async def freecad_wall_thickness(object: str, min_wall: float = 1.0, samples: int = 3000,
                                 doc: str | None = None) -> str:
    """Ray-cast wall thickness of a solid: min/p5/median/max, share of samples below min_wall,
    and clustered thin-spot locations."""
    return await _call("wall_thickness", 300, object=object, min_wall=min_wall, samples=samples, doc=doc)


@mcp.tool()
async def freecad_printability(object: str, up: str = "+z", nozzle: float | None = None,
                               layer: float | None = None, max_overhang: float = 45.0,
                               bed: list[float] | None = None, doc: str | None = None) -> str:
    """FDM print check for one solid in a given orientation (up: +z -z +x -x +y -y or 'x,y,z'):
    bed fit, bed contact area, overhang area beyond max_overhang (degrees from vertical) with region
    locations, near-horizontal ceilings, walls thinner than the nozzle / two lines, separate bodies.
    nozzle/layer/bed default to the print profile. Compare orientations by calling with different 'up'."""
    return await _call("printability", 300, object=object, up=up, nozzle=nozzle, layer=layer,
                       max_overhang=max_overhang, bed=bed, doc=doc)


# ------------------------------------------------------------------ Phase 3: projects & output

_PROJ_HELP = ("project: folder path, a folder name under ~/Documents (e.g. 'MyPart'), an open document "
              "name, or omitted for the active document's folder.")


@mcp.tool(description="""Summarise a parametric project folder: document (open or not), build script and
build() signature, folders, and every top-level parameter with its group, comment and effective value
(flags values overridden by params_override.json or differing in the Params sheet). filter narrows by
name/comment/group. """ + _PROJ_HELP)
async def freecad_project_info(project: str | None = None, filter: str | None = None) -> str:
    return await _call("project_info", 60, project=project, filter=filter)


@mcp.tool(description="""Persistently change project parameters. persist: 'auto' (default: params_override.json
if the script reads it, else edit the script), 'override', 'source' (rewrite the literal in build_*.py,
keeping comments), or 'none' (only for this rebuild). Also updates the Params sheet if the document is open.
rebuild=true rebuilds right after (export/save as in freecad_project_rebuild). """ + _PROJ_HELP)
async def freecad_project_params(values: dict, project: str | None = None,
                                 persist: Literal["auto", "override", "source", "none"] = "auto",
                                 rebuild: bool = False, export: bool = False, save: bool = False) -> str:
    return await _call("project_params", 900, project=project, values=values, persist=persist,
                       rebuild=rebuild, export=export, save=save)


@mcp.tool(description="""Rebuild a project from its build script inside FreeCAD (opens the .FCStd if needed),
calling build(doc, export_dir) / build() as the script defines. overrides apply to this run only (the
script may still write them into its Params sheet). export=true passes the project's stl/ dir so the
script writes STL+STEP; save=true saves the document; checks=true appends geometry and fit checks.
Returns the change summary plus the script's info lines. """ + _PROJ_HELP)
async def freecad_project_rebuild(project: str | None = None, overrides: dict | None = None, export: bool = False,
                                  save: bool = False, checks: bool = False, timeout: float = 900) -> str:
    return await _call("project_rebuild", timeout, project=project, overrides=overrides, export=export,
                       save=save, checks=checks)


@mcp.tool()
async def freecad_project_new(name: str, description: str = "", folder: str | None = None,
                              force: bool = False) -> str:
    """Scaffold a new parametric project in ~/Documents/<name> (or folder): build_<name>.py following the
    house conventions (parameter block with comments, params_override.json loader, SHEET_PARAMS, Params
    sheet, Rebuild_<name> macro, build(doc, export_dir) exporting STL+STEP), README.md, stl/ step/
    images/, and <name>.FCStd built once and saved. Then edit the script's shape functions and rebuild."""
    return await _call("project_new", 300, name=name, description=description, folder=folder, force=force)


@mcp.tool()
async def freecad_export(objects: list[str] | None = None, format: str = "stl", dir: str | None = None,
                         path: str | None = None, tolerance: float = 0.01, angular: float = 0.1,
                         single_file: bool = False, names: dict | None = None, colors: bool = False,
                         doc: str | None = None) -> str:
    """Export objects (default: visible solids) as stl, 3mf, obj, amf, ply (meshed with chord
    tolerance mm / angular rad), or step, iges, brep. Default location: the document's folder
    stl/ (mesh) or step/ (CAD), one file per object named after its label (names={obj: filename}
    to override); single_file or path writes one combined file. Reports size, facets and
    watertightness per file. Existing files are replaced.
    colors=true (3mf only): ONE file, each object in its display colour as a separate part kept in place,
    one extruder per colour (PrusaSlicer/Orca volume metadata + core 3MF base materials) - for printing a
    multi-colour model in one go (default name <doc>_color.3mf)."""
    return await _call("export", 600, objects=objects, format=format, dir=dir, path=path, tolerance=tolerance,
                       angular=angular, single_file=single_file, names=names, colors=colors, doc=doc)


@mcp.tool()
async def freecad_compare_mesh(object: str, reference: str, tolerance: float = 0.1, samples: int = 4000,
                               search: float = 3.0, image: bool = True, views: list[str] | None = None,
                               doc: str | None = None):
    """Deviation between a CAD object and a reference (Mesh object in the document, STL file path, or
    another shape), both ways along surface normals. Reports bbox/centre offset, mean/RMS/p95/max
    deviation, % within tolerance, worst regions with signed values (+ = CAD oversize), and areas
    where one side has no counterpart within `search` (missing or extra features). The image is a
    deviation map (views: top bottom front back left right). Models must already be aligned."""
    return _out(await _call("compare_mesh", 600, object=object, reference=reference, tolerance=tolerance,
                            samples=samples, search=search, image=image, views=views, doc=doc))


@mcp.tool()
async def freecad_checkpoint(action: Literal["save", "list", "restore", "open"] = "save", label: str | None = None,
                             name: str | None = None, doc: str | None = None) -> str:
    """Document snapshots outside the undo stack (survive restarts, don't touch the saved file).
    save(label) before risky multi-step work; list; restore(name, default newest) replaces the
    document's objects in place as one undo step; open(name) opens a checkpoint as a separate
    document for comparison."""
    return await _call("checkpoint", 300, action=action, label=label, name=name, doc=doc)


# ------------------------------------------------------------------ Phase 4: drafting & targets

@mcp.tool()
async def freecad_sketch(name: str, detail: Literal["summary", "geometry", "constraints", "full"] = "summary",
                         image: bool = False, doc: str | None = None):
    """Sketcher diagnostics: solver result, degrees of freedom, conflicting / redundant / partially
    redundant / malformed constraints (1-based, as in the GUI), closed vs open wires, plane and
    attachment. detail adds geometry (g0.. with coords) and/or constraints (c1.. with refs and
    values); image=true draws the sketch with geometry indices."""
    return _out(await _call("sketch", 60, name=name, detail=detail, image=image, doc=doc))


@mcp.tool()
async def freecad_drawing(objects: list[str], views: list[str] | None = None, template: str = "A4_Landscape_TD",
                          scale: float | None = None, hidden: bool = True, dims: bool = True,
                          title: str | None = None, path: str | None = None, keep: bool = False,
                          preview: bool = True, doc: str | None = None):
    """TechDraw drawing exported to PDF (+SVG): projection group (default front, top, right, iso;
    ISO first-angle), largest standard scale that fits the sheet unless scale is given, hidden
    lines, automatic overall-extent dimensions and hole/boss diameters, title block filled.
    Default path: <doc folder>/images/<objects>_drawing.pdf. keep=false removes the page again
    (no model change); keep=true leaves the page in the document. Returns dimension values and a
    preview image. GUI only."""
    return _out(await _call("drawing", 300, objects=objects, views=views, template=template, scale=scale,
                            hidden=hidden, dims=dims, title=title, path=path, keep=keep, preview=preview, doc=doc))


@mcp.tool()
async def freecad_orient(object: str, max_overhang: float = 50.0, max_bridge: float = 10.0,
                         load: list[float] | str | None = None, top: int = 5, doc: str | None = None) -> str:
    """Rank FDM print orientations for a solid: the six axes plus lying flat on each large planar face.
    Scores support volume (overhangs steeper than max_overhang from vertical, minus flat spans
    <= max_bridge that can bridge), print height, bed contact and bed fit (print profile), and with
    `load` (model direction, e.g. [0,0,1] or "+x") how much of it pulls across the weak layer lines.
    Returns a ranked table and the best `up` for freecad_print_prep."""
    return await _call("orient", 300, object=object, max_overhang=max_overhang, max_bridge=max_bridge,
                       load=load, top=top, doc=doc)


@mcp.tool()
async def freecad_print_prep(object: str, up: str = "auto", supports: bool = True, teardrops: bool = True,
                             chamfer: float | None = None, max_overhang: float = 50.0, max_bridge: float = 10.0,
                             spacing: float = 3.0, tabs: bool = True, load: list[float] | str | None = None,
                             name: str | None = None, doc: str | None = None) -> str:
    """Make a print-ready copy of a solid (<name>_print; the original is untouched): rotated so `up`
    (auto = best from freecad_orient, or +z/-x/'x,y,z') points up, placed on the bed, horizontal holes
    turned into teardrops, bed edges chamfered (default = nozzle, against elephant's foot). With
    supports=true also builds engineered snap-off supports (<name>_print_supports): only under
    overhangs that need them (flat spans <= max_bridge anchored on 2 sides bridge instead), grid walls
    2 lines thick every `spacing` mm for line contact, a one-layer vertical gap and nozzle-wide side
    gap from the part (print profile), and a grip tab on bed-standing islands. Verifies no overlap and
    reports gaps, volume and mass. Export both together for printing."""
    return await _call("print_prep", 900, object=object, up=up, supports=supports, teardrops=teardrops,
                       chamfer=chamfer, max_overhang=max_overhang, max_bridge=max_bridge, spacing=spacing,
                       tabs=tabs, load=load, name=name, doc=doc)


@mcp.tool()
async def freecad_strength(object: str, fixed: list[str], loads: list[dict], up: str = "+z",
                           material: str | None = None, mesh_size: float | None = None, image: bool = True,
                           keep: bool = False, doc: str | None = None):
    """Quick linear-static FEM check of a printed part (Gmsh + CalculiX, on a copy in a scratch doc).
    fixed: face selectors held rigidly, e.g. ["%Cylinder r<2.6"] (bolt holes) or ["<Z"].
    loads: [{"ref": selector, "force": N, "dir": "-z" | [x,y,z]}] - total force spread over the matched
    faces. up: the PRINT direction in model coords (layer adhesion is checked across it).
    Reports max von Mises vs in-layer strength, max tension ACROSS layers vs layer adhesion, deflection,
    safety factors + verdict (aim >= 3 for prints), stress hot spots with their faces (thicken/fillet
    there). Material from the print profile (typical PLA/PETG/ABS/ASA/PA/PC values; override with
    material=...). Assumes solid material. image: von Mises colour plot (GUI only)."""
    return _out(await _call("strength", 900, object=object, fixed=fixed, loads=loads, up=up, material=material,
                            mesh_size=mesh_size, image=image, keep=keep, doc=doc))


@mcp.tool()
async def freecad_split(object: str, axis: str | None = None, positions: list[float] | None = None,
                        joint: Literal["pin", "peg", "none"] = "pin",
                        fit: Literal["press", "snug", "slide", "loose"] = "press", pin_d: float | None = None,
                        margin: float = 5.0, bosses: bool = True, name: str | None = None,
                        doc: str | None = None) -> str:
    """Split a part that is too big for the print bed (profile bed minus margin) into pieces
    <name>1..n, choosing cut planes with small, compact cross-sections (or give axis + positions).
    Joints hold without glue: separate press-fit pins (<name>_pins, laid flat with a small flat so they
    print support-free; holes in both halves) or integral pegs (joint='peg'), 2+ per interface, sized to
    keep a 1.2 mm wall. Thin walls get small 45-deg bosses on the inside of the cut to carry a pin.
    Clearance = profile fit class. The original is untouched."""
    return await _call("split", 900, object=object, axis=axis, positions=positions, joint=joint, fit=fit,
                       pin_d=pin_d, margin=margin, bosses=bosses, name=name, doc=doc)


@mcp.tool()
async def freecad_color_split(objects: list[str], colors: list | None = None,
                              fit: Literal["press", "snug", "slide", "loose"] = "press",
                              joint: Literal["pin", "peg"] = "pin", name: str | None = None,
                              purge_g: float = 0.2, doc: str | None = None) -> str:
    """One single-colour print per colour region instead of a multi-colour print. objects = colour
    regions in priority order (base first, inlays/details last; later ones win where they overlap).
    colors: names ('red', 'white', ...) or RGB per object (default: their view colours).
    For each touching pair it finds the straight assembly direction, then: inlays/pockets get a
    press-fit clearance (exact 2D offset for prism-like logos/text, else 3D offset) and hold by friction
    (warns if engaged < 1.2 mm); flat stacked joints get press-fit pins. Reports pieces
    <name>1_<colour>.., the assembly order, LOCKED pieces that can't be assembled, and the purge
    filament a multi-colour print would waste (rough)."""
    return await _call("color_split", 900, objects=objects, colors=colors, fit=fit, joint=joint, name=name,
                       purge_g=purge_g, doc=doc)


@mcp.tool()
async def freecad_motion(moving: list[str] | str, fixed: list[str] | None = None, rotate: dict | None = None,
                         translate: dict | None = None, steps: int = 36, show_worst: bool = True,
                         doc: str | None = None) -> str:
    """Clearance through a range of motion (hinge, lid, slider, print-in-place). Give one of
    rotate={"axis_ref": "Base:faces %Cylinder r<4 [0]", "from": 0, "to": 110} (or "axis": "y",
    "point": [x,y,z]) or translate={"dir": "x", "from": 0, "to": 20}. fixed defaults to all other
    visible solids. Reports collision ranges with the worst interference (and a red ghost object of
    that position), else the minimum clearance along the motion with its fit class and whether it is
    safe to print in place (>= profile loose fit), plus a compact gap profile."""
    return await _call("motion", 600, moving=moving, fixed=fixed, rotate=rotate, translate=translate, steps=steps,
                       show_worst=show_worst, doc=doc)


@mcp.tool()
async def freecad_illustrate(kind: Literal["all", "parts", "steps", "views", "cutaway", "manual"] | list[str] = "all",
                             objects: list[str] | None = None, steps: list[dict] | None = None,
                             base: str | None = None, tools: list | None = None, hardware: list | None = None,
                             title: str | None = None, view: str = "iso", views: list[str] | None = None,
                             cut: Literal["x", "y", "z"] | None = None, at: float | None = None,
                             width: int = 1000, out_dir: str | None = None, preview: bool = True,
                             preview_width: int = 1000, doc: str | None = None):
    """Assembly/usage illustration pages (PNG) rendered from the model in a scratch doc (yours untouched):
    parts = printed parts grouped by identical shape with letters A, B.. + quantity + size, plus
    `hardware` (["4x M3x10 screw", "2x M3 heat-set insert", "magnet 6x3"]) and `tools`
    (["2.5 mm hex key", "soldering iron", "soft mallet", "clamp", "CA glue"]) drawn as icons;
    steps = LEGO-style pages: step number, callout of the parts added, new parts pulled out along their
    insertion direction with red arrows. Order + directions are planned automatically (base = largest part
    or `base`; each step adds parts that touch and can slide straight in, keeping later parts insertable;
    identical parts go in together) or given as steps=[{"add": ["Lid"], "dir": "-z", "note": "...",
    "view": "iso_left"}]; split/colour-split pins are shown via their hidden <name>_pins_placed;
    views = hero iso + back/front/right/top (views=[...] to change), with overall size;
    cutaway = quarter cut from the main view + straight section (cut axis x/y/z at `at`, default centre).
    manual = all of the above + a print list (per part: colour, quantity, least-support orientation, size, grams;
    printer settings) bound into one A4 PDF <title>_manual.pdf.
    view: iso iso_left iso_back iso_back_right iso_below.. front back left right top bottom or dir:x,y,z.
    Files go to <project>/images (or out_dir); returns paths, the step list and one preview image."""
    return _out(await _call("illustrate", 900, kind=kind, objects=objects, steps=steps, base=base, tools=tools,
                            hardware=hardware, title=title, view=view, views=views, cut=cut, at=at, width=width,
                            out_dir=out_dir, preview=preview, preview_width=preview_width, doc=doc))


@mcp.tool()
async def freecad_fastener(object: str,
                           kind: Literal["screw_hole", "tapped_hole", "thread", "nut_trap", "insert", "insert_boss",
                                         "selftap", "snap"],
                           at: list[float] | str, axis: str | list[float] | None = None, size: str = "M3",
                           depth: float | None = None, length: float | None = None,
                           head: Literal["socket", "countersunk", "none"] = "socket",
                           fit: Literal["press", "snug", "slide", "loose"] | None = None,
                           slot: float = 0.0, slot_dir: str | list[float] | None = None, buried: float = 0.0,
                           height: float | None = None, hook: str | list[float] | None = None,
                           thickness: float = 1.6, width: float = 6.0, deflection: float = 1.0, retain: float = 90.0,
                           material: str | None = None, name: str | None = None, doc: str | None = None) -> str:
    """Add a printable fastener feature to an object (in place for a Part::Feature as one undo step, else into
    `name`/<obj>_f). at = [x,y,z] or a selector on the object ('faces >Z' -> face centre, axis into the part;
    'edges %Circle z=10 [0]' -> circle centre). axis = direction INTO the material (out of it for thread/snap).
    kinds: screw_hole (ISO clearance + socket counterbore / 90-deg countersink; depth omitted = through),
    tapped_hole (internal ISO metric thread, entry countersink), thread (external threaded rod of `length`),
    nut_trap (hex pocket; slot = side slot length along slot_dir; buried = depth below the surface for a captive
    nut), insert (heat-set insert hole), insert_boss (boss of `height` + insert hole), selftap (pilot hole),
    snap (cantilever hook: length/thickness/width/deflection, catch towards `hook`; reports strain vs the
    material's limit and push-in force). Sizes M2..M24 (coarse pitch). Clearances from the print profile."""
    return await _call("fastener", 300, object=object, kind=kind, at=at, axis=axis, size=size, depth=depth,
                       length=length, head=head, fit=fit, slot=slot, slot_dir=slot_dir, buried=buried, height=height,
                       hook=hook, thickness=thickness, width=width, deflection=deflection, retain=retain,
                       material=material, name=name, doc=doc)


@mcp.tool()
async def freecad_print_profile(action: Literal["show", "set", "record_gauge"] = "show",
                                material: str | None = None, values: dict | None = None,
                                round: dict | None = None, square: dict | None = None,
                                pin_dia: float | None = None, plate: list[float] | None = None,
                                insert_m3: float | None = None, selftap_m3: float | None = None,
                                notes: str | None = None) -> str:
    """The user's printer profile: bed, nozzle, layer, slicer and per-material RADIAL fit clearances
    (press < snug < slide < loose), M3 insert/self-tap hole sizes, XY/Z scale. Used by printability,
    fit/measure fit classes and cl.fit()/cl.hole(). Values not from a printed FitGauge are guesses.
    show; set: values merged at top level ({"printer": "...", "bed": [x,y,z], "nozzle": 0.4,
    "default_material": "PETG"}) or into `material` ({"fits": {"slide": 0.15}}).
    record_gauge: FitGauge results for `material`: round/square = {class: hole label (1/100 mm)},
    e.g. round={"press": 5, "snug": 10, "slide": 15, "loose": 30}; pin_dia = printed pin caliper
    reading; plate = [x, y, z] caliper readings of the gauge plate; insert_m3 / selftap_m3 = best hole."""
    return await _call("print_profile", 30, action=action, material_name=material, values=values, round=round,
                       square=square, pin_dia=pin_dia, plate=plate, insert_m3=insert_m3, selftap_m3=selftap_m3,
                       notes=notes)


@mcp.tool()
async def freecad_target(target: Literal["gui", "headless"] | None = None, stop_worker: bool = False) -> str:
    """Choose which FreeCAD instance the tools talk to. 'gui' (default) = the user's FreeCAD window.
    'headless' = a windowless freecadcmd worker started on demand (for heavy batch work or when the
    GUI isn't running; no view/drawing tools; results are tagged [headless]). stop_worker=true stops
    a worker this server started. No arguments: report both instances."""
    out = []
    try:
        if stop_worker:
            out.append(await asyncio.to_thread(client.stop_worker))
            if client.target() == "headless":
                client.set_target("gui")
        if target == "headless":
            out.append(await asyncio.to_thread(client.start_worker))
            client.set_target("headless")
        elif target == "gui":
            client.set_target("gui")
    except LinkError as e:
        out.append("ERROR: %s" % e)
    gui = await asyncio.to_thread(client.reachable, "gui")
    hl = await asyncio.to_thread(client.reachable, "headless")
    out.append("target: %s | GUI %s | headless worker %s" % (client.target(), "up" if gui else "not reachable",
                                                            "up" if hl else "not running"))
    return "\n".join(out)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
