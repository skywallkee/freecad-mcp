# freecad MCP tool catalogue (what a session sees in its tool list)

## freecad_status()
FreeCAD version, open documents (active marked *), saved/modified state, and user names
defined in the persistent exec namespace.

## freecad_exec(code, fresh=False, rollback_on_error=True, recompute=False, max_output=4000, timeout=300)
Run Python inside the FreeCAD GUI (main thread). Persistent namespace across calls
(fresh=true resets it). Preloaded: App/FreeCAD, Gui/FreeCADGui, Part, V (App.Vector), math,
step() (recompute+redraw), last_run (module of the last freecad_run_file).
Returns: status, a compact diff of model changes (+ added, ~ changed with volume/bbox deltas,
- removed), problem objects, printed output, and the value of a trailing expression.
The whole call is one undo step; errors roll it back unless rollback_on_error=false.

## freecad_run_file(path, overrides=None, then=None, shared=False, rollback_on_error=True, recompute=False, max_output=4000, timeout=600)
Execute a Python file inside FreeCAD as __main__, without sending its source.
overrides: {"NAME": value} replaces top-level literal assignments (NAME = 1.5, A, B = 1, 2)
before running, and is re-applied after load so later JSON/param overrides can't clobber it.
then: extra code run in the file's namespace afterwards, e.g.
"build(App.getDocument('MyPart'), None)"; its trailing expression is returned.
shared=true runs in the persistent exec namespace. Returns the same change summary as freecad_exec.

## freecad_tree(doc=None, depth=2, filter=None, limit=150)
Compact object tree of a document (default: active): name [type] 'label' bb WxDxH vol,
hidden/invalid/error flags, children nested as in the GUI tree. filter = substring of
name/label/type for a flat match list.

## freecad_inspect(name, doc=None, detail='summary')
One object by name or label: placement, bbox, topology counts, validity, volume/area/centre
of mass, expressions, dependencies. detail=props adds all properties, full adds view props.

## freecad_view(doc=None, views=None, size=380, objects=None, highlight=None, background='White', format='png', ortho=True)
Render the 3D view to ONE contact-sheet image (default views iso, front, top, right).
views: iso front back top bottom left right dimetric trimetric, or "dir:x,y,z" (view direction).
objects: show only these (temporarily) and fit to them. highlight: select these (drawn in the
selection colour). size = px per tile (120-1600). Orthographic unless ortho=false. The user's
camera, visibility and selection are restored afterwards. Use one view / small size when enough.

## freecad_doc(action='list', name=None, path=None, force=False)
Document management. new(name), open(path), save(name?), saveas(path, force to overwrite),
close(name?, force to discard unsaved changes), activate(name), recompute(name?) -> diff and
problems. name defaults to the active document.

## freecad_undo(doc=None, steps=1, redo=False)
Undo (or redo) the last steps in a document; each freecad_exec/run_file call is one step.
Returns what was undone and the resulting model diff.

## freecad_console(tail=40, grep=None)
Last lines of FreeCAD's Report view (warnings, errors, recompute messages), optionally
filtered by a case-insensitive substring.

## freecad_measure(a, b=None, doc=None)


## freecad_section(objects=None, plane='z', offset=0.0, region=None, size=560, image=True, max_gap=5.0, angle=None, doc=None)
Cut objects (default: all visible solids) with a plane and return per-object loop count, area
and extents, in-plane gaps/overlaps between objects (the real fit clearances), plus a 2D drawing
with a mm grid, colour per object and red markers at the smallest gaps.
plane: 'x'|'y'|'z' or a normal 'nx,ny,nz'; offset: plane position along the normal
(e.g. plane='y', offset=0 is the XZ mid-section through a part centred on the axis).
region: [u0, v0, u1, v1] zooms the drawing to a window in image coords (mm) for small features.
angle rotates the in-plane axes. image=false for text only.

## freecad_check_geometry(objects=None, bop=True, doc=None)
Validate shapes (default: all visible): isValid, OCC check (with boolean self-intersection check
when bop=true), open shells, inside-out or multiple solids, large tolerances, tiny edges/faces.
One line per object; 'ok' or the list of problems.

## freecad_fit(objects=None, min_clearance=0.0, max_gap=5.0, doc=None)
Assembly fit check between every pair of objects (default: all visible solids): INTERFERENCE
volume and region, contact, or minimum gap with location. Pairs with gap < min_clearance are
flagged TIGHT; pairs farther than max_gap are only counted. Sorted worst first.

