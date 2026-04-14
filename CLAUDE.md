# Final GUI — Claude Code Project Guide

## Project Overview

A PyQt5 desktop application for controlling a laboratory instrument system. The system includes a Core-XY gantry (CNC motion), an incubator (temperature + pump control via Arduino), a microscope/camera, and automation routines for well-plate scanning.

## Architecture

### Entry Point
- `main.py` — Main window (`QMainWindow`), launches the gantry as a separate **multiprocessing** process. Also manages the camera, incubator serial thread, and tab widgets.

### Backend (child processes / threads)
| File | Role |
|---|---|
| `backend/gantry.py` | Core-XY gantry G-code backend; runs in its own `Process` |
| `backend/controller.py` | Xbox controller reader (pygame); emits normalized input dicts to gantry |
| `backend/incubator.py` | Bidirectional serial thread for the Arduino incubator board |
| `backend/routine.py` | Automation routine controller (sequences of gantry moves) |

### GUI Tabs
| File | Role |
|---|---|
| `tabs/sensors_tab.py` | Live sensor display (CO2, temp, RH, heater PWM, pump state) |
| `tabs/automation_tab.py` | Well-plate automation with plate presets (12/24/48/96/Custom) |
| `tabs/microscope_tab.py` | Microscope/camera feed and controls |

### Camera
- `camera.py` — Camera capture
- `tabs/camera_detection/` — Circle detection scripts (OpenCV)

## Key Technologies
- **PyQt5** — GUI framework
- **pyqtgraph** — Live plotting (dark theme: `#1e1e1e` bg, `#dddddd` fg)
- **multiprocessing** — Gantry runs in a separate process; communication via `Queue`
- **threading** — `IncubatorSerial` is a daemon thread
- **pygame** — Xbox controller input (joystick polling at 60 Hz)
- **pyserial** — Serial communication with Arduino (115200 baud)
- **OpenCV (cv2)** — Camera circle detection
- **DNX64** — Camera light control (DLL at `C:\Users\macke\Desktop\Project Code\camera_light_test\`)

## Inter-process Communication
- GUI ↔ Gantry: two `multiprocessing.Queue` objects (`q_to_gui`, `q_from_gui`)
- Controller → Gantry: `q_from_controller` queue
- Incubator → GUI: `queue.Queue` with tuples: `("status", str)`, `("data", dict)`, `("raw", str)`
- Messages are plain dicts with a `"type"` key

## Arduino Incubator Protocol
The Arduino outputs one line every ~5 s:
```
CO2: 412 ppm | Temp: 36.85 C | RH: 72.34 % | Setpoint: 37.00 C | Error: 0.15 | Heater PWM: 47 | Pump: STOPPED
```
Commands sent TO the Arduino: `FWD <ms>`, `REV <ms>`, `STOP`, `SETPOINT <temp>`

## Controller Mapping
Xbox controller actions are stored in `backend/config/controller_map.json` (auto-created). Default mapping: left stick → XY, right stick → Z, triggers → E, face buttons → step size changes.

## Running the App
```
python main.py
```
Requires: PyQt5, pyqtgraph, pygame, pyserial, opencv-python

## Notes
- The gantry firmware is **Marlin** (`FIRMWARE_IS_MARLIN = True`)
- Pump is on `FAN0` (`PUMP_FAN_INDEX = 0`)
- The DNX64 camera light DLL path is hardcoded to `C:\Users\macke\Desktop\Project Code\camera_light_test\`
- `try/except` blocks wrap all optional hardware imports so the app runs in simulation mode without connected hardware
