# gantry_new.py
# ---------------------------------------------------------------------
# Backend for Core-XY Gantry
# - G-code passthrough
# - Relative jogs (from controller queue) and absolute moves
# - Step/Feed settings
# - Pump control (FAN0)
# - NEW: "move_steps_xy" (converts step counts → mm and queues a smooth jog)
# - Smooth motion: deltas flushed every 50 ms as a single G1 at current feed
# ---------------------------------------------------------------------

from __future__ import annotations
import queue, time
from dataclasses import dataclass, field
from typing import Dict, Optional

try:
    import serial
    import serial.tools.list_ports as list_ports
except Exception:
    serial = None
    list_ports = None

PUMP_FAN_INDEX = 0
FIRMWARE_IS_MARLIN = True


# ------------------------------ data classes ---------------------------------
@dataclass
class StepSizes:
    xy_step: float = 0.200
    z_step: float = 0.050
    e_step: float = 0.020
    def clamp(self):
        self.xy_step = max(0.005, min(self.xy_step, 5.0))
        self.z_step  = max(0.001, min(self.z_step,  2.0))
        self.e_step  = max(0.001, min(self.e_step,  1.0))

@dataclass
class GantryState:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    e: float = 0.0
    steps: StepSizes = field(default_factory=StepSizes)
    feed: int = 3000
    pump_0: int = 0


# --------------------------- hardware backends --------------------------------
class StepperControlBoard:
    def __init__(self, baudrate: int = 115200, verbose: bool = False):
        if serial is None or list_ports is None:
            raise RuntimeError("pyserial not available")
        self.verbose = verbose
        self.baudrate = baudrate
        self.ser = None
        port = self._probe()
        if port is None:
            raise RuntimeError("No printer found")
        import serial as _serial
        self.ser = _serial.Serial(port, baudrate=self.baudrate, timeout=1)
        self._setup()
        self.x = self.y = self.z = self.e = 0.0

    def _probe(self) -> Optional[str]:
        for p in list_ports.comports():
            try:
                import serial as _serial
                with _serial.Serial(p.device, self.baudrate, timeout=0.6) as s:
                    s.write(b"\nM115\n"); s.flush(); time.sleep(0.2)
                    if "FIRMWARE_NAME" in s.read_all().decode(errors="ignore"):
                        return p.device
            except Exception:
                continue
        return None

    def _send_line(self, gcode: str):
        if not gcode: return
        if self.verbose: print("[TX]", gcode)
        self.ser.write(gcode.encode("utf-8") + b"\n"); self.ser.flush()

    def _setup(self):
        self._send_line("G21")  # mm
        self._send_line("G91")  # relative default for jogs

    def send_gcode(self, cmd: str):
        self._send_line(cmd)

    def quick_stop(self):
        self._send_line("M410")

    def fan_set(self, index: int, value_0_255: int):
        v = max(0, min(255, int(value_0_255)))
        if v <= 0:
            self._send_line(f"M107 P{index}")
            self._send_line("M107")  # some firmwares ignore P
        else:
            self._send_line(f"M106 P{index} S{v}")

    def jog(self, axes: Dict[str, float], feed: int):
        axes = {k: v for k, v in axes.items() if abs(v) > 1e-6}
        if not axes: return
        self._send_line("G91")
        self._send_line(f"G1 F{int(feed)} " + " ".join(f"{k}{v:.4f}" for k, v in axes.items()))
        self.x += axes.get("X", 0.0); self.y += axes.get("Y", 0.0)
        self.z += axes.get("Z", 0.0); self.e += axes.get("E", 0.0)

    def abs_move(self, axes: Dict[str, float], feed: int):
        axes = {k: v for k, v in axes.items() if isinstance(v, (int, float))}
        if not axes: return
        self._send_line("G90")
        self._send_line(f"G1 F{int(feed)} " + " ".join(f"{k}{v:.4f}" for k, v in axes.items()))
        self._send_line("G91")
        self.x = axes.get("X", self.x)
        self.y = axes.get("Y", self.y)
        self.z = axes.get("Z", self.z)
        self.e = axes.get("E", self.e)

    def home(self):
        self._send_line("G90"); self._send_line("G28"); self._send_line("G91")
        self.x = self.y = self.z = 0.0

    def request_data(self):
        pass


