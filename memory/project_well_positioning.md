---
name: Well position calculation from home
description: Planned feature — compute absolute well positions from Set Home + ΔX/ΔY inputs
type: project
---

Once the user sets home at the center of well A1, every well position can be calculated as:
- X = col × ΔX
- Y = row × ΔY

**Why:** This enables absolute moves to any well, skipping wells, resuming from a specific well, clicking a well on the preview to move there, and eliminates error accumulation from chained relative moves.

**How to apply:** When fleshing out the routine, switch from `move_rel` well-to-well hops to `move_abs` from home coordinates. Add a `compute_well_position(row, col, dx, dy)` helper. Consider adding click-to-move on the WellPlatePreview widget.

**Current state:** Routine uses relative moves (`move_rel`) in `backend/routine.py`. Home position is tracked internally by the gantry. ΔX and ΔY are already input fields in the Automation tab.
