# Recipes

Short, proven sequences for common requests. Adapt them; skip steps that don't apply.

## Enclosure for a board (e.g. "case for my Raspberry Pi 4")
1. `freecad_status`; `freecad_doc new` if there's no suitable document.
2. `freecad_enclosure(board="rpi4", lid="screw", vents=true)` - check the overlap line says 0.
3. Optional name: `freecad_label(object="Enclosure_lid", text="...", at="faces >Z", mode="engrave")`.
4. `freecad_ready(objects=["Enclosure_base", "Enclosure_lid"])`, `freecad_print_fix` if flagged.
5. `freecad_render` for a picture; `freecad_illustrate(kind="manual", hardware=[...from the enclosure report],
   tools=["soldering iron", "screwdriver"])` if instructions are wanted.
6. `freecad_export(objects=[base, lid])`. Tell the user connector positions are typical - check their board.

## Print a downloaded or scanned STL
1. `freecad_mesh_import(path)` - note defects fixed, units, holes found.
2. Modify if asked: new features as solids, `cl.cut`/`cl.union` with the imported solid.
3. `freecad_ready` -> `freecad_print_fix` -> `freecad_print_prep` -> `freecad_export`.

## Two-colour sign or badge on a single-colour printer
1. Model the base and the text/logo (`freecad_label` emboss, or separate solids).
2. `freecad_color_split(objects=[base, text])` - press-fit inlays, no glue; note the purge saving it reports.
3. `freecad_ready` on the pieces; export each piece; tell the user which colour each file is.
Multi-colour printer instead: keep one model and `freecad_export(format="3mf", colors=true)`.

## Part too big for the bed
1. `freecad_ready` confirms "doesn't fit the bed".
2. `freecad_split(object)` - pieces + `<name>_pins`; check every piece now fits.
3. `freecad_ready` on the pieces; export pieces and the pins file.
4. Optional: `freecad_illustrate(kind="steps")` shows the pins going in.

## Print-in-place hinged box
1. Box and lid as solids; `freecad_mechanism(kind="hinge", ...)` sized to the box edge, or model knuckles with
   `cl.fit("loose")` clearance.
2. `freecad_motion(moving=lid, rotate={"axis_ref": "Base:faces %Cylinder r<4 [0]", "from": 0, "to": 110})` -
   no collisions and "print-in-place: OK".
3. `freecad_ready` on the assembly (overlaps must be 0).

## Bracket that must hold a load ("holds 5 kg on a shelf")
1. Model it; `freecad_strength(fixed=[screw holes], loads=[{"ref": load face, "force": 49, "dir": "-z"}],
   up=<print direction>)` - 5 kg = 49 N.
2. Safety factor < 3 or layers governing: thicken/fillet the hot spots or re-orient; repeat.
3. Screw holes with `freecad_fastener(kind="screw_hole", head="countersunk")`.
4. `freecad_ready(up=<same print direction>)`, then export.

## S/M/L versions of a project
1. `freecad_project_info` for the parameter names.
2. `freecad_variants(variants={"S": {...}, "M": {...}, "L": {...}})` - table of size, grams, verdict.
