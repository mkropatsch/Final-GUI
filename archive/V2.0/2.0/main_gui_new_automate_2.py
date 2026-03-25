# main_gui_new_automate.py
# ---------------------------------------------------------------------
# GUI Controller for Core-XY Gantry
# - LEFT: XY plot (relative to Work Home)
# - RIGHT TOP: State
# - RIGHT MID (left col): Step   Speed  (+ "Move XY (steps)")
# - RIGHT MID (right col): Manual Control (direction pad + Z) — QTimer jogs @50ms
# - RIGHT BOTTOM: Pump Control (FAN0)
# - Messages panel
# - Work Home (user XY origin), Machine Home (G28), E-STOP
#
# NEW:
#   1) Grid Routine (serpentine):
#        • Inputs: Cols × Rows, ΔX steps, ΔY steps, Dwell (s)
#        • Start/Stop buttons
#        • Cancels automatically if you use manual controls (safety & responsiveness)
#   2) Manual responsiveness preserved (same 50ms jog timers)
#
# Run:  python main_gui_new_automate.py [--simulate]
# Deps: PyQt5, pyqtgraph  (optional: qdarkstyle)
# ---------------------------------------------------------------------

from __future__ import annotations
import sys, multiprocessing as mp
from typing import Dict, Optional, Iterator, Tuple

from PyQt5.QtCore import Qt, QTimer, QDateTime
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGridLayout, QGroupBox, QFormLayout,
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QComboBox,
    QSlider, QMessageBox, QSpinBox, QCheckBox
)
import pyqtgraph as pg

# #### Light mode ###
# import pyqtgraph as pg

# pg.setConfigOption('background', (255, 255, 255))
# pg.setConfigOption('foreground', (0, 0, 0))
# ### END light mode ###
 
# --------------------------- child (gantry) entry ----------------------------
def gantry_process_main(q_to_gui, q_from_gui, q_from_controller, simulate: bool) -> None:
    # IMPORTANT: use the backend you shipped (drains controller inputs)
    from gantry_gui_new_automate_2 import GantrySystem
    g = GantrySystem(
        q_to_gui=q_to_gui,
        q_from_gui=q_from_gui,
        q_from_controller=q_from_controller,
        simulate=simulate,
    )
    g.run()


