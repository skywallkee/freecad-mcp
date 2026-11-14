---
name: freecad
description: Workflow for modelling, checking and exporting parts in a live FreeCAD session through the freecad MCP (ClaudeLink). Use whenever a task involves FreeCAD, CAD parts, 3D-printable models, STL/STEP files, or parametric FreeCAD project folders.
---

# FreeCAD via the freecad MCP

The `freecad_*` tools drive the user's running FreeCAD 1.0 GUI. Never launch the FreeCAD GUI;
if it isn't reachable, ask the user to start it (or use `freecad_target('headless')` for
windowless work, then switch back with `'gui'`).

## Loop (keep it short; every reply is already a compact diff)
1. `freecad_status` → `freecad_tree` (depth 1) to orient. For a project: `freecad_project_info`.
2. Build/change:
   - project parameters → `freecad_project_params` (persist) or `freecad_project_rebuild(overrides=…)` (try-out)
   - new geometry → `freecad_exec` with `cl` helpers (`cl.help()`), one coherent step per call
   - risky multi-step work → `freecad_checkpoint(save)` first
3. Read the change summary (bbox/volume deltas, INVALID, recompute problems). Fix before moving on.
4. Verify with numbers, not looks:
   - after booleans/fillets: `freecad_check_geometry`
   - assemblies: `freecad_fit` (interference / gaps), then `freecad_section` through mating features
     (use `region` to zoom) for the real clearances
   - critical dimensions: `freecad_measure` with selectors (`Obj:faces %Cylinder r>5 [0]`)
   - printed parts: `freecad_printability` (compare `up` orientations), `freecad_wall_thickness`
   - rebuilt from a reference mesh: `freecad_compare_mesh`
5. One `freecad_view` (small size, 1–2 views) to confirm visually.
6. Deliver: `freecad_export` (STL/3MF/STEP), `freecad_drawing` for a dimensioned PDF, `freecad_doc save`.

## Printed fits
- Clearances come from the print profile: `freecad_print_profile` (show) and `cl.fit('press'|'snug'|'slide'|'loose')`,
  `cl.hole('insert_m3'|'selftap_m3')` in code. If the profile says GUESS, say so and suggest printing the
  FitGauge (`examples/fit_gauge` in the freecad-mcp repo); record results with `record_gauge`.
- `freecad_fit` / `freecad_measure` label small gaps with the fit class they give on this printer.

## Token discipline
- Prefer `freecad_run_file` / project tools over pasting scripts; pass only overrides.
- Don't dump properties: `freecad_inspect` summary first, `detail=props` only when needed.
- Images: `size` 250–400, one or two views; `freecad_section` beats full renders for fits.
- Batch related edits into one `freecad_exec`; rely on its diff instead of re-reading the tree.

## Project conventions
- Folder `~/Documents/<Name>/`: `<Name>.FCStd`, `build_<name>.py` (UPPERCASE literal parameters at the
  top with `# comment`, `# --- group ---` headings), `params_override.json`, `Params` sheet +
  `Rebuild_<Name>.FCMacro`, `build(doc, export_dir)` writing `stl/` + `step/`, `images/`.
- New projects: `freecad_project_new`, then edit the shape functions in the script and rebuild.
- Never save, overwrite exports or edit build scripts of an existing project unless the task asks for it;
  report findings and offer the fix.

## FreeCAD gotchas
- `obj.Shape` of a Part::Feature already includes its Placement; don't transform it again.
- `obj.Placement.Base.z = x` edits a copy: assign a whole Placement (`cl.place`).
- `FreeCADGui.activateDocument` doesn't exist; use `freecad_doc activate`.
- TechDraw `make*Dim` create objects in the ACTIVE document (the drawing tool handles this).
- Sketcher constraint indices in messages are 1-based (`freecad_sketch` reports them that way).
