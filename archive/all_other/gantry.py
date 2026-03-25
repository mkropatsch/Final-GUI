# gantry.py
# -----------------------------------------------------------------------------
# Core-XY gantry control that ONLY emits dict messages to GUI:
#   State:   {"type":"state", ...}
#   Message: {"type":"message","level":"info|warning|error","text":"..."}
#
# It ONLY consumes dict messages from Controller:
#   {"type":"input","cmd": <string>, "value": <tuple|number>}
#
# And dict messages from GUI (as before), e.g.:
#   {"type":"set_steps", ...}, {"type":"home_all"}, {"type":"set_feed", ...}
# -----------------------------------------------------------------------------

from __future__ import annotations
import math
import queue
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

try:
    import serial
    import serial.tools.list_ports as list_ports
except Exception:
    serial = None
    list_ports = None


# ------------------------------ data classes ---------------------------------

@dataclass
class StepSizes:
    xy_step: float = 0.20
    z_step: float = 0.05
    e_step: float = 0.02
    def clamp(self):
        self.xy_step = max(0.005, min(self.xy_step, 5.0))
        self.z_step = max(0.001, min(self.z_step, 2.0))
        self.e_step = max(0.001, min(self.e_step, 1.0))

@dataclass
class GantryState:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    e: float = 0.0
    bed: Optional[float] = None
    bed_set: Optional[float] = None
    steps: StepSizes = field(default_factory=StepSizes)
    feed: int = 3000


# --------------------------- hardware backends --------------------------------

class StepperControlBoard:
    """Minimal Marlin-like serial interface (relative movement)."""
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
        # cached
        self.x = self.y = self.z = self.e = 0.0
        self.bed_temp = self.bed_set = None

    def _probe(self) -> Optional[str]:
        for p in list_ports.comports():
            try:
                import serial as _serial
                with _serial.Serial(p.device, self.baudrate, timeout=0.8) as s:
                    s.write(b"\nM115\n"); s.flush(); time.sleep(0.2)
                    if "FIRMWARE_NAME" in s.read_all().decode(errors="ignore"):
                        return p.device
            except Exception:
                continue
        return None

    def _send(self, gcode: str):
        if self.verbose:
            print("[TX]", gcode)
        self.ser.write(gcode.encode("utf-8") + b"\n"); self.ser.flush()

    def _recv(self) -> str:
        time.sleep(0.03)
        data = self.ser.read_all().decode(errors="ignore")
        if self.verbose and data.strip():
            print("[RX]", data.strip())
        return data

    def _setup(self):
        self._send("M302 S0")  # ignore cold extrude
        self._send("M83")      # E relative
        self._send("G91")      # XYZ relative
        self._send("M203 X6000 Y6000 Z1800 E1800")

    def jog(self, axes: Dict[str, float], feed: int):
        axes = {k: v for k, v in axes.items() if abs(v) > 1e-6}
        if not axes:
            return
        s = " ".join(f"{k}{round(v,4)}" for k, v in axes.items())
        self._send(f"G1 F{feed} {s}")

    def home(self):
        self._send("G90"); self._send("G28"); self._send("G91")

    def request_data(self):
        self._send("M105"); t = self._recv()
        import re
        m = re.search(r"B:(\d+\.?\d*)\s*/\s*(\d+\.?\d*)", t)
        if m:
            self.bed_temp = float(m.group(1)); self.bed_set = float(m.group(2))
        self._send("M114"); p = self._recv()
        m = re.search(r"X:([-\d\.]+)\s+Y:([-\d\.]+)\s+Z:([-\d\.]+)\s+E:([-\d\.]+)", p)
        if m:
            self.x = float(m.group(1)); self.y = float(m.group(2))
            self.z = float(m.group(3)); self.e = float(m.group(4))


class StepperControlBoardSimulator:
    """Simulator for development without hardware."""
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.x = self.y = self.z = self.e = 0.0
        self.bed_temp = 25.0; self.bed_set = 37.0

    def _log(self, s: str):
        if self.verbose: print("[SIM]", s)

    def jog(self, axes: Dict[str, float], feed: int):
        axes = {k: v for k, v in axes.items() if abs(v) > 1e-6}
        if not axes: return
        self._log(f"G1 F{feed} " + " ".join(f"{k}{v:.4f}" for k, v in axes.items()))
        self.x += axes.get("X", 0.0); self.y += axes.get("Y", 0.0)
        self.z += axes.get("Z", 0.0); self.e += axes.get("E", 0.0)

    def home(self):
        self._log("G90; G28; G91")
        self.x = self.y = self.z = 0.0

    def request_data(self):
        # drift bed towards set
        self.bed_temp += (self.bed_set - self.bed_temp) * 0.1


