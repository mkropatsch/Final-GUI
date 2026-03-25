#!/usr/bin/env python3
"""
Command-line engine for the Core-XY Gantry.

This file:
- Starts GantrySystem (your existing backend) in a background thread.
- Provides a simple text command language to control the gantry.
- Prints state (X, Y, Z, E, feed) and messages to the terminal.

Place this file in the same folder as your backend:
    gantry_gui_new_automate_2.py

Run:
    python gantry_engine_cli.py --simulate
or:
    python gantry_engine_cli.py          (for real hardware, if you wire it that way)
"""

from __future__ import annotations

import argparse
import queue
import threading
import time
from typing import Any, Dict, List, Tuple, Optional
import shlex

# ---------------------------------------------------------------------
# Backend import
# ---------------------------------------------------------------------
# NOTE: Adjust this import if your file / class is named differently.
# It should point to the same GantrySystem that your GUI uses.
from gantry_gui_new_automate_2 import GantrySystem  # type: ignore


# ---------------------------------------------------------------------
# Command parsing
# ---------------------------------------------------------------------

def _parse_kv(args: List[str]) -> Dict[str, Any]:
    """
    Parse arguments like:
        x=1 y=-2 f=2000  10 0
    into a dict:
        {"x":1.0, "y":-2.0, "f":2000, "_pos": ["10","0"]}
    """
    out: Dict[str, Any] = {}
    for a in args:
        if "=" in a:
            k, v = a.split("=", 1)
            k = k.strip().lower()
            v = v.strip()
        else:
            out.setdefault("_pos", []).append(a)
            continue

        try:
            # try int first, then float
            if any(ch in v.lower() for ch in (".", "e")):
                out[k] = float(v)
            else:
                out[k] = int(v)
        except ValueError:
            out[k] = v
    return out


