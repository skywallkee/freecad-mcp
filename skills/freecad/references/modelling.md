# Modelling

## freecad_exec and the `cl` helpers
`freecad_exec` runs Python in FreeCAD with a persistent namespace (`App`, `Gui`, `Part`, `V = App.Vector`,
`math`, `cl`). Each call is one undo step and is rolled back on error. The reply lists added/changed objects
with bbox, volume and validity - read it instead of re-querying.

`cl.help()` lists everything. The ones you will use most:

| Helper | Use |
|---|---|
| `cl.show(shape, name)` | put a shape in the document; replaces an object of the same name in place |
| `cl.sel(obj_or_shape, selector)`, `cl.sel1(...)` | pick faces/edges/vertices by selector (below) |
| `cl.polygon / extrude / revolve / ring / polar` | quick primitives: 2D outline -> prism, (r, z) profile -> revolved solid, tube, circular pattern |
| `cl.union(list)`, `cl.cut(base, tools)` | booleans that also clean coplanar seams |
| `cl.fillet(shape, edges, r)`, `cl.chamfer(...)` | with a per-edge fallback when OCC rejects the whole set |
| `cl.place(obj, pos, axis, angle)` | assign a whole Placement |
| `cl.fit('slide')`, `cl.hole('insert_m3')` | clearances and hardware holes from the print profile |
| `cl.thread / tapped_hole / screw_hole / nut_trap / insert_hole / selftap_hole / snap_hook` | fastener shapes placed at a point; axis points into the material |
| `cl.check(shape)` | one-line validity summary |

Work in one coherent step per call (e.g. "body with its pockets"), name objects meaningfully, and keep the
construction in a function you can re-run rather than editing shapes by hand.

## Selectors (shared by `cl.sel`, refs like `Obj:selector` in measure/fit/strength/fastener/label/lighten)
```
faces / edges / vertices        kind (default faces)
>Z  <X                          extreme along an axis (by centre)
|Z                              faces with normal parallel to Z / edges parallel to Z
#Z                              faces with normal perpendicular to Z
%Cylinder %Plane %Circle        surface / curve type
r=5  r<3  r>10                  radius (tolerance 0.01)
z=41  x>0  y<-2                 centre coordinate filter
near(10,0,41)                   sort by distance to a point
[0]  [-1]  [0:3]                index into the sorted result
```
Examples: `Housing:faces %Cylinder r>29 [0]`, `Plate:edges %Circle z=10`, `Lid:faces z=6`.

## Parametric projects
A project is a folder `<Name>/` with `<Name>.FCStd`, `build_<name>.py` (UPPERCASE literal parameters at the top
with `# comment`s and `# --- group ---` headings, then `build(doc, export_dir)`), optional
`params_override.json`, a `Params` sheet and `stl/ step/ images/`.
- Read: `freecad_project_info` (every parameter, its effective value and where it comes from).
- Try a change: `freecad_project_rebuild(overrides={...})` - temporary.
- Keep a change: `freecad_project_params(values={...}, rebuild=true)` - persists (override JSON or source).
- New: `freecad_project_new(name)` scaffolds the folder; then edit the shape code in the build script.
- Variants: `freecad_variants` (see design-helpers.md).
Changing parameters beats editing geometry by hand: the design stays rebuildable.

## Sketches and drawings
- `freecad_sketch`: solver state, degrees of freedom, conflicting/redundant constraints (1-based indices).
- `freecad_drawing`: TechDraw PDF/SVG with projection views, scale, dimensions and title block.

## Measuring well
- `freecad_measure(a)` describes one ref; `freecad_measure(a, b)` gives distance/angle/plane separation, and for
  two cylinders the radial clearance with its fit class on this printer.
- `freecad_section` through a mating feature shows the real gap as numbers and a dimensioned 2D image; use
  `region` to zoom. It is the most reliable answer to "will these fit?".
