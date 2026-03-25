# gantry_keyboard_friendly.py
# -----------------------------------------------------------------------------
# Core-XY gantry control that ONLY talks via dict messages.
#
# Outbound to GUI (q_to_gui):
#   {"type":"state", ...}
#   {"type":"message","level":"info|warning|error","text":"..."}
#   {"type":"controller_state","mapping": {...}}   # forwarded if controller sends it
#
# Inbound from Controller (q_from_controller):
#   {"type":"input","cmd": <str>, "value": <tuple|number>}
#
# Inbound from GUI (q_from_gui):
#   {"type":"set_steps","xy_step":..,"z_step":..,"e_step":..}
#   {"type":"set_feed","feed_mm_min":..}
#   {"type":"home_all"}
#
# Works with either:
#   • Simulator backend (default when --simulate used in new_main.py)
#   • Marlin-like serial board (if pyserial present and simulate=False)
# -----------------------------------------------------------------------------

from __future__ import annotations
import math, time, queue
from dataclasses import dataclass
from typing import Dict, Optional

try:
    import serial  # type: ignore
    from serial.tools import list_ports  # type: ignore
except Exception:
    serial = None
    list_ports = None


# ------------------------------ data models -----------------------------------

@dataclass
class Steps:
    xy_step: float = 0.20
    z_step: float = 0.05
    e_step: float = 0.02

    def clamp(self) -> None:
        self.xy_step = max(0.001, min(self.xy_step, 10.0))
        self.z_step  = max(0.001, min(self.z_step,  10.0))
        self.e_step  = max(0.001, min(self.e_step,  10.0))


@dataclass
class State:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    e: float = 0.0
    xy_step: float = 0.20
    z_step: float = 0.05
    e_step: float = 0.02
    feed: int = 3000


# --------------------------- hardware backends --------------------------------

class StepperControlBoard:
    """
    Minimal Marlin-like serial interface (relative movement).
    Uses: G90/G91, G1, G28, M105, M114.
    """
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

    def _log(self, s: str): 
        if self.verbose: print("[HW]", s)

    def _probe(self) -> Optional[str]:
        for p in list_ports.comports():
            try:
                import serial as _serial
                with _serial.Serial(p.device, self.baudrate, timeout=0.6) as s:
                    s.write(b"\nM115\n"); s.flush(); time.sleep(0.2)
                    if "FIRMWARE_NAME" in s.read_all().decode(errors="ignore"):
                        return p.device
            except Exception:
                pass
        return None

    def _setup(self):
        self._send("G90")  # absolute
        self._send("G92 X0 Y0 Z0 E0")  # zero
        self._send("G91")  # switch to relative
        self._send("M211 S0")  # disable soft endstops → allow negatives


    def _send(self, gcode: str):
        self._log(f"> {gcode}")
        self.ser.write((gcode + "\n").encode())
        self.ser.flush()

    def _recv(self) -> str:
        time.sleep(0.03)
        out = self.ser.read_all().decode(errors="ignore")
        if self.verbose and out.strip(): print("[HW recv]", out.strip())
        return out

    def jog(self, axes: Dict[str, float], feed: int):
        if not axes:
            return
        s = " ".join(f"{k}{round(v,4)}" for k, v in axes.items())
        self._send(f"G1 F{feed} {s}")

    def home(self):
        self._send("G90"); self._send("G28"); self._send("G91")
        self._send("M211 S0")  # keep negatives allowed after homing


    def poll_position(self):
        self._send("M114"); p = self._recv()
        import re
        m = re.search(r"X:([-\d\.]+)\s+Y:([-\d\.]+)\s+Z:([-\d\.]+)\s+E:([-\d\.]+)", p)
        if m:
            self.x = float(m.group(1)); self.y = float(m.group(2))
            self.z = float(m.group(3)); self.e = float(m.group(4))


class StepperControlBoardSimulator:
    """Simulator for development without hardware."""
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.x = self.y = self.z = self.e = 0.0

    def _log(self, s: str):
        if self.verbose: print("[SIM]", s)

    def jog(self, axes: Dict[str, float], feed: int):
        # simple integrator
        self.x += axes.get("X", 0.0)
        self.y += axes.get("Y", 0.0)
        self.z += axes.get("Z", 0.0)
        self.e += axes.get("E", 0.0)
        self._log(f"Jog {axes} @F{feed}")

    def home(self):
        self.x = self.y = self.z = self.e = 0.0
        self._log("Home all")

    def poll_position(self):
        # nothing to do; positions kept locally
        pass


# ------------------------------ main system -----------------------------------

