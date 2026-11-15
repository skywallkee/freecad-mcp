# Documentation and output

## Quick looks vs presentation
- `freecad_view`: fast check of what you built (1-2 views, size 250-400). Restores the user's camera.
- `freecad_render`: presentation image - soft light, cast shadows, ambient occlusion; ~5 s; works headless.
  Uses display colours (a palette if all are default). For an exploded shot, move the parts first.

## Assembly and usage instructions - `freecad_illustrate`
- `kind="parts"`: printed parts grouped by identical shape, lettered, with quantity and size, plus `hardware`
  (e.g. ["4x M3x8 screw", "4x M3 heat-set insert"]) and `tools` (["soldering iron", "2.5 mm hex key"]) as icons.
  Pass the real hardware and tools the design needs - the tool doesn't infer them.
- `kind="steps"`: LEGO-style pages; the order and insertion directions are planned automatically (parts that
  can't go in straight are reported), or give `steps=[{"add": [...], "dir": "-z", "note": "..."}]`.
- `kind="views"`, `kind="cutaway"` (quarter cut + section).
- `kind="manual"`: all of the above + a print list (colour, quantity, least-support orientation, grams) as one
  A4 PDF. Files go to the project's `images/` folder unless `out_dir` is given.

## Drawings
`freecad_drawing`: dimensioned TechDraw PDF/SVG for machining or sharing; `keep=false` leaves no trace.

## Export
- `freecad_export(objects, format)`: stl / 3mf / obj / amf / ply (mesh tolerance) or step / iges / brep.
  Default folder: the project's `stl/` or `step/`. Reports watertightness per file.
- Print copies: export `<obj>_print` and `<obj>_print_supports` together.
- Multi-colour printer: `format="3mf", colors=true` - one file, parts in place, one extruder per colour
  (PrusaSlicer/Orca volume metadata; Bambu-family slicers may need the colours assigned by hand).
- Never overwrite a project's existing exports unless asked; give a new `dir` or `path` otherwise.