## freecad_wall_thickness(object, min_wall=1.0, samples=3000, doc=None)
Ray-cast wall thickness of a solid: min/p5/median/max, share of samples below min_wall,
and clustered thin-spot locations.

## freecad_printability(object, up='+z', nozzle=None, layer=None, max_overhang=45.0, bed=None, doc=None)
FDM print check for one solid in a given orientation (up: +z -z +x -x +y -y or 'x,y,z'):
bed fit, bed contact area, overhang area beyond max_overhang (degrees from vertical) with region
locations, near-horizontal ceilings, walls thinner than the nozzle / two lines, separate bodies.
nozzle/layer/bed default to the print profile. Compare orientations by calling with different 'up'.

## freecad_project_info(project=None, filter=None)


## freecad_project_params(values, project=None, persist='auto', rebuild=False, export=False, save=False)


## freecad_project_rebuild(project=None, overrides=None, export=False, save=False, checks=False, timeout=900)


## freecad_project_new(name, description='', folder=None, force=False)
Scaffold a new parametric project in ~/Documents/<name> (or folder): build_<name>.py following the
house conventions (parameter block with comments, params_override.json loader, SHEET_PARAMS, Params
sheet, Rebuild_<name> macro, build(doc, export_dir) exporting STL+STEP), README.md, stl/ step/
images/, and <name>.FCStd built once and saved. Then edit the script's shape functions and rebuild.

## freecad_export(objects=None, format='stl', dir=None, path=None, tolerance=0.01, angular=0.1, single_file=False, names=None, colors=False, doc=None)
Export objects (default: visible solids) as stl, 3mf, obj, amf, ply (meshed with chord
tolerance mm / angular rad), or step, iges, brep. Default location: the document's folder
stl/ (mesh) or step/ (CAD), one file per object named after its label (names={obj: filename}
to override); single_file or path writes one combined file. Reports size, facets and
watertightness per file. Existing files are replaced.
colors=true (3mf only): ONE file, each object in its display colour as a separate part kept in place,
one extruder per colour (PrusaSlicer/Orca volume metadata + core 3MF base materials) - for printing a
multi-colour model in one go (default name <doc>_color.3mf).

## freecad_compare_mesh(object, reference, tolerance=0.1, samples=4000, search=3.0, image=True, views=None, doc=None)
Deviation between a CAD object and a reference (Mesh object in the document, STL file path, or
another shape), both ways along surface normals. Reports bbox/centre offset, mean/RMS/p95/max
deviation, % within tolerance, worst regions with signed values (+ = CAD oversize), and areas
where one side has no counterpart within `search` (missing or extra features). The image is a
deviation map (views: top bottom front back left right). Models must already be aligned.

