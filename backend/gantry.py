# gantry_gui_new_automate_2.2.py

# 2.2 - Adding Xbox controller

# ---------------------------------------------------------------------
# Backend for Core-XY Gantry
# - G-code passthrough
# - Relative jogs (from controller queue) and absolute moves
# - Step/Feed settings
# - Pump control (FAN0)
# - "move_steps_xy" (converts step counts → mm and queues a smooth jog)
# - Smooth motion: deltas flushed every 50 ms as a single G1 at current feed
# ---------------------------------------------------------------------



# ------- LOOK FOR 'Fix' TO FIND AREAS FOR IMPROVEMENT (identified after commenting) -----


from __future__ import annotations  # Make type hints more flexible and less likely to break things
import queue, time  # queue -> mailbox system, time -> run in loops with timing
from dataclasses import dataclass, field # clean data storage -> containers for storing values like position
from typing import Dict, Optional # Doesn't change behavior, makes it easier to understand

try:  # lets code talk to motor board, works -> send G-code, fails -> simulation
    import serial
    import serial.tools.list_ports as list_ports
except Exception:
    serial = None
    list_ports = None

PUMP_FAN_INDEX = 0  # pump is connected to FAN0
FIRMWARE_IS_MARLIN = True  # Using marlin firmware


# ------------------------------ data classes ---------------------------------
@dataclass # dataclass just stores everything in a neat little box, groups everything nicely
class StepSizes: # stores movement increment sizes , not actual machine position, just how big jog should be
    xy_step: float = 0.500 # xy move per jog unit
    z_step: float = 0.10 # z move per jog unit
    e_step: float = 0.020 # e move per jog unit
    def clamp(self): # safety limiter, if there is a crazy step size, force it to stay inside an allowed range
        self.xy_step = max(0.005, min(self.xy_step, 5.0)) # xy range
        self.z_step  = max(0.001, min(self.z_step,  2.0)) # z range
        self.e_step  = max(0.001, min(self.e_step,  1.0)) # e range
# Big picture: stores current jog size settings and prevent unreasonable settings

@dataclass 
class GantryState: # stores system's current idea of the gantry state, when program starts, all begin at 0
    # Backend stored state, not necessarily true measured hardware position
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    e: float = 0.0
    steps: StepSizes = field(default_factory=StepSizes) # takes from StepSize class
    feed: int = 3000 # movement speed
    pump_0: int = 0 # pump state -> 0 = off
    # This tracking assumes motion is perfect, based off software not hardware
    # Not necessarily real feedback

# class vs dataclass: class = person (can do things), dataclass = ID card (just holds info)

@dataclass
class MotionChannel:
    name: str
    board: object | None = None
    state: GantryState = field(default_factory=GantryState)
    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0
    de: float = 0.0
    connected: bool = False
    port: str | None = None

# --------------------------- hardware backends --------------------------------


