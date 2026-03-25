# controller_2_0.py
# Adding joystick index
# -----------------------------------------------------------------------------
# Xbox controller reader (pygame) that ONLY emits dict messages:
#   {"type":"input","cmd": <string>, "value": <tuple|number>}
#
# Mapping persistence & updates:
#   - At startup, loads config/controller_map.json if present
#   - Accepts GUI updates as {"type":"mapping","update": {...}} (new schema)
#   - Saves mapping atomically whenever updated
#   - Notifies gantry with {"type":"controller_state","mapping": {...}} so GUI
#     can reflect the *actual* mapping in use
# -----------------------------------------------------------------------------

from __future__ import annotations
import os
import sys
import time
import json
from typing import Dict, Any
import pygame
import queue


class XboxController:
    def __init__(self, q_from_gui_to_ctrl, q_to_gantry,
                 deadzone: float = 0.10, joystick_index: int = 0) -> None:
        self.q_from_gui = q_from_gui_to_ctrl
        self.q_to_gantry = q_to_gantry
        self.deadzone = deadzone
        self.joystick_index = joystick_index

        # Default mapping; GUI or disk can override
        self.mapping: Dict[str, str] = {
            "joyL": "xy_motion",        # (x,y)  left stick
            "joyR": "z_motion",         # (x,y)  right stick -> use y
            "trig": "e_motion",         # (lt,rt) triggers mapped to E
            "a": "z_step_size_inc",
            "b": "z_step_size_dec",
            "x": "e_step_size_dec",
            "y": "e_step_size_inc",
            "lb": "xy_step_size_dec",
            "rb": "xy_step_size_inc",
            "back": "home_all",
            "start": "home_all",
            "dpad_U": "none",
            "dpad_D": "none",
            "dpad_L": "none",
            "dpad_R": "none",
        }

        # Debounce store for buttons
        self._last_button_time: Dict[str, float] = {}

        # Load persisted mapping if available
        self._load_mapping_from_disk()
        # Tell gantry (and thus the GUI) what we are using
        self._emit_controller_state()

    # ------------------------------ persistence -------------------------------

    @staticmethod
    def _config_path() -> str:
        """
        Resolve config/controller_map.json relative to this file so child
        process has a stable location regardless of cwd.
        """
        base = os.path.dirname(os.path.abspath(__file__))
        cfg_dir = os.path.join(base, "config")
        os.makedirs(cfg_dir, exist_ok=True)
        return os.path.join(cfg_dir, "controller_map.json")

    def _load_mapping_from_disk(self) -> None:
        """Load mapping from JSON if present; ignore corrupt files."""
        path = self._config_path()
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    disk = json.load(f)
                if isinstance(disk, dict):
                    # Only adopt keys we know; leave others alone
                    for k, v in disk.items():
                        if k in self.mapping and isinstance(v, str):
                            self.mapping[k] = v
        except Exception as e:
            print(f"[Controller] Failed to load mapping: {e}")

    def _save_mapping_atomic(self) -> None:
        """Persist current mapping atomically."""
        path = self._config_path()
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.mapping, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except Exception as e:
            print(f"[Controller] Failed to save mapping: {e}")

    # ------------------------------ utilities --------------------------------

    def _dz(self, v: float) -> float:
        return v if abs(v) >= self.deadzone else 0.0

    @staticmethod
    def _trig01(v: float) -> float:
        # raw trigger -1..+1 => 0..1
        return max(0.0, min(1.0, (v + 1.0) / 2.0))

    def _emit_input(self, cmd: str, value: Any) -> None:
        """Emit a normalized controller input message to the gantry."""
        if not cmd or cmd == "none":
            return
        self.q_to_gantry.put({"type": "input", "cmd": cmd, "value": value})

    def _emit_controller_state(self) -> None:
        """
        Inform gantry (and thus GUI) of our active mapping.
        Gantry will forward as {"type":"controller_state","mapping": {...}}.
        """
        self.q_to_gantry.put({"type": "controller_state", "mapping": dict(self.mapping)})

    def _drain_gui_updates(self) -> None:
        """
        Non-blocking drain of GUI → controller queue.
        Accepts both new and legacy message shapes and persists updates.
        """
        try:
            while True:
                msg = self.q_from_gui.get_nowait()
                if not isinstance(msg, dict):
                    continue

                # New schema: {"type":"mapping","update": {...}}
                upd = None
                if msg.get("type") == "mapping" and isinstance(msg.get("update"), dict):
                    upd = msg["update"]
                # Back-compat schema: {"update_mapping": {...}}
                elif "update_mapping" in msg and isinstance(msg["update_mapping"], dict):
                    upd = msg["update_mapping"]

                if not upd:
                    continue

                # Apply only known keys; coerce to str
                changed = False
                for k, v in upd.items():
                    if k in self.mapping:
                        newv = str(v)
                        if self.mapping[k] != newv:
                            self.mapping[k] = newv
                            changed = True

                if changed:
                    # Persist and notify
                    self._save_mapping_atomic()
                    self._emit_controller_state()
                    print("[Controller] Mapping updated & saved.")

        except queue.Empty:
            pass

    # ------------------------------- runtime ----------------------------------

    def read_controller(self) -> None:
        """
        Main loop:
          - reads joystick state via pygame
          - accepts mapping updates from GUI
          - emits normalized 'input' messages to gantry
        """
        pygame.init()
        try:
            js = pygame.joystick.Joystick(self.joystick_index)
            js.init()
        except Exception as e:
            print(f"[Controller] No joystick: {e}")
            # Keep listening for mapping updates even without a device
            while True:
                self._drain_gui_updates()
                time.sleep(0.1)

        clock = pygame.time.Clock()

        while True:
            clock.tick(60)
            pygame.event.pump()
            self._drain_gui_updates()

            # Axes
            joyL = (self._dz(js.get_axis(0)), self._dz(js.get_axis(1)))
            joyR = (self._dz(js.get_axis(2)), self._dz(js.get_axis(3)))
            trig = (self._trig01(js.get_axis(4)), self._trig01(js.get_axis(5)))

            if (joyL[0] or joyL[1]) and self.mapping.get("joyL") != "none":
                self._emit_input(self.mapping["joyL"], joyL)
            if (joyR[0] or joyR[1]) and self.mapping.get("joyR") != "none":
                # keep both components; gantry uses Y for Z and may ignore X
                self._emit_input(self.mapping["joyR"], joyR)
            if (trig[0] or trig[1]) and self.mapping.get("trig") != "none":
                self._emit_input(self.mapping["trig"], trig)

            # Buttons (+ dpad)
            names = ["a","b","x","y","lb","rb","back","start","ljoy","rjoy","xbox"]
            btn = {n: js.get_button(i) for i, n in enumerate(names)}
            dpx, dpy = js.get_hat(0)
            btn.update({
                "dpad_L": 1 if dpx == -1 else 0,
                "dpad_R": 1 if dpx == +1 else 0,
                "dpad_D": 1 if dpy == -1 else 0,
                "dpad_U": 1 if dpy == +1 else 0,
            })

            now = time.time()
            for name, pressed in btn.items():
                if not pressed:
                    continue
                cmd = self.mapping.get(name, "none")
                if cmd == "none":
                    continue
                last = self._last_button_time.get(name, 0.0)
                if now - last < 0.25:  # debounce
                    continue
                self._last_button_time[name] = now
                self._emit_input(cmd, 1)