def parse_full_command(line: str) -> List[Dict[str, Any]]:
    """
    Parse a *single-line* command into one or more engine messages (dicts).

    Examples:
      home                   -> {"type":"home_all"}
      estop                  -> {"type":"btn_estop"}
      feed 3000              -> {"type":"set_feed","feed_mm_min":3000}
      rel x=1 y=-2 f=2000    -> gantry_cmd move_rel
      abs x=10 y=5 f=3000    -> gantry_cmd move_abs
      steps 10 0             -> gantry_cmd move_steps_xy
      zsteps 5               -> relative Z move using z_step
      pump 128               -> fan_set
      gcode G1 X10 Y20       -> gcode passthrough
      grid cols=3 rows=2 sx=10 sy=10 nz=0 dwell=0 total=0 serp=1 -> grid routine
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return []

    tokens = shlex.split(line)
    if not tokens:
        return []

    cmd = tokens[0].lower()
    args = tokens[1:]
    msgs: List[Dict[str, Any]] = []

    # HOME
    if cmd in ("home", "g28"):
        msgs.append({"type": "home_all"})
        return msgs

    # ESTOP
    if cmd in ("estop", "emerg", "panic", "e-stop"):
        msgs.append({"type": "btn_estop"})
        return msgs

    # FEED (set feed in mm/min)
    if cmd in ("feed", "f"):
        if not args:
            raise ValueError("feed requires a value, e.g. 'feed 3000'")
        val = float(args[0])
        msgs.append({"type": "set_feed", "feed_mm_min": int(val)})
        return msgs

    # RELATIVE MOVE (mm)
    if cmd in ("rel", "move_rel", "mr"):
        kv = _parse_kv(args)
        dx = kv.get("x") or kv.get("dx") or 0.0
        dy = kv.get("y") or kv.get("dy") or 0.0
        dz = kv.get("z") or kv.get("dz") or 0.0
        de = kv.get("e") or kv.get("de") or 0.0
        feed = kv.get("f") or kv.get("feed") or None

        msg: Dict[str, Any] = {
            "type": "gantry_cmd",
            "cmd": "move_rel",
            "dx": float(dx),
            "dy": float(dy),
            "dz": float(dz),
            "de": float(de),
        }
        if feed is not None:
            msg["feed_mm_min"] = int(feed)
        msgs.append(msg)
        return msgs

    # ABSOLUTE MOVE (mm)
    if cmd in ("abs", "move_abs", "ma"):
        kv = _parse_kv(args)
        msg = {"type": "gantry_cmd", "cmd": "move_abs"}

        for axis in ("x", "y", "z", "e"):
            val = kv.get(axis)
            if val is not None:
                msg[axis.upper()] = float(val)

        feed = kv.get("f") or kv.get("feed") or None
        if feed is not None:
            msg["feed_mm_min"] = int(feed)

        msgs.append(msg)
        return msgs
    # Step size (xy_step, z_step, e_step)
    if cmd in ("step", "stepsize", "setstep"):
        kv = _parse_kv(args)
        # kv might contain xy = ... z=...
        msg: Dict[str, Any] = {"type": "set_steps"}
        if "xy" in kv: msg["xy_step"] = kv["xy"]
        if "z" in kv: msg["z_step"] = kv["z"]
        if "e" in kv: msg["e_step"] = kv["e"]
        if len(msg) == 1: # only "type" present
            raise ValueError("step needs at least one of xy=, z=")
        msgs.append(msg)
        return msgs
    # XY STEPS
    if cmd in ("steps", "s"):
        kv = _parse_kv(args)
        pos = kv.get("_pos", [])

        if "nx" in kv or "ny" in kv:
            nx = kv.get("nx", 0)
            ny = kv.get("ny", 0)
        elif len(pos) >= 2:
            nx = int(float(pos[0]))
            ny = int(float(pos[1]))
        else:
            raise ValueError("steps requires nx ny, e.g. 'steps 10 0'")

        msgs.append({
            "type": "gantry_cmd",
            "cmd": "move_steps_xy",
            "nx": int(nx),
            "ny": int(ny),
        })
        return msgs

    # Z STEPS (converted to mm using z_step in the engine state)
    if cmd in ("zsteps", "zstep", "zs"):
        # We'll resolve the actual mm in the engine using last_state["z_step"]
        kv = _parse_kv(args)
        pos = kv.get("_pos", [])

        if "nz" in kv:
            nz = int(kv["nz"])
        elif pos:
            nz = int(float(pos[0]))
        else:
            raise ValueError("zsteps requires nz, e.g. 'zsteps 5'")

        # Special marker: engine will interpret this
        msgs.append({"__special__": "zsteps", "nz": nz})
        return msgs

    # PUMP / FAN (FAN0 duty)
    if cmd in ("pump", "fan"):
        if not args:
            raise ValueError("pump requires duty 0..255")
        duty = int(float(args[0]))
        msgs.append({"type": "fan_set", "index": 0, "value": duty})
        return msgs

    # RAW GCODE PASSTHROUGH
    if cmd in ("gcode", "g"):
        raw = line[len(tokens[0]):].strip()
        if not raw:
            raise ValueError("gcode requires a line, e.g. 'gcode G1 X1'")
        msgs.append({"type": "gcode", "cmd": raw})
        return msgs

    # GRID ROUTINE
    if cmd in ("grid", "scan"):
        kv = _parse_kv(args)
        # cols, rows: number of positions
        cols = int(kv.get("cols", kv.get("c", 0)))
        rows = int(kv.get("rows", kv.get("r", 0)))
        if cols <= 0 or rows <= 0:
            raise ValueError("grid requires cols and rows, e.g. 'grid cols=3 rows=2'")

        nx_steps = int(kv.get("sx", kv.get("nx", 0)))
        ny_steps = int(kv.get("sy", kv.get("ny", 0)))
        nz_steps = int(kv.get("nz", 0))     # Z steps per point (0 = ignore Z)
        dwell_s = float(kv.get("dwell", kv.get("dw", 0.0)))
        total_s = float(kv.get("total", kv.get("t", 0.0)))
        serp = kv.get("serp", kv.get("serpentine", 1))
        serpentine = bool(int(serp))

        msgs.append({
            "__special__": "grid",
            "cols": cols,
            "rows": rows,
            "nx_steps": nx_steps,
            "ny_steps": ny_steps,
            "nz_steps": nz_steps,
            "dwell_s": dwell_s,
            "total_s": total_s,
            "serpentine": serpentine,
        })
        return msgs

    raise ValueError(f"Unknown command '{cmd}'")


class CommandAccumulator:
    """
    Optional multi-step command buffer.

    Lets you do:
        rel
        x=1
        y=-2
        f=2000
        go

    or:
        abs
        x=10
        y=5
        go

    If you enter a one-line command (e.g. 'rel x=1 y=-2 f=2000'),
    it bypasses the buffer and executes immediately.
    """

    def __init__(self):
        self.mode: Optional[str] = None  # "rel" or "abs"
        self.params: Dict[str, Any] = {}

    def clear(self) -> None:
        self.mode = None
        self.params = {}

    def _build_rel_msg(self) -> Dict[str, Any]:
        dx = self.params.get("x", 0.0)
        dy = self.params.get("y", 0.0)
        dz = self.params.get("z", 0.0)
        de = self.params.get("e", 0.0)
        feed = self.params.get("f", self.params.get("feed", None))
        msg: Dict[str, Any] = {
            "type": "gantry_cmd",
            "cmd": "move_rel",
            "dx": float(dx),
            "dy": float(dy),
            "dz": float(dz),
            "de": float(de),
        }
        if feed is not None:
            msg["feed_mm_min"] = int(feed)
        return msg

    def _build_abs_msg(self) -> Dict[str, Any]:
        msg: Dict[str, Any] = {
            "type": "gantry_cmd",
            "cmd": "move_abs",
        }
        for axis in ("x", "y", "z", "e"):
            if axis in self.params:
                msg[axis.upper()] = float(self.params[axis])
        feed = self.params.get("f", self.params.get("feed", None))
        if feed is not None:
            msg["feed_mm_min"] = int(feed)
        return msg

    def process_line(self, line: str) -> List[Dict[str, Any]]:
        """
        Returns a list of messages to send to the engine.
        May be empty (e.g. when just updating the buffer).
        """
        line = line.strip()
        if not line:
            return []

        # Buffer management commands
        low = line.lower()
        if low in ("clear", "cancel"):
            self.clear()
            print("[BUFFER] Cleared.")
            return []
        if low in ("show", "buffer"):
            print(f"[BUFFER] mode={self.mode}, params={self.params}")
            return []

        # If no mode is active, try full commands OR start buffered rel/abs
        if self.mode is None:
            if low in ("rel", "move_rel", "mr"):
                self.mode = "rel"
                self.params = {}
                print("[BUFFER] Started relative move. Enter x=, y=, z=, e=, f= then 'go'.")
                return []
            if low in ("abs", "move_abs", "ma"):
                self.mode = "abs"
                self.params = {}
                print("[BUFFER] Started absolute move. Enter x=, y=, z=, e=, f= then 'go'.")
                return []

            # Otherwise, treat as a full, one-line command
            return parse_full_command(line)

        # We ARE in buffered mode (rel / abs)
        if low in ("go", "run", "exec", "execute"):
            if self.mode == "rel":
                msg = self._build_rel_msg()
            elif self.mode == "abs":
                msg = self._build_abs_msg()
            else:
                print("[BUFFER] Unknown mode; clearing.")
                self.clear()
                return []
            self.clear()
            return [msg]

        # Parse parameter: k=v
        if "=" in line:
            k, v = line.split("=", 1)
            k = k.strip().lower()
            v = v.strip()
            try:
                if any(ch in v.lower() for ch in (".", "e")):
                    val: Any = float(v)
                else:
                    val = int(v)
            except ValueError:
                val = v
            self.params[k] = val
            return []

        print(f"[BUFFER] Unrecognized input in buffer mode: '{line}'")
        return []


# ---------------------------------------------------------------------
# Engine wrapper & grid routine
# ---------------------------------------------------------------------

class GantryEngine:
    """
    Headless wrapper around GantrySystem.

    - Starts GantrySystem.run() in a background thread.
    - Tracks latest state (x,y,z,e, steps, feed).
    - Provides helpers for special commands like zsteps and grid.
    """

    def __init__(self, simulate: bool = True):
        self.q_to_gui: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=1000)
        self.q_from_gui: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=1000)
        self.q_from_controller: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=1000)

        # Adjust this constructor if your GantrySystem signature is different.
        self.gantry = GantrySystem(
            q_to_gui=self.q_to_gui,
            q_from_gui=self.q_from_gui,
            q_from_controller=self.q_from_controller,
            simulate=simulate,
        )

        self._stop_flag = False

        self.last_state: Dict[str, Any] = {
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
            "e": 0.0,
            "xy_step": 0.0,
            "z_step": 0.0,
            "e_step": 0.0,
            "feed": 0,
        }

        # Start gantry run loop
        self._t_gantry = threading.Thread(target=self.gantry.run, daemon=True)
        self._t_gantry.start()

        # Start listener
        self._t_listener = threading.Thread(target=self._listener_loop, daemon=True)
        self._t_listener.start()

    def _listener_loop(self) -> None:
        """
        Read messages from q_to_gui and update last_state / print messages.
        """
        while not self._stop_flag:
            try:
                msg = self.q_to_gui.get(timeout=0.5)
            except queue.Empty:
                continue

            if not isinstance(msg, dict):
                continue

            typ = msg.get("type")
            if typ == "state":
                self.last_state["x"] = float(msg.get("x", 0.0))
                self.last_state["y"] = float(msg.get("y", 0.0))
                self.last_state["z"] = float(msg.get("z", 0.0))
                self.last_state["e"] = float(msg.get("e", 0.0))
                self.last_state["xy_step"] = float(msg.get("xy_step", 0.0))
                self.last_state["z_step"] = float(msg.get("z_step", 0.0))
                #self.last_state["e_step"] = float(msg.get("e_step", 0.0))
                self.last_state["feed"] = int(msg.get("feed", 0))
                
            elif typ == "message":
                level = msg.get("level", "info").upper()
                text = msg.get("text", "")
                print(f"[MSG:{level}] {text}")

    # --- helpers for special commands --------------------------------

    def send_msg(self, msg: Dict[str, Any]) -> None:
        """
        Send a single message dict to the gantry.

        Handles special commands like "__special__": "zsteps" or "grid".
        """
        special = msg.get("__special__")
        if special == "zsteps":
            nz = int(msg["nz"])
            self._handle_zsteps(nz)
        elif special == "grid":
            self._handle_grid(
                cols=int(msg["cols"]),
                rows=int(msg["rows"]),
                nx_steps=int(msg["nx_steps"]),
                ny_steps=int(msg["ny_steps"]),
                nz_steps=int(msg["nz_steps"]),
                dwell_s=float(msg["dwell_s"]),
                total_s=float(msg["total_s"]),
                serpentine=bool(msg["serpentine"]),
            )
        else:
            # Normal message goes straight to GantrySystem
            self.q_from_gui.put(msg)

    def _handle_zsteps(self, nz: int) -> None:
        """
        Convert Z steps to mm using last_state["z_step"] and issue move_rel.
        """
        z_step = float(self.last_state.get("z_step", 0.0))
        if z_step == 0.0:
            print("[WARN] z_step is 0; cannot perform zsteps")
            return
        dz = nz * z_step
        msg = {
            "type": "gantry_cmd",
            "cmd": "move_rel",
            "dx": 0.0,
            "dy": 0.0,
            "dz": float(dz),
            "de": 0.0,
        }
        self.q_from_gui.put(msg)

    @staticmethod
    def _grid_iter(
        cols: int, rows: int, nx: int, ny: int, serpentine: bool
    ) -> List[Tuple[int, int]]:
        """
        Generate (nx_steps, ny_steps) pairs for a serpentine grid.

        Starts at top-left, moves across X, then down in Y, etc.
        """
        moves: List[Tuple[int, int]] = []
        if cols <= 0 or rows <= 0:
            return moves

        for r in range(rows):
            forward = (r % 2 == 0) or (not serpentine)
            if forward:
                for _c in range(1, cols):
                    moves.append((nx, 0))
            else:
                for _c in range(1, cols):
                    moves.append((-nx, 0))
            if r < rows - 1:
                moves.append((0, ny))
        return moves

    def _handle_grid(
        self,
        cols: int,
        rows: int,
        nx_steps: int,
        ny_steps: int,
        nz_steps: int,
        dwell_s: float,
        total_s: float,
        serpentine: bool,
    ) -> None:
        """
        Run a grid routine using step moves and optional Z cycles.

        - cols, rows: number of positions
        - nx_steps, ny_steps: XY step increments per grid cell
        - nz_steps: Z steps per point (0 = ignore Z)
        - dwell_s: time in seconds to pause at Z-down (if nz_steps != 0)
        - total_s: if > 0, repeat grids until this total time has elapsed
        - serpentine: zig-zag or always left-to-right
        """
        if cols <= 0 or rows <= 0:
            print("[GRID] cols and rows must be >= 1")
            return

        start_time = time.time()
        cycle = 0

        while True:
            cycle += 1
            print(f"[GRID] Starting cycle {cycle}")
            moves = self._grid_iter(cols, rows, nx_steps, ny_steps, serpentine)

            nx_total = 0
            ny_total = 0
            
            # Update (fix) for routine: STILL NEED TO TEST
            # Trying to fix the z wait merging with the XY movement
            STEP_DELAY = 0.08 # must be > backend motion_dt (0.05s) so each phase flushes separately
            
            
            for dx_steps, dy_steps in moves:
                # Optional Z-down/Z-up at each point
                if nz_steps != 0:
                    z_step = float(self.last_state.get("z_step", 0.0))
                    if z_step != 0.0:
                        dz_down = -nz_steps * z_step
                        dz_up = -dz_down
                        # Down
                        self.q_from_gui.put({
                            "type": "gantry_cmd",
                            "cmd": "move_rel",
                            "dx": 0.0, "dy": 0.0, "dz": dz_down, "de": 0.0,
                        })
                        
                        # NEW FOR TESTING ROUTINE
                        # Barrier for merge
                        self.q_from_gui.put({"type": "gcode", "cmd": "M400"})
                        time.sleep(dwell_s) 
                        
                        
                        # Up
                        self.q_from_gui.put({
                            "type": "gantry_cmd",
                            "cmd": "move_rel",
                            "dx": 0.0, "dy": 0.0, "dz": dz_up, "de": 0.0,
                        })

                        # NEW FOR ROUTINE TESTING
                        self.q_from_gui.put({"type": "gcode", "cmd": "M400"})
                        # Small delay before XY so Z-up doesn't merge with the XY step move
                       # time.sleep(STEP_DELAY)
                        
                # XY step move
                self.q_from_gui.put({
                    "type": "gantry_cmd",
                    "cmd": "move_steps_xy",
                    "nx": int(dx_steps),
                    "ny": int(dy_steps),
                })
                ## NEW
                self.q_from_gui.put({"type": "gcode", "cmd": "M400"})
                nx_total += dx_steps
                ny_total += dy_steps

                # NEW FOR ROUTINE TESTING
                #time.sleep(STEP_DELAY)  # small spacing between moves


            # Undo XY displacement so we return to the starting point
            if nx_total or ny_total:
                print(f"[GRID] Returning to origin with {-nx_total} X-steps, {-ny_total} Y-steps")
                self.q_from_gui.put({
                    "type": "gantry_cmd",
                    "cmd": "move_steps_xy",
                    "nx": -nx_total,
                    "ny": -ny_total,
                })
                self.q_from_gui.put({"type": "gcode", "cmd": "M400"}) ## NEW barrier
                time.sleep(0.1)

            if total_s > 0.0 and (time.time() - start_time) >= total_s:
                print("[GRID] Total time reached; stopping grid.")
                break

            if total_s <= 0.0:
                print("[GRID] Single grid complete; stopping.")
                break

    def estop(self) -> None:
        self.q_from_gui.put({"type": "btn_estop"})

    def close(self) -> None:
        """
        Best-effort shutdown.
        """
        print("[ENGINE] E-STOP and shutdown.")
        self.estop()
        self._stop_flag = True
        time.sleep(0.2)


# ---------------------------------------------------------------------
# CLI main
# ---------------------------------------------------------------------

def print_help() -> None:
    print(
        "Commands:\n"
        "  home                      - home all axes (G28)\n"
        "  estop                     - emergency stop\n"
        "  feed 3000                 - set feed (mm/min)\n"
        "  rel x=1 y=-2 f=2000       - relative move in mm\n"
        "  abs x=10 y=5 f=3000       - absolute move in mm\n"
        "  steps 10 0                - move XY by steps (nx, ny)\n"
        "  zsteps 5                  - move Z by 5 steps (uses z_step from state)\n"
        "  step xy=0.5 z=0.1 e=0.02  - set step size(s) in mm per 'step'\n"
        "  pump 128                  - set pump (fan0) duty 0..255\n"
        "  gcode G1 X10 Y20 F3000    - send raw gcode\n"
        "  grid cols=3 rows=2 sx=10 sy=10 nz=0 dwell=0 total=0 serp=1\n"
        "                            - grid routine (step-based, serpentine scan)\n"
        "  pos / where               - show last reported position\n"
        "  rel / abs + multi-line:\n"
        "      rel\n"
        "      x=1\n"
        "      y=-2\n"
        "      f=2000\n"
        "      go\n"
        "  buffer, show              - show buffered command\n"
        "  clear / cancel            - clear buffered command\n"
        "  help                      - show this help\n"
        "  quit / exit               - leave\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Command-line Core-XY Gantry Engine")
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Run with simulator backend (same as GUI's simulate mode)",
    )
    args = parser.parse_args()

    engine = GantryEngine(simulate=args.simulate)
    accumulator = CommandAccumulator()

    print("Command-line Gantry Engine")
    print("Type 'help' for commands, 'quit' to exit.\n")

    try:
        while True:
            try:
                line = input("> ")
            except (EOFError, KeyboardInterrupt):
                break

            line = line.strip()
            if not line:
                continue

            low = line.lower()
            if low in ("quit", "exit"):
                break

            if low in ("help", "?"):
                print_help()
                continue

            if low in ("pos", "where"):
                s = engine.last_state
                print(
                    f"Current position: "
                    f"X={s['x']:.3f} Y={s['y']:.3f} Z={s['z']:.3f} E={s['e']:.3f} "
                    f"(feed={s['feed']})"
                )
                continue

            try:
                msgs = accumulator.process_line(line)
            except Exception as e:
                print("Parse error:", e)
                continue

            for msg in msgs:
                engine.send_msg(msg)

    finally:
        engine.close()


if __name__ == "__main__":
    main()
