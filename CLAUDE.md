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

`idle` → `z_down` → `move_xy` → `z_up` → `wait` → `dispense` → `retract` → (repeat or `idle`)

- Well grid iteration via `_grid_iter()` — yields `(row, col, target_well, phase)`, supports serpentine scan
- Emits Qt signals: `status_changed(str)`, `log_message(str)`
- **Pump/dispense integration is incomplete** (TODO in code) — dispense phase exists but doesn't call incubator serial

## Arduino Incubator Protocol

The Arduino outputs one line every ~5 s matching this pattern (current firmware):
```
CO2: 412 ppm | Temp: 36.85 C | RH: 72.34 % | Setpoint: 37.00 C | Heater PWM: 47
```
Commands sent TO the Arduino: `FWD <ms>`, `REV <ms>`, `STOP`, `SETPOINT <temp>`

Pump commands use format `pump1/pump2/pump3/pump4` with `forward/reverse/stop`. The older firmware format (which included "Error" and "Pump: STOPPED" fields) is **not** matched by the current regex.

## Camera

- `MicroscopeTab` implements its own `cv2.VideoCapture` (default index 1); `camera.py` exists but is not currently wired in
- `MicroscopeTab` emits `frame_ready(QPixmap)` signal; `AutomationTab` connects to it for the live feed
- Circle detection scripts in `tabs/camera_detection/` (4 variants) exist but are **not** automatically invoked by the tab UI
- DNX64 camera light DLL path is hardcoded: `C:\Users\macke\Desktop\Project Code\camera_light_test\DNX64.dll`

## Controller Mapping

Xbox controller actions stored in `backend/config/controller_map.json` (auto-created on first run). Default: left stick → XY, right stick → Z, triggers → E, face buttons → step size. Deadzone: 0.20. Button presses are debounced via `_last_button_time`.

## UI / Styling

- Dark theme throughout: pyqtgraph background `#1e1e1e`, foreground `#dddddd`
- Optional `qdarkstyle` applied at startup if installed
- Sensor/plot data uses pyqtgraph; tab container is `QStackedWidget`
- Snapshot output directories: `microscope_snapshots/`, `well_plate_snapshots/`, `circle_detect_snapshots/`
- Sensor logs saved to `sensor_logs/` as JSON