class StepperControlBoard: # class (unlike dataclass) can have behavior functions, more of a full-featured object rather than a container

    def __init__(self, port: str | None = None, baudrate: int = 115200, verbose: bool = False): # if true, prints each command it sends, baudrate is communication speed
        if serial is None or list_ports is None:
            raise RuntimeError("pyserial not available") 
        self.verbose = verbose
        self.baudrate = baudrate
        self.ser = None

        if port is None:
            port = self._probe() # searches to find serial port board is connected to
        else:
            if not self._is_firmware_port(port):
                raise RuntimeError(f"Selected port {port} is not a valid gantry board.")
            
        if port is None:
            raise RuntimeError("No printer found")
        
        self.port = port

        import serial as _serial
        self.ser = _serial.Serial(self.port, baudrate=self.baudrate, timeout=1)
        self._setup() # after connecting, sends setup commands
        self.x = self.y = self.z = self.e = 0.0 # creates board object's internal software position values, not actually read from the board


    def _probe(self) -> Optional[str]:
        for p in list_ports.comports():
            try:
                import serial as _serial
                with _serial.Serial(p.device, self.baudrate, timeout=1.5) as s:
                    s.write(b"\nM115\n"); s.flush(); time.sleep(0.2) # M115 asks firmware to identify itself
                    if "FIRMWARE_NAME" in s.read_all().decode(errors="ignore"):
                        return p.device
            except Exception:
                continue
        return None # would mean the search failed
    
    def _is_firmware_port(self, port: str) -> bool:
        try:
            import serial as _serial
            with _serial.Serial(port, self.baudrate, timeout=1.5) as s:
                s.write(b"\nM115\n")
                s.flush()
                time.sleep(0.2)
                reply = s.read_all().decode(errors="ignore")
                return "FIRMWARE_NAME" in reply
        except Exception:
            return False


    def _send_line(self, gcode: str): # send one command function
        if not gcode: return # prevents sending blank commands accidentally
        if self.verbose: print("[TX]", gcode) # if verbose is true, will print the command
        self.ser.write(gcode.encode("utf-8") + b"\n"); self.ser.flush() # sends G-code text over serial, flush pushes the command out immediately


    ## New for messages: ##
    def _read_available_lines(self, max_lines: int = 50) -> list[str]: #limits output at 50 lines
        """Read whatever lines are currently waiting in the serial buffer."""
        lines: list[str] = []
        if self.ser is None:
            return lines
        # Read until buffer empties or max_lines hit
        for _ in range(max_lines):
            try:
                if self.ser.in_waiting <= 0:
                    break
                raw = self.ser.readline().decode("utf-8", errors="ignore").strip()
                if raw:
                    lines.append(raw)
            except Exception:
                break
        return lines
    ### End New


    def _setup(self):
        self._send_line("G21")  # mm
        self._send_line("G91")  # relative default for jogs (relative positioning -> X10 = move 10 mm from where you are now)


    def send_gcode(self, cmd: str): # thin wrapper around _send_line()
        self._send_line(cmd)


    ## NEW reply commands for gcode ##
    def send_gcode_with_reply(self, cmd: str, wait_s: float = 0.4) -> list[str]: #sends a command then waits for a reply
        """Send a line and collect reply lines for a short window.""" #good for anything that answers in text (M115, M114)
        self._send_line(cmd)
        t0 = time.monotonic()
        out: list[str] = []
        while time.monotonic() - t0 < wait_s:
            out.extend(self._read_available_lines())
            time.sleep(0.01)
        return out
    ## END new message reply


    def quick_stop(self):
        self._send_line("M410") #stops motion immediately


    def fan_set(self, index: int, value_0_255: int):
        v = max(0, min(255, int(value_0_255)))
        if v <= 0:
            self._send_line(f"M107 P{index}") # fan off
            self._send_line("M107")  # some firmwares ignore P
        else:
            self._send_line(f"M106 P{index} S{v}") # fan on / set speed


    def jog(self, axes: Dict[str, float], feed: int): # performs a relative move
        axes = {k: v for k, v in axes.items() if abs(v) > 1e-6} #removes tiny near-zero values
        if not axes: return # nothing real, don't send a move
        self._send_line("G91") # relative mode is active (better for jogs)
        self._send_line(f"G1 F{int(feed)} " + " ".join(f"{k}{v:.4f}" for k, v in axes.items())) #G1 -> linear move
        self.x += axes.get("X", 0.0); self.y += axes.get("Y", 0.0) # code updates stored position value
        self.z += axes.get("Z", 0.0); self.e += axes.get("E", 0.0)


    def abs_move(self, axes: Dict[str, float], feed: int): #performs absolute move
        axes = {k: v for k, v in axes.items() if isinstance(v, (int, float))}
        if not axes: return
        self._send_line("G90") #absolute positioning mode
        self._send_line(f"G1 F{int(feed)} " + " ".join(f"{k}{v:.4f}" for k, v in axes.items())) #sends absolute move command
        self._send_line("G91") #switches back to relative mode after (rest of system expects relative jogs)
        self.x = axes.get("X", self.x) #updates software position assuming successful movement
        self.y = axes.get("Y", self.y)
        self.z = axes.get("Z", self.z)
        self.e = axes.get("E", self.e)


    def home(self): # switches to absolute, sends home, switches back to relative
        self._send_line("G90"); self._send_line("G28"); self._send_line("G91")
        self.x = self.y = self.z = 0.0
    
    # ----- User Set Home ------
    def set_home(self):
        # Set current position as the new software zero
        self._send_line("G92 X0 Y0 Z0 E0")
        self.x = self.y = self.z = self.e = 0.0


