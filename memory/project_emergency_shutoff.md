---
name: Automated gantry emergency shutoff
description: Brainstormed design for auto e-stop if gantry behaves erratically during a routine
type: project
---

Automated emergency shutoff for the gantry if it starts behaving erratically during a routine.

**Why:** Safety requirement — if the gantry moves unexpectedly (firmware bug, corrupted command, process hang), the routine should halt automatically without user intervention.

**How to apply:** Implement in layers; software catches logical errors, hardware catches physical overrun.

## Detection strategies (pick one or combine)

1. **Position bounds check** — Before each move, validate destination is inside a configured bounding box (max X/Y travel). Reject + e-stop if out of bounds. Most practical first step.
2. **Move duration watchdog** — Each move has an expected max duration (distance ÷ max speed × 1.5). If no "move complete" response within timeout, halt everything.
3. **Position feedback validation** — After each move, compare expected vs reported position. If error > tolerance (e.g. 2 mm), stop the routine.
4. **Process heartbeat** — `p_gantry` sends a heartbeat through the queue every ~100 ms. Main GUI poll timer checks it; if silent for 500 ms, trigger shutoff.

## Shutoff sequence

1. Send immediate STOP command to gantry queue (bypass routine queue)
2. Call `routine.stop()`
3. Call `incubator.stop_all()` (stop pumps too)
4. Post red message to automation tab message panel
5. Show `QMessageBox.critical()` popup with reason

## UI additions

- Persistent red **E-STOP** button visible across all tabs (manual override)
- Configurable max X/Y travel bounds in Automation tab settings
- Message panel logs which check triggered it and at which routine step

## Hardware note

Software bounds checking catches logical/firmware errors. For physical motor driver failures (where software commands may be ignored), a **hardware limit switch** that cuts motor power is the reliable backstop. Best practice: both layers.

**Current state:** Not yet implemented. Routine uses `RoutineController` in `backend/routine.py`. Gantry process is `p_gantry` in `main.py`.
