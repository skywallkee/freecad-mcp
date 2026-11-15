# Troubleshooting

## FreeCAD not reachable
`freecad_status` fails: ask the user to start FreeCAD (the ClaudeLink addon starts with it and logs
"listening on 127.0.0.1:47823"). Windowless work (the default) does not need it; only `freecad_show`,
`freecad_illustrate`, `freecad_drawing` and strength colour plots use the window. Never kill or relaunch the user's FreeCAD.

## A tool changed the wrong thing
Tools that modify objects saved a checkpoint first: `freecad_checkpoint action=list`, then `restore name=<n>`.
Each `freecad_exec` call is also one undo step (`freecad_undo`).

## Invalid shapes and failing booleans
- Run `freecad_check_geometry` to see what is wrong (BOP errors, open shells, tiny edges).
- Coincident faces/surfaces (two cylinders of the same radius, faces exactly flush) make booleans fail or leave
  slivers: overlap by 0.01-0.05 mm or offset slightly.
- A cylinder's seam lying exactly in another solid's planar end face can break a fuse: rotate the cylinder a few
  degrees about its axis.
- `makeOffsetShape` fails on shapes that still carry a Placement: bake it first
  (`t = s.copy(); m = t.Placement.toMatrix(); t.Placement = App.Placement(); t.transformShape(m, True)`).
- Helical sweeps: use `Part.makeLongHelix` and a pipe shell with a fixed binormal along the axis; `makeHelix`
  with a Frenet sweep gives solids that booleans silently fail on.
- Fillets that OCC rejects as a set often work edge by edge: `cl.fillet` / `cl.chamfer` fall back automatically.

## Placement surprises
- `obj.Shape` of a Part::Feature already includes its Placement - don't apply it twice.
- `obj.Placement.Base.z = x` changes a copy: assign a whole Placement (`cl.place`).
- Objects inside App::Part/Body: use their global placement (the tools do this for you).

## Selectors pick the wrong face
`>Z`/`<Z` choose by face centre; after adding features the extreme may be a small new face. Use a coordinate
filter (`faces z=6`), a type filter (`%Plane`) or `near(x,y,z)` plus an index.

## Results look wrong
- Units: an imported part that is a few mm or a few km across was probably in metres or inches.
- Strength numbers far off: check the load is in newtons (5 kg = 49 N) and the fixed faces are the real supports.
- Fits feel tight/loose in reality: the profile is a guess until the FitGauge is printed and recorded.

## A call timed out with "the job is still running in FreeCAD"
A call that overruns its timeout is interrupted automatically ("was interrupted and FreeCAD is free again").
If the message says the job is inside one long geometry-kernel call, it stops when that call returns;
`freecad_cancel` repeats the interrupt. Then find a cheaper way: fewer faces, a simplified copy, a longer
timeout only if the step is known to be slow. Threads are handled for you in `freecad_strength` (simulated as
plain cylinders) and in the print checks (upright thread flanks need no support).

## The window shows an old or grey model
The windowless worker and the window are separate FreeCAD instances. `freecad_show` saves and reloads the file
in the window (it refuses if the window has unsaved changes to that file - ask before `force=true`). Colours set
windowless are remembered on the objects and applied when the file opens in the window.
