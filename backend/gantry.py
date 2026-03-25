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

class GantrySystem: # basically the manager
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
                 simulate: bool = False,
                 port: str | None = None,
                 motion_dt: float = 0.05,
                 gui_dt: float = 0.20,
                 base_feed: int = 3000): # Setup for the whole backend loop
        self.q_to_gui = q_to_gui # send information back to the GUI (backend's outbox)
        self.q_from_gui = q_from_gui # recieve commands from the GUI, buttons, settings, etc
        self.q_from_controller = q_from_controller # recieves commnads from the GUI
        self._port = port 

        self.motion_dt = motion_dt # stores how often motion is flushed (currently 50 ms)
        self.gui_dt = gui_dt # how often backend sends state updates to the GUI (currently 200 ms, 5 times per second)
        self.feed = base_feed # stores current speed setting (currently 3000)
        self.steps = StepSizes() # reads the earlier dataclass StepSizes
        self.state = GantryState(steps=self.steps, feed=self.feed) # reads the dataclass GantryState

        # screen coordinates: +Y up → invert machine Y if needed
        self.flip_x = +1.0
        self.flip_y = -1.0 # currently y is inverted on the coordinate plane
        self.flip_z = +1.0
        self.flip_e = +1.0

        self._dx = self._dy = self._dz = self._de = 0.0 # temporary stored motion amounts, stores before sending manual inputs

        self._simulate_flag = simulate #stores if asked for simulator mode
        self._board = None # placeholder for board object (either StepperControlBoard or StepperControlBoardSimulator)



    # ----------------------------- main loop ---------------------------------


    def run(self) -> None: # main backend loop
        board = self._try_board() # choses real board or simulator
        self._board = board # stores that choice
        t_motion = time.monotonic() # creates motion flush timing
        t_gui = time.monotonic() # creates GUI update timing
        self._send_message("info", "Gantry started.") # startup message to GUI

        while True: # continuous engine loop
            self._drain_gui(board)         # settings, abs moves, pump, estop, etc. for GUI
            self._drain_controller()       # manual-jog inputs from controller, read manual motion input and add to motion buckets

            now = time.monotonic() #gets the current time for the timing checks
            if now - t_motion >= self.motion_dt:
                self._flush_motion(board); t_motion = now # if it's been at least 50 ms since last flush, send collected motion now
            if now - t_gui >= self.gui_dt: # updates the GUI

                # ------ currently does nothing (Fix) -----
                # wants to ask for hardware data
                try: board.request_data()
                except Exception as e: self._send_message("warning", f"request_data failed: {e}")
                self._publish_state(board); t_gui = now # sends current state snapshot to GUI and resets timer

            time.sleep(0.001) # loop pauses 1 ms per cycle



    def _try_board(self): # helps chooses real board or simulator
        if self._simulate_flag or serial is None:
            self._send_message("warning", "Using simulator backend.")
            return StepperControlBoardSimulator()
        try:
            return StepperControlBoard(port=self._port)
        except Exception as e:
            self._send_message("warning", f"No board detected, using simulator: {e}")
            return StepperControlBoardSimulator()


    # ------------------------------ inbound ----------------------------------
