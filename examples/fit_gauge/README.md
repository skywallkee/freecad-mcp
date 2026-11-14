# FitGauge

Printer clearance calibration. Print it once per material, test the fits, and tell Claude the results:
they go into the ClaudeLink print profile, so every future part uses your printer's real clearances.

## Set up (from this repo)
1. Copy this folder to `~/Documents/FitGauge/`.
2. In FreeCAD, create a new document and save it as `~/Documents/FitGauge/FitGauge.FCStd`.
3. Ask Claude to rebuild the `FitGauge` project with export (`freecad_project_rebuild`, `export=true`).
   This builds the parts and writes `stl/` and `step/`.

## Print
- File: `stl/fit_gauge_all.stl` (plate + pin + peg, all already in print orientation — plate flat, pin and peg standing).
- Your normal profile for that material: 0.2 mm layers, 3+ walls, 20 %+ infill, **no supports, no brim** (brims hide elephant's foot).
- Plate is 90.8 × 55.2 × 6 mm, about 24 cm³.

## Test (labels are the radial clearance per side in 1/100 mm)
Let the parts cool fully. Don't sand or ream anything.

| Row | Feature | What to find |
|---|---|---|
| back | round holes 0 … 40 for the **Ø8 pin** | for each fit class below, the label of the **tightest** hole that gives it |
| middle | square holes 0 … 40 for the **8 mm peg** | same |
| front left | M3 heat-insert holes 3.8 / 4.0 / 4.2 (blind) | which one the insert goes in straight and holds firmly |
| front right | M3 self-tap holes 2.5 / 2.7 / 2.9 | which one a screw cuts into cleanly without splitting |

Fit classes:
- **press** – pushed in with force, stays put
- **snug** – goes in by hand with light force, no wobble
- **slide** – moves freely, barely any play
- **loose** – clear play (hinges, rotating parts)

Also measure with calipers: the **pin diameter**, and the plate's **X, Y, Z** (nominal 90.80 / 55.20 / 6.00).

Then tell Claude something like:
> PETG: round press 5, snug 10, slide 15, loose 30; square press 10, snug 15, slide 20, loose 30;
> pin 7.94; plate 90.62 x 55.10 x 6.03; insert 4.0; self-tap 2.7

## Files
- `FitGauge.FCStd`, built by `build_fit_gauge.py` (clearance list, nominal size, hardware holes at the top)
- `Params` sheet + `Rebuild_FitGauge.FCMacro` as in the other projects
- `stl/`, `step/` – exports