# -------- Need to fill this so we can ask the board for data ------- (Fix)
    def request_data(self):
        try:
            replies = self.send_gcode_with_reply("M114", wait_s=0.2)
            for line in replies:
                if self.verbose:
                    print("[M114]", line)
        except Exception:
            pass



class StepperControlBoardSimulator: #pretend machine if board isn't connected
    # Good for testing 

    def __init__(self, verbose: bool = False): # start the pretend machine at zero, with the pump off
        self.verbose = verbose
        self.x = self.y = self.z = self.e = 0.0
        self.pump = {0: 0}


    def _log(self, s: str): # help function, commands can be good for debugging
        if self.verbose: print("[SIM]", s) # when verbose is on, it prints messages of commands


    def send_gcode(self, cmd: str): self._log(cmd) # jogs the command, writes to the serial port, prints command text only


    def quick_stop(self): self._log("M410") #pretend we sent the emergency stop


    def fan_set(self, index: int, value_0_255: int): #fake version of pump control
        v = max(0, min(255, int(value_0_255))); self.pump[index] = v # sets range to 0-255
        if v <= 0: self._log(f"M107 P{index}")
        else:      self._log(f"M106 P{index} S{v}")


    def jog(self, axes: Dict[str, float], feed: int): #fake relative move function
        axes = {k: v for k, v in axes.items() if abs(v) > 1e-6} #removes tiny values
        if not axes: return #no move is made
        self._log(f"G91; G1 F{feed} " + " ".join(f"{k}{v:.4f}" for k, v in axes.items())) #log the move
        self.x += axes.get("X", 0.0); self.y += axes.get("Y", 0.0) #updates stored position
        self.z += axes.get("Z", 0.0); self.e += axes.get("E", 0.0)


    def abs_move(self, axes: Dict[str, float], feed: int): # fake absolute move function
        axes = {k: v for k, v in axes.items() if isinstance(v, (int, float))}
        if not axes: return
        self._log(f"G90; G1 F{feed} " + " ".join(f"{k}{v:.4f}" for k, v in axes.items()) + "; G91")
        self.x = axes.get("X", self.x)
        self.y = axes.get("Y", self.y)
        self.z = axes.get("Z", self.z)
        self.e = axes.get("E", self.e)


    def home(self): # fake homing
        self._log("G90; G28; G91")
        self.x = self.y = self.z = 0.0

# ----- New - User Set Home -----
    def set_home(self):
        self._log("G92 X0 Y0 Z0 E0")
        self.x = self.y = self.z = self.e = 0.0


# ------- Same as before, currently does nothing (Fix) ------ (but _publish_state still reads position from simulator object)
# Doesn't matter as much because no board is connected
    def request_data(self): pass



# ------------------------------ Gantry system ---------------------------------

