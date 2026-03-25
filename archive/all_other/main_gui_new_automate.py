# main_gui_control_new.py
# ---------------------------------------------------------------------
# GUI Controller for Core-XY Gantry
# - LEFT: XY plot (relative to Work Home)
# - RIGHT TOP: State
# - RIGHT MID (left column): Step   Speed  (+ NEW: "Move XY (steps)")
# - RIGHT MID (right column): Manual Control (direction pad + Z/E) — now driven by 50ms QTimers
# - RIGHT BOTTOM: Pump Control (FAN0)
# - Messages panel
# - Work Home (user XY origin), Machine Home (G28), E-STOP
#
# NEW FEATURE:
#   ΔX steps / ΔY steps + "Move XY (steps)" button
#   → moves by (steps * current XY step size) in X/Y (relative).
#
# Robust Manual Control:
#   Buttons use press/release-driven QTimers @ 50ms so jogging is consistent on all OS/themes.
#
# Run:  python main_gui_control_new.py [--simulate]
# Deps: PyQt5, pyqtgraph  (optional: qdarkstyle)
# ---------------------------------------------------------------------

from __future__ import annotations
import sys, multiprocessing as mp
from typing import Dict, Optional

from PyQt5.QtCore import Qt, QTimer, QDateTime
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGridLayout, QGroupBox, QFormLayout,
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QComboBox,
    QSlider, QMessageBox, QSpinBox
)
import pyqtgraph as pg


# --------------------------- child (gantry) entry ----------------------------
def gantry_process_main(q_to_gui, q_from_gui, q_from_controller, simulate: bool) -> None:
    # IMPORTANT: use the main backend that drains controller inputs
    from gantry_gui_new_automate import GantrySystem
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
        self.resize(1320, 840)

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
        self.xy_plot.showGrid(x=True, y=True, alpha=0.25)
        self.xy_plot.setLabel("left", "Y_rel (mm)")
        self.xy_plot.setLabel("bottom", "X_rel (mm)")
        self.xy_plot.invertY(True)  # screen +Y up
        self._xy_point = self.xy_plot.plot([0], [0], pen=None, symbol="o", symbolSize=10)
        vxy.addWidget(self.xy_plot)
        grid.addWidget(self.xy_group, 0, 0, 3, 1)

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

        # Add rows to form (order matches your original)
        form.addRow(row_presets)
        form.addRow("XY step (mm)", self.in_xy)
        form.addRow("Z step (mm)", self.in_z)
        #form.addRow("E step (mm)", self.in_e)
        form.addRow("Feed (mm/min)", row_feed)
        form.addRow(row_act)
        form.addRow(row_move)  # <--- the only visual addition

        right_grid.addWidget(self.ctrl_group, 1, 0, 1, 1)

        # ---- Manual Control panel (restored) — QTimer-driven jogs
        self.manual_group = QGroupBox("Manual Control")
        mc = QGridLayout(self.manual_group)

        def mk(btn_text: str) -> QPushButton:
            b = QPushButton(btn_text)
            b.setFixedWidth(60); b.setFixedHeight(36)
            return b

        # Top row: ↖  ↑  ↗  Z↑  E+
        self.btn_ul = mk("↖"); self.btn_up = mk("↑"); self.btn_ur = mk("↗")
        self.btn_zp = mk("Z↑")#; self.btn_ep = mk("E+")
        # Mid row: ←  •  →  (spacers)
        self.btn_lf = mk("←"); self.btn_c = mk("•"); self.btn_rt = mk("→")
        # Bottom row: ↙  ↓  ↘  Z↓  E−
        self.btn_dl = mk("↙"); self.btn_dn = mk("↓"); self.btn_dr = mk("↘")
        self.btn_zm = mk("Z↓")#; self.btn_em = mk("E−")

        # Layout 3x3 + 2 side columns
        mc.addWidget(self.btn_ul, 0, 0); mc.addWidget(self.btn_up, 0, 1); mc.addWidget(self.btn_ur, 0, 2)
        mc.addWidget(self.btn_zp, 0, 3)#; mc.addWidget(self.btn_ep, 0, 4)
        mc.addWidget(self.btn_lf, 1, 0); mc.addWidget(self.btn_c, 1, 1); mc.addWidget(self.btn_rt, 1, 2)
        mc.addWidget(self.btn_dl, 2, 0); mc.addWidget(self.btn_dn, 2, 1); mc.addWidget(self.btn_dr, 2, 2)
        mc.addWidget(self.btn_zm, 2, 3)#; mc.addWidget(self.btn_em, 2, 4)

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

        # =========================== TIMERS/STATE ===========================
        self._poll = QTimer(self); self._poll.setInterval(50)
        self._poll.timeout.connect(self._drain_gantry_messages)
        self._poll.start()

        self._pump_timer = QTimer(self); self._pump_timer.setSingleShot(True)

        # Manual jog timers (50 ms) — start on pressed, stop on released
        self._mk_jog_timers()

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

        # NEW: Move XY (steps)
        self.btn_move_steps.clicked.connect(self._on_move_steps_xy)

        # Pump
        self.sld_pump.valueChanged.connect(self._on_pump_duty_change)
        self._pump_timer.timeout.connect(self._on_pump_off)
        self.btn_p_on.clicked.connect(self._on_pump_on)
        self.btn_p_off.clicked.connect(self._on_pump_off)
        self.btn_p_run.clicked.connect(self._on_pump_run)

        # Optional dark style
        try:
            import qdarkstyle
            self.setStyleSheet(qdarkstyle.load_stylesheet_pyqt5())
        except Exception:
            pass

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
        # Z / E
        mk_timer("zp",    lambda: self._jog_z(+1))
        mk_timer("zm",    lambda: self._jog_z(-1))
        mk_timer("ep",    lambda: self._jog_e(+1))
        mk_timer("em",    lambda: self._jog_e(-1))

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
        #self.btn_ep.pressed.connect(self._t["ep"].start);  self.btn_ep.released.connect(self._t["ep"].stop)
        #self.btn_em.pressed.connect(self._t["em"].start);  self.btn_em.released.connect(self._t["em"].stop)

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
        self.q_gui_to_gantry.put({"type": "gantry_cmd", "cmd": "move_abs", "X": self._home_abs["x"], "Y": self._home_abs["y"]})

    # NEW: Move XY by N steps (relative)
    def _on_move_steps_xy(self):
        try:
            nx = int(float(self.in_steps_x.text() or "0"))
            ny = int(float(self.in_steps_y.text() or "0"))
        except ValueError:
            QMessageBox.warning(self, "Invalid", "Enter integer step counts for X and Y.")
            return
        self.q_gui_to_gantry.put({"type": "gantry_cmd", "cmd": "move_steps_xy", "nx": nx, "ny": ny})
        self._post_msg(f"Queued XY move: ΔX={nx}×step, ΔY={ny}×step")

    # Manual jog helpers (unit: "one GUI step" → backend multiplies by step sizes)
    def _jog_xy(self, sx: int, sy: int):
        self.q_ctrl_to_gantry.put({"type": "input", "cmd": "xy_motion", "value": (sx, sy)})

    def _jog_z(self, s: int):
        self.q_ctrl_to_gantry.put({"type": "input", "cmd": "z_motion", "value": (0, s)})

    def _jog_e(self, s: int):
        self.q_ctrl_to_gantry.put({"type": "input", "cmd": "e_motion", "value": (0, s)})

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
        self.q_gui_to_gantry.put({"type": "btn_estop"})
        self._pump_timer.stop()
        self._send_pump(0)
        self._post_msg("E-STOP sent.")

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
    win = StageGUI(simulate=args.simulate)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
