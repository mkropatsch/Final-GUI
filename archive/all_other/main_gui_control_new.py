# main_gui_control_new.py
# GUI-driven stage control with:
# - User-set XY "work home" (relative origin = 0,0 after Set Home)
# - Go to Home returns XY to saved origin; Z is NOT moved
# - Machine Home (G28) preserved
# - Pump Control (FAN0 via M106/M107)
# - Hysteresis + dwell to prevent wiggle when reaching home

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
    """Launch gantry process (prefers gantry_new)."""
    try:
        from gantry_new import GantrySystem
    except ModuleNotFoundError:
        try:
            from gantry import GantrySystem
        except ModuleNotFoundError:
            from gantry_keyboard_friendly import GantrySystem  # type: ignore
    g = GantrySystem(
        q_to_gui=q_to_gui,
        q_from_gui=q_from_gui,
        q_from_controller=q_from_controller,
        simulate=simulate,
    )
    g.run()


# ------------------------------ GUI main window ------------------------------
class StageGUI(QMainWindow):
    def __init__(self, simulate: bool = True):
        super().__init__()
        self.setWindowTitle("Core-XY Gantry — GUI Controller")
        self.resize(1200, 780)

        # IPC
        ctx = mp.get_context("spawn")
        self.q_gantry_to_gui = ctx.Queue(maxsize=1000)
        self.q_gui_to_gantry = ctx.Queue(maxsize=1000)
        self.q_ctrl_to_gantry = ctx.Queue(maxsize=1000)

        # Start gantry process
        self.p_gantry = ctx.Process(
            target=gantry_process_main,
            args=(self.q_gantry_to_gui, self.q_gui_to_gantry, self.q_ctrl_to_gantry, simulate),
            daemon=True,
        )
        self.p_gantry.start()

        # ---------------- Layout ----------------
        root = QWidget(self); self.setCentralWidget(root)
        grid = QGridLayout(root)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(2, 2)

        # XY plot (relative)
        self.xy_group = QGroupBox("XY Position (relative to Work Home 0,0)")
        vxy = QVBoxLayout(self.xy_group)
        self.xy_plot = pg.PlotWidget()
        self.xy_plot.setAspectLocked(True)
        self.xy_plot.showGrid(x=True, y=True, alpha=0.25)
        self.xy_plot.setLabel("left", "Y_rel (mm)")
        self.xy_plot.setLabel("bottom", "X_rel (mm)")
        self.xy_plot.invertY(True)
        self._xy_point = self.xy_plot.plot([0], [0], pen=None, symbol="o", symbolSize=10)
        vxy.addWidget(self.xy_plot)
        grid.addWidget(self.xy_group, 0, 0, 3, 1)

        # ---------- STATE ----------
        self.state_group = QGroupBox("State")
        st = QFormLayout(self.state_group)
        self.lab_x = QLabel("0.000"); self.lab_y = QLabel("0.000")
        self.lab_z = QLabel("0.000"); self.lab_e = QLabel("0.000")
        for lab in [self.lab_x, self.lab_y, self.lab_z, self.lab_e]:
            lab.setAlignment(Qt.AlignRight)
        st.addRow("X_rel", self.lab_x)
        st.addRow("Y_rel", self.lab_y)
        st.addRow("Z (abs)", self.lab_z)
        st.addRow("E (abs)", self.lab_e)

        # ---------- CONTROLS ----------
        self.ctrl_group = QGroupBox("Step & Speed")
        form = QFormLayout(self.ctrl_group)

        row_presets = QHBoxLayout()
        self.cmb_preset = QComboBox(); self.cmb_preset.addItems(["Fine", "Medium", "Coarse"])
        row_presets.addWidget(QLabel("Preset")); row_presets.addWidget(self.cmb_preset); row_presets.addStretch()

        self.in_xy = QLineEdit("0.20")
        self.in_z  = QLineEdit("0.05")
        self.in_e  = QLineEdit("0.02")

        self.sld_feed = QSlider(Qt.Horizontal); self.sld_feed.setRange(100, 12000)
        self.sld_feed.setValue(3000)
        self.lab_feed = QLabel(f"{self.sld_feed.value()} mm/min"); self.lab_feed.setAlignment(Qt.AlignRight)
        row_feed = QHBoxLayout(); row_feed.addWidget(self.sld_feed, stretch=1); row_feed.addWidget(self.lab_feed)

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

        form.addRow(row_presets)
        form.addRow("XY step", self.in_xy)
        form.addRow("Z step", self.in_z)
        form.addRow("E step", self.in_e)
        form.addRow("Feed (mm/min)", row_feed)
        form.addRow(row_act)

        # ---------- MESSAGES ----------
        self.msg_group = QGroupBox("Messages")
        vmsg = QVBoxLayout(self.msg_group)
        self.lab_msg = QLabel("Ready."); self.lab_msg.setWordWrap(True)
        vmsg.addWidget(self.lab_msg)

        right_col = QVBoxLayout()
        right_col.addWidget(self.state_group)
        right_col.addWidget(self.ctrl_group)
        right_col.addWidget(self.msg_group, stretch=1)
        grid.addLayout(right_col, 0, 1, 3, 1)

        # ---------- JOG PANEL ----------
        self.jog_group = QGroupBox("Manual Control")
        j = QGridLayout(self.jog_group)

        def mk(txt, w=48, h=36):
            b = QPushButton(txt); b.setFixedSize(w, h); return b

        self.btn_ul = mk("↖"); self.btn_up = mk("↑"); self.btn_ur = mk("↗")
        self.btn_le = mk("←"); self.btn_ct = mk("•"); self.btn_ri = mk("→")
        self.btn_dl = mk("↙"); self.btn_dn = mk("↓"); self.btn_dr = mk("↘")
        self.btn_zp = mk("Z↑"); self.btn_zm = mk("Z↓")
        self.btn_ep = mk("E+"); self.btn_em = mk("E−")

        j.addWidget(self.btn_ul, 0, 0); j.addWidget(self.btn_up, 0, 1); j.addWidget(self.btn_ur, 0, 2)
        j.addWidget(self.btn_le, 1, 0); j.addWidget(self.btn_ct, 1, 1); j.addWidget(self.btn_ri, 1, 2)
        j.addWidget(self.btn_dl, 2, 0); j.addWidget(self.btn_dn, 2, 1); j.addWidget(self.btn_dr, 2, 2)
        j.addWidget(self.btn_zp, 0, 3); j.addWidget(self.btn_zm, 2, 3)
        j.addWidget(self.btn_ep, 0, 4); j.addWidget(self.btn_em, 2, 4)

        grid.addWidget(self.jog_group, 0, 2, 2, 1)

        # ---------- PUMP PANEL ----------
        self.pump_group = QGroupBox("Pump Control (FAN0 → M106/M107)")
        pp = QFormLayout(self.pump_group)

        row_duty = QHBoxLayout()
        self.sld_pump = QSlider(Qt.Horizontal); self.sld_pump.setRange(0, 255); self.sld_pump.setValue(0)
        self.lab_pump = QLabel("0 / 255  (0%)"); self.lab_pump.setAlignment(Qt.AlignRight)
        row_duty.addWidget(self.sld_pump, stretch=1); row_duty.addWidget(self.lab_pump)

        row_timed = QHBoxLayout()
        self.spn_secs = QSpinBox(); self.spn_secs.setRange(1, 3600); self.spn_secs.setValue(60)
        self.btn_pump_on  = QPushButton("Pump ON")
        self.btn_pump_off = QPushButton("Pump OFF")
        self.btn_pump_run = QPushButton("Run Timed")
        row_timed.addWidget(QLabel("Seconds:")); row_timed.addWidget(self.spn_secs)
        row_timed.addStretch()
        row_timed.addWidget(self.btn_pump_on); row_timed.addWidget(self.btn_pump_off); row_timed.addWidget(self.btn_pump_run)

        pp.addRow("Duty (0..255)", row_duty)
        pp.addRow(row_timed)

        grid.addWidget(self.pump_group, 2, 2, 1, 1)

        # ---------- SIGNALS ----------
        self.cmb_preset.currentTextChanged.connect(self._apply_preset_fields)
        self.btn_apply.clicked.connect(self._apply_steps_to_gantry)
        self.btn_home.clicked.connect(lambda: self.q_gui_to_gantry.put({"type": "home_all"}))

        self.btn_sethome.clicked.connect(self._set_home_xy_origin)
        self.btn_gohome.clicked.connect(self._goto_home_start_xy)

        self.btn_estop.clicked.connect(self._on_estop)

        self.sld_feed.valueChanged.connect(lambda v: self.lab_feed.setText(f"{v} mm/min"))
        self.sld_feed.sliderReleased.connect(self._apply_feed_to_gantry)

        self._wire_jog_buttons()

        self.sld_pump.valueChanged.connect(self._on_pump_duty_change)
        self.btn_pump_on.clicked.connect(self._on_pump_on)
        self.btn_pump_off.clicked.connect(self._on_pump_off)
        self.btn_pump_run.clicked.connect(self._on_pump_run)

        # ---------- TIMERS ----------
        self._poll = QTimer(self); self._poll.setInterval(100)
        self._poll.timeout.connect(self._drain_gantry_messages)
        self._poll.start()

        self._jx = self._jy = self._jz = self._je = 0.0
        self._jog_tick = QTimer(self); self._jog_tick.setInterval(16)
        self._jog_tick.timeout.connect(self._tick_emit_inputs)
        self._jog_tick.start()

        # Home + pump state
        self._last_abs = {"x": 0.0, "y": 0.0, "z": 0.0, "e": 0.0}
        self._home_abs: Optional[Dict[str, float]] = None
        self._goto_active_xy = False
        self._goto_tol_enter = 0.02   # enter deadband
        self._goto_tol_exit = 0.05    # exit deadband
        self._goto_stable_needed = 6  # consecutive in-band ticks
        self._goto_stable_count = 0
        self._goto_timer = QTimer(self); self._goto_timer.setInterval(16)
        self._goto_timer.timeout.connect(self._goto_home_tick_xy)

        self._pump_timer = QTimer(self); self._pump_timer.setSingleShot(True)
        self._pump_timer.timeout.connect(self._on_pump_off)

    # ---------- Jog logic ----------
    def _wire_jog_buttons(self):
        def press_xy(dx, dy):
            self._jx = float(dx); self._jy = float(dy)
            self._emit_input("xy_motion", (self._jx, self._jy))
        def release_xy():
            self._jx = self._jy = 0.0
            self._emit_input("xy_motion", (0.0, 0.0))
        mapping = [
            (self.btn_up,  (0, +1)), (self.btn_dn,  (0, -1)),
            (self.btn_le, (-1,  0)), (self.btn_ri,  (+1, 0)),
            (self.btn_ul, (-1, +1)), (self.btn_ur, (+1, +1)),
            (self.btn_dl, (-1, -1)), (self.btn_dr, (+1, -1)),
        ]
        for btn, (dx, dy) in mapping:
            btn.pressed.connect(lambda dx=dx, dy=dy: press_xy(dx, dy))
            btn.released.connect(release_xy)
        self.btn_zp.pressed.connect(lambda: self._set_z(+1))
        self.btn_zp.released.connect(lambda: self._set_z(0))
        self.btn_zm.pressed.connect(lambda: self._set_z(-1))
        self.btn_zm.released.connect(lambda: self._set_z(0))
        self.btn_ep.pressed.connect(lambda: self._set_e(+1))
        self.btn_ep.released.connect(lambda: self._set_e(0))
        self.btn_em.pressed.connect(lambda: self._set_e(-1))
        self.btn_em.released.connect(lambda: self._set_e(0))
        self.btn_ct.clicked.connect(self._goto_home_start_xy)

    # ---------- Inputs ----------
    def _emit_input(self, cmd: str, value):
        if not cmd: return
        self.q_ctrl_to_gantry.put({"type": "input", "cmd": cmd, "value": value})

    def _tick_emit_inputs(self):
        if self._jx or self._jy:
            self._emit_input("xy_motion", (self._jx, self._jy))
        if self._jz:
            self._emit_input("z_motion", (0.0, self._jz))
        if self._je:
            lt = 1.0 if self._je < 0 else 0.0
            rt = 1.0 if self._je > 0 else 0.0
            self._emit_input("e_motion", (lt, rt))

    def _set_z(self, sign: int):
        self._jz = float(sign)
        if sign == 0:
            self._emit_input("z_motion", (0.0, 0.0))

    def _set_e(self, sign: int):
        self._je = float(sign)
        if sign == 0:
            self._emit_input("e_motion", (0.0, 0.0))

    # ---------- E-STOP ----------
    def _on_estop(self):
        self.q_gui_to_gantry.put({"type": "btn_estop"})
        self._jx = self._jy = self._jz = self._je = 0.0
        self._goto_active_xy = False
        self._goto_timer.stop()
        self._pump_timer.stop()
        self._emit_input("xy_motion", (0.0, 0.0))
        self._emit_input("z_motion", (0.0, 0.0))
        self._emit_input("e_motion", (0.0, 0.0))
        self._send_pump(0)
        self.lab_msg.setText("E-STOP sent.")

    # ---------- Work-home ----------
    def _set_home_xy_origin(self):
        ax, ay = self._last_abs["x"], self._last_abs["y"]
        self._home_abs = {"x": ax, "y": ay}
        self.lab_msg.setText(f"Work Home set at (X{ax:.3f}, Y{ay:.3f}). Relative now 0,0.")

    def _goto_home_start_xy(self):
        if not self._home_abs:
            self.lab_msg.setText("No Work Home set yet. Use 'Set Home'.")
            return
        self._goto_active_xy = True
        self._goto_stable_count = 0
        self._goto_timer.start()

    def _goto_home_tick_xy(self):
        """Drive XY toward saved absolute origin; Z not moved.
           Uses hysteresis + dwell to avoid oscillation at the target."""
        if not (self._goto_active_xy and self._home_abs):
            return

        cx, cy = self._last_abs["x"], self._last_abs["y"]
        tx, ty = self._home_abs["x"], self._home_abs["y"]
        ex, ey = tx - cx, ty - cy

        in_band = (abs(ex) <= self._goto_tol_enter) and (abs(ey) <= self._goto_tol_enter)

        if in_band:
            self._goto_stable_count += 1
            if self._goto_stable_count >= self._goto_stable_needed:
                self._goto_active_xy = False
                self._goto_timer.stop()
                self._emit_input("xy_motion", (0.0, 0.0))
                self.lab_msg.setText("Arrived at Work Home (XY).")
            else:
                # hold still while accumulating stable ticks
                self._emit_input("xy_motion", (0.0, 0.0))
            return
        else:
            # outside the wider exit band => reset stability counter
            if (abs(ex) > self._goto_tol_exit) or (abs(ey) > self._goto_tol_exit):
                self._goto_stable_count = 0

        def sgn(v: float) -> float:
            return 0.0 if abs(v) <= self._goto_tol_enter else (1.0 if v > 0 else -1.0)

        dx = sgn(ex)
        dy = sgn(ey)

        # Approach slowdown near target
        d_inf = max(abs(ex), abs(ey))
        scale = 1.0
        if d_inf < 0.50:
            scale = 0.5
        if d_inf < 0.10:
            scale = 0.25

        # IMPORTANT: backend inverts Y (flip_y = -1), so invert here, too
        self._emit_input("xy_motion", (dx * scale, -dy * scale))

    # ---------- Pump ----------
    def _on_pump_duty_change(self, v: int):
        pct = int(round(100 * v / 255.0))
        self.lab_pump.setText(f"{v} / 255  ({pct}%)")

    def _send_gcode(self, cmd: str):
        self.q_gui_to_gantry.put({"type": "gcode", "cmd": cmd})

    def _send_pump(self, duty_0_255: int):
        d = max(0, min(255, int(duty_0_255)))
        if d <= 0:
            self._send_gcode("M107 P0")
            self.q_gui_to_gantry.put({"type": "fan_set", "index": 0, "value": 0})
        else:
            self._send_gcode(f"M106 P0 S{d}")
            self.q_gui_to_gantry.put({"type": "fan_set", "index": 0, "value": d})

    def _on_pump_on(self):
        duty = int(self.sld_pump.value())
        self._pump_timer.stop()
        self._send_pump(duty)
        self.lab_msg.setText(f"Pump ON at {duty}/255.")

    def _on_pump_off(self):
        self._pump_timer.stop()
        self._send_pump(0)
        self.lab_msg.setText("Pump OFF.")

    def _on_pump_run(self):
        duty = int(self.sld_pump.value())
        secs = int(self.spn_secs.value())
        if duty <= 0:
            QMessageBox.information(self, "Pump", "Set duty > 0 to run the pump.")
            return
        self._send_pump(duty)
        self._pump_timer.start(secs * 1000)
        self.lab_msg.setText(f"Pump RUN {secs}s at {duty}/255…")

    # ---------- Controls / Gantry messaging ----------
    def _apply_preset_fields(self, name: str):
        if name == "Fine":
            self.in_xy.setText("0.01"); self.in_z.setText("0.005"); self.in_e.setText("0.005")
        elif name == "Medium":
            self.in_xy.setText("0.10"); self.in_z.setText("0.05");  self.in_e.setText("0.02")
        else:
            self.in_xy.setText("1.00"); self.in_z.setText("0.20");  self.in_e.setText("0.10")

    def _apply_steps_to_gantry(self):
        try:
            xy = float(self.in_xy.text()); z = float(self.in_z.text()); e = float(self.in_e.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid", "Enter numeric step sizes.")
            return
        self.q_gui_to_gantry.put({"type": "set_steps", "xy_step": xy, "z_step": z, "e_step": e})

    def _apply_feed_to_gantry(self):
        self.q_gui_to_gantry.put({"type": "set_feed", "feed_mm_min": int(self.sld_feed.value())})

    # ---------- Gantry → GUI ----------
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
        # Absolute from hardware
        ax = float(s.get("x", 0.0)); ay = float(s.get("y", 0.0))
        az = float(s.get("z", 0.0)); ae = float(s.get("e", 0.0))
        self._last_abs["x"] = ax; self._last_abs["y"] = ay
        self._last_abs["z"] = az; self._last_abs["e"] = ae

        # Relative XY based on saved origin (if any)
        if self._home_abs:
            xr = ax - self._home_abs["x"]
            yr = ay - self._home_abs["y"]
        else:
            xr = ax
            yr = ay

        # Plot & labels (XY relative; Z/E absolute)
        self._xy_point.setData([xr], [yr])
        self.lab_x.setText(f"{xr:.3f}")
        self.lab_y.setText(f"{yr:.3f}")
        self.lab_z.setText(f"{az:.3f}")
        self.lab_e.setText(f"{ae:.3f}")

        # keep UI fields in sync, if gantry publishes them
        if "xy_step" in s: self.in_xy.setText(f"{float(s['xy_step']):.3f}")
        if "z_step"  in s: self.in_z.setText(f"{float(s['z_step']):.3f}")
        if "e_step"  in s: self.in_e.setText(f"{float(s['e_step']):.3f}")

    # ---------- Shutdown ----------
    def closeEvent(self, ev):
        try:
            self._poll.stop(); self._jog_tick.stop(); self._goto_timer.stop(); self._pump_timer.stop()
        except Exception:
            pass
        try:
            self._send_pump(0)
        except Exception:
            pass
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
    # Optional dark theme
    try:
        import qdarkstyle
        app.setStyleSheet(qdarkstyle.load_stylesheet_pyqt5())
    except Exception:
        pass

    win = StageGUI(simulate=args.simulate)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