# ------------------------------ Gantry system ---------------------------------

class GantrySystem:
    """
    Consumes:
        - Controller -> {"type":"input","cmd":..., "value":...}
        - GUI        -> {"type":"set_steps"/"home_all"/"set_feed"...}
    Produces:
        - GUI        -> {"type":"state", ...}
                      -> {"type":"message","level":"info|warning|error","text":...}
    """
    def __init__(self, q_to_gui, q_from_gui, q_from_controller,
                 simulate: bool = False,
                 motion_dt: float = 0.05,
                 gui_dt: float = 0.20,
                 base_feed: int = 3000):
        self.q_to_gui = q_to_gui
        self.q_from_gui = q_from_gui
        self.q_from_controller = q_from_controller

        self.motion_dt = motion_dt
        self.gui_dt = gui_dt
        self.feed = base_feed
        self.steps = StepSizes()
        self.state = GantryState(steps=self.steps, feed=self.feed)

        # axis inversion (joystick up is -Y, invert to screen +Y)
        self.flip_x = +1.0
        self.flip_y = -1.0
        self.flip_z = +1.0
        self.flip_e = +1.0

    # ----------------------------- process loop ------------------------------

    def run(self) -> None:
        # Construct hardware inside child
        board = StepperControlBoardSimulator() if (serial is None) else self._try_board()
        t_motion = time.monotonic()
        t_gui = time.monotonic()
        self._send_message("info", "Gantry started.")

        while True:
            self._drain_gui(board)
            self._drain_controller()

            now = time.monotonic()
            if now - t_motion >= self.motion_dt:
                self._flush_motion(board)
                t_motion = now
            if now - t_gui >= self.gui_dt:
                board.request_data()
                self._publish_state(board)
                t_gui = now

            time.sleep(0.001)

    def _try_board(self):
        try:
            return StepperControlBoard()
        except Exception as e:
            self._send_message("warning", f"No board detected, using simulator: {e}")
            return StepperControlBoardSimulator()

    # ------------------------------- inbound ---------------------------------

    def _drain_gui(self, board) -> None:
        try:
            while True:
                msg = self.q_from_gui.get_nowait()
                if not isinstance(msg, dict):
                    continue
                typ = msg.get("type")
                if typ in ("gantry_cmd", "set_steps", "home_all", "set_feed"):
                    # accept both explicit "gantry_cmd" and legacy names
                    self._handle_gui_command(board, msg)
        except queue.Empty:
            pass

    def _handle_gui_command(self, board, msg: Dict) -> None:
        typ = msg.get("type")
        if typ == "home_all":
            board.home()
            self._send_message("info", "Homing all axes.")
        elif typ == "set_steps" or (typ == "gantry_cmd" and msg.get("cmd") == "set_steps"):
            for k in ("xy_step", "z_step", "e_step"):
                if k in msg:
                    setattr(self.steps, k, float(msg[k]))
            self.steps.clamp()
            self._send_message("info", f"Step sizes set XY={self.steps.xy_step:.3f} Z={self.steps.z_step:.3f} E={self.steps.e_step:.3f}")
        elif typ == "set_feed" or (typ == "gantry_cmd" and msg.get("cmd") == "set_feed"):
            self.feed = int(msg.get("feed_mm_min", self.feed))
            self.state.feed = self.feed
            self._send_message("info", f"Feed set to {self.feed} mm/min.")

    def _drain_controller(self) -> None:
        try:
            while True:
                msg = self.q_from_controller.get_nowait()
                if not isinstance(msg, dict):
                    continue

                # NEW: pass-through of controller state to GUI
                if msg.get("type") == "controller_state":
                    # forward exactly as-is so GUI can consume it
                    self.q_to_gui.put({"type": "controller_state", "mapping": msg.get("mapping", {})})
                    continue

                # Existing: only process normalized input messages
                if msg.get("type") == "input":
                    cmd = str(msg.get("cmd", ""))
                    val = msg.get("value")
                    self._apply_input(cmd, val)
        except queue.Empty:
            pass

    # ------------------------------- motion ----------------------------------

    def _apply_input(self, cmd: str, val) -> None:
        # accumulate deltas
        if cmd == "xy_motion" and isinstance(val, (tuple, list)) and len(val) == 2:
            jx, jy = float(val[0]), float(val[1])
            self._dx += self.flip_x * (jx * self.steps.xy_step)
            self._dy += self.flip_y * (jy * self.steps.xy_step)

        elif cmd == "z_motion" and isinstance(val, (tuple, list)) and len(val) == 2:
            _jx, jy = float(val[0]), float(val[1])
            self._dz += self.flip_z * (jy * self.steps.z_step)

        elif cmd == "e_motion" and isinstance(val, (tuple, list)) and len(val) == 2:
            lt, rt = float(val[0]), float(val[1])
            self._de += self.flip_e * ((rt - lt) * self.steps.e_step)

        elif cmd == "xy_step_size_inc":
            self.steps.xy_step *= 1.5; self.steps.clamp()
            self._send_message("info", f"XY step -> {self.steps.xy_step:.4f}")

        elif cmd == "xy_step_size_dec":
            self.steps.xy_step /= 1.5; self.steps.clamp()
            self._send_message("info", f"XY step -> {self.steps.xy_step:.4f}")

        elif cmd == "z_step_size_inc":
            self.steps.z_step *= 1.5; self.steps.clamp()
            self._send_message("info", f"Z step -> {self.steps.z_step:.4f}")

        elif cmd == "z_step_size_dec":
            self.steps.z_step /= 1.5; self.steps.clamp()
            self._send_message("info", f"Z step -> {self.steps.z_step:.4f}")

        elif cmd == "e_step_size_inc":
            self.steps.e_step *= 1.5; self.steps.clamp()
            self._send_message("info", f"E step -> {self.steps.e_step:.4f}")

        elif cmd == "e_step_size_dec":
            self.steps.e_step /= 1.5; self.steps.clamp()
            self._send_message("info", f"E step -> {self.steps.e_step:.4f}")

        elif cmd == "home_all":
            # forward to GUI handler via gantry command
            self.q_from_gui.put({"type": "home_all"})

    def _flush_motion(self, board) -> None:
        axes = {}
        if abs(getattr(self, "_dx", 0.0)) > 1e-6: axes["X"] = getattr(self, "_dx")
        if abs(getattr(self, "_dy", 0.0)) > 1e-6: axes["Y"] = getattr(self, "_dy")
        if abs(getattr(self, "_dz", 0.0)) > 1e-6: axes["Z"] = getattr(self, "_dz")
        if abs(getattr(self, "_de", 0.0)) > 1e-6: axes["E"] = getattr(self, "_de")

        if axes:
            dist = math.sqrt(sum(v*v for v in axes.values()))
            feed = max(300, min(int((dist / self.motion_dt) * 60.0), self.feed))
            board.jog(axes, feed)
            # clear accumulators
            self._dx = self._dy = self._dz = self._de = 0.0

    # ------------------------------- outbound ---------------------------------

    def _publish_state(self, board) -> None:
        # pull from hardware/sim
        self.state.x = getattr(board, "x", 0.0)
        self.state.y = getattr(board, "y", 0.0)
        self.state.z = getattr(board, "z", 0.0)
        self.state.e = getattr(board, "e", 0.0)
        self.state.bed = getattr(board, "bed_temp", None)
        self.state.bed_set = getattr(board, "bed_set", None)

        self.q_to_gui.put({
            "type": "state",
            "x": self.state.x,
            "y": self.state.y,
            "z": self.state.z,
            "e": self.state.e,
            "bed_temp": self.state.bed,
            "bed_set": self.state.bed_set,
            "xy_step": self.steps.xy_step,
            "z_step": self.steps.z_step,
            "e_step": self.steps.e_step,
            "feed": self.feed,
        })

    def _send_message(self, level: str, text: str) -> None:
        self.q_to_gui.put({"type": "message", "level": level, "text": text})

    # initialize accumulators on first use
    _dx = 0.0
    _dy = 0.0
    _dz = 0.0
    _de = 0.0