# functions are where incoming messages get turned into actual behavior

    def _drain_gui(self, board) -> None: #handles discrete commands from buttons/forms/messages
        try:
            while True:
                msg = self.q_from_gui.get_nowait()
                if not isinstance(msg, dict): continue #chekcs if msg is not an instance of a specified class or something
                typ = msg.get("type") #decides what type of command it is

                if typ == "gcode": #sends raw G-code
                    cmd = str(msg.get("cmd", "")).strip()
                    if cmd:
                        try:
                            # Special-case barrier: ensure any queued move_rel / move_abs have been sent first
                            if cmd.upper().startswith("M400"): # "finsh moves" forces a pause and wait until all queued movements are completed
                                try:
                                    self._flush_motion(board) # <-- only if this exists in the class
                                except Exception:
                                    pass
                            if hasattr(board, "send_gcode_with_reply"):
                                replies = board.send_gcode_with_reply(cmd, wait_s=0.8)
                                for line in replies:
                                    self._send_message("info", f"[GCODE] {line}") # if raw G-code, send it and show any reply lines
                            else:
                                board.send_gcode(cmd)
                                
                        except Exception as e: self._send_message("error", f"GCODE failed: {e}")

                elif typ == "home_all": # "home all axes now", triggers board-level home function
                    try: board.home(); self._send_message("info", "Homing all axes.")
                    except Exception as e: self._send_message("error", f"Home failed: {e}")

                elif typ == "set_steps" or (typ == "gantry_cmd" and msg.get("cmd") == "set_steps"): # updates jog size settings and keeps in range
                    for k in ("xy_step", "z_step", "e_step"):
                        if k in msg: setattr(self.steps, k, float(msg[k]))
                    self.steps.clamp()
                    self._send_message("info", f"Steps XY={self.steps.xy_step:.3f} Z={self.steps.z_step:.3f} E={self.steps.e_step:.3f}")

                elif typ == "set_feed" or (typ == "gantry_cmd" and msg.get("cmd") == "set_feed"): # changes feed settings
                    self.feed = int(msg.get("feed_mm_min", self.feed))
                    self.state.feed = self.feed
                    self._send_message("info", f"Feed={self.feed} mm/min")

                elif typ == "fan_set": # set pump/fan output
                    try:
                        board.fan_set(int(msg.get("index", PUMP_FAN_INDEX)), int(msg.get("value", 0)))
                        self.state.pump_0 = int(msg.get("value", 0))
                    except Exception as e:
                        self._send_message("error", f"Pump set failed: {e}")

                elif typ == "btn_estop": # emergency stop branch, clears all queued motion 
                    try: board.quick_stop()
                    except Exception: pass
                    self._dx = self._dy = self._dz = self._de = 0.0
                    try: board.fan_set(PUMP_FAN_INDEX, 0)
                    except Exception: pass # apparently this is a bad practice? (Fix)
                    self._send_message("warning", "E-STOP: motion aborted, pump off.")

                elif typ == "gantry_cmd": # one message type, multiple motion-related commands
                    cmd = str(msg.get("cmd", ""))


                    if cmd == "move_rel": #handles relative moves
                        dx = float(msg.get("dx", 0.0)); dy = float(msg.get("dy", 0.0))
                        dz = float(msg.get("dz", 0.0)); de = float(msg.get("de", 0.0))
                        if "feed_mm_min" in msg:
                            self.feed = int(msg["feed_mm_min"]); self.state.feed = self.feed
                        self._dx += self.flip_x * dx; self._dy += self.flip_y * dy
                        self._dz += self.flip_z * dz; self._de += self.flip_e * de
                        self._send_message("info", f"Queued move_rel dx={dx} dy={dy} dz={dz} de={de}")

                    elif cmd == "move_abs": # handles absolute moves
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

                    elif cmd == "move_steps_xy": # reads integer nx and ny and converts into mm and applies axis flips, part of grid routine
                        nx = int(msg.get("nx", 0)); ny = int(msg.get("ny", 0))
                        dx = float(nx) * self.steps.xy_step
                        dy = float(ny) * self.steps.xy_step
                        self._dx += self.flip_x * dx
                        self._dy += self.flip_y * dy
                        self._send_message("info", f"Queued move_steps_xy: dx={dx:.4f}, dy={dy:.4f} (nx={nx}, ny={ny})")

                    # --- New user set home
                    elif cmd == "set_home":
                        try:
                            board.set_home()
                            self._dx = self._dy = self._dz = self._de = 0.0
                            self.state.x = self.state.y = self.state.z = self.state.e = 0.0
                            self._send_message("info", "Current position set has home (0, 0, 0).")
                        except Exception as e:
                            self._send_message("error", f"Set home failed: {e}")

        except queue.Empty:
            pass



    def _drain_controller(self) -> None: #reads messages from the controller, more focused on manual motion commnads
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
                
                cmd = str(msg.get("cmd", ""))
                val = msg.get("value")

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
                    self.steps.xy_step = min(self.steps.xy_step + 0.05, 5.0)
                    self.state.steps.xy_step = self.steps.xy_step
                    self._send_message("info", f"XY step = {self.steps.xy_step:.3f} mm")
                    
                elif cmd == "xy_step_size_dec":
                    self.steps.xy_step = max(self.steps.xy_step - 0.05, 0.005)
                    self.state.steps.xy_step = self.steps.xy_step
                    self._send_message("info", f"XY step = {self.steps.xy_step:.3f} mm")
                    
                elif cmd == "z_step_size_inc":
                    self.steps.z_step = min(self.steps.z_step + 0.01, 2.0)
                    self.state.steps.z_step = self.steps.z_step
                    self._send_message("info", f"Z step = {self.steps.z_step:.3f} mm")
                    
                elif cmd == "z_step_size_dec":
                    self.steps.z_step = max(self.steps.z_step - 0.01, 0.001)
                    self.state.steps.z_step = self.steps.z_step
                    self._send_message("info", f"Z step = {self.steps.z_step:.3f} mm")
                    
                elif cmd == "home_all":
                    try:
                        self._flush_motion(self._board)
                    except Exception:
                        pass
                    
                    try:
                        self._board.home()
                        self.state.x = self.state.y = self.state.z = 0.0
                        self._send_message("info", "Machine homed from controller.")
                    except Exception as e:
                        self._send_message("error", f"Controller home failed: {e}")
                        
        except queue.Empty:
            pass
        # --- Main thing: never directly sends motion to the board, changes _dx, _dy, _dz
        # Means controller/manual motion is always going through the buffered-motion path
        # drain_controller : accumulators change
        # flush_motion : later sends one jog command
        # manual motion is "smoothed" into periodic chunks rather than becoming one serial command per tiny input update

