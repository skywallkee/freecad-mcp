---
name: freecad
description: Workflow and ground rules for designing, checking and preparing 3D-printable parts in a live FreeCAD session through the freecad MCP (ClaudeLink). Load it before your first freecad_* tool call - it holds the rules that protect the user's open documents and the checks a part must pass. Use it whenever a task involves FreeCAD, CAD modelling, parametric project folders, STL/STEP/3MF files, or making something to 3D print - enclosures, brackets, gears, hinges, labels, multi-colour or too-big prints, strength or fit questions, assembly instructions or renders - even if FreeCAD isn't named. Not for how-to questions about other programs (Blender, Fusion 360, OpenSCAD code, Inkscape), slicer settings or printer hardware faults.
---

# FreeCAD through the freecad MCP

The `freecad_*` tools drive the user's running FreeCAD 1.0. Every tool answers with a compact text summary
(and an image only when asked), so you can work by numbers instead of screenshots. This file is the map;
the `references/` files hold the detail - read one when the task enters its area, not before.

| When the task involves... | Read |
|---|---|
| building geometry, selectors, `cl` helpers, projects, sketches, drawings | `references/modelling.md` |
| getting a part printed: fits, orientation, supports, strength, fasteners, splitting, colours | `references/printing.md` |
| gears, hinges, springs, patterns/vents, bearings/magnets/servos, enclosures, text/QR, downloaded STLs, size variants | `references/design-helpers.md` |
| instructions, manuals, renders, drawings, export formats | `references/docs-output.md` |
| a common request end to end (enclosure, downloaded STL, 2-colour sign, too-big part, ...) | `references/recipes.md` |
| a tool failed, invalid shape, FreeCAD unreachable | `references/troubleshooting.md` |

## Ground rules (these protect the user's work)
- **Never start FreeCAD yourself.** If `freecad_status` can't reach it, ask the user to open it, or use
  `freecad_target('headless')` for windowless work (no view/illustrate images there) and switch back to `'gui'`.
- **Don't save, overwrite exports or edit an existing project's build script unless the task asks for it.**
  Report what you found and offer the change. New objects you create are fine. For a new design, work in a new
  (unsaved) document and offer to save it or turn it into a parametric project. Exporting the print files the
  user asked for IS the deliverable: write them to a new folder (e.g. `~/Documents/<DesignName>/stl/`), never
  over existing files, and say where they are.
- **In a parametric project the build script is the source of truth.** Rebuilding replaces the objects, so
  features added afterwards by in-place tools (fastener, label, lighten, print_fix, seats) are lost on the next
  rebuild. Either add them in the build script (the `cl.screw_hole / insert_hole / ...` helpers make that easy)
  or apply them as the last step and say so.
- Tools that modify or replace objects (fastener, print_fix, label, lighten, seats, split, print_prep) save an
  automatic checkpoint first and say so; `freecad_checkpoint action=restore` undoes them. Before a long manual
  sequence of `freecad_exec` edits, take `freecad_checkpoint save` yourself.
- Printed fits come from the user's print profile. If `freecad_print_profile` says GUESS, every clearance is an
  estimate - tell the user once and suggest printing the FitGauge to measure their printer.

## The loop
1. **Orient**: `freecad_status`, then `freecad_tree` (depth 1). For a project folder: `freecad_project_info`.
2. **Build** one coherent step at a time: a dedicated tool when one exists (see the map below), otherwise
   `freecad_exec` with the `cl` helpers. Projects: change parameters with `freecad_project_params` /
   `freecad_project_rebuild`, not by pasting code.
3. **Read the change summary** each call returns (bbox and volume deltas, INVALID, recompute errors). Fix
   problems before the next step - they compound.
4. **Verify with numbers** (gates below), then one small `freecad_view` (1-2 views, size 300) to confirm.
5. **Deliver** what was asked: files, a summary of dimensions and clearances, and what is still unverified.

## Which tool when
- **Check**: `freecad_check_geometry` (after booleans/fillets), `freecad_measure` (dimensions, distances,
  cylinder clearances), `freecad_fit` (interference/gaps between parts), `freecad_section` (real clearances
  through mating features), `freecad_wall_thickness`, `freecad_printability`.
- **Print it**: `freecad_ready` (one verdict per part - start here) -> `freecad_print_fix` (what it flags) ->
  `freecad_orient` / `freecad_print_prep` (oriented copy + engineered supports) -> `freecad_export`.
  Loaded part: `freecad_strength`. Moving part: `freecad_motion`. Too big: `freecad_split`. Several colours on a
  single-colour printer: `freecad_color_split`; on a multi-colour printer: `freecad_export(format='3mf', colors=true)`.
- **Add features**: `freecad_fastener` (screw holes, threads, nut traps, heat-set inserts, snap hooks),
  `freecad_label` (text, stamps, QR), `freecad_lighten` (weight-saving patterns, vents),
  `freecad_stdpart` (bearing/magnet/servo seats).
- **Generate**: `freecad_mechanism` (gears, hinges, living hinges, flexures, springs), `freecad_enclosure`
  (box for a board), `freecad_project_new` (parametric project), `freecad_variants` (S/M/L).
- **Bring in**: `freecad_mesh_import` (repair a downloaded/scanned STL, find its holes, make it a solid),
  `freecad_compare_mesh` (how close a model is to a reference mesh).
- **Show**: `freecad_view` (quick check), `freecad_render` (presentation image), `freecad_illustrate`
  (parts list, LEGO-style steps, views, cutaway, PDF manual), `freecad_drawing` (dimensioned PDF).

## Gates before calling something done
- Geometry valid (`freecad_check_geometry`, or the INVALID flag in summaries) and one solid per printed part.
- Mating parts: `freecad_fit` shows no interference and the gaps match the intended fit class.
- Printed parts: `freecad_ready` says READY (or the remaining notes are explained to the user).
- Loaded parts: `freecad_strength` safety factor >= 3, layer direction considered.
- Say plainly what was not checked (e.g. "fits are profile guesses", "not checked in a slicer").

## Token discipline
- Prefer dedicated tools and `freecad_run_file`/project tools over long `freecad_exec` scripts.
- `freecad_inspect` summary before `detail=props`; images small and few; `freecad_section` beats renders for fits.
- Batch related edits into one `freecad_exec` and trust its diff instead of re-reading the tree.

## Top FreeCAD pitfalls (more in references/troubleshooting.md)
- `obj.Shape` of a Part::Feature already includes its Placement - don't transform it again.
- `obj.Placement.Base.z = x` edits a copy: assign a whole Placement (`cl.place`).
- Selector `faces >Z` picks the highest face; after embossing that may be a letter top - use `faces z=<height>`.
