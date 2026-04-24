# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A PyQt5 desktop application for controlling a laboratory instrument system: Core-XY gantry (CNC motion), incubator (temperature + pump control via Arduino), microscope/camera, and automation routines for well-plate scanning.

## Running the App

```bash
python main.py
```

No formal test suite or lint config. The app runs in **simulator mode** without any connected hardware — hardware imports are wrapped in `try/except` so every component degrades gracefully. Toggle "Simulator" vs "Board" mode in the UI to switch serial communication on/off.

Dependencies (no requirements.txt): `PyQt5`, `pyqtgraph`, `pygame`, `pyserial`, `opencv-python`, `numpy`. Optional: `qdarkstyle`.

## Process & Thread Architecture

```
Main GUI Process (QMainWindow — main.py)
├── Gantry Process (mp.Process — backend/gantry.py)
│   ├── Two motion channels: "needle" + "camera" (independent serial ports)
│   └── XboxController Process (mp.Process — backend/controller.py, optional)
├── IncubatorSerial daemon thread (backend/incubator.py)
├── Camera capture QTimer (33 ms, OpenCV)
└── Tabs: SensorsTab, MicroscopeTab, AutomationTab
```

## Inter-process Communication

| Channel | Mechanism | Message Format |
|---|---|---|
| GUI → Gantry | `multiprocessing.Queue` (`q_from_gui`) | dicts with `"type"` key |
| Gantry → GUI | `multiprocessing.Queue` (`q_to_gui`) | dicts: `{"type": "state"\|"message"\|"disconnected", ...}` |
| Controller → Gantry | `multiprocessing.Queue` (`q_from_controller`) | normalized input dicts |
| Incubator → GUI | `queue.Queue` (threading) | tuples: `("status"\|"data"\|"raw", payload)` |

The GUI drains the gantry queue every **50 ms** via a `QTimer`. Gantry move commands accumulate across that 50 ms window and flush as a single G1 in `_flush_motion()`.

## Gantry Motion Details

- **Two independent channels**: `"needle"` and `"camera"` — each has its own serial port and `GantryState` dataclass
- **Motion target combo** in UI: "Needle", "Camera", or "Both" — all queued moves route to selected channel(s)
- Position is **software-tracked** (not read back from firmware); there is no encoder feedback
- Y-axis is flipped (`flip_y = -1.0`) to match screen coordinate orientation
- Step size clamps: XY `[0.005, 5.0]` mm, Z `[0.001, 2.0]` mm, E `[0.001, 1.0]` mm
- Absolute moves use the G90 → G1 → G91 sequence (Marlin firmware assumed: `FIRMWARE_IS_MARLIN = True`)

## Gantry Queue Message Types

Key `"type"` values sent from GUI to gantry process:
- `"gcode"` — raw G-code string
- `"home_all"` — trigger homing
- `"set_steps"` / `"set_feed"` — update step/feed settings (broadcast to both channels)
- `"fan_set"` — fan/pump control (`PUMP_FAN_INDEX = 0`)
- `"btn_estop"` — emergency stop
- `"gantry_cmd"` — structured move command

## Routine Automation (backend/routine.py)

`RoutineController` is a `QObject` (not a separate process) that drives automation via `QTimer`-based state machine phases:

`z2_down` → `aspirate` → `z2_up` → `z1_down` → `dispense` → `z1_up` → `xy_move` → (repeat or complete)

- Z2 is the aspirate needle (A-axis), Z1 is the dispense needle (Z-axis)
- `RoutineConfig` dataclass holds `aspirate_ms`, `dispense_ms`, `settle_ms`, `mode` ("pump" or "contact"), plate geometry
- Well grid iteration via `_grid_iter()` — yields `(row, col, dx, dy)` direction vectors; supports serpentine scan
- Pump sinks (`aspirate_sink`, `dispense_sink`) are `Callable[[int], None]` injected at construction; active only when `mode == "pump"` and the sink is not `None`
- Emits Qt signals: `status_changed(dict)` with keys `"status"` and `"phase"`, `log_message(str)`

## Arduino Incubator Protocol

The Arduino outputs one line every ~5 s matching this pattern (current firmware):
```
CO2: 412 ppm | Temp: 36.85 C | RH: 72.34 % | Setpoint: 37.00 C | Heater PWM: 47
```
Commands sent TO the Arduino — see `IncubatorSerial` in `backend/incubator.py` for the exact strings (they differ from the older firmware format). The older firmware format (which included "Error" and "Pump: STOPPED" fields) is **not** matched by the current regex.

## Camera

- **`CameraManager`** (`backend/camera_manager.py`) is the active camera backend — wired into `main.py`. Uses `cv2.VideoCapture` with `CAP_DSHOW`, 640×480, 33 ms QTimer (~30 FPS). `camera.py` still exists but is not wired in.
- Raw BGR frames flow: `CameraManager.frame_ready` → `StageGUI2.raw_frame_ready` (signal) → `AutomationTab.receive_raw_frame` + `MicroscopeTab.receive_frame`
- `CameraManager` signals: `frame_ready(object)`, `camera_state_changed(bool)`, `preview_state_changed(bool)`, `error_occurred(str)`
- Circle detection scripts in `tabs/camera_detection/` (4 variants) exist but are **not** automatically invoked by the tab UI
- DNX64 camera light DLL path is hardcoded: `C:\Users\macke\Desktop\Project Code\camera_light_test\DNX64.dll`

