# Design helpers

Each helper creates or changes geometry and reports the engineering check that matters. Typical values
below are good starting points; adjust to the user's request.

## Mechanisms - `freecad_mechanism(kind, params, at, name)`
| kind | key params (defaults) | check it reports |
|---|---|---|
| `gear` | module 1.5, teeth 20, thickness 6, bore, backlash (2x slide fit) | undercut warning under ~17 teeth |
| `gear_pair` | + teeth2, bore2 | centre distance, ratio, contact ratio (>= 1.2), interference, flank gap |
| `hinge` | length 40, diameter 8, width 20, thickness 3, knuckles 5 | print-in-place clearance (loose fit) |
| `living_hinge` | length, thickness (0.5-0.6), span 4, angle 90 | web strain vs material - PP/nylon last, PETG/PLA crack |
| `flexure` | length 20, thickness 1, width 10, gap 20 | stiffness N/mm, travel before the strain limit |
| `spring` | diameter 12, wire 1.6, coils 6, length 20 | rate N/mm, safe travel (40% of solid travel) |
Printing modules below ~1 mm with a 0.4 nozzle gives soft teeth; module >= 1.0-1.5 is safer.

## Weight and vents - `freecad_lighten(object, at, pattern, cell, wall, margin, depth, check)`
Patterns hex / grid / circles / slots, whole cells only, kept `margin` from edges and holes. Omit `depth` to cut
through. Pass `check={"fixed": [...], "loads": [...]}` on load-bearing parts: it runs FEM before and after and
flags a safety factor under 3. Vents: `pattern="slots"`.

## Standard parts - `freecad_stdpart(kind, size, at, axis, mode, target)`
- `mode="part"`: a grey reference model (not for printing) to design around.
- `mode="seat"`: cut the seat into `target` - bearing seat (press fit + through relief), magnet pocket, servo
  pocket with self-tap holes. `axis` points into the material.
- Known: bearings 623-626, 608, 688, 6000/6001, 6200/6201, 6800, 6804, MR85, MR105, LM8UU; magnets "DxH";
  servos sg90, mg996r; boards rpi4, rpi5, rpi-zero, arduino-uno, arduino-nano, esp32-devkitc.
Dimensions are typical datasheet values - remind the user to measure the actual part.

## Enclosures - `freecad_enclosure(board | size, wall, floor, clearance, standoff, lid, vents)`
Base with rounded corners, insert standoffs at the board's holes, port cut-outs for its edge connectors, lid with a
locating lip fixed by 4 M3 screws through corner ears (`lid="screw"`) or snap hooks (`lid="snap"`). Makes
`<name>_base`, `<name>_lid`, a reference `<name>_board`, checks overlaps and lists the hardware. Follow with
`freecad_ready` on base and lid, and `freecad_label` for a name on the lid if wanted.

## Text, stamps, QR - `freecad_label(object, text, at, mode, kind, size, offset)`
- Measures the real letter strokes: emboss needs >= two perimeters, engrave >= one nozzle width; it says the size
  that prints. Bold sans (default) prints best.
- `kind="stamp"`: engraved date/version on the bed face (in print orientation) - invisible but traceable.
- `kind="qr"`: data up to 106 bytes, module ~1 mm+; print it in a second colour (filament change) or engrave.
- It refuses labels that hang off the face. After embossing, `faces >Z` may pick a letter top - use `faces z=<h>`.

## Downloaded or scanned meshes - `freecad_mesh_import(path, scale)`
Reports and repairs defects (open surface, non-manifold, flipped facets, loose pieces), flags wrong units
(`scale=25.4` inches, `1000` metres), lists the largest flat faces and fitted holes/bosses, and converts to a
solid for booleans. Curved areas stay triangulated: add new features as separate solids and fuse/cut them.

## Size variants - `freecad_variants(variants={"S": {...}, "L": {...}}, project)`
Rebuilds the project per variant, exports to `stl/<variant>/`, runs `freecad_ready`, and tabulates size, grams and
verdict; the project is restored afterwards. Parameter names come from `freecad_project_info`.