class GantrySystem:
    def __init__(self, q_to_gui, q_from_gui, q_from_controller, simulate: bool = True):
        self.q_to_gui = q_to_gui
        self.q_from_gui = q_from_gui
        self.q_from_controller = q_from_controller

        self.steps = Steps()
        self.state = State()
        self.feed = 3000

        # timing
        self.motion_dt = 0.05   # batch motion every 50 ms
        self.state_dt  = 0.10   # publish state every 100 ms
        self._dx = self._dy = self._dz = self._de = 0.0

        # backend
        if simulate:
            self.board = StepperControlBoardSimulator()
            self._send_message("info", "Gantry started (SIMULATOR).")
        else:
            try:
                self.board = StepperControlBoard()
                self._send_message("info", "Gantry started (HARDWARE).")
            except Exception as e:
                self.board = StepperControlBoardSimulator()
                self._send_message("warning", f"Hardware unavailable, using simulator. ({e})")

    # ------------------------------- loop -------------------------------------

    def run(self):
        t_motion = time.time()
        t_state  = time.time()
        while True:
            self._drain_gui()          # GUI → gantry
            self._drain_controller()   # controller/keyboard → gantry

            now = time.time()
            if now - t_motion >= self.motion_dt:
                self._flush_motion()
                t_motion = now

            if now - t_state >= self.state_dt:
                self.board.poll_position()
                self._publish_state()
                t_state = now

    # ------------------------------- inbound ----------------------------------

    def _drain_gui(self) -> None:
        try:
            while True:
                msg = self.q_from_gui.get_nowait()
                if not isinstance(msg, dict):
                    continue
                typ = msg.get("type")
                if   typ == "home_all": self._handle_home_all()
                elif typ == "set_steps": self._handle_set_steps(msg)
                elif typ == "set_feed":  self._handle_set_feed(msg)
        except queue.Empty:
            pass

    def _handle_home_all(self):
        self.board.home()
        self._send_message("info", "Homing all axes.")

    def _handle_set_steps(self, msg: Dict):
        for k in ("xy_step", "z_step", "e_step"):
            if k in msg:
                setattr(self.steps, k, float(msg[k]))
        self.steps.clamp()
        self._send_message(
            "info",
            f"Step sizes set XY={self.steps.xy_step:.3f}  Z={self.steps.z_step:.3f}  E={self.steps.e_step:.3f}"
        )

    def _handle_set_feed(self, msg: Dict):
        self.feed = int(msg.get("feed_mm_min", self.feed))
        self.state.feed = self.feed
        self._send_message("info", f"Feed set to {self.feed} mm/min.")

    def _drain_controller(self) -> None:
        try:
            while True:
                msg = self.q_from_controller.get_nowait()
                if not isinstance(msg, dict): 
                    continue

                # Forward controller_state straight to GUI (mapping updates etc.)
                if msg.get("type") == "controller_state":
                    self.q_to_gui.put(msg)
                    continue

                if msg.get("type") != "input":
                    continue
                cmd = msg.get("cmd"); val = msg.get("value")

                if cmd == "xy_motion":
                    jx, jy = (val if isinstance(val, (tuple, list)) else (0.0, 0.0))
                    self._dx += float(jx) * self.steps.xy_step
                    self._dy += float(jy) * self.steps.xy_step

                elif cmd == "z_motion":
                    _, jy = (val if isinstance(val, (tuple, list)) else (0.0, 0.0))
                    self._dz += float(jy) * self.steps.z_step

                elif cmd == "e_motion":
                    lt, rt = (val if isinstance(val, (tuple, list)) else (0.0, 0.0))
                    self._de += (float(rt) - float(lt)) * self.steps.e_step

                elif cmd == "xy_step_size_inc":
                    self.steps.xy_step *= 1.5; self.steps.clamp()
                    self._send_message("info", f"XY step -> {self.steps.xy_step:.3f}")
                elif cmd == "xy_step_size_dec":
                    self.steps.xy_step /= 1.5; self.steps.clamp()
                    self._send_message("info", f"XY step -> {self.steps.xy_step:.3f}")

                elif cmd == "z_step_size_inc":
                    self.steps.z_step *= 1.5; self.steps.clamp()
                    self._send_message("info", f"Z step -> {self.steps.z_step:.3f}")
                elif cmd == "z_step_size_dec":
                    self.steps.z_step /= 1.5; self.steps.clamp()
                    self._send_message("info", f"Z step -> {self.steps.z_step:.3f}")

                elif cmd == "e_step_size_inc":
                    self.steps.e_step *= 1.5; self.steps.clamp()
                    self._send_message("info", f"E step -> {self.steps.e_step:.4f}")
                elif cmd == "e_step_size_dec":
                    self.steps.e_step /= 1.5; self.steps.clamp()
                    self._send_message("info", f"E step -> {self.steps.e_step:.4f}")

                elif cmd == "home_all":
                    # Route to the same handler as GUI button
                    self._handle_home_all()

        except queue.Empty:
            pass

    # ------------------------------- motion -----------------------------------

    def _flush_motion(self) -> None:
        axes = {}
        if abs(self._dx) > 1e-6: axes["X"] = self._dx
        if abs(self._dy) > 1e-6: axes["Y"] = self._dy
        if abs(self._dz) > 1e-6: axes["Z"] = self._dz
        if abs(self._de) > 1e-6: axes["E"] = self._de

        if axes:
            dist = math.sqrt(sum(v*v for v in axes.values()))
            feed = max(300, min(int((dist / self.motion_dt) * 60.0), self.feed))
            self.board.jog(axes, feed)
            # clear accumulators
            self._dx = self._dy = self._dz = self._de = 0.0

    # ------------------------------- outbound ---------------------------------

    def _publish_state(self) -> None:
        # update state from backend
        self.state.x = getattr(self.board, "x", 0.0)
        self.state.y = getattr(self.board, "y", 0.0)
        self.state.z = getattr(self.board, "z", 0.0)
        self.state.e = getattr(self.board, "e", 0.0)
        self.state.xy_step = self.steps.xy_step
        self.state.z_step  = self.steps.z_step
        self.state.e_step  = self.steps.e_step
        self.state.feed    = self.feed

        self.q_to_gui.put({
            "type": "state",
            "x": self.state.x, "y": self.state.y, "z": self.state.z, "e": self.state.e,
            "xy_step": self.state.xy_step,
            "z_step": self.state.z_step,
            "e_step": self.state.e_step,
            "feed": self.state.feed,
        })

    def _send_message(self, level: str, text: str) -> None:
        self.q_to_gui.put({"type": "message", "level": level, "text": text})