# ---- DIFFERENCE: ----- 
# drain_gui : handles what user asks for
# drain_controller : handles ongoing motion input that needs to be accumulated



    # ------------------------------- motion ----------------------------------
# completes the full loop

# Takes stored motion, combines into one move, sends that move, then clears the stored amounts
# Motion is not sent continuously 
    def _flush_motion(self, board) -> None: # collects motion 
        dx, dy, dz, de = self._dx, self._dy, self._dz, self._de # motion into local variables
        self._dx = self._dy = self._dz = self._de = 0.0 # resets to 0
        axes = {} # empty dictionary for move command (only includes axes that are actually moving)
        if abs(dx) > 1e-9: axes["X"] = dx
        if abs(dy) > 1e-9: axes["Y"] = dy
        if abs(dz) > 1e-9: axes["Z"] = dz
        if abs(de) > 1e-9: axes["E"] = de
        if axes: # only sends if there is at least one axis to move
            try: board.jog(axes, self.feed) # sends combined move to the board as one relative jog
            except Exception as e: self._send_message("error", f"Jog failed: {e}")

    # ------------------------------- outbound --------------------------------
    # Takes the current backend state and sends it to the GUI
    # Frontend learns current position, step sizes, and feed
    def _publish_state(self, board) -> None: # sends motion
        self.state.x = getattr(board, "x", 0.0) # getattr avoids crashing if the attribute is missing
        self.state.y = getattr(board, "y", 0.0)
        self.state.z = getattr(board, "z", 0.0)
        self.state.e = getattr(board, "e", 0.0)
        self.q_to_gui.put({
            "type": "state",
            "x": self.state.x, "y": self.state.y, "z": self.state.z, "e": self.state.e,
            "xy_step": self.steps.xy_step, "z_step": self.steps.z_step, "e_step": self.steps.e_step,
            "feed": self.feed,
        }) # basically a "state packet"
        # does not currently have pump (Fix)
        # this is where the GUI gets the position

    def _send_message(self, level: str, text: str): # state/messages sent back to the GUI
        self.q_to_gui.put({"type": "message", "level": level, "text": text})