class GantrySystem:  # basically the manager
    """
    Consumes (GUI):
      - {"type":"set_steps"|"set_feed"|"home_all"|"gcode"|"fan_set"|"btn_estop"}
      - {"type":"gantry_cmd","cmd": "move_rel"|"move_abs"|"move_steps_xy"|"set_home"}
    Consumes (Controller):
      - {"type":"input","cmd": "xy_motion"|"z_motion"|"e_motion", "value": tuple}
    Produces (GUI):
      - {"type":"state", ...}
      - {"type":"message","level": "...", "text": "..."}
    """

    def __init__(self, q_to_gui, q_from_gui, q_from_controller,
                 simulate: bool = False,
                 ports: dict[str, str | None] | None = None,
                 motion_dt: float = 0.05,
                 gui_dt: float = 0.20,
                 base_feed: int = 3000):
        self.q_to_gui = q_to_gui
        self.q_from_gui = q_from_gui
        self.q_from_controller = q_from_controller

        self._ports = ports or {"needle": None, "camera": None}

        self.motion_dt = motion_dt
        self.gui_dt = gui_dt
        self.feed = base_feed
        self.steps = StepSizes()

        # Keep one top-level state for backward compatibility with existing GUI
        self.state = GantryState(steps=self.steps, feed=self.feed)

        # screen coordinates: +Y up → invert machine Y if needed
        self.flip_x = +1.0
        self.flip_y = -1.0
        self.flip_z = +1.0
        self.flip_e = +1.0

        self._simulate_flag = simulate

        # Two independent motion channels
        self.channels: dict[str, MotionChannel] = {
            "needle": MotionChannel(
                name="needle",
                state=GantryState(steps=self.steps, feed=self.feed),
                port=self._ports.get("needle"),
            ),
            "camera": MotionChannel(
                name="camera",
                state=GantryState(steps=self.steps, feed=self.feed),
                port=self._ports.get("camera"),
            ),
        }

    # ----------------------------- main loop ---------------------------------

    def run(self) -> None:
        self._connect_channels()
        t_motion = time.monotonic()
        t_gui = time.monotonic()
        self._send_message("info", "Gantry started.")

        while True:
            self._drain_gui()
            self._drain_controller()

            now = time.monotonic()

            if now - t_motion >= self.motion_dt:
                for ch in self.channels.values():
                    self._flush_motion(ch)
                t_motion = now

            if now - t_gui >= self.gui_dt:
                for ch in self.channels.values():
                    if ch.connected and ch.board is not None:
                        try:
                            ch.board.request_data()
                        except Exception as e:
                            self._send_message("warning", f"{ch.name.capitalize()} request_data failed: {e}")

                self._publish_state()
                t_gui = now

            time.sleep(0.001)

    def _connect_channels(self) -> None:
        for name, ch in self.channels.items():
            if self._simulate_flag or serial is None:
                ch.board = StepperControlBoardSimulator()
                ch.connected = True
                self._send_message("warning", f"{name.capitalize()} using simulator backend.")
                continue

            if not ch.port:
                ch.board = None
                ch.connected = False
                self._send_message("warning", f"{name.capitalize()} board not connected (no port selected).")
                continue

            try:
                ch.board = StepperControlBoard(port=ch.port)
                ch.connected = True
                self._send_message("info", f"{name.capitalize()} board connected on {ch.port}.")
            except Exception as e:
                ch.board = None
                ch.connected = False
                self._send_message("warning", f"{name.capitalize()} board failed to connect: {e}")

    # ------------------------------ helpers ----------------------------------

    def _normalize_target(self, raw_target: str | None) -> str:
        target = str(raw_target or "needle").strip().lower()
        if target in ("needle", "camera", "both"):
            return target
        return "needle"

    def _target_channels(self, raw_target: str | None) -> list[MotionChannel]:
        target = self._normalize_target(raw_target)
        if target == "both":
            return [self.channels["needle"], self.channels["camera"]]
        return [self.channels[target]]

    # ------------------------------ inbound ----------------------------------

    def _drain_gui(self) -> None:
        try:
            while True:
                msg = self.q_from_gui.get_nowait()
                if not isinstance(msg, dict):
                    continue

                typ = msg.get("type")
                target = self._normalize_target(msg.get("target"))
                targets = self._target_channels(target)

                if typ == "gcode":
                    cmd = str(msg.get("cmd", "")).strip()
                    if cmd:
                        for ch in targets:
                            board = ch.board
                            if board is None:
                                continue
                            try:
                                if cmd.upper().startswith("M400"):
                                    try:
                                        self._flush_motion(ch)
                                    except Exception:
                                        pass

                                if hasattr(board, "send_gcode_with_reply"):
                                    replies = board.send_gcode_with_reply(cmd, wait_s=0.8)
                                    for line in replies:
                                        self._send_message("info", f"[{ch.name.upper()} GCODE] {line}")
                                else:
                                    board.send_gcode(cmd)

                            except Exception as e:
                                self._send_message("error", f"{ch.name.capitalize()} GCODE failed: {e}")

                elif typ == "home_all":
                    for ch in targets:
                        board = ch.board
                        if board is None:
                            continue
                        try:
                            board.home()
                            ch.dx = ch.dy = ch.dz = ch.de = 0.0
                            ch.state.x = ch.state.y = ch.state.z = ch.state.e = 0.0
                            self._send_message("info", f"Homing all axes on {ch.name}.")
                        except Exception as e:
                            self._send_message("error", f"{ch.name.capitalize()} home failed: {e}")

                elif typ == "set_steps" or (typ == "gantry_cmd" and msg.get("cmd") == "set_steps"):
                    for k in ("xy_step", "z_step", "e_step"):
                        if k in msg:
                            setattr(self.steps, k, float(msg[k]))
                    self.steps.clamp()

                    self.state.steps.xy_step = self.steps.xy_step
                    self.state.steps.z_step = self.steps.z_step
                    self.state.steps.e_step = self.steps.e_step

                    for ch in self.channels.values():
                        ch.state.steps.xy_step = self.steps.xy_step
                        ch.state.steps.z_step = self.steps.z_step
                        ch.state.steps.e_step = self.steps.e_step

                    self._send_message(
                        "info",
                        f"Steps XY={self.steps.xy_step:.3f} Z={self.steps.z_step:.3f} E={self.steps.e_step:.3f}"
                    )

                elif typ == "set_feed" or (typ == "gantry_cmd" and msg.get("cmd") == "set_feed"):
                    self.feed = int(msg.get("feed_mm_min", self.feed))
                    self.state.feed = self.feed
                    for ch in self.channels.values():
                        ch.state.feed = self.feed
                    self._send_message("info", f"Feed={self.feed} mm/min")

                elif typ == "fan_set":
                    for ch in targets:
                        board = ch.board
                        if board is None:
                            continue
                        try:
                            board.fan_set(int(msg.get("index", PUMP_FAN_INDEX)), int(msg.get("value", 0)))
                            ch.state.pump_0 = int(msg.get("value", 0))
                        except Exception as e:
                            self._send_message("error", f"{ch.name.capitalize()} pump set failed: {e}")

                elif typ == "btn_estop":
                    for ch in self.channels.values():
                        board = ch.board
                        if board is None:
                            continue
                        try:
                            board.quick_stop()
                        except Exception:
                            pass

                        ch.dx = ch.dy = ch.dz = ch.de = 0.0

                        try:
                            board.fan_set(PUMP_FAN_INDEX, 0)
                        except Exception:
                            pass

                    self._send_message("warning", "E-STOP: motion aborted, pump off.")

                elif typ == "gantry_cmd":
                    cmd = str(msg.get("cmd", ""))

                    if cmd == "move_rel":
                        dx = float(msg.get("dx", 0.0))
                        dy = float(msg.get("dy", 0.0))
                        dz = float(msg.get("dz", 0.0))
                        de = float(msg.get("de", 0.0))

                        if "feed_mm_min" in msg:
                            self.feed = int(msg["feed_mm_min"])
                            self.state.feed = self.feed
                            for ch in self.channels.values():
                                ch.state.feed = self.feed

                        for ch in targets:
                            ch.dx += self.flip_x * dx
                            ch.dy += self.flip_y * dy
                            ch.dz += self.flip_z * dz
                            ch.de += self.flip_e * de

                        self._send_message(
                            "info",
                            f"Queued {target} move_rel dx={dx} dy={dy} dz={dz} de={de}"
                        )

                    elif cmd == "move_abs":
                        X = msg.get("X", None)
                        Y = msg.get("Y", None)
                        Z = msg.get("Z", None)
                        E = msg.get("E", None)
                        feed = int(msg.get("feed_mm_min", self.feed))

                        axes = {}
                        if isinstance(X, (int, float)):
                            axes["X"] = float(X)
                        if isinstance(Y, (int, float)):
                            axes["Y"] = float(Y)
                        if isinstance(Z, (int, float)):
                            axes["Z"] = float(Z)
                        if isinstance(E, (int, float)):
                            axes["E"] = float(E)

                        for ch in targets:
                            board = ch.board
                            if board is None:
                                continue
                            try:
                                board.abs_move(axes, feed)
                                ch.state.feed = feed
                                self._send_message("info", f"{ch.name.capitalize()} ABS move -> {axes} @ F{feed}")
                            except Exception as e:
                                self._send_message("error", f"{ch.name.capitalize()} ABS move failed: {e}")

                        self.feed = feed
                        self.state.feed = feed

                    elif cmd == "move_steps_xy":
                        nx = int(msg.get("nx", 0))
                        ny = int(msg.get("ny", 0))
                        dx = float(nx) * self.steps.xy_step
                        dy = float(ny) * self.steps.xy_step

                        for ch in targets:
                            ch.dx += self.flip_x * dx
                            ch.dy += self.flip_y * dy

                        self._send_message(
                            "info",
                            f"Queued {target} move_steps_xy: dx={dx:.4f}, dy={dy:.4f} (nx={nx}, ny={ny})"
                        )

                    elif cmd == "set_home":
                        for ch in targets:
                            board = ch.board
                            if board is None:
                                continue
                            try:
                                board.set_home()
                                ch.dx = ch.dy = ch.dz = ch.de = 0.0
                                ch.state.x = ch.state.y = ch.state.z = ch.state.e = 0.0
                                self._send_message(
                                    "info",
                                    f"{ch.name.capitalize()} current position set as home (0, 0, 0)."
                                )
                            except Exception as e:
                                self._send_message("error", f"{ch.name.capitalize()} set home failed: {e}")

        except queue.Empty:
            pass

    def _drain_controller(self) -> None:
        try:
            while True:
                msg = self.q_from_controller.get_nowait()

                if not isinstance(msg, dict):
                    continue

                msg_type = msg.get("type")

                # Forward controller mapping info to the GUI
                if msg_type == "controller_state":
                    self.q_to_gui.put({
                        "type": "controller_state",
                        "mapping": msg.get("mapping", {})
                    })
                    continue

                if msg.get("type") != "input":
                    continue

                needle = self.channels["needle"]
                cmd = str(msg.get("cmd", ""))
                val = msg.get("value")

                if cmd == "xy_motion" and isinstance(val, (tuple, list)) and len(val) == 2:
                    jx, jy = float(val[0]), float(val[1])
                    needle.dx += self.flip_x * (jx * self.steps.xy_step)
                    needle.dy += self.flip_y * (jy * self.steps.xy_step)

                elif cmd == "z_motion" and isinstance(val, (tuple, list)) and len(val) == 2:
                    _jx, jy = float(val[0]), float(val[1])
                    needle.dz += self.flip_z * (jy * self.steps.z_step)

                elif cmd == "e_motion" and isinstance(val, (tuple, list)) and len(val) == 2:
                    lt, rt = float(val[0]), float(val[1])
                    needle.de += self.flip_e * ((rt - lt) * self.steps.e_step)

                elif cmd == "xy_step_size_inc":
                    self.steps.xy_step = min(self.steps.xy_step + 0.05, 5.0)
                    self.state.steps.xy_step = self.steps.xy_step
                    for ch in self.channels.values():
                        ch.state.steps.xy_step = self.steps.xy_step
                    self._send_message("info", f"XY step = {self.steps.xy_step:.3f} mm")

                elif cmd == "xy_step_size_dec":
                    self.steps.xy_step = max(self.steps.xy_step - 0.05, 0.005)
                    self.state.steps.xy_step = self.steps.xy_step
                    for ch in self.channels.values():
                        ch.state.steps.xy_step = self.steps.xy_step
                    self._send_message("info", f"XY step = {self.steps.xy_step:.3f} mm")

                elif cmd == "z_step_size_inc":
                    self.steps.z_step = min(self.steps.z_step + 0.01, 2.0)
                    self.state.steps.z_step = self.steps.z_step
                    for ch in self.channels.values():
                        ch.state.steps.z_step = self.steps.z_step
                    self._send_message("info", f"Z step = {self.steps.z_step:.3f} mm")

                elif cmd == "z_step_size_dec":
                    self.steps.z_step = max(self.steps.z_step - 0.01, 0.001)
                    self.state.steps.z_step = self.steps.z_step
                    for ch in self.channels.values():
                        ch.state.steps.z_step = self.steps.z_step
                    self._send_message("info", f"Z step = {self.steps.z_step:.3f} mm")

                elif cmd == "home_all":
                    try:
                        self._flush_motion(needle)
                    except Exception:
                        pass

                    try:
                        if needle.board is not None:
                            needle.board.home()
                        needle.state.x = needle.state.y = needle.state.z = 0.0
                        self._send_message("info", "Machine homed from controller.")
                    except Exception as e:
                        self._send_message("error", f"Controller home failed: {e}")

        except queue.Empty:
            pass

    # ------------------------------- motion ----------------------------------

    def _flush_motion(self, ch: MotionChannel) -> None:
        board = ch.board
        if board is None:
            ch.dx = ch.dy = ch.dz = ch.de = 0.0
            return

        dx, dy, dz, de = ch.dx, ch.dy, ch.dz, ch.de
        ch.dx = ch.dy = ch.dz = ch.de = 0.0

        axes = {}
        if abs(dx) > 1e-9:
            axes["X"] = dx
        if abs(dy) > 1e-9:
            axes["Y"] = dy
        if abs(dz) > 1e-9:
            axes["Z"] = dz
        if abs(de) > 1e-9:
            axes["E"] = de

        if axes:
            try:
                board.jog(axes, self.feed)
            except Exception as e:
                self._send_message("error", f"{ch.name.capitalize()} jog failed: {e}")

    # ------------------------------- outbound --------------------------------

    def _publish_state(self) -> None:
        needle = self.channels["needle"]
        camera = self.channels["camera"]

        if needle.board is not None:
            needle.state.x = getattr(needle.board, "x", 0.0)
            needle.state.y = getattr(needle.board, "y", 0.0)
            needle.state.z = getattr(needle.board, "z", 0.0)
            needle.state.e = getattr(needle.board, "e", 0.0)

        if camera.board is not None:
            camera.state.x = getattr(camera.board, "x", 0.0)
            camera.state.y = getattr(camera.board, "y", 0.0)
            camera.state.z = getattr(camera.board, "z", 0.0)
            camera.state.e = getattr(camera.board, "e", 0.0)

        # Keep old top-level values for compatibility
        self.state.x = needle.state.x
        self.state.y = needle.state.y
        self.state.z = needle.state.z
        self.state.e = needle.state.e

        self.q_to_gui.put({
            "type": "state",

            # old fields
            "x": self.state.x,
            "y": self.state.y,
            "z": self.state.z,
            "e": self.state.e,

            # new needle fields
            "needle_x": needle.state.x,
            "needle_y": needle.state.y,
            "needle_z": needle.state.z,
            "needle_e": needle.state.e,

            # new camera fields
            "camera_x": camera.state.x,
            "camera_y": camera.state.y,
            "camera_z": camera.state.z,
            "camera_e": camera.state.e,

            "xy_step": self.steps.xy_step,
            "z_step": self.steps.z_step,
            "e_step": self.steps.e_step,
            "feed": self.feed,
        })

    def _mark_disconnected(self, ch: MotionChannel, e: Exception) -> None:
        """Mark a channel as disconnected and notify the GUI."""
        ch.connected = False
        ch.board = None
        ch.dx = ch.dy = ch.dz = ch.de = 0.0
        self.q_to_gui.put({
            "type": "disconnected",
            "channel": ch.name,
            "port": ch.port or "unknown",
            "reason": str(e),
        })

    def _send_message(self, level: str, text: str):
        self.q_to_gui.put({"type": "message", "level": level, "text": text})