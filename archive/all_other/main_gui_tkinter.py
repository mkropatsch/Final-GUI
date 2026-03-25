# main_gui_tkinter.py
# Tkinter GUI replacement for main_gui_new_automate_2.py (PyQt version)
# Keeps the same multiprocessing queues + GantrySystem backend.

from __future__ import annotations

import argparse
import multiprocessing as mp
import time
from dataclasses import dataclass
from typing import Dict, Iterator, Optional, Tuple

import tkinter as tk
from tkinter import ttk, messagebox


# --------------------------- child (gantry) entry ----------------------------

def gantry_process_main(q_to_gui, q_from_gui, q_from_controller, simulate: bool) -> None:
    from gantry_gui_new_automate_2 import GantrySystem
    g = GantrySystem(
        q_to_gui=q_to_gui,
        q_from_gui=q_from_gui,
        q_from_controller=q_from_controller,
        simulate=simulate,
    )
    g.run()


# ------------------------------- Tk GUI --------------------------------------

@dataclass
class JogRepeater:
    """Repeats a function every dt_ms while active (used for press-and-hold jog)."""
    dt_ms: int = 50
    _after_id: Optional[str] = None
    _active: bool = False

    def start(self, root: tk.Tk, fn):
        if self._active:
            return
        self._active = True

        def _tick():
            if not self._active:
                return
            fn()
            self._after_id = root.after(self.dt_ms, _tick)

        _tick()

    def stop(self, root: tk.Tk):
        self._active = False
        if self._after_id is not None:
            try:
                root.after_cancel(self._after_id)
            except Exception:
                pass
        self._after_id = None