class StepperControlBoardSimulator:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.x = self.y = self.z = self.e = 0.0
        self.pump = {0: 0}
    def _log(self, s: str):
        if self.verbose: print("[SIM]", s)
    def send_gcode(self, cmd: str): self._log(cmd)
    def quick_stop(self): self._log("M410")
    def fan_set(self, index: int, value_0_255: int):
        v = max(0, min(255, int(value_0_255))); self.pump[index] = v
        if v <= 0: self._log(f"M107 P{index}")
        else:      self._log(f"M106 P{index} S{v}")
    def jog(self, axes: Dict[str, float], feed: int):
        axes = {k: v for k, v in axes.items() if abs(v) > 1e-6}
        if not axes: return
        self._log(f"G91; G1 F{feed} " + " ".join(f"{k}{v:.4f}" for k, v in axes.items()))
        self.x += axes.get("X", 0.0); self.y += axes.get("Y", 0.0)
        self.z += axes.get("Z", 0.0); self.e += axes.get("E", 0.0)
    def abs_move(self, axes: Dict[str, float], feed: int):
        axes = {k: v for k, v in axes.items() if isinstance(v, (int, float))}
        if not axes: return
        self._log(f"G90; G1 F{feed} " + " ".join(f"{k}{v:.4f}" for k, v in axes.items()) + "; G91")
        self.x = axes.get("X", self.x)
        self.y = axes.get("Y", self.y)
        self.z = axes.get("Z", self.z)
        self.e = axes.get("E", self.e)
    def home(self):
        self._log("G90; G28; G91")
        self.x = self.y = self.z = 0.0
    def request_data(self): pass