# ---------------------------------- GUI --------------------------------------
class StageGUI(QMainWindow):
    def __init__(self, simulate: bool = True):
        super().__init__()
        self.setWindowTitle("Core-XY Gantry — GUI Controller")
        self.resize(1320, 900)

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

        # =========================== LAYOUT ===========================
        root = QWidget(self); self.setCentralWidget(root)
        grid = QGridLayout(root)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 3)

        # ---- Left: XY plot
        self.xy_group = QGroupBox("XY Position (relative to Work Home 0,0)")
        vxy = QVBoxLayout(self.xy_group)
        self.xy_plot = pg.PlotWidget()
        self.xy_plot.setAspectLocked(True)
        #self.xy_plot.showGrid(x=True, y=True, alpha=0.25)
        self.xy_plot.setLabel("left", "Y_rel (mm)")
        self.xy_plot.setLabel("bottom", "X_rel (mm)")
        self.xy_plot.invertY(True)  # screen +Y up
        self._xy_point = self.xy_plot.plot([0], [0], pen=None, symbol="o", symbolSize=10)
        vxy.addWidget(self.xy_plot)
        grid.addWidget(self.xy_group, 0, 0, 3, 1)
        
        ### New thing (light mode)
        
        #self.xy_plot.setBackground('w')
        self.xy_plot.showGrid(x=True, y=True, alpha=0.5)

        self.xy_plot.getAxis('left').setPen('k')
        self.xy_plot.getAxis('bottom').setPen('k')
        ### End light mode
        
        # ---- Right column container (two columns stacked)
        right_grid = QGridLayout()
        grid.addLayout(right_grid, 0, 1, 3, 1)

        # ---- State
        self.state_group = QGroupBox("State")
        st = QFormLayout(self.state_group)
        self.lab_x = QLabel("0.000"); self.lab_y = QLabel("0.000")
        self.lab_z = QLabel("0.000"); self.lab_e = QLabel("0.000")
        for lab in (self.lab_x, self.lab_y, self.lab_z, self.lab_e):
            lab.setAlignment(Qt.AlignRight)
        st.addRow("X_rel", self.lab_x)
        st.addRow("Y_rel", self.lab_y)
        st.addRow("Z (abs)", self.lab_z)
       # st.addRow("E (abs)", self.lab_e)
        right_grid.addWidget(self.state_group, 0, 0, 1, 2)

        # ---- Step   Speed (kept as before; NEW row added at the end)
        self.ctrl_group = QGroupBox("Step   Speed")
        form = QFormLayout(self.ctrl_group)

        row_presets = QHBoxLayout()
        self.cmb_preset = QComboBox()
        self.cmb_preset.addItems(["Fine", "Medium", "Coarse"])
        row_presets.addWidget(QLabel("Preset"))
        row_presets.addWidget(self.cmb_preset)
        row_presets.addStretch()

        self.in_xy = QLineEdit("0.200")
        self.in_z  = QLineEdit("0.050")
        self.in_e  = QLineEdit("0.020")

        self.sld_feed = QSlider(Qt.Horizontal); self.sld_feed.setRange(100, 12000)
        self.sld_feed.setValue(3000)
        self.lab_feed = QLabel("3000 mm/min"); self.lab_feed.setAlignment(Qt.AlignRight)
        row_feed = QHBoxLayout()
        row_feed.addWidget(self.sld_feed, 1); row_feed.addWidget(self.lab_feed)

        row_act = QHBoxLayout()
        self.btn_apply   = QPushButton("Apply step sizes")
        self.btn_sethome = QPushButton("Set Home (XY → 0,0 here)")
        self.btn_gohome  = QPushButton("Go to Home (XY)")
        self.btn_home    = QPushButton("Machine Home (G28)")
        self.btn_estop   = QPushButton("E-STOP")
        self.btn_estop.setStyleSheet("QPushButton{background:#b51f1f;color:white;font-weight:bold}")
        row_act.addWidget(self.btn_apply)
        row_act.addStretch()
        row_act.addWidget(self.btn_sethome)
        row_act.addWidget(self.btn_gohome)
        row_act.addWidget(self.btn_home)
        row_act.addWidget(self.btn_estop)

        # --- NEW: Move XY by N steps (uses current XY step size)
        row_move = QHBoxLayout()
        self.in_steps_x = QLineEdit("0"); self.in_steps_y = QLineEdit("0")
        for w in (self.in_steps_x, self.in_steps_y):
            w.setFixedWidth(80); w.setAlignment(Qt.AlignRight)
        self.btn_move_steps = QPushButton("Move XY (steps)")
        row_move.addWidget(QLabel("ΔX steps")); row_move.addWidget(self.in_steps_x)
        row_move.addWidget(QLabel("ΔY steps")); row_move.addWidget(self.in_steps_y)
        row_move.addStretch(); row_move.addWidget(self.btn_move_steps)
        
        # --- NEW: Move Z by N steps (uses current Z step size)
        row_move_z = QHBoxLayout()
        self.in_steps_z = QLineEdit("0")
        self.in_steps_z.setFixedWidth(80)
        self.in_steps_z.setAlignment(Qt.AlignRight)
        self.btn_move_z_steps = QPushButton("Move Z (steps)")
        row_move_z.addWidget(QLabel("ΔZ steps"))
        row_move_z.addWidget(self.in_steps_z)
        row_move_z.addStretch()
        row_move_z.addWidget(self.btn_move_z_steps)

        # Add rows to form (order matches your original)
        form.addRow(row_presets)
        form.addRow("XY step (mm)", self.in_xy)
        form.addRow("Z step (mm)", self.in_z)
        #form.addRow("E step (mm)", self.in_e)
        form.addRow("Feed (mm/min)", row_feed)
        form.addRow(row_act)
        form.addRow(row_move)  # <--- existing addition
        form.addRow(row_act)
        form.addRow(row_move) # Move XY (steps)
        form.addRow(row_move_z) # Move Z steps

        right_grid.addWidget(self.ctrl_group, 1, 0, 1, 1)

        # ---- Manual Control panel — QTimer-driven jogs @ 50ms
        self.manual_group = QGroupBox("Manual Control")
        mc = QGridLayout(self.manual_group)

        def mk(btn_text: str) -> QPushButton:
            b = QPushButton(btn_text)
            b.setFixedWidth(60); b.setFixedHeight(36)
            return b

        # Top row: ↖  ↑  ↗  Z↑
        self.btn_ul = mk("↖"); self.btn_up = mk("↑"); self.btn_ur = mk("↗")
        self.btn_zp = mk("Z↑")
        # Mid row: ←  •  →
        self.btn_lf = mk("←"); self.btn_c = mk("•"); self.btn_rt = mk("→")
        # Bottom row: ↙  ↓  ↘  Z↓
        self.btn_dl = mk("↙"); self.btn_dn = mk("↓"); self.btn_dr = mk("↘")
        self.btn_zm = mk("Z↓")

        mc.addWidget(self.btn_ul, 0, 0); mc.addWidget(self.btn_up, 0, 1); mc.addWidget(self.btn_ur, 0, 2)
        mc.addWidget(self.btn_zp, 0, 3)
        mc.addWidget(self.btn_lf, 1, 0); mc.addWidget(self.btn_c, 1, 1); mc.addWidget(self.btn_rt, 1, 2)
        mc.addWidget(self.btn_dl, 2, 0); mc.addWidget(self.btn_dn, 2, 1); mc.addWidget(self.btn_dr, 2, 2)
        mc.addWidget(self.btn_zm, 2, 3)

        right_grid.addWidget(self.manual_group, 1, 1, 1, 1)

        # ---- Pump Control (FAN0)
        self.pump_group = QGroupBox("Pump Control (FAN0 → M106/M107)")
        fp = QFormLayout(self.pump_group)
        self.sld_pump = QSlider(Qt.Horizontal); self.sld_pump.setRange(0, 255); self.sld_pump.setValue(0)
        self.lab_pump = QLabel("0 / 255  (0%)")
        row_pd = QHBoxLayout(); row_pd.addWidget(self.sld_pump, 1); row_pd.addWidget(self.lab_pump)

        self.spn_secs = QSpinBox(); self.spn_secs.setRange(1, 999); self.spn_secs.setValue(60)
        row_pb = QHBoxLayout()
        self.btn_p_on = QPushButton("Pump ON"); self.btn_p_off = QPushButton("Pump OFF"); self.btn_p_run = QPushButton("Run Timed")
        row_pb.addWidget(QLabel("Seconds:")); row_pb.addWidget(self.spn_secs)
        row_pb.addStretch()
        row_pb.addWidget(self.btn_p_on); row_pb.addWidget(self.btn_p_off); row_pb.addWidget(self.btn_p_run)

        fp.addRow("Duty (0..255)", row_pd)
        fp.addRow(row_pb)
        right_grid.addWidget(self.pump_group, 2, 1, 1, 1)

        # ---- Messages
        self.msg_group = QGroupBox("Messages")
        vmsg = QVBoxLayout(self.msg_group)
        self.lab_msg = QLabel("Ready."); self.lab_msg.setWordWrap(True)
        vmsg.addWidget(self.lab_msg)
        right_grid.addWidget(self.msg_group, 2, 0, 1, 1)

        # ---- NEW: Grid Routine (automation)
        self.routine_group = QGroupBox("Grid Automation Routine")
        rg = QFormLayout(self.routine_group)
        row_grid = QHBoxLayout()
        self.spn_cols = QSpinBox(); self.spn_cols.setRange(1, 100); self.spn_cols.setValue(4)
        self.spn_rows = QSpinBox(); self.spn_rows.setRange(1, 100); self.spn_rows.setValue(3)
        row_grid.addWidget(QLabel("Cols")); row_grid.addWidget(self.spn_cols)
        row_grid.addSpacing(12)
        row_grid.addWidget(QLabel("Rows")); row_grid.addWidget(self.spn_rows)

        row_spacing = QHBoxLayout()
        self.in_dx_steps = QLineEdit("210")
        self.in_dy_steps = QLineEdit("220")
        self.in_dz_steps = QLineEdit("0")
        for w in (self.in_dx_steps, self.in_dy_steps, self.in_dz_steps):
            w.setFixedWidth(80); w.setAlignment(Qt.AlignRight)
        row_spacing.addWidget(QLabel("ΔX steps")); row_spacing.addWidget(self.in_dx_steps)
        row_spacing.addWidget(QLabel("ΔY steps")); row_spacing.addWidget(self.in_dy_steps)
        row_spacing.addWidget(QLabel("ΔZ steps")); row_spacing.addWidget(self.in_dz_steps)

        row_dwell = QHBoxLayout()
        self.spn_dwell = QSpinBox(); self.spn_dwell.setRange(0, 3600); self.spn_dwell.setValue(1)
        self.chk_serp = QCheckBox("Serpentine"); self.chk_serp.setChecked(True)
        row_dwell.addWidget(QLabel("Wait (s)")); row_dwell.addWidget(self.spn_dwell)
        row_dwell.addStretch(); row_dwell.addWidget(self.chk_serp)

        row_run = QHBoxLayout()
        self.btn_r_start = QPushButton("Start Routine")
        self.btn_r_stop  = QPushButton("Stop Routine")
        row_run.addWidget(self.btn_r_start); row_run.addWidget(self.btn_r_stop); row_run.addStretch()

        rg.addRow(row_grid)
        rg.addRow(row_spacing)
        rg.addRow(row_dwell)
        rg.addRow(row_run)
        # place under Messages to keep your right column tidy
        right_grid.addWidget(self.routine_group, 3, 0, 1, 2)

        # =========================== TIMERS/STATE ===========================
        self._poll = QTimer(self); self._poll.setInterval(50)
        self._poll.timeout.connect(self._drain_gantry_messages)
        self._poll.start()

        self._pump_timer = QTimer(self); self._pump_timer.setSingleShot(True)

        # Manual jog timers (50 ms) — start on pressed, stop on released
        self._mk_jog_timers()

        # Routine timer
        self._routine_timer = QTimer(self); self._routine_timer.setSingleShot(True)
        self._routine_iter: Optional[Iterator[Tuple[int, int]]] = None
        self._routine_active = False
        self._routine_waiting = False  # true only during dwell
        
        # New: Z-routine parameters and phase
        self._routine_nz: int=0  # ΔZ in "steps"
        self._routine_dwell: int = 0 # dwell at Z-down (seconds)
        self._routine_phase: str = "idle"   # "z_down" -> "z_wait" -> "z_up" -> "xy_move"

        self._home_abs: Optional[Dict[str, float]] = None
        self._last_abs = {"x": 0.0, "y": 0.0, "z": 0.0, "e": 0.0}

        # =========================== WIRES ===========================
        # Presets & step/feed
        self.cmb_preset.currentTextChanged.connect(self._apply_preset_fields)
        self.sld_feed.valueChanged.connect(lambda v: self.lab_feed.setText(f"{v} mm/min"))
        self.btn_apply.clicked.connect(self._apply_steps_to_gantry)
        self.sld_feed.sliderReleased.connect(self._apply_feed_to_gantry)

        # Home / E-STOP
        self.btn_estop.clicked.connect(self._on_estop)
        self.btn_home.clicked.connect(lambda: self.q_gui_to_gantry.put({"type": "home_all"}))
        self.btn_sethome.clicked.connect(self._set_home_xy_origin)
        self.btn_gohome.clicked.connect(self._goto_home_xy)

        # Move XY (steps)
        self.btn_move_steps.clicked.connect(self._on_move_steps_xy)
        # Move Z (steps)
        self.btn_move_z_steps.clicked.connect(self._on_move_z_steps)

        # Pump
        self.sld_pump.valueChanged.connect(self._on_pump_duty_change)
        self._pump_timer.timeout.connect(self._on_pump_off)
        self.btn_p_on.clicked.connect(self._on_pump_on)
        self.btn_p_off.clicked.connect(self._on_pump_off)
        self.btn_p_run.clicked.connect(self._on_pump_run)

        # Routine wires
        self.btn_r_start.clicked.connect(self._on_routine_start)
        self.btn_r_stop.clicked.connect(self._on_routine_stop)
        self._routine_timer.timeout.connect(self._routine_tick)

        # # Optional dark style
        # try:
        #     import qdarkstyle
        #     self.setStyleSheet(qdarkstyle.load_stylesheet_pyqt5())
        # except Exception:
        #     pass

        self._post_msg("Gantry started.")

    # =========================== JOG TIMERS ===========================
    def _mk_jog_timers(self):
        """Create 50ms timers for each manual control button."""
        self._t = {}  # name -> QTimer

        def mk_timer(name, fn):
            t = QTimer(self); t.setInterval(50); t.timeout.connect(fn); self._t[name] = t
            return t

        # XY
        mk_timer("up",    lambda: self._jog_xy(0, +1))
        mk_timer("dn",    lambda: self._jog_xy(0, -1))
        mk_timer("lf",    lambda: self._jog_xy(-1, 0))
        mk_timer("rt",    lambda: self._jog_xy(+1, 0))
        mk_timer("ul",    lambda: self._jog_xy(-1, +1))
        mk_timer("ur",    lambda: self._jog_xy(+1, +1))
        mk_timer("dl",    lambda: self._jog_xy(-1, -1))
        mk_timer("dr",    lambda: self._jog_xy(+1, -1))
        # Z
        mk_timer("zp",    lambda: self._jog_z(+1))
        mk_timer("zm",    lambda: self._jog_z(-1))

        # Connect press/release
        self.btn_up.pressed.connect(self._t["up"].start);  self.btn_up.released.connect(self._t["up"].stop)
        self.btn_dn.pressed.connect(self._t["dn"].start);  self.btn_dn.released.connect(self._t["dn"].stop)
        self.btn_lf.pressed.connect(self._t["lf"].start);  self.btn_lf.released.connect(self._t["lf"].stop)
        self.btn_rt.pressed.connect(self._t["rt"].start);  self.btn_rt.released.connect(self._t["rt"].stop)
        self.btn_ul.pressed.connect(self._t["ul"].start);  self.btn_ul.released.connect(self._t["ul"].stop)
        self.btn_ur.pressed.connect(self._t["ur"].start);  self.btn_ur.released.connect(self._t["ur"].stop)
        self.btn_dl.pressed.connect(self._t["dl"].start);  self.btn_dl.released.connect(self._t["dl"].stop)
        self.btn_dr.pressed.connect(self._t["dr"].start);  self.btn_dr.released.connect(self._t["dr"].stop)

        self.btn_zp.pressed.connect(self._t["zp"].start);  self.btn_zp.released.connect(self._t["zp"].stop)
        self.btn_zm.pressed.connect(self._t["zm"].start);  self.btn_zm.released.connect(self._t["zm"].stop)

    # =========================== HELPERS / ACTIONS ===========================
    def _post_msg(self, text: str, level: str = "info"):
        stamp = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        self.lab_msg.setText(f"[{stamp}] {text}")

    # Presets just fill the fields (no send)
    def _apply_preset_fields(self, name: str):
        if name == "Fine":
            self.in_xy.setText("0.010"); self.in_z.setText("0.005"); self.in_e.setText("0.005")
        elif name == "Medium":
            self.in_xy.setText("0.200"); self.in_z.setText("0.050"); self.in_e.setText("0.020")
        else:
            self.in_xy.setText("1.000"); self.in_z.setText("0.200"); self.in_e.setText("0.100")

    def _apply_steps_to_gantry(self):
        try:
            xy = float(self.in_xy.text()); z = float(self.in_z.text()); e = float(self.in_e.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid", "Enter numeric step sizes.")
            return
        self.q_gui_to_gantry.put({"type": "set_steps", "xy_step": xy, "z_step": z, "e_step": e})

    def _apply_feed_to_gantry(self):
        self.q_gui_to_gantry.put({"type": "set_feed", "feed_mm_min": int(self.sld_feed.value())})

    # Work Home
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

    # Move XY by N steps (relative)
    def _on_move_steps_xy(self):
        try:
            nx = int(float(self.in_steps_x.text() or "0"))
            ny = int(float(self.in_steps_y.text() or "0"))
        except ValueError:
            QMessageBox.warning(self, "Invalid", "Enter integer step counts for X and Y.")
            return
        self.q_gui_to_gantry.put({"type": "gantry_cmd", "cmd": "move_steps_xy", "nx": nx, "ny": ny})
        self._post_msg(f"Queued XY move: ΔX={nx}×step, ΔY={ny}×step")
        
    def _get_z_step_mm(self) -> float:
        """Return current Z step size in mm, using the field synced from backend."""
        try:
            return float(self.in_z.text())
        except ValueError:
            return 0.0
        
    def _on_move_z_steps(self):
        """Move Z by N 'steps' using the current Z step size (mm/step)."""
        try:
            nz = int(float(self.in_steps_z.text() or "0"))
        except ValueError:
            QMessageBox.warning(self, "Invalid." "Enter integer step count for Z.")
            return
        z_step_mm = self._get_z_step_mm()
        dz_mm = nz * z_step_mm
        
        if dz_mm == 0.0:
            self._post_msg("Z move: no motion (ΔZ=0).")
            
        # Use relative move on Z only; backend already supports 'move_rel' with dz
        self.q_gui_to_gantry.put({
            "type": "gantry_cmd",
            "cmd": "move_rel",
            "dz": dz_mm
        })
        
        self._post_msg(f"Queued Z move: ΔZ={nz}xstep ({dz_mm: .4f} mm)")
    # Manual jog helpers (unit: "one GUI step" → backend multiplies by step sizes)
    def _routine_stop_if_active(self):
        if self._routine_active:
            self._on_routine_stop()
            self._post_msg("Routine stopped due to manual input.")

    def _jog_xy(self, sx: int, sy: int):
        self._routine_stop_if_active()
        self.q_ctrl_to_gantry.put({"type": "input", "cmd": "xy_motion", "value": (sx, sy)})

    def _jog_z(self, s: int):
        self._routine_stop_if_active()
        self.q_ctrl_to_gantry.put({"type": "input", "cmd": "z_motion", "value": (0, s)})

    # Pump
    def _on_pump_duty_change(self, v: int):
        pct = int(round(100 * v / 255.0))
        self.lab_pump.setText(f"{v} / 255  ({pct}%)")

    def _send_gcode(self, cmd: str):
        self.q_gui_to_gantry.put({"type": "gcode", "cmd": cmd})

    def _send_pump(self, duty_0_255: int):
        d = max(0, min(255, int(duty_0_255)))
        if d <= 0:
            self._send_gcode("M107 P0")
        else:
            self._send_gcode(f"M106 P0 S{d}")
        self.q_gui_to_gantry.put({"type": "fan_set", "index": 0, "value": d})

    def _on_pump_on(self):
        self._pump_timer.stop()
        self._send_pump(int(self.sld_pump.value()))
        self._post_msg(f"Pump ON at {int(self.sld_pump.value())}/255.")

    def _on_pump_off(self):
        self._pump_timer.stop()
        self._send_pump(0)
        self._post_msg("Pump OFF.")

    def _on_pump_run(self):
        duty = int(self.sld_pump.value())
        secs = int(self.spn_secs.value())
        if duty <= 0:
            QMessageBox.information(self, "Pump", "Set duty > 0 to run the pump.")
            return
        self._send_pump(duty)
        self._pump_timer.start(secs * 1000)
        self._post_msg(f"Pump RUN {secs}s at {duty}/255…")

    # Gantry → GUI
    def _drain_gantry_messages(self):
        while not self.q_gantry_to_gui.empty():
            msg = self.q_gantry_to_gui.get()
            if not isinstance(msg, dict):
                continue
            typ = msg.get("type")
            if typ == "state":
                self._apply_state(msg)
            elif typ == "message":
                stamp = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
                self.lab_msg.setText(f"[{stamp}] {msg.get('text','')}")

    def _apply_state(self, s: Dict):
        ax = float(s.get("x", 0.0)); ay = float(s.get("y", 0.0))
        az = float(s.get("z", 0.0)); ae = float(s.get("e", 0.0))
        self._last_abs.update({"x": ax, "y": ay, "z": az, "e": ae})

        if self._home_abs:
            xr = ax - self._home_abs["x"]
            yr = ay - self._home_abs["y"]
        else:
            xr, yr = ax, ay

        self._xy_point.setData([xr], [yr])
        self.lab_x.setText(f"{xr:.3f}")
        self.lab_y.setText(f"{yr:.3f}")
        self.lab_z.setText(f"{az:.3f}")
        self.lab_e.setText(f"{ae:.3f}")

        if "xy_step" in s: self.in_xy.setText(f"{float(s['xy_step']):.3f}")
        if "z_step"  in s: self.in_z.setText(f"{float(s['z_step']):.3f}")
       # if "e_step"  in s: self.in_e.setText(f"{float(s['e_step']):.3f}")
        if "feed"    in s:
            val = int(s["feed"])
            self.sld_feed.setValue(val); self.lab_feed.setText(f"{val} mm/min")

    # E-STOP
    def _on_estop(self):
        self._on_routine_stop()
        self.q_gui_to_gantry.put({"type": "btn_estop"})
        self._pump_timer.stop()
        self._send_pump(0)
        self._post_msg("E-STOP sent.")

    # ---------- Routine (grid serpentine) ----------
    def _grid_iter(self, cols: int, rows: int, nx: int, ny: int, serp: bool) -> Iterator[Tuple[int, int]]:
        """Yield (nx, ny) step moves to visit a cols×rows grid from top-left."""
        if cols <= 0 or rows <= 0:
            return
        for r in range(rows):
            # Horizontal direction for this row
            forward = (r % 2 == 0) or (not serp)
            if forward:
                # 0 .. cols-1  (we're already at col 0)
                for c in range(1, cols):
                    yield (nx, 0)
            else:
                # cols-1 .. 1 backward
                for c in range(1, cols):
                    yield (-nx, 0)
            # Move down to next row (except after last row)
            if r < rows - 1:
                yield (0, ny)  # vertical step between rows

    def _on_routine_start(self):
        # Read inputs
        try:
            cols = int(self.spn_cols.value())
            rows = int(self.spn_rows.value())
            nx = int(float(self.in_dx_steps.text()))
            ny = int(float(self.in_dy_steps.text()))
            nz = int(float(self.in_dz_steps.text() or "0")) # New: Z steps
            dwell_s = int(self.spn_dwell.value())
        except ValueError:
            QMessageBox.warning(self, "Routine", "Please enter valid integers for steps and dwell.")
            return

        if cols <= 0 or rows <= 0:
            QMessageBox.warning(self, "Routine", "Grid must be at least 1×1.")
            return

        # Build iterator once; routine tick consumes it
        self._routine_iter = iter(self._grid_iter(cols, rows, nx, ny, self.chk_serp.isChecked()))
        
        # Store Z routine settings
        self._routine_nz = nz
        self._routine_dwell = dwell_s
        
        
        self._routine_active = True
        self._routine_waiting = False
        self._routine_phase = "z_down" # Start by doing Z cycle at the current cell
        
        
        self._post_msg(
            f"Routine started: {cols}×{rows}, ΔX={nx} steps, ΔY={ny} steps, "
            f"ΔZ={nz} steps, dwell={dwell_s}s."
        )
        
        # kick the first action immediately
        self._routine_timer.start(0)

    def _on_routine_stop(self):
        self._routine_active = False
        self._routine_waiting = False
        self._routine_iter = None
        self._routine_timer.stop()
        self._routine_phase = "idle"

    def _routine_issue_next_move(self):
        """Issue the next XY move (no dwell) in the grid iterator."""
        if not self._routine_active or self._routine_iter is None:
            self._post_msg("Routine complete.")
            return
        try:
            nx, ny = next(self._routine_iter)
        except StopIteration:
            self._routine_active = False
            self._routine_waiting = False
            self._routine_iter = None
            self._post_msg("Routine complete.")
            return

        # send movement as "move_steps_xy" so backend converts steps→mm via current XY step size
        self.q_gui_to_gantry.put({
            "type": "gantry_cmd", 
            "cmd": "move_steps_xy", 
            "nx": nx, 
            "ny": ny
        })
        
    def _routine_tick(self):
        # After dwell or short delay, push next move if still active
        if not self._routine_active:
            return
        
        nz = self._routine_nz
        dwell_s = self._routine_dwell
        z_step_mm = self._get_z_step_mm()
        
        # If ΔZ is zero or z_step_mm is zero, just fall back to XY-only behavior
        if nz == 0 or z_step_mm == 0.0 or dwell_s < 0:
            self._routine_phase = "idle"
            self._routine_waiting = False
            try:
                nx, ny = next(self._routine_iter)
            except (StopIteration, TypeError):
                self.routine_active = False
                self._post_msg("Routine complete.")
                return
            self.q_gui_to_gantry.put({
                "type": "gantry_cmd",
                "cmd": "move_steps_xy",
                "nx": nx,
                "ny": ny
            })
            if dwell_s > 0:
                self._routine_timer.start(dwell_s * 1000)
            else:
                self._routine_timer.start(50)
            return
        # Z-cycle + XY state machine
        if self._routine_phase == "z_down":
            # Move Z *down* by nz steps (sign choice: here negative is "down")
            dz_mm = -nz * z_step_mm
            if dz_mm != 0.0:
                self.q_gui_to_gantry.put({
                    "type": "gantry_cmd",
                    "cmd": "move_rel",
                    "dz": dz_mm
                })
            # After going down, wait at that depth
            self._routine_phase = "z_wait"
            if dwell_s > 0:
                self._routine_timer.start(dwell_s * 1000)
            else:
                # no dwell, go straight to z_up on next tick
                self._routine_timer.start(50)
        elif self._routine_phase == "z_wait":
            # Done waiting, move Z *up* by same amount
            dz_mm = nz * z_step_mm
            if dz_mm != 0.0:
                self.q_gui_to_gantry.put({
                    "type": "gantry_cmd",
                    "cmd": "move_rel",
                    "dz": dz_mm
                })
            # After raising, Z, we will move XY to next cell
            self._routine_phase = "xy_move"
            self._routine_timer.start(50) # small delay before XY move
            
        elif self._routine_phase == "xy_move":
            # Move to next XY cell in grid
            self._routine_issue_next_move()
            if not self._routine_active:
                # done, no more cells
                self._routine_phase = "idle"
                return
            # At the new cell, start another Z-cycle
            self._routine_phase = "z_down"
            self._routine_timer.start(50)
            
        else:
            # Unknown or idle phase -> stop
            self._routine_active = False
            self._routine_phase = "idle"
            self._post_msg("Routine stopped (invalid phase).")

    # Shutdown
    def closeEvent(self, ev):
        try:
            self.p_gantry.terminate()
            self.p_gantry.join(timeout=1.0)
        except Exception:
            pass
        ev.accept()


# ----------------------------------- entry -----------------------------------
def main():
    mp.freeze_support()
    try:
        mp.set_start_method("spawn")
    except RuntimeError:
        pass

    import argparse
    parser = argparse.ArgumentParser(description="GUI-controlled Core-XY Gantry")
    parser.add_argument("--simulate", action="store_true", help="Run with simulator backend")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    
    ### LIGHT MODE ###
    
    app.setStyleSheet("""
    QWidget {
        font-size: 12pt;
        font-weight: 500;
    }

    QGroupBox {
        font-size: 13pt;
        font-weight: bold;
    }

    QPushButton {
        font-size: 12pt;
        font-weight: bold;
    }

    QLabel {
        font-size: 12pt;
    }
    """)
    
    from PyQt5.QtGui import QPalette, QColor
    from PyQt5.QtCore import Qt

    app.setStyle("Fusion")

    # light_palette = QPalette()

    # light_palette.setColor(QPalette.Window, QColor(255, 255, 255))
    # light_palette.setColor(QPalette.WindowText, Qt.black)
    # light_palette.setColor(QPalette.Base, QColor(255, 255, 255))
    # light_palette.setColor(QPalette.AlternateBase, QColor(245, 245, 245))
    # light_palette.setColor(QPalette.ToolTipBase, Qt.white)
    # light_palette.setColor(QPalette.ToolTipText, Qt.black)
    # light_palette.setColor(QPalette.Text, Qt.black)
    # light_palette.setColor(QPalette.Button, QColor(240, 240, 240))
    # light_palette.setColor(QPalette.ButtonText, Qt.black)
    # light_palette.setColor(QPalette.BrightText, Qt.red)
    # light_palette.setColor(QPalette.Highlight, QColor(0, 120, 215))
    # light_palette.setColor(QPalette.HighlightedText, Qt.white)

    # app.setPalette(light_palette)
    ##### END Light mode ###
    win = StageGUI(simulate=args.simulate)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