class StageTk:
    def __init__(self, root: tk.Tk, simulate: bool = True):
        self.root = root
        self.root.title("Core-XY Gantry — Tkinter GUI Controller")
        self.root.geometry("1320x900")

        # IPC
        ctx = mp.get_context("spawn")
        self.q_gantry_to_gui = ctx.Queue(maxsize=1000)
        self.q_gui_to_gantry = ctx.Queue(maxsize=1000)
        self.q_ctrl_to_gantry = ctx.Queue(maxsize=1000)

        # Start backend
        self.p_gantry = ctx.Process(
            target=gantry_process_main,
            args=(self.q_gantry_to_gui, self.q_gui_to_gantry, self.q_ctrl_to_gantry, simulate),
            daemon=True,
        )
        self.p_gantry.start()

        # State
        self._home_abs: Optional[Dict[str, float]] = None
        self._last_abs = {"x": 0.0, "y": 0.0, "z": 0.0, "e": 0.0}

        # Routine state
        self._routine_iter: Optional[Iterator[Tuple[int, int]]] = None
        self._routine_active = False
        self._routine_phase: str = "idle"
        self._routine_nz: int = 0
        self._routine_dwell: int = 0
        self._routine_after_id: Optional[str] = None

        # Polling
        self._poll_dt_ms = 50
        self._poll_after_id: Optional[str] = None

        # Pump timer
        self._pump_after_id: Optional[str] = None

        # XY canvas redraw throttle
        self._xy_resize_after: Optional[str] = None

        self._build_ui()

        # Start polling loop
        self._poll_loop()

        # Close handling
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._post_msg("Gantry started.")

    # -------------------------- UI construction --------------------------

    def _build_ui(self):
        self.root.columnconfigure(0, weight=3)
        self.root.columnconfigure(1, weight=3)
        self.root.rowconfigure(0, weight=1)

        # Left frame (XY canvas)
        left = ttk.Frame(self.root, padding=8)
        left.grid(row=0, column=0, sticky="nsew")
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        lf_group = ttk.LabelFrame(left, text="XY Position (relative to Work Home 0,0)", padding=8)
        lf_group.grid(row=0, column=0, sticky="nsew")
        lf_group.rowconfigure(0, weight=1)
        lf_group.columnconfigure(0, weight=1)

        self.xy_canvas = tk.Canvas(lf_group, bg="white")
        self.xy_canvas.grid(row=0, column=0, sticky="nsew")

        # XY plot settings
        self._canvas_range_mm = 50.0  # +/- shown range in mm
        self._xy_dot_id = None
        self._xy_readout_id = None
        self._xy_vline_id = None
        self._xy_hline_id = None

        # Draw after layout is realized
        self.root.after(50, self._draw_xy_canvas_grid)

        # Right frame (controls)
        right = ttk.Frame(self.root, padding=8)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.columnconfigure(1, weight=1)

        # State group
        st = ttk.LabelFrame(right, text="State", padding=8)
        st.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        st.columnconfigure(1, weight=1)

        self.var_x = tk.StringVar(value="0.000")
        self.var_y = tk.StringVar(value="0.000")
        self.var_z = tk.StringVar(value="0.000")

        ttk.Label(st, text="X_rel").grid(row=0, column=0, sticky="w")
        ttk.Label(st, textvariable=self.var_x).grid(row=0, column=1, sticky="e")
        ttk.Label(st, text="Y_rel").grid(row=1, column=0, sticky="w")
        ttk.Label(st, textvariable=self.var_y).grid(row=1, column=1, sticky="e")
        ttk.Label(st, text="Z (abs)").grid(row=2, column=0, sticky="w")
        ttk.Label(st, textvariable=self.var_z).grid(row=2, column=1, sticky="e")

        # Step/Speed group
        ctrl = ttk.LabelFrame(right, text="Step   Speed", padding=8)
        ctrl.grid(row=1, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        ctrl.columnconfigure(1, weight=1)

        # Preset
        ttk.Label(ctrl, text="Preset").grid(row=0, column=0, sticky="w")
        self.cmb_preset = ttk.Combobox(ctrl, values=["Fine", "Medium", "Coarse"], state="readonly")
        self.cmb_preset.set("Medium")
        self.cmb_preset.grid(row=0, column=1, sticky="ew")
        self.cmb_preset.bind("<<ComboboxSelected>>", lambda _e: self._apply_preset_fields(self.cmb_preset.get()))

        # Step sizes
        self.ent_xy = ttk.Entry(ctrl)
        self.ent_z = ttk.Entry(ctrl)
        self.ent_xy.insert(0, "0.200")
        self.ent_z.insert(0, "0.050")

        ttk.Label(ctrl, text="XY step (mm)").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.ent_xy.grid(row=1, column=1, sticky="ew", pady=(6, 0))
        ttk.Label(ctrl, text="Z step (mm)").grid(row=2, column=0, sticky="w")
        self.ent_z.grid(row=2, column=1, sticky="ew")

        # Feed
        ttk.Label(ctrl, text="Feed (mm/min)").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.feed_var = tk.IntVar(value=3000)
        self.lab_feed = ttk.Label(ctrl, text="3000 mm/min")

        feed_row = ttk.Frame(ctrl)
        feed_row.grid(row=3, column=1, sticky="ew", pady=(6, 0))
        feed_row.columnconfigure(0, weight=1)

        self.sld_feed = ttk.Scale(feed_row, from_=100, to=12000, orient="horizontal", command=self._on_feed_change)
        self.sld_feed.set(3000)
        self.sld_feed.grid(row=0, column=0, sticky="ew")
        self.lab_feed.grid(row=0, column=1, padx=(8, 0))
        ttk.Button(ctrl, text="Apply feed", command=self._apply_feed_to_gantry).grid(row=4, column=1, sticky="e", pady=(4, 0))

        # Action buttons
        act = ttk.Frame(ctrl)
        act.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(act, text="Apply step sizes", command=self._apply_steps_to_gantry).grid(row=0, column=0, padx=3)
        ttk.Button(act, text="Set Home (XY → 0,0 here)", command=self._set_home_xy_origin).grid(row=0, column=1, padx=3)
        ttk.Button(act, text="Go to Home (XY)", command=self._goto_home_xy).grid(row=0, column=2, padx=3)
        ttk.Button(act, text="Machine Home (G28)", command=lambda: self.q_gui_to_gantry.put({"type": "home_all"})).grid(row=0, column=3, padx=3)

        estop = ttk.Button(act, text="E-STOP", command=self._on_estop)
        estop.grid(row=0, column=4, padx=3)

        # Move XY steps
        mv = ttk.Frame(ctrl)
        mv.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Label(mv, text="ΔX steps").grid(row=0, column=0, sticky="w")
        self.ent_steps_x = ttk.Entry(mv, width=8)
        self.ent_steps_x.insert(0, "0")
        self.ent_steps_x.grid(row=0, column=1, padx=(4, 10))

        ttk.Label(mv, text="ΔY steps").grid(row=0, column=2, sticky="w")
        self.ent_steps_y = ttk.Entry(mv, width=8)
        self.ent_steps_y.insert(0, "0")
        self.ent_steps_y.grid(row=0, column=3, padx=(4, 10))

        ttk.Button(mv, text="Move XY (steps)", command=self._on_move_steps_xy).grid(row=0, column=4, padx=3)

        # Move Z steps
        mvz = ttk.Frame(ctrl)
        mvz.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Label(mvz, text="ΔZ steps").grid(row=0, column=0, sticky="w")
        self.ent_steps_z = ttk.Entry(mvz, width=8)
        self.ent_steps_z.insert(0, "0")
        self.ent_steps_z.grid(row=0, column=1, padx=(4, 10))
        ttk.Button(mvz, text="Move Z (steps)", command=self._on_move_z_steps).grid(row=0, column=2, padx=3)

        # Manual Control group
        man = ttk.LabelFrame(right, text="Manual Control", padding=8)
        man.grid(row=1, column=1, sticky="nsew", pady=(0, 8))
        for c in range(4):
            man.columnconfigure(c, weight=1)

        def mk_btn(text):
            return ttk.Button(man, text=text)

        self._jog = {
            "ul": JogRepeater(), "up": JogRepeater(), "ur": JogRepeater(),
            "lf": JogRepeater(), "rt": JogRepeater(),
            "dl": JogRepeater(), "dn": JogRepeater(), "dr": JogRepeater(),
            "zp": JogRepeater(), "zm": JogRepeater(),
        }

        btn_ul = mk_btn("↖"); btn_up = mk_btn("↑"); btn_ur = mk_btn("↗"); btn_zp = mk_btn("Z↑")
        btn_lf = mk_btn("←"); btn_c  = mk_btn("•"); btn_rt = mk_btn("→")
        btn_dl = mk_btn("↙"); btn_dn = mk_btn("↓"); btn_dr = mk_btn("↘"); btn_zm = mk_btn("Z↓")

        btn_ul.grid(row=0, column=0, padx=2, pady=2)
        btn_up.grid(row=0, column=1, padx=2, pady=2)
        btn_ur.grid(row=0, column=2, padx=2, pady=2)
        btn_zp.grid(row=0, column=3, padx=2, pady=2)

        btn_lf.grid(row=1, column=0, padx=2, pady=2)
        btn_c.grid(row=1, column=1, padx=2, pady=2)
        btn_rt.grid(row=1, column=2, padx=2, pady=2)

        btn_dl.grid(row=2, column=0, padx=2, pady=2)
        btn_dn.grid(row=2, column=1, padx=2, pady=2)
        btn_dr.grid(row=2, column=2, padx=2, pady=2)
        btn_zm.grid(row=2, column=3, padx=2, pady=2)

        self._bind_jog(btn_up, "up", lambda: self._jog_xy(0, +1))
        self._bind_jog(btn_dn, "dn", lambda: self._jog_xy(0, -1))
        self._bind_jog(btn_lf, "lf", lambda: self._jog_xy(-1, 0))
        self._bind_jog(btn_rt, "rt", lambda: self._jog_xy(+1, 0))
        self._bind_jog(btn_ul, "ul", lambda: self._jog_xy(-1, +1))
        self._bind_jog(btn_ur, "ur", lambda: self._jog_xy(+1, +1))
        self._bind_jog(btn_dl, "dl", lambda: self._jog_xy(-1, -1))
        self._bind_jog(btn_dr, "dr", lambda: self._jog_xy(+1, -1))
        self._bind_jog(btn_zp, "zp", lambda: self._jog_z(+1))
        self._bind_jog(btn_zm, "zm", lambda: self._jog_z(-1))

        btn_c.configure(command=lambda: None)

        # Pump group
        pump = ttk.LabelFrame(right, text="Pump Control (FAN0 → M106/M107)", padding=8)
        pump.grid(row=2, column=1, sticky="nsew", pady=(0, 8))
        pump.columnconfigure(0, weight=1)

        self.lab_pump = ttk.Label(pump, text="0 / 255  (0%)")
        self.sld_pump = ttk.Scale(pump, from_=0, to=255, orient="horizontal", command=self._on_pump_change)
        self.sld_pump.set(0)
        self.sld_pump.grid(row=0, column=0, sticky="ew")
        self.lab_pump.grid(row=0, column=1, padx=(8, 0))

        secs_row = ttk.Frame(pump)
        secs_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(secs_row, text="Seconds:").grid(row=0, column=0, sticky="w")
        self.ent_pump_secs = ttk.Entry(secs_row, width=6)
        self.ent_pump_secs.insert(0, "60")
        self.ent_pump_secs.grid(row=0, column=1, padx=(6, 10))

        ttk.Button(secs_row, text="Pump ON", command=self._on_pump_on).grid(row=0, column=2, padx=3)
        ttk.Button(secs_row, text="Pump OFF", command=self._on_pump_off).grid(row=0, column=3, padx=3)
        ttk.Button(secs_row, text="Run Timed", command=self._on_pump_run).grid(row=0, column=4, padx=3)

        # Messages
        msg = ttk.LabelFrame(right, text="Messages", padding=8)
        msg.grid(row=2, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        msg.columnconfigure(0, weight=1)
        self.lab_msg = ttk.Label(msg, text="Ready.", wraplength=460, justify="left")
        self.lab_msg.grid(row=0, column=0, sticky="ew")

        # Grid Routine
        rt = ttk.LabelFrame(right, text="Grid Automation Routine", padding=8)
        rt.grid(row=3, column=0, columnspan=2, sticky="ew")
        for c in range(8):
            rt.columnconfigure(c, weight=0)

        ttk.Label(rt, text="Cols").grid(row=0, column=0, sticky="w")
        self.ent_cols = ttk.Entry(rt, width=5); self.ent_cols.insert(0, "4")
        self.ent_cols.grid(row=0, column=1, padx=(4, 12))
        ttk.Label(rt, text="Rows").grid(row=0, column=2, sticky="w")
        self.ent_rows = ttk.Entry(rt, width=5); self.ent_rows.insert(0, "3")
        self.ent_rows.grid(row=0, column=3, padx=(4, 12))

        ttk.Label(rt, text="ΔX steps").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.ent_dx = ttk.Entry(rt, width=8); self.ent_dx.insert(0, "210")
        self.ent_dx.grid(row=1, column=1, padx=(4, 12), pady=(6, 0))
        ttk.Label(rt, text="ΔY steps").grid(row=1, column=2, sticky="w", pady=(6, 0))
        self.ent_dy = ttk.Entry(rt, width=8); self.ent_dy.insert(0, "220")
        self.ent_dy.grid(row=1, column=3, padx=(4, 12), pady=(6, 0))
        ttk.Label(rt, text="ΔZ steps").grid(row=1, column=4, sticky="w", pady=(6, 0))
        self.ent_dz = ttk.Entry(rt, width=8); self.ent_dz.insert(0, "0")
        self.ent_dz.grid(row=1, column=5, padx=(4, 12), pady=(6, 0))

        ttk.Label(rt, text="Wait (s)").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.ent_dwell = ttk.Entry(rt, width=8); self.ent_dwell.insert(0, "1")
        self.ent_dwell.grid(row=2, column=1, padx=(4, 12), pady=(6, 0))

        self.var_serp = tk.BooleanVar(value=True)
        ttk.Checkbutton(rt, text="Serpentine", variable=self.var_serp).grid(row=2, column=2, columnspan=2, sticky="w", pady=(6, 0))

        ttk.Button(rt, text="Start Routine", command=self._on_routine_start).grid(row=3, column=0, pady=(10, 0))
        ttk.Button(rt, text="Stop Routine", command=self._on_routine_stop).grid(row=3, column=1, pady=(10, 0))

    def _bind_jog(self, btn: ttk.Button, key: str, fn):
        def _press(_e):
            self._routine_stop_if_active()
            self._jog[key].start(self.root, fn)

        def _release(_e):
            self._jog[key].stop(self.root)

        btn.bind("<ButtonPress-1>", _press)
        btn.bind("<ButtonRelease-1>", _release)
        btn.bind("<Leave>", _release)

    # -------------------------- Helpers / messaging --------------------------

    def _post_msg(self, text: str):
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self.lab_msg.configure(text=f"[{stamp}] {text}")

    def _get_int(self, entry: ttk.Entry, default: int = 0) -> int:
        try:
            return int(float(entry.get().strip()))
        except Exception:
            return default

    # -------------------------- Step/Feed actions --------------------------

    def _apply_preset_fields(self, name: str):
        if name == "Fine":
            self._set_entry(self.ent_xy, "0.010")
            self._set_entry(self.ent_z, "0.005")
        elif name == "Medium":
            self._set_entry(self.ent_xy, "0.200")
            self._set_entry(self.ent_z, "0.050")
        else:
            self._set_entry(self.ent_xy, "1.000")
            self._set_entry(self.ent_z, "0.200")

    def _set_entry(self, entry: ttk.Entry, text: str):
        entry.delete(0, tk.END)
        entry.insert(0, text)

    def _apply_steps_to_gantry(self):
        try:
            xy = float(self.ent_xy.get())
            z = float(self.ent_z.get())
        except ValueError:
            messagebox.showwarning("Invalid", "Enter numeric step sizes.")
            return
        self.q_gui_to_gantry.put({"type": "set_steps", "xy_step": xy, "z_step": z, "e_step": 0.02})
        self._post_msg(f"Step sizes sent: XY={xy}, Z={z}")

    def _on_feed_change(self, _val):
        v = int(float(self.sld_feed.get()))
        self.feed_var.set(v)
        self.lab_feed.configure(text=f"{v} mm/min")

    def _apply_feed_to_gantry(self):
        v = int(float(self.sld_feed.get()))
        self.q_gui_to_gantry.put({"type": "set_feed", "feed_mm_min": v})
        self._post_msg(f"Feed sent: {v} mm/min")

    # -------------------------- Home / moves --------------------------

    def _set_home_xy_origin(self):
        ax, ay = self._last_abs["x"], self._last_abs["y"]
        self._home_abs = {"x": ax, "y": ay}
        self._post_msg(f"Work Home set at (X{ax:.3f}, Y{ay:.3f}). Relative now 0,0.")

    def _goto_home_xy(self):
        if not self._home_abs:
            self._post_msg("No Work Home set yet. Use 'Set Home'.")
            return
        self.q_gui_to_gantry.put({
            "type": "gantry_cmd", "cmd": "move_abs",
            "X": self._home_abs["x"], "Y": self._home_abs["y"]
        })
        self._post_msg("Queued: Go to Work Home (XY).")

    def _on_move_steps_xy(self):
        nx = self._get_int(self.ent_steps_x, 0)
        ny = self._get_int(self.ent_steps_y, 0)
        self.q_gui_to_gantry.put({"type": "gantry_cmd", "cmd": "move_steps_xy", "nx": nx, "ny": ny})
        self._post_msg(f"Queued XY move: ΔX={nx}×step, ΔY={ny}×step")

    def _get_z_step_mm(self) -> float:
        try:
            return float(self.ent_z.get())
        except Exception:
            return 0.0

    def _on_move_z_steps(self):
        nz = self._get_int(self.ent_steps_z, 0)
        z_step_mm = self._get_z_step_mm()
        dz_mm = nz * z_step_mm
        if dz_mm == 0.0:
            self._post_msg("Z move: no motion (ΔZ=0).")
            return
        self.q_gui_to_gantry.put({"type": "gantry_cmd", "cmd": "move_rel", "dz": dz_mm})
        self._post_msg(f"Queued Z move: ΔZ={nz}×step ({dz_mm:.4f} mm)")

    # -------------------------- Manual jog --------------------------

    def _routine_stop_if_active(self):
        if self._routine_active:
            self._on_routine_stop()
            self._post_msg("Routine stopped due to manual input.")

    def _jog_xy(self, sx: int, sy: int):
        self.q_ctrl_to_gantry.put({"type": "input", "cmd": "xy_motion", "value": (sx, sy)})

    def _jog_z(self, s: int):
        self.q_ctrl_to_gantry.put({"type": "input", "cmd": "z_motion", "value": (0, s)})

    # -------------------------- Pump --------------------------

    def _on_pump_change(self, _val):
        v = int(float(self.sld_pump.get()))
        pct = int(round(100 * v / 255.0))
        self.lab_pump.configure(text=f"{v} / 255  ({pct}%)")

    def _send_gcode(self, cmd: str):
        self.q_gui_to_gantry.put({"type": "gcode", "cmd": cmd})

    def _send_pump(self, duty_0_255: int):
        d = max(0, min(255, int(duty_0_255)))
        if d <= 0:
            self._send_gcode("M107 P0")
        else:
            self._send_gcode(f"M106 P0 S{d}")
        self.q_gui_to_gantry.put({"type": "fan_set", "index": 0, "value": d})

    def _cancel_pump_timer(self):
        if self._pump_after_id is not None:
            try:
                self.root.after_cancel(self._pump_after_id)
            except Exception:
                pass
        self._pump_after_id = None

    def _on_pump_on(self):
        self._cancel_pump_timer()
        duty = int(float(self.sld_pump.get()))
        self._send_pump(duty)
        self._post_msg(f"Pump ON at {duty}/255.")

    def _on_pump_off(self):
        self._cancel_pump_timer()
        self._send_pump(0)
        self._post_msg("Pump OFF.")

    def _on_pump_run(self):
        duty = int(float(self.sld_pump.get()))
        secs = self._get_int(self.ent_pump_secs, 60)
        if duty <= 0:
            messagebox.showinfo("Pump", "Set duty > 0 to run the pump.")
            return
        self._send_pump(duty)
        self._cancel_pump_timer()
        self._pump_after_id = self.root.after(secs * 1000, self._on_pump_off)
        self._post_msg(f"Pump RUN {secs}s at {duty}/255…")

    # -------------------------- Routine (grid serpentine) --------------------------

    def _grid_iter(self, cols: int, rows: int, nx: int, ny: int, serp: bool) -> Iterator[Tuple[int, int]]:
        if cols <= 0 or rows <= 0:
            return
        for r in range(rows):
            forward = (r % 2 == 0) or (not serp)
            if forward:
                for _c in range(1, cols):
                    yield (nx, 0)
            else:
                for _c in range(1, cols):
                    yield (-nx, 0)
            if r < rows - 1:
                yield (0, ny)

    def _on_routine_start(self):
        cols = self._get_int(self.ent_cols, 0)
        rows = self._get_int(self.ent_rows, 0)
        nx = self._get_int(self.ent_dx, 0)
        ny = self._get_int(self.ent_dy, 0)
        nz = self._get_int(self.ent_dz, 0)
        dwell_s = self._get_int(self.ent_dwell, 0)

        if cols <= 0 or rows <= 0:
            messagebox.showwarning("Routine", "Grid must be at least 1×1.")
            return

        self._routine_iter = iter(self._grid_iter(cols, rows, nx, ny, self.var_serp.get()))
        self._routine_nz = nz
        self._routine_dwell = dwell_s
        self._routine_active = True
        self._routine_phase = "z_down"

        self._post_msg(
            f"Routine started: {cols}×{rows}, ΔX={nx} steps, ΔY={ny} steps, "
            f"ΔZ={nz} steps, dwell={dwell_s}s."
        )
        self._routine_schedule(0)

    def _on_routine_stop(self):
        self._routine_active = False
        self._routine_iter = None
        self._routine_phase = "idle"
        if self._routine_after_id is not None:
            try:
                self.root.after_cancel(self._routine_after_id)
            except Exception:
                pass
        self._routine_after_id = None

    def _routine_issue_next_move(self):
        if not self._routine_active or self._routine_iter is None:
            self._post_msg("Routine complete.")
            return
        try:
            nx, ny = next(self._routine_iter)
        except StopIteration:
            self._routine_active = False
            self._routine_iter = None
            self._post_msg("Routine complete.")
            return
        self.q_gui_to_gantry.put({"type": "gantry_cmd", "cmd": "move_steps_xy", "nx": nx, "ny": ny})

    def _routine_schedule(self, delay_ms: int):
        if self._routine_after_id is not None:
            try:
                self.root.after_cancel(self._routine_after_id)
            except Exception:
                pass
        self._routine_after_id = self.root.after(delay_ms, self._routine_tick)

    def _routine_tick(self):
        if not self._routine_active:
            return

        nz = self._routine_nz
        dwell_s = self._routine_dwell
        z_step_mm = self._get_z_step_mm()

        # XY-only fallback
        if nz == 0 or z_step_mm == 0.0 or dwell_s < 0:
            self._routine_phase = "idle"
            self._routine_issue_next_move()
            if not self._routine_active:
                return
            self._routine_schedule(max(50, dwell_s * 1000))
            return

        if self._routine_phase == "z_down":
            dz_mm = -nz * z_step_mm
            if dz_mm != 0.0:
                self.q_gui_to_gantry.put({"type": "gantry_cmd", "cmd": "move_rel", "dz": dz_mm})
            self._routine_phase = "z_wait"
            self._routine_schedule(max(50, dwell_s * 1000))

        elif self._routine_phase == "z_wait":
            dz_mm = nz * z_step_mm
            if dz_mm != 0.0:
                self.q_gui_to_gantry.put({"type": "gantry_cmd", "cmd": "move_rel", "dz": dz_mm})
            self._routine_phase = "xy_move"
            self._routine_schedule(50)

        elif self._routine_phase == "xy_move":
            self._routine_issue_next_move()
            if not self._routine_active:
                self._routine_phase = "idle"
                return
            self._routine_phase = "z_down"
            self._routine_schedule(50)

        else:
            self._routine_active = False
            self._routine_phase = "idle"
            self._post_msg("Routine stopped (invalid phase).")

    # -------------------------- Gantry → GUI polling --------------------------

    def _poll_loop(self):
        while True:
            try:
                msg = self.q_gantry_to_gui.get_nowait()
            except Exception:
                break

            if isinstance(msg, dict):
                typ = msg.get("type")
                if typ == "state":
                    self._apply_state(msg)
                elif typ == "message":
                    self._post_msg(str(msg.get("text", "")))

        self._poll_after_id = self.root.after(self._poll_dt_ms, self._poll_loop)

    def _apply_state(self, s: Dict):
        ax = float(s.get("x", 0.0)); ay = float(s.get("y", 0.0))
        az = float(s.get("z", 0.0)); ae = float(s.get("e", 0.0))
        self._last_abs.update({"x": ax, "y": ay, "z": az, "e": ae})

        if self._home_abs:
            xr = ax - self._home_abs["x"]
            yr = ay - self._home_abs["y"]
        else:
            xr, yr = ax, ay

        self.var_x.set(f"{xr:.3f}")
        self.var_y.set(f"{yr:.3f}")
        self.var_z.set(f"{az:.3f}")

        # Sync fields from backend if provided
        if "xy_step" in s:
            self._set_entry(self.ent_xy, f"{float(s['xy_step']):.3f}")
        if "z_step" in s:
            self._set_entry(self.ent_z, f"{float(s['z_step']):.3f}")
        if "feed" in s:
            val = int(s["feed"])
            self.sld_feed.set(val)
            self.lab_feed.configure(text=f"{val} mm/min")

        # Update plot dot + readout
        if self._xy_dot_id is not None:
            self._update_xy_dot(xr, yr)

    # -------------------------- XY canvas drawing --------------------------

    def _draw_xy_canvas_grid(self):
        c = self.xy_canvas
        c.delete("all")

        w = c.winfo_width() or 600
        h = c.winfo_height() or 600

        # Plot margins (room for labels/ticks)
        self._plot_left = 75
        self._plot_right = w - 20
        self._plot_top = 20
        self._plot_bot = h - 55

        self._plot_w = max(10, self._plot_right - self._plot_left)
        self._plot_h = max(10, self._plot_bot - self._plot_top)

        rng = int(round(float(self._canvas_range_mm)))
        if rng <= 0:
            rng = 50

        major = 10
        minor = 5

        # Frame
        c.create_rectangle(self._plot_left, self._plot_top, self._plot_right, self._plot_bot, outline="#888")

        def vline(mm, color, width=1):
            x = self._mm_to_canvas_x(mm)
            c.create_line(x, self._plot_top, x, self._plot_bot, fill=color, width=width)

        def hline(mm, color, width=1):
            y = self._mm_to_canvas_y(mm)
            c.create_line(self._plot_left, y, self._plot_right, y, fill=color, width=width)

        # Minor grid (skip majors)
        for mm in range(-rng, rng + 1, minor):
            if (mm % major) != 0:
                vline(mm, "#f0f0f0")
                hline(mm, "#f0f0f0")

        # Major grid + ticks + labels
        for mm in range(-rng, rng + 1, major):
            vline(mm, "#e0e0e0")
            hline(mm, "#e0e0e0")

            # X ticks
            x = self._mm_to_canvas_x(mm)
            c.create_line(x, self._plot_bot, x, self._plot_bot + 6, fill="#666")
            c.create_text(x, self._plot_bot + 18, text=str(mm), fill="#444", font=("Segoe UI", 9))

            # Y ticks
            y = self._mm_to_canvas_y(mm)
            c.create_line(self._plot_left - 6, y, self._plot_left, y, fill="#666")
            c.create_text(self._plot_left - 10, y, text=str(mm), fill="#444", font=("Segoe UI", 9), anchor="e")

        # Axes at 0
        c.create_line(self._mm_to_canvas_x(0), self._plot_top, self._mm_to_canvas_x(0), self._plot_bot, fill="#b0b0b0", width=2)
        c.create_line(self._plot_left, self._mm_to_canvas_y(0), self._plot_right, self._mm_to_canvas_y(0), fill="#b0b0b0", width=2)

        # Axis labels
        c.create_text((self._plot_left + self._plot_right) / 2, h - 22, text="X (mm)", fill="#222", font=("Segoe UI", 11))
        try:
            c.create_text(22, (self._plot_top + self._plot_bot) / 2, text="Y (mm)", fill="#222", font=("Segoe UI", 11), angle=90)
        except Exception:
            c.create_text(22, (self._plot_top + self._plot_bot) / 2, text="Y\n(\nm\nm\n)", fill="#222", font=("Segoe UI", 11), justify="center")

        # Live readout
        self._xy_readout_id = c.create_text(
            self._plot_left + 8, self._plot_top + 8,
            text="X=0.000 mm, Y=0.000 mm",
            fill="#111", font=("Consolas", 10), anchor="nw"
        )

        # Crosshairs + dot
        self._xy_vline_id = c.create_line(0, 0, 0, 0, fill="#cc0000")
        self._xy_hline_id = c.create_line(0, 0, 0, 0, fill="#cc0000")
        self._xy_dot_id = c.create_oval(0, 0, 0, 0, fill="red", outline="")

        # Draw at current values (don’t force to 0)
        try:
            x0 = float(self.var_x.get())
            y0 = float(self.var_y.get())
        except Exception:
            x0, y0 = 0.0, 0.0
        self._update_xy_dot(x0, y0)

        # Resize redraw (throttled)
        c.bind("<Configure>", self._on_xy_canvas_resize)

    def _on_xy_canvas_resize(self, _event):
        if self._xy_resize_after is not None:
            try:
                self.root.after_cancel(self._xy_resize_after)
            except Exception:
                pass
        self._xy_resize_after = self.root.after(80, self._draw_xy_canvas_grid)

    def _mm_to_canvas_x(self, x_mm: float) -> float:
        rng = float(self._canvas_range_mm)
        x_mm = max(-rng, min(rng, x_mm))
        return self._plot_left + (x_mm + rng) / (2 * rng) * self._plot_w

    def _mm_to_canvas_y(self, y_mm: float) -> float:
        rng = float(self._canvas_range_mm)
        y_mm = max(-rng, min(rng, y_mm))
        # invert: +Y up
        return self._plot_top + (rng - y_mm) / (2 * rng) * self._plot_h

    def _update_xy_dot(self, x_mm: float, y_mm: float):
        if self._xy_readout_id is not None:
            self.xy_canvas.itemconfigure(self._xy_readout_id, text=f"X={x_mm:.3f} mm, Y={y_mm:.3f} mm")

        cx = self._mm_to_canvas_x(x_mm)
        cy = self._mm_to_canvas_y(y_mm)

        if self._xy_vline_id is not None:
            self.xy_canvas.coords(self._xy_vline_id, cx, self._plot_top, cx, self._plot_bot)
        if self._xy_hline_id is not None:
            self.xy_canvas.coords(self._xy_hline_id, self._plot_left, cy, self._plot_right, cy)

        if self._xy_dot_id is not None:
            r = 6
            self.xy_canvas.coords(self._xy_dot_id, cx - r, cy - r, cx + r, cy + r)

    # -------------------------- E-STOP / shutdown --------------------------

    def _on_estop(self):
        self._on_routine_stop()
        self.q_gui_to_gantry.put({"type": "btn_estop"})
        self._cancel_pump_timer()
        self._send_pump(0)
        self._post_msg("E-STOP sent.")

    def on_close(self):
        try:
            self._on_routine_stop()
            for jr in self._jog.values():
                jr.stop(self.root)
        except Exception:
            pass

        try:
            if self._poll_after_id is not None:
                self.root.after_cancel(self._poll_after_id)
        except Exception:
            pass

        try:
            self._cancel_pump_timer()
        except Exception:
            pass

        try:
            self.p_gantry.terminate()
            self.p_gantry.join(timeout=1.0)
        except Exception:
            pass

        self.root.destroy()


def main():
    mp.freeze_support()
    try:
        mp.set_start_method("spawn")
    except RuntimeError:
        pass

    parser = argparse.ArgumentParser(description="Tkinter GUI-controlled Core-XY Gantry")
    parser.add_argument("--simulate", action="store_true", help="Run with simulator backend")
    args = parser.parse_args()

    root = tk.Tk()
    try:
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass

    StageTk(root, simulate=args.simulate)
    root.mainloop()


if __name__ == "__main__":
    main()
