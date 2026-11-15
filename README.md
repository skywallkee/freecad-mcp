# FreeCAD MCP (ClaudeLink)

Lets Claude drive a live FreeCAD 1.0 session through MCP tools, with compact, token-frugal results.

```
Claude --stdio--> server/ (MCP, Python + uv) --127.0.0.1:47823 JSON-lines + token--> addon/ClaudeLink (inside FreeCAD)
```

## Requirements
- FreeCAD 1.0, [uv](https://docs.astral.sh/uv/), Claude Code (or any MCP client).
- Tested on Windows. Other platforms need the paths in `server/src/freecad_mcp/client.py`
  (FreeCAD config dir, `freecadcmd` location; `FREECADCMD` env var) adjusted.

## Install
1. Clone this repo.
2. Link the addon into FreeCAD's user Mod folder (Windows, no admin needed):
   ```
   mklink /J "%APPDATA%\FreeCAD\Mod\ClaudeLink" "<repo>\addon\ClaudeLink"
   ```
3. Start FreeCAD. The Report view shows `ClaudeLink 0.1.0 listening on 127.0.0.1:47823`.
   A random token is created in `%APPDATA%\FreeCAD\ClaudeLink\token.txt`; only local processes that
   can read it can connect.
4. Register the MCP server:
   ```
   claude mcp add --scope user freecad -- uv run --directory <repo>\server freecad-mcp
   ```
5. Optional: copy `skills/freecad` to `~/.claude/skills/freecad` for the modelling workflow skill.

## Tools: core
| Tool | Purpose |
|---|---|
| `freecad_status` | version, open docs, namespace |
| `freecad_exec` | run Python on FreeCAD's main thread; persistent namespace; returns change diff + trailing expression value; one undo step, rolled back on error |
| `freecad_run_file` | run a file with `overrides` of top-level constants and a `then` snippet |
| `freecad_tree` / `freecad_inspect` | compact model tree / one object in detail |
| `freecad_view` | multi-view contact-sheet image; isolate/highlight; camera restored |
| `freecad_doc` | new/open/save/saveas/close/activate/recompute |
| `freecad_undo` | undo/redo with diff |
| `freecad_console` | Report view tail |

## Tools: measure & verify
| Tool | Purpose |
|---|---|
| `freecad_measure` | describe one ref, or distance/angle/plane separation/cylinder clearance between two |
| `freecad_section` | plane cut: areas, extents, in-plane gaps & overlaps, 2D drawing with mm grid; `region` zooms |
| `freecad_check_geometry` | validity, OCC/BOP check, open shells, tolerances, tiny edges/faces |
| `freecad_fit` | pairwise interference volume / contact / min gap, worst first |
| `freecad_wall_thickness` | ray-cast thickness stats and thin-spot locations (mesh tolerance reported) |
| `freecad_printability` | bed fit, contact area, overhang regions, too-thin walls for a given `up` |

Refs: `Obj` or `Obj:<selector>`; selectors shared with `cl.sel` (see `cl.help()`), e.g.
`Housing:faces %Cylinder r>29 [0]`, `Bezel:>Z`, `Plate:edges %Circle z=10`.

`cl` helpers in the exec namespace: `sel/sel1/name_of`, `show` (replace in place), `place/placement`,
`polygon/extrude/revolve/ring/polar/union/cut`, `fillet/chamfer` (per-edge fallback), `check`, `help`.

## Tools: projects & output
| Tool | Purpose |
|---|---|
| `freecad_project_info` | project folder summary + every parameter (group, comment, effective value, override/sheet flags) |
| `freecad_project_params` | persist values: `auto` (override JSON if the script reads it, else source), `override`, `source` (rewrite literal, keep comment), `none`; syncs the Params sheet; optional rebuild |
| `freecad_project_rebuild` | run build script (`build(doc, export_dir)` / `build()`), temporary overrides, export/save/checks |
| `freecad_project_new` | scaffold `~/Documents/<Name>` with the house-style build script, macro, folders, saved .FCStd |
| `freecad_export` | stl/3mf/obj/amf/ply (tolerance) or step/iges/brep; default to project stl/ or step/; watertight report; `colors=true` writes one multi-colour 3MF (parts kept in place, one extruder per colour) |
| `freecad_compare_mesh` | two-way normal deviation CAD vs mesh/STL: stats, worst regions, missing/extra areas, deviation map |
| `freecad_checkpoint` | save/list/restore(in place, one undo step)/open snapshots in FreeCAD's cache dir |

Project = folder with `<Name>.FCStd` + `build_*.py` (top-level UPPERCASE literal constants before the
first def are parameters; `# --- group ---` comment lines group them; `params_override.json` may override).

## Tools: drafting & targets
| Tool | Purpose |
|---|---|
| `freecad_sketch` | solver result, DoF, conflicting/redundant/malformed constraints, open wires; geometry/constraint lists; sketch image |
| `freecad_drawing` | TechDraw PDF+SVG: projection group, auto standard scale & layout, hidden lines, extent + diameter dims, title block; preview image; `keep=false` leaves no trace |
| `freecad_target` | switch tools between the GUI and a windowless `freecadcmd` worker (started on demand, results tagged `[headless]`) |

## Tools: printing
| Tool | Purpose |
|---|---|
| `freecad_orient` | rank orientations (6 axes + lie flat on the largest planar faces) by support volume, height, bed contact, bed fit and optional load direction across layers |
| `freecad_print_prep` | non-destructive `<obj>_print` copy: oriented, on the bed, teardrop horizontal holes, bed-edge chamfer; plus engineered snap-off supports `<obj>_print_supports` |

| `freecad_strength` | layer-aware linear FEM (Gmsh + CalculiX, bundled with FreeCAD): von Mises vs in-layer strength, tension across layers vs layer adhesion, deflection, safety factors, hot spots |
| `freecad_split` | split a part bigger than the bed at small, compact sections; press-fit pins (printed flat) or integral pegs, 2+ per interface; small 45° bosses carry pins in thin walls |
| `freecad_color_split` | one single-colour piece per colour region; finds each piece's assembly direction; press-fit inlay pockets (exact 2D offset for logos/text) or pinned flat joints; flags pieces that can't be assembled; estimates the purge a multi-colour print would waste |
| `freecad_motion` | rotate/slide parts through their travel: collision ranges with a ghost of the worst position, min clearance with fit class, print-in-place check |
| `freecad_illustrate` | assembly/usage PNG pages: parts + hardware + tools sheet (identical parts grouped, lettered, sized; tool icons), LEGO-style steps (auto assembly order that keeps later parts insertable, parts pulled out along their insertion direction with arrows; or your own steps), hero + orthographic views with overall size, quarter cutaway + section; `manual` adds a print list (colour, quantity, least-support orientation, grams) and binds everything into one A4 PDF; saved to the project's `images/` |
| `freecad_fastener` | printable fasteners on a part: ISO clearance holes with counterbore/countersink, internal and external ISO metric threads (M2-M24, clearance from the profile), nut traps (side slot or buried/captive), heat-set insert holes and bosses, self-tap holes, cantilever snap hooks with strain check and push-in force. Also as `cl.thread / tapped_hole / screw_hole / nut_trap / insert_hole / selftap_hole / snap_hook` |
| `freecad_ready` | one print-readiness verdict per part: validity, bed fit, supports, downward fillets, face-down counterbores, horizontal holes, walls thinner than the nozzle, holes < 2 mm, warping risk (material, footprint, sharp corners), tipping, overlaps; optional strength; each problem names its fix |
| `freecad_print_fix` | fixes on the model: downward fillets -> 45-deg chamfers, one-layer membranes over face-down counterbores, teardrops, corner pads against warping |
| `freecad_label` | embossed/engraved text (stroke width measured against the nozzle), date/version stamp under the part, QR codes (built-in encoder) |
| `freecad_mechanism` | involute gears and meshed pairs, print-in-place pin hinges, living hinges, parallelogram flexures, compression springs - each with its check (mesh gap, clearance, strain, stiffness, rate) |
| `freecad_lighten` | hex/grid/circle/slot cut-outs on a face with a solid border; grams saved; optional FEM before/after |
| `freecad_stdpart` | bearings, magnets, servos, boards as reference parts or seats cut into a part |
| `freecad_enclosure` | enclosure for a Pi 4/5/Zero, Arduino Uno/Nano, ESP32 or a size: insert standoffs, port cut-outs, screw-ear or snap lid, vents, hardware list |
| `freecad_mesh_import` | repair a downloaded/scanned STL/OBJ/3MF/PLY, list flat faces and fitted holes/bosses, convert to a solid |
| `freecad_variants` | S/M/L-style variants of a project: rebuild, export to stl/<variant>/, print-check, size/weight table |
| `freecad_render` | presentation renders with soft lighting, cast shadows and ambient occlusion (pure numpy, works headless) |

Engineered supports go only under overhangs that need them (anchored flat spans up to `max_bridge`
bridge instead). Each support is a thin vertical wall designed in its own plane: the part's section
(at the wall's centre and both faces) is grown by one layer with a robust 2D offset and subtracted, so
the walls touch the part only as lines with a one-layer gap. Walls run in a grid (`spacing`), get small
grip fins at bed level, and every piece is distance-checked; anything closer than half the gap is left
out and reported.

Tools that change or replace objects (split, color_split, print_prep, fastener, print_fix, label, lighten,
stdpart seats) save a checkpoint first (`freecad_checkpoint` restores it); turn off with the profile setting
`auto_checkpoint: false`.

Strength results assume solid material (enough perimeters / infill) and are a quick linear check,
not a certification; aim for a safety factor of 3 or more on printed parts. Material values are typical
datasheet numbers per filament, overridable per material in the print profile (`mech`).

## Print profile
`freecad_print_profile` keeps the printer (bed, nozzle, layer, slicer) and per-material radial fit
clearances (press < snug < slide < loose), M3 insert / self-tap hole sizes and XY/Z scale in
`%APPDATA%\FreeCAD\ClaudeLink\print_profile.json` (`CLAUDELINK_PROFILE` overrides the path, used by tests).
Measure them with the FitGauge print in `examples/fit_gauge` (`record_gauge`). Used by `freecad_printability`
defaults, fit-class labels in `freecad_fit` / `freecad_measure`, and `cl.fit()` / `cl.hole()` / `cl.profile()`.

Workflow skill: `skills/freecad/SKILL.md` (ground rules, loop, which-tool-when map, gates before "done") with
`references/` loaded on demand (modelling, printing, design helpers, docs/output, recipes, troubleshooting).
Its test cases are in `skills/freecad/evals/` (runnable with `claude plugin eval`).

## Dev
- Hot-reload addon code inside a running FreeCAD: `freecad_exec("import claudelink; claudelink.restart()")`.
- Headless instance (no window): `freecadcmd addon\ClaudeLink\headless_serve.py` (port 47824; MCP with `CLAUDELINK_TARGET=headless`).
- Tests: `uv run --directory server python ../tests/smoke_headless.py`, `../tests/phase2_headless.py`, `../tests/phase3_headless.py`, `../tests/phase4_mcp.py`, `../tests/profile_headless.py`, `../tests/printprep_headless.py`, `../tests/strength_headless.py`, `../tests/split_headless.py`, `../tests/illustrate_headless.py`, `../tests/fastener_headless.py`, `../tests/ready_headless.py`, `../tests/designs_headless.py`, `../tests/mcp_roundtrip.py`; with FreeCAD open, `../tests/gui_live_mcp.py` runs the printing/docs tools over MCP against the GUI.
- `mcp` is pinned `<2` (2.x renamed FastMCP).

## License
MIT, see `LICENSE`.
