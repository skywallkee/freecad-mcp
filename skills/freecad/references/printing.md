# Getting a part printed

## The print profile
`freecad_print_profile` holds the printer (bed, nozzle, layer), material, and the measured radial clearances per
fit class: press < snug < slide < loose, plus M3 insert and self-tap hole sizes. Every fit-aware tool uses it.
If it says GUESS, the values are typical, not measured: mention it once and suggest printing the FitGauge
(`examples/fit_gauge` in the repo) and recording results with `record_gauge`. Typical PETG guesses: press 0.05,
snug 0.10, slide 0.15, loose 0.25 mm per side.

## Standard sequence for a printed part
1. `freecad_ready(objects)` - one verdict per part: validity, bed fit, support need, downward fillets, face-down
   counterbores, horizontal holes, walls thinner than the nozzle, holes < 2 mm, warping risk, tipping, overlaps
   between assembly parts. `up="auto"` picks the least-support orientation; give `up` when the design dictates it
   (e.g. layer direction for strength).
2. `freecad_print_fix(object, up)` - fixes the model itself: downward fillets -> 45-deg chamfers, one-layer
   membranes over face-down counterbores, teardrop holes, optional corner pads (`ears=true`) against warping.
3. Re-run `freecad_ready` to confirm.
4. `freecad_print_prep(object)` - oriented `<obj>_print` copy on the bed with a bed-edge chamfer and engineered
   snap-off supports `<obj>_print_supports` (only where needed). `freecad_orient` ranks orientations if you want
   to compare first.
5. `freecad_export(objects=[...], format='stl'|'3mf')` - one file per object by default; `single_file` to combine.

## Strength
`freecad_strength(object, fixed=[selectors], loads=[{"ref", "force", "dir"}], up=print_direction)` runs a linear
FEM check. It compares stress with in-layer strength AND tension across layers with layer adhesion - printed
parts usually fail between layers, so `up` matters. Aim for a safety factor >= 3. If layers govern, re-orient so
the load runs along the layers, or thicken/fillet the hot spots it names. It assumes solid material.

## Fits between parts
- Design clearances with `cl.fit(...)`; check with `freecad_fit` (interference volume, min gap, fit class) and
  `freecad_section` through the joint.
- Moving parts: `freecad_motion` with a rotation or slide - reports collisions (with a red ghost of the worst
  position) or the minimum clearance and whether it is safe to print in place (>= loose fit).

## Fasteners (`freecad_fastener`)
- Screws into plastic: heat-set inserts (`insert` / `insert_boss`) hold best and survive re-assembly; self-tap
  holes (`selftap`) are fine for a few cycles.
- Printed threads (`tapped_hole`, `thread`): only M5 and up print well; below that, use inserts.
- `screw_hole` gives ISO clearance with a counterbore (socket heads) or countersink.
- `nut_trap` with `slot` lets a nut slide in from the side; `buried` hides it (pause the print to drop it in).
- `snap` hooks report strain against the material's limit and the push-in force: if it says TOO STIFF, lengthen
  the beam rather than thinning it below two perimeters.
- `at` = point or face selector, `axis` = direction INTO the material (out of it for thread and snap).

## Too big for the bed
`freecad_split(object)` chooses cut planes with small sections and joins the pieces with press-fit pins
(printed lying flat) or integral pegs, at least two per joint; thin walls get small 45-deg bosses. It leaves a
hidden `<name>_pins_placed` for assembly illustrations.

## Several colours
- Single-colour printer: `freecad_color_split(objects=[base, ..., details])` - later regions win overlaps; each
  piece gets a press-fit pocket or pins and an assembly direction; it flags pieces that can't be assembled and
  estimates the purge filament a multi-colour print would waste.
- Multi-colour printer: keep the parts and `freecad_export(format='3mf', colors=true)` - one file, parts kept in
  place, one extruder per colour.

## Warping and adhesion
ABS/ASA/nylon/PC warp most, PETG some, PLA least. Large footprints and sharp corners raise the risk;
`freecad_ready` grades it and suggests a brim, rounded footprint corners or corner pads.