## Well Calibration (tabs/calibration_dialog.py)

- `CalibrationDialog` is a `QDialog` that captures live frames and lets the user click to define well edge points
- `ransac_circle(points, n_iter, inlier_thresh)` is the core fitter — picks 3 random edge points per iteration to fit a circle, tracks best inlier count
- On `Accepted`, returns `well_center_px: tuple` and `well_radius_px: float` to `AutomationTab.set_calibration_result()`
- Once calibrated, `AutomationTab.receive_raw_frame()` runs RANSAC live on each camera frame and overlays a smoothed circle (window=8 frames, ±25% radius tolerance)

## Well Plate Map & Per-Well Notes

- `WellPlatePreview` is a custom `QWidget` using `QPainter` — draws a labelled grid (row letters A–Z, column numbers) with hover and highlight states
- Clicking a well opens `WellPopup` (frameless popup with "Move To" and "Information" options)
- "Information" opens `WellInfoDialog` — view/edit freetext notes per well, saved in `AutomationTab._well_notes: dict[str, str]`
- "Move To" computes `(col * dx, row * dy)` from the ΔX/ΔY inputs and emits `move_to_well_requested(float, float)` → `StageGUI2._on_move_to_well()` → absolute gantry move; requires home to be set first
- **Set Home** dialog reminds user to center the needle on well A1 (top-left) before confirming

## Routine Scheduling

- `ScheduleDialog` lets users configure: start now vs. future datetime, repeat interval (hours), stop after N hours, stop after N runs
- Active schedule stored in `AutomationTab._schedule: dict | None`; button changes to "Manage Schedule" (amber) when active
- `_sched_timer` (15 s interval) polls for the scheduled start time; `_inter_pass_timer` (one-shot) waits between repeat passes
- `ScheduleManagePopup` (frameless popup) offers "Run Next Pass Now", "Edit Schedule", "Cancel Schedule"
- `set_runtime_status()` triggers `_schedule_next_pass()` automatically when status becomes `"Complete"`

## Pump Control

- 2 pumps in `AutomationTab` "Pump Test" panel: Pump 1 is bidirectional (`pump1_forward_requested`, `pump1_reverse_requested`, `pump1_stop_requested`); Pump 2 is single-direction (`pump2_run_requested`, `pump2_stop_requested`)
- Duration input per pump (ms); signals relay through `StageGUI2` handlers → `IncubatorSerial` methods: `pump1_forward(ms)`, `pump1_reverse(ms)`, `stop1()`, `pump2_run(ms)`, `stop2()`, `stop_all()`
- Actual Arduino wire protocol: `pump1 forward <ms>`, `pump1 reverse <ms>`, `stop1`, `pump2 <ms>`, `stop2`, `stopall`, `setpoint <temp>`
- Pump operations are gated by `_pump_not_connected()` — shows a warning if `self.incubator` is not an `IncubatorSerial` instance
- Incubator is connected via `SensorsTab.incubator_connected` signal → `_on_incubator_connected()` which also arms the routine dispense sink

## Guided Tour & Help Panel

- `GuideWindow` (`tabs/guide_panel.py`) is a `QDialog` docked as a floating panel — contains collapsible section cards (HTML body) describing each feature area. Opened via the help button in `StageGUI2`.
- `GuidedTour` (`tabs/guided_tour.py`) overlays a `TourHighlight` (semi-transparent border widget) and `TourPopup` (step dialog) on the main window. Steps are a list of `(widget_ref, title, text)` tuples defined in `GuidedTour.__init__`. `start()` / `next_step()` / `prev_step()` / `finish()` drive the sequence.
- Both are instantiated in `StageGUI2.__init__` and wired to the guide button via `guide_window.btn_start_tour.clicked`.

## Controller Mapping

Xbox controller actions stored in `backend/config/controller_map.json` (auto-created on first run). Default: left stick → XY, right stick → Z, triggers → E, face buttons → step size. Deadzone: 0.20. Button presses are debounced via `_last_button_time`.

## Main Window (`StageGUI2`)

The top-level `QMainWindow` subclass is `StageGUI2` (in `main.py`). It owns the gantry `mp.Process`, the `IncubatorSerial` thread, `CameraManager`, `RoutineController`, and all tab widgets. Signal wiring between tabs and backend components all lives in `StageGUI2`.

## UI / Styling

- Dark theme throughout: pyqtgraph background `#1e1e1e`, foreground `#dddddd`
- Optional `qdarkstyle` applied at startup if installed
- Sensor/plot data uses pyqtgraph; tab container is `QStackedWidget`
- Snapshot output directories: `microscope_snapshots/`, `well_plate_snapshots/`, `circle_detect_snapshots/`
- Sensor logs saved to `sensor_logs/` as JSON