## freecad_checkpoint(action='save', label=None, name=None, doc=None)
Document snapshots outside the undo stack (survive restarts, don't touch the saved file).
save(label) before risky multi-step work; list; restore(name, default newest) replaces the
document's objects in place as one undo step; open(name) opens a checkpoint as a separate
document for comparison.

## freecad_sketch(name, detail='summary', image=False, doc=None)
Sketcher diagnostics: solver result, degrees of freedom, conflicting / redundant / partially
redundant / malformed constraints (1-based, as in the GUI), closed vs open wires, plane and
attachment. detail adds geometry (g0.. with coords) and/or constraints (c1.. with refs and
values); image=true draws the sketch with geometry indices.

## freecad_drawing(objects, views=None, template='A4_Landscape_TD', scale=None, hidden=True, dims=True, title=None, path=None, keep=False, preview=True, doc=None)
TechDraw drawing exported to PDF (+SVG): projection group (default front, top, right, iso;
ISO first-angle), largest standard scale that fits the sheet unless scale is given, hidden
lines, automatic overall-extent dimensions and hole/boss diameters, title block filled.
Default path: <doc folder>/images/<objects>_drawing.pdf. keep=false removes the page again
(no model change); keep=true leaves the page in the document. Returns dimension values and a
preview image. GUI only.

## freecad_orient(object, max_overhang=50.0, max_bridge=10.0, load=None, top=5, doc=None)
Rank FDM print orientations for a solid: the six axes plus lying flat on each large planar face.
Scores support volume (overhangs steeper than max_overhang from vertical, minus flat spans
<= max_bridge that can bridge), print height, bed contact and bed fit (print profile), and with
`load` (model direction, e.g. [0,0,1] or "+x") how much of it pulls across the weak layer lines.
Returns a ranked table and the best `up` for freecad_print_prep.

## freecad_print_prep(object, up='auto', supports=True, teardrops=True, chamfer=None, max_overhang=50.0, max_bridge=10.0, spacing=3.0, tabs=True, load=None, name=None, doc=None)
Make a print-ready copy of a solid (<name>_print; the original is untouched): rotated so `up`
(auto = best from freecad_orient, or +z/-x/'x,y,z') points up, placed on the bed, horizontal holes
turned into teardrops, bed edges chamfered (default = nozzle, against elephant's foot). With
supports=true also builds engineered snap-off supports (<name>_print_supports): only under
overhangs that need them (flat spans <= max_bridge anchored on 2 sides bridge instead), grid walls
2 lines thick every `spacing` mm for line contact, a one-layer vertical gap and nozzle-wide side
gap from the part (print profile), and a grip tab on bed-standing islands. Verifies no overlap and
reports gaps, volume and mass. Export both together for printing.

## freecad_strength(object, fixed, loads, up='+z', material=None, mesh_size=None, image=True, keep=False, doc=None)
Quick linear-static FEM check of a printed part (Gmsh + CalculiX, on a copy in a scratch doc).
fixed: face selectors held rigidly, e.g. ["%Cylinder r<2.6"] (bolt holes) or ["<Z"].
loads: [{"ref": selector, "force": N, "dir": "-z" | [x,y,z]}] - total force spread over the matched
faces. up: the PRINT direction in model coords (layer adhesion is checked across it).
Reports max von Mises vs in-layer strength, max tension ACROSS layers vs layer adhesion, deflection,
safety factors + verdict (aim >= 3 for prints), stress hot spots with their faces (thicken/fillet
there). Material from the print profile (typical PLA/PETG/ABS/ASA/PA/PC values; override with
material=...). Assumes solid material. image: von Mises colour plot (GUI only).

## freecad_split(object, axis=None, positions=None, joint='pin', fit='press', pin_d=None, margin=5.0, bosses=True, name=None, doc=None)
Split a part that is too big for the print bed (profile bed minus margin) into pieces
<name>1..n, choosing cut planes with small, compact cross-sections (or give axis + positions).
Joints hold without glue: separate press-fit pins (<name>_pins, laid flat with a small flat so they
print support-free; holes in both halves) or integral pegs (joint='peg'), 2+ per interface, sized to
keep a 1.2 mm wall. Thin walls get small 45-deg bosses on the inside of the cut to carry a pin.
Clearance = profile fit class. The original is untouched.

## freecad_color_split(objects, colors=None, fit='press', joint='pin', name=None, purge_g=0.2, doc=None)
One single-colour print per colour region instead of a multi-colour print. objects = colour
regions in priority order (base first, inlays/details last; later ones win where they overlap).
colors: names ('red', 'white', ...) or RGB per object (default: their view colours).
For each touching pair it finds the straight assembly direction, then: inlays/pockets get a
press-fit clearance (exact 2D offset for prism-like logos/text, else 3D offset) and hold by friction
(warns if engaged < 1.2 mm); flat stacked joints get press-fit pins. Reports pieces
<name>1_<colour>.., the assembly order, LOCKED pieces that can't be assembled, and the purge
filament a multi-colour print would waste (rough).

## freecad_motion(moving, fixed=None, rotate=None, translate=None, steps=36, show_worst=True, doc=None)
Clearance through a range of motion (hinge, lid, slider, print-in-place). Give one of
rotate={"axis_ref": "Base:faces %Cylinder r<4 [0]", "from": 0, "to": 110} (or "axis": "y",
"point": [x,y,z]) or translate={"dir": "x", "from": 0, "to": 20}. fixed defaults to all other
visible solids. Reports collision ranges with the worst interference (and a red ghost object of
that position), else the minimum clearance along the motion with its fit class and whether it is
safe to print in place (>= profile loose fit), plus a compact gap profile.

## freecad_illustrate(kind='all', objects=None, steps=None, base=None, tools=None, hardware=None, title=None, view='iso', views=None, cut=None, at=None, width=1000, out_dir=None, preview=True, preview_width=1000, doc=None)
Assembly/usage illustration pages (PNG) rendered from the model in a scratch doc (yours untouched):
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
Files go to <project>/images (or out_dir); returns paths, the step list and one preview image.

## freecad_fastener(object, kind, at, axis=None, size='M3', depth=None, length=None, head='socket', fit=None, slot=0.0, slot_dir=None, buried=0.0, height=None, hook=None, thickness=1.6, width=6.0, deflection=1.0, retain=90.0, material=None, name=None, doc=None)
Add a printable fastener feature to an object (in place for a Part::Feature as one undo step, else into
`name`/<obj>_f). at = [x,y,z] or a selector on the object ('faces >Z' -> face centre, axis into the part;
'edges %Circle z=10 [0]' -> circle centre). axis = direction INTO the material (out of it for thread/snap).
kinds: screw_hole (ISO clearance + socket counterbore / 90-deg countersink; depth omitted = through),
tapped_hole (internal ISO metric thread, entry countersink), thread (external threaded rod of `length`),
nut_trap (hex pocket; slot = side slot length along slot_dir; buried = depth below the surface for a captive
nut), insert (heat-set insert hole), insert_boss (boss of `height` + insert hole), selftap (pilot hole),
snap (cantilever hook: length/thickness/width/deflection, catch towards `hook`; reports strain vs the
material's limit and push-in force). Sizes M2..M24 (coarse pitch). Clearances from the print profile.

## freecad_ready(objects=None, up='auto', material=None, strength=None, doc=None)
ONE print-readiness verdict per part (default: visible solids): validity/watertight, bed fit, support
need, downward fillets, face-down counterbores, horizontal holes, walls thinner than the nozzle (with
locations), holes < 2 mm, warping risk (material, footprint, sharp corners), tipping/small contact, overlaps
between assembly parts; optional strength={"object","fixed","loads",...} (freecad_strength args).
up: print direction or "auto" (least support). Each problem names the tool that fixes it.

## freecad_print_fix(object, up='auto', fillets=True, membranes=True, teardrops=True, ears=False, ear_d=None, name=None, doc=None)
Fix print problems ON THE MODEL for print direction `up` (auto = least support): downward-facing
fillets -> 45-deg chamfers, one-layer membranes over face-down counterbores/stepped holes (poke through
after printing), teardrop horizontal holes, optional corner pads (ears) at sharp footprint corners against
warping. In place for a Part::Feature (one undo step + auto checkpoint) or into `name`.

## freecad_label(object, text=None, at='faces >Z', mode='emboss', kind='text', size=None, depth=None, font=None, xdir=None, offset=None, module=None, up='auto', name=None, doc=None)
Text, version stamps and QR codes on a planar face (at = face selector; note 'faces >Z' picks the highest
    face, which after embossing may be a letter - use e.g. 'faces z=6'). size = cap height mm (auto fits the
    face); offset = [dx, dy] in the face's text frame; font: sans (bold, default), sans-regular, mono,
    condensed or a .ttf path; multi-line with 
. Measures the real letter strokes: warns below two perimeters
    (emboss) or one nozzle width (engrave) and says the size that prints. kind=stamp: engraved text (default
    today's date, 4 mm) on the face that sits on the bed in print orientation `up`. kind=qr: text = data (<=106
    bytes), module = square size mm. Refuses labels that would hang off the face. In place + auto checkpoint.

## freecad_mechanism(kind, params=None, at=None, name=None, doc=None)
Printable mechanisms as new objects, sized from the print profile, each with its engineering check.
params per kind (all optional):
gear: module 1.5, teeth 20, thickness 6, pressure_angle 20, backlash (default 2x slide fit), bore, dflat;
gear_pair: + teeth2 40, bore2 -> meshed at the right centre distance; ratio, contact ratio, mesh gap;
hinge (print-in-place pin): length 40, diameter 8, width 20, thickness 3, knuckles 5, clearance (loose fit);
living_hinge: length 40, thickness, span 4, width 20, plate 3, angle 90 -> web strain vs material;
flexure (parallelogram): length 20, thickness 1, width 10, gap 20 -> stiffness N/mm, max travel;
spring (compression, square wire): diameter 12, wire 1.6, coils 6, length 20 -> rate N/mm, travel.

## freecad_lighten(object, at='faces >Z', pattern='hex', cell=None, wall=None, margin=None, depth=None, slot_len=None, angle=0.0, check=None, name=None, doc=None)
Lightweighting / vent pattern cut into a planar face (at = face selector): hex, grid, circles or slots
(vents). cell = pitch mm (10), wall = rib width (>= 4 nozzle widths), margin = solid border kept from the
face edges and holes; depth omitted = through the part along the face normal. Only whole cells inside the
border. Reports volume and grams saved; check={"fixed": [...], "loads": [...]} (freecad_strength args)
runs FEM before and after and flags a safety factor under 3. In place + auto checkpoint.

## freecad_stdpart(kind, size=None, at=None, axis='z', mode='part', target=None, fit=None, name=None, doc=None)
Standard parts. mode=part: simplified reference model (transparent grey, not for printing);
mode=seat: cut its seat into `target` at `at`, axis INTO the material: bearing seat (outer d + press fit,
with a through relief), magnet pocket, servo pocket with self-tap holes. size: bearing 623 624 625 626
608 688 6000 6001 6200 6201 6800 6804 MR85 MR105 LM8UU; magnet 'DxH' ('6x3'); servo sg90 mg996r; board
rpi4 rpi5 rpi-zero arduino-uno arduino-nano esp32-devkitc. Typical datasheet dimensions - measure yours.

## freecad_enclosure(board=None, size=None, wall=2.0, floor=2.0, clearance=1.5, standoff=5.0, headroom=3.0, lid='screw', vents=False, corner_r=3.0, name='Enclosure', doc=None)
Enclosure for a board (rpi4 rpi5 rpi-zero arduino-uno arduino-nano esp32-devkitc) or an inside size
[x, y, height]: base with rounded corners, standoffs with heat-set insert holes at the board's mounting
holes, port cut-outs for its edge connectors, lid with a locating lip, fixed by 4 M3 screws through corner
ears into inserts (lid='screw') or by 4 snap hooks into wall windows (lid='snap'); optional vent slots.
Makes <name>_base, <name>_lid and a reference <name>_board; checks overlaps; lists the hardware.

## freecad_mesh_import(path=None, mesh=None, name=None, repair=True, solid=True, features=True, max_facets=60000, scale=1.0, doc=None)
Import a downloaded/scanned STL/OBJ/3MF/PLY (path) or use a Mesh object (mesh): report defects (open
surface, non-manifold, self-intersections, flipped facets, loose pieces, wrong units), repair them,
decimate above max_facets, list the largest flat faces and fitted holes/bosses (diameter, axis, position)
and convert to a solid <name> (coplanar triangles merged into real faces) for booleans and measuring.
scale=25.4 for inch files, 1000 for metres.

## freecad_variants(variants, project=None, objects=None, format='stl', check=True, up='auto')
Size variants of a parametric project: variants={"S": {"WIDTH": 30}, "L": {"WIDTH": 60}} (parameter
names from freecad_project_info). Each variant is rebuilt, its parts (objects or visible solids) exported to
<project>/stl/<variant>/ and print-checked (freecad_ready); returns a size/grams/verdict table. The project
is rebuilt with its original values afterwards (nothing is saved).

## freecad_render(objects=None, view='iso', size=700, aspect=1.33, shadows=True, ao=True, edges=False, colors=None, background='studio', out=None, quality='normal', image=True, doc=None)
Presentation render (no external renderer needed, works headless): soft key/fill/rim light, plastic
material, cast shadows on a ground plane, ambient occlusion, 2x anti-aliasing. objects default = visible
solids, display colours (palette if all default; colors={obj: [r,g,b]} overrides). view as freecad_illustrate
(iso, iso_left, iso_back, front, top, dir:x,y,z). Saves PNG to <project>/images (or out); returns it.
For exploded shots, move the parts first (or use freecad_illustrate steps).

## freecad_print_profile(action='show', material=None, values=None, round=None, square=None, pin_dia=None, plate=None, insert_m3=None, selftap_m3=None, notes=None)
The user's printer profile: bed, nozzle, layer, slicer and per-material RADIAL fit clearances
(press < snug < slide < loose), M3 insert/self-tap hole sizes, XY/Z scale. Used by printability,
fit/measure fit classes and cl.fit()/cl.hole(). Values not from a printed FitGauge are guesses.
show; set: values merged at top level ({"printer": "...", "bed": [x,y,z], "nozzle": 0.4,
"default_material": "PETG"}) or into `material` ({"fits": {"slide": 0.15}}).
record_gauge: FitGauge results for `material`: round/square = {class: hole label (1/100 mm)},
e.g. round={"press": 5, "snug": 10, "slide": 15, "loose": 30}; pin_dia = printed pin caliper
reading; plate = [x, y, z] caliper readings of the gauge plate; insert_m3 / selftap_m3 = best hole.

## freecad_target(target=None, stop_worker=False)
Choose which FreeCAD instance the tools talk to. 'gui' (default) = the user's FreeCAD window.
'headless' = a windowless freecadcmd worker started on demand (for heavy batch work or when the
GUI isn't running; no view/drawing tools; results are tagged [headless]). stop_worker=true stops
a worker this server started. No arguments: report both instances.