# ------------------------------ Gantry system ---------------------------------
class GantrySystem:
    """
    Consumes (GUI):
      - {"type":"set_steps"|"set_feed"|"home_all"|"gcode"|"fan_set"|"btn_estop"}
      - {"type":"gantry_cmd","cmd": "move_rel"|"move_abs"|"move_steps_xy"}
    Consumes (Controller):
      - {"type":"input","cmd": "xy_motion"|"z_motion"|"e_motion", "value": tuple}
    Produces (GUI):
      - {"type":"state", ...}
      - {"type":"message","level": "...", "text": "..."}
    """
    def __init__(self, q_to_gui, q_from_gui, q_from_controller,
                 simulate: bool = False, motion_dt: float = 0.05, gui_dt: float = 0.20,
                 base_feed: int = 3000):
        self.q_to_gui = q_to_gui
        self.q_from_gui = q_from_gui
        self.q_from_controller = q_from_controller

        self.motion_dt = motion_dt
        self.gui_dt = gui_dt
        self.feed = base_feed
        self.steps = StepSizes()
        self.state = GantryState(steps=self.steps, feed=self.feed)

        # screen coordinates: +Y up → invert machine Y if needed
        self.flip_x = +1.0
        self.flip_y = -1.0
        self.flip_z = +1.0
        self.flip_e = +1.0

        self._dx = self._dy = self._dz = self._de = 0.0

        self._simulate_flag = simulate
        self._board = None

    # ----------------------------- main loop ---------------------------------
    def run(self) -> None:
        board = self._try_board()
        self._board = board
        t_motion = time.monotonic()
        t_gui = time.monotonic()
        self._send_message("info", "Gantry started.")

        while True:
            self._drain_gui(board)         # settings, abs moves, pump, estop, etc.
            self._drain_controller()       # manual-jog inputs from GUI timers

            now = time.monotonic()
            if now - t_motion >= self.motion_dt:
                self._flush_motion(board); t_motion = now
            if now - t_gui >= self.gui_dt:
                try: board.request_data()
                except Exception as e: self._send_message("warning", f"request_data failed: {e}")
                self._publish_state(board); t_gui = now

            time.sleep(0.001)

    def _try_board(self):
        if self._simulate_flag or serial is None:
            self._send_message("warning", "Using simulator backend.")
            return StepperControlBoardSimulator()
        try:
            return StepperControlBoard()
        except Exception as e:
            self._send_message("warning", f"No board detected, using simulator: {e}")
            return StepperControlBoardSimulator()

    # ------------------------------ inbound ----------------------------------
    def _drain_gui(self, board) -> None:
        try:
            while True:
                msg = self.q_from_gui.get_nowait()
                if not isinstance(msg, dict): continue
                typ = msg.get("type")

                if typ == "gcode":
                    cmd = str(msg.get("cmd", "")).strip()
                    if cmd:
                        try: board.send_gcode(cmd)
                        except Exception as e: self._send_message("error", f"GCODE failed: {e}")

                elif typ == "home_all":
                    try: board.home(); self._send_message("info", "Homing all axes.")
                    except Exception as e: self._send_message("error", f"Home failed: {e}")

                elif typ == "set_steps" or (typ == "gantry_cmd" and msg.get("cmd") == "set_steps"):
                    for k in ("xy_step", "z_step", "e_step"):
                        if k in msg: setattr(self.steps, k, float(msg[k]))
                    self.steps.clamp()
                    self._send_message("info", f"Steps XY={self.steps.xy_step:.3f} Z={self.steps.z_step:.3f} E={self.steps.e_step:.3f}")

                elif typ == "set_feed" or (typ == "gantry_cmd" and msg.get("cmd") == "set_feed"):
                    self.feed = int(msg.get("feed_mm_min", self.feed))
                    self.state.feed = self.feed
                    self._send_message("info", f"Feed={self.feed} mm/min")

                elif typ == "fan_set":
                    try:
                        board.fan_set(int(msg.get("index", PUMP_FAN_INDEX)), int(msg.get("value", 0)))
                        self.state.pump_0 = int(msg.get("value", 0))
                    except Exception as e:
                        self._send_message("error", f"Pump set failed: {e}")

                elif typ == "btn_estop":
                    try: board.quick_stop()
                    except Exception: pass
                    self._dx = self._dy = self._dz = self._de = 0.0
                    try: board.fan_set(PUMP_FAN_INDEX, 0)
                    except Exception: pass
                    self._send_message("warning", "E-STOP: motion aborted, pump off.")

                elif typ == "gantry_cmd":
                    cmd = str(msg.get("cmd", ""))

                    if cmd == "move_rel":
                        dx = float(msg.get("dx", 0.0)); dy = float(msg.get("dy", 0.0))
                        dz = float(msg.get("dz", 0.0)); de = float(msg.get("de", 0.0))
                        if "feed_mm_min" in msg:
                            self.feed = int(msg["feed_mm_min"]); self.state.feed = self.feed
                        self._dx += self.flip_x * dx; self._dy += self.flip_y * dy
                        self._dz += self.flip_z * dz; self._de += self.flip_e * de
                        self._send_message("info", f"Queued move_rel dx={dx} dy={dy} dz={dz} de={de}")

                    elif cmd == "move_abs":
                        X = msg.get("X", None); Y = msg.get("Y", None)
                        Z = msg.get("Z", None); E = msg.get("E", None)
                        feed = int(msg.get("feed_mm_min", self.feed))
                        axes = {}
                        if isinstance(X, (int, float)): axes["X"] = float(X)
                        if isinstance(Y, (int, float)): axes["Y"] = float(Y)
                        if isinstance(Z, (int, float)): axes["Z"] = float(Z)
                        if isinstance(E, (int, float)): axes["E"] = float(E)
                        try:
                            board.abs_move(axes, feed)
                            self.feed = feed; self.state.feed = feed
                            self._send_message("info", f"ABS move -> {axes} @ F{feed}")
                        except Exception as e:
                            self._send_message("error", f"ABS move failed: {e}")

                    elif cmd == "move_steps_xy":  # NEW
                        nx = int(msg.get("nx", 0)); ny = int(msg.get("ny", 0))
                        dx = float(nx) * self.steps.xy_step
                        dy = float(ny) * self.steps.xy_step
                        self._dx += self.flip_x * dx
                        self._dy += self.flip_y * dy
                        self._send_message("info", f"Queued move_steps_xy: dx={dx:.4f}, dy={dy:.4f} (nx={nx}, ny={ny})")

        except queue.Empty:
            pass

    def _drain_controller(self) -> None:
        try:
            while True:
                msg = self.q_from_controller.get_nowait()
                if not isinstance(msg, dict): continue
                if msg.get("type") != "input": continue
                cmd = str(msg.get("cmd", "")); val = msg.get("value")

                if cmd == "xy_motion" and isinstance(val, (tuple, list)) and len(val) == 2:
                    jx, jy = float(val[0]), float(val[1])
                    self._dx += self.flip_x * (jx * self.steps.xy_step)
                    self._dy += self.flip_y * (jy * self.steps.xy_step)
                elif cmd == "z_motion" and isinstance(val, (tuple, list)) and len(val) == 2:
                    _jx, jy = float(val[0]), float(val[1])
                    self._dz += self.flip_z * (jy * self.steps.z_step)
                elif cmd == "e_motion" and isinstance(val, (tuple, list)) and len(val) == 2:
                    _lt, rt = float(val[0]), float(val[1])
                    self._de += self.flip_e * (rt * self.steps.e_step)
        except queue.Empty:
            pass

    # ------------------------------- motion ----------------------------------
    def _flush_motion(self, board) -> None:
        dx, dy, dz, de = self._dx, self._dy, self._dz, self._de
        self._dx = self._dy = self._dz = self._de = 0.0
        axes = {}
        if abs(dx) > 1e-9: axes["X"] = dx
        if abs(dy) > 1e-9: axes["Y"] = dy
        if abs(dz) > 1e-9: axes["Z"] = dz
        if abs(de) > 1e-9: axes["E"] = de
        if axes:
            try: board.jog(axes, self.feed)
            except Exception as e: self._send_message("error", f"Jog failed: {e}")

    # ------------------------------- outbound --------------------------------
    def _publish_state(self, board) -> None:
        self.state.x = getattr(board, "x", 0.0)
        self.state.y = getattr(board, "y", 0.0)
        self.state.z = getattr(board, "z", 0.0)
        self.state.e = getattr(board, "e", 0.0)
        self.q_to_gui.put({
            "type": "state",
            "x": self.state.x, "y": self.state.y, "z": self.state.z, "e": self.state.e,
            "xy_step": self.steps.xy_step, "z_step": self.steps.z_step, "e_step": self.steps.e_step,
            "feed": self.feed,
        })

    def _send_message(self, level: str, text: str):
        self.q_to_gui.put({"type": "message", "level": level, "text": text})
