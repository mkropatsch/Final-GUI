# controller_keyboard.py
from __future__ import annotations
import os, time, json, pygame, queue
from typing import Dict, Any

class KeyboardController:
    def __init__(self, q_from_gui_to_ctrl, q_to_gantry, repeat_hz: float = 60.0) -> None:
        self.q_from_gui = q_from_gui_to_ctrl
        self.q_to_gantry = q_to_gantry
        self.dt = 1.0 / repeat_hz
        # same keys as Xbox mapping so GUI + disk persistence still work
        self.mapping: Dict[str, str] = {
            "joyL": "xy_motion", "joyR": "z_motion", "trig": "e_motion",
            "a": "z_step_size_inc", "b": "z_step_size_dec",
            "x": "e_step_size_dec", "y": "e_step_size_inc",
            "lb": "xy_step_size_dec", "rb": "xy_step_size_inc",
            "back": "home_all", "start": "home_all",
            "dpad_U": "none", "dpad_D": "none", "dpad_L": "none", "dpad_R": "none",
        }
        self._last_button_time: Dict[str, float] = {}
        self._load_mapping_from_disk()
        self._emit_controller_state()

    # ---------- persistence (same path as controller.py) ----------
    @staticmethod
    def _config_path() -> str:
        base = os.path.dirname(os.path.abspath(__file__))
        cfg_dir = os.path.join(base, "config")
        os.makedirs(cfg_dir, exist_ok=True)
        return os.path.join(cfg_dir, "controller_map.json")

    def _load_mapping_from_disk(self) -> None:
        try:
            path = self._config_path()
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    disk = json.load(f)
                if isinstance(disk, dict):
                    for k, v in disk.items():
                        if k in self.mapping and isinstance(v, str):
                            self.mapping[k] = v
        except Exception:
            pass

    def _emit_controller_state(self) -> None:
        self.q_to_gantry.put({"type": "controller_state", "mapping": dict(self.mapping)})

    def _drain_gui_updates(self) -> None:
        try:
            while True:
                msg = self.q_from_gui.get_nowait()
                if not isinstance(msg, dict): continue
                upd = None
                if msg.get("type") == "mapping" and isinstance(msg.get("update"), dict):
                    upd = msg["update"]
                elif "update_mapping" in msg and isinstance(msg["update_mapping"], dict):
                    upd = msg["update_mapping"]
                if not upd: continue
                changed = False
                for k, v in upd.items():
                    if k in self.mapping:
                        v = str(v)
                        if self.mapping[k] != v:
                            self.mapping[k] = v; changed = True
                if changed:
                    try:
                        path = self._config_path(); tmp = path + ".tmp"
                        with open(tmp, "w", encoding="utf-8") as f:
                            json.dump(self.mapping, f, indent=2); f.flush(); os.fsync(f.fileno())
                        os.replace(tmp, path)
                    except Exception:
                        pass
                    self._emit_controller_state()
        except queue.Empty:
            pass

    def _emit_input(self, cmd: str, value: Any) -> None:
        if not cmd or cmd == "none": return
        self.q_to_gantry.put({"type": "input", "cmd": cmd, "value": value})

    def _debounced_button(self, logical_name: str) -> None:
        cmd = self.mapping.get(logical_name, "none")
        if cmd == "none": return
        now = time.time()
        last = self._last_button_time.get(logical_name, 0.0)
        if now - last >= 0.25:
            self._last_button_time[logical_name] = now
            self._emit_input(cmd, 1)

    def read_controller(self) -> None:
        pygame.init()
        screen = pygame.display.set_mode((260, 120))
        pygame.display.set_caption("Keyboard Controller - click here to focus")

        clock = pygame.time.Clock()

        while True:
            clock.tick(60)
            pygame.event.pump()
            self._drain_gui_updates()

            keys = pygame.key.get_pressed()

            # --- XY from WASD (like a stick) ---
            # --- XY from WASD (like a stick) ---
            jx = float(keys[pygame.K_d]) - float(keys[pygame.K_a])   # +1 right, -1 left
            jy = float(keys[pygame.K_s]) - float(keys[pygame.K_w])   # +1 down, -1 up
            if jx or jy:
                print("XY:", jx, jy)  # <-- debug line
                self._emit_input(self.mapping.get("joyL","xy_motion"), (jx, jy))


            # --- Z from arrows (use Y component only) ---
            # Up arrow -> +Z; Down arrow -> -Z  (we feed +1/-1; gantry handles scaling)
            z_jy = float(keys[pygame.K_UP]) - float(keys[pygame.K_DOWN])
            if z_jy:
                print("Z:", z_jy)
                self._emit_input(self.mapping.get("joyR","z_motion"), (0.0, z_jy))

            # --- E from [ ] as (lt, rt) pair in 0..1 ---
            lt = float(keys[pygame.K_LEFTBRACKET])
            rt = float(keys[pygame.K_RIGHTBRACKET])
            if lt or rt:
                print("E:", (lt, rt))
                self._emit_input(self.mapping.get("trig","e_motion"), (lt, rt))

            # --- Step-size buttons & actions (debounced on key-down) ---
            for ev in pygame.event.get():
                if ev.type == pygame.KEYDOWN:
                    if ev.key == pygame.K_h: self._debounced_button("back")  # home_all
                    if ev.key == pygame.K_COMMA: self._debounced_button("lb")  # xy_step_size_dec
                    if ev.key == pygame.K_PERIOD: self._debounced_button("rb") # xy_step_size_inc
                    if ev.key == pygame.K_k: self._debounced_button("b")       # z_step_size_dec
                    if ev.key == pygame.K_l: self._debounced_button("a")       # z_step_size_inc
                    if ev.key == pygame.K_n: self._debounced_button("x")       # e_step_size_dec
                    if ev.key == pygame.K_m: self._debounced_button("y")       # e_step_size_inc
