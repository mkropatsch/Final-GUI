# main_gui_control.py
# GUI-driven stage control: buttons emit the same messages your gantry already understands.
# No keyboard controller process, no pygame window.

from __future__ import annotations
import sys, os, multiprocessing as mp
from typing import Dict

from PyQt5.QtCore import Qt, QTimer, QDateTime
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGridLayout, QGroupBox, QFormLayout,
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QComboBox, QSlider, QMessageBox
)
import pyqtgraph as pg

# --------------------------- child (gantry) entry ----------------------------
def gantry_process_main(q_to_gui, q_from_gui, q_from_controller, simulate: bool) -> None:
    """
    Launch the gantry process (your existing gantry backend).
    """
    try:
        from gantry import GantrySystem
    except ModuleNotFoundError:
        # If you renamed it, try the keyboard-friendly variant you created earlier.
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
        self.setWindowTitle("Core-XY Gantry — GUI Controller (no keyboard)")
        self.resize(1100, 720)

        # IPC (spawn-safe)
        ctx = mp.get_context("spawn")
        self.q_gantry_to_gui = ctx.Queue(maxsize=1000)
        self.q_gui_to_gantry = ctx.Queue(maxsize=1000)
        self.q_ctrl_to_gantry = ctx.Queue(maxsize=1000)  # we reuse this queue for "input" traffic

        # Start gantry process only (no controller process)
        self.p_gantry = ctx.Process(
            target=gantry_process_main,
            args=(self.q_gantry_to_gui, self.q_gui_to_gantry, self.q_ctrl_to_gantry, simulate),
            daemon=True,
        )
        self.p_gantry.start()

        # ----------------------------- layout ---------------------------------
        root = QWidget(self); self.setCentralWidget(root)
        grid = QGridLayout(root)
        grid.setColumnStretch(0, 3)  # plot
        grid.setColumnStretch(1, 2)  # state + controls
        grid.setColumnStretch(2, 2)  # jog panel

        # XY plot (coordinate map)
        self.xy_group = QGroupBox("XY Position (mm)")
        vxy = QVBoxLayout(self.xy_group)
        self.xy_plot = pg.PlotWidget()
        self.xy_plot.setAspectLocked(True)
        self.xy_plot.showGrid(x=True, y=True, alpha=0.25)
        self.xy_plot.setLabel("left", "Y (mm)")
        self.xy_plot.setLabel("bottom", "X (mm)")
        self.xy_plot.invertY(True)  # so up is +Y
        self._xy_point = self.xy_plot.plot([0], [0], pen=None, symbol="o", symbolSize=10)
        vxy.addWidget(self.xy_plot)
        grid.addWidget(self.xy_group, 0, 0, 3, 1)

        # State group
        self.state_group = QGroupBox("State")
        st = QFormLayout(self.state_group)
        self.lab_x = QLabel("0.000"); self.lab_x.setAlignment(Qt.AlignRight)
        self.lab_y = QLabel("0.000"); self.lab_y.setAlignment(Qt.AlignRight)
        self.lab_z = QLabel("0.000"); self.lab_z.setAlignment(Qt.AlignRight)
        self.lab_e = QLabel("0.000"); self.lab_e.setAlignment(Qt.AlignRight)
        st.addRow("X", self.lab_x)
        st.addRow("Y", self.lab_y)
        st.addRow("Z", self.lab_z)
        st.addRow("E", self.lab_e)

        # Controls group (step sizes + feed + actions)
        self.ctrl_group = QGroupBox("Step & Speed")
        form = QFormLayout(self.ctrl_group)

        # Presets selector (optional; just fills the fields)
        row_presets = QHBoxLayout()
        self.cmb_preset = QComboBox(); self.cmb_preset.addItems(["Fine", "Medium", "Coarse"])
        row_presets.addWidget(QLabel("Preset")); row_presets.addWidget(self.cmb_preset); row_presets.addStretch()

        # Step size fields
        self.in_xy = QLineEdit("0.20")
        self.in_z  = QLineEdit("0.05")
        self.in_e  = QLineEdit("0.02")

        # Feed slider (mm/min)
        self.sld_feed = QSlider(Qt.Horizontal)
        self.sld_feed.setRange(100, 12000)
        self.sld_feed.setValue(3000)
        self.lab_feed = QLabel(f"{self.sld_feed.value()} mm/min")
        self.lab_feed.setAlignment(Qt.AlignRight)
        row_feed = QHBoxLayout()
        row_feed.addWidget(self.sld_feed, stretch=1)
        row_feed.addWidget(self.lab_feed)

        # Buttons
        row_act = QHBoxLayout()
        self.btn_apply   = QPushButton("Apply step sizes")
        self.btn_sethome = QPushButton("Set Home")
        self.btn_gohome  = QPushButton("Go to Home")
        self.btn_home    = QPushButton("Home all (G28)")
        self.btn_estop   = QPushButton("E-STOP")
        self.btn_estop.setStyleSheet("QPushButton{background:#b51f1f;color:white;font-weight:bold}")

        row_act.addWidget(self.btn_apply)
        row_act.addStretch()
        row_act.addWidget(self.btn_sethome)
        row_act.addWidget(self.btn_gohome)
        row_act.addWidget(self.btn_home)
        row_act.addWidget(self.btn_estop)


        form.addRow(row_presets)
        form.addRow("XY step (mm/tick)", self.in_xy)
        form.addRow("Z step (mm/tick)",  self.in_z)
        form.addRow("E step (mm/tick)",  self.in_e)
        form.addRow("Feed (mm/min)", row_feed)
        form.addRow(row_act)

        # Messages
        self.msg_group = QGroupBox("Messages")
        vmsg = QVBoxLayout(self.msg_group)
        self.lab_msg = QLabel("Ready."); self.lab_msg.setWordWrap(True)
        vmsg.addWidget(self.lab_msg)

        right_col = QVBoxLayout()
        right_col.addWidget(self.state_group)
        right_col.addWidget(self.ctrl_group)
        right_col.addWidget(self.msg_group, stretch=1)
        grid.addLayout(right_col, 0, 1, 3, 1)

        # Jog panel
        self.jog_group = QGroupBox("Manual Control")
        j = QGridLayout(self.jog_group)

        def mk(txt, w=48, h=36):
            b = QPushButton(txt); b.setFixedSize(w, h); return b

        # 8-way pad + center
        self.btn_ul = mk("↖"); self.btn_up = mk("↑"); self.btn_ur = mk("↗")
        self.btn_le = mk("←"); self.btn_ct = mk("•"); self.btn_ri = mk("→")
        self.btn_dl = mk("↙"); self.btn_dn = mk("↓"); self.btn_dr = mk("↘")
        # Z & E
        self.btn_zp = mk("Z↑"); self.btn_zm = mk("Z↓")
        self.btn_ep = mk("E+"); self.btn_em = mk("E−")

        j.addWidget(self.btn_ul, 0, 0); j.addWidget(self.btn_up, 0, 1); j.addWidget(self.btn_ur, 0, 2)
        j.addWidget(self.btn_le, 1, 0); j.addWidget(self.btn_ct, 1, 1); j.addWidget(self.btn_ri, 1, 2)
        j.addWidget(self.btn_dl, 2, 0); j.addWidget(self.btn_dn, 2, 1); j.addWidget(self.btn_dr, 2, 2)
        j.addWidget(self.btn_zp, 0, 3); j.addWidget(self.btn_zm, 2, 3)
        j.addWidget(self.btn_ep, 0, 4); j.addWidget(self.btn_em, 2, 4)
        grid.addWidget(self.jog_group, 0, 2, 2, 1)

        # ------------------------------ signals --------------------------------
        self.cmb_preset.currentTextChanged.connect(self._apply_preset_fields)
        self.btn_apply.clicked.connect(self._apply_steps_to_gantry)
        self.btn_home.clicked.connect(lambda: self.q_gui_to_gantry.put({"type": "home_all"}))
        self.btn_sethome.clicked.connect(self._set_home)
        self.btn_gohome.clicked.connect(self._goto_home_start)
        self.btn_estop.clicked.connect(self._on_estop)



        self.sld_feed.valueChanged.connect(lambda v: self.lab_feed.setText(f"{v} mm/min"))
        self.sld_feed.sliderReleased.connect(self._apply_feed_to_gantry)

        self._wire_jog_buttons()

        # ------------------------------- timers --------------------------------
        # Poll gantry → GUI
        self._poll = QTimer(self); self._poll.setInterval(100)
        self._poll.timeout.connect(self._drain_gantry_messages)
        self._poll.start()

        # Continuous jog “tick” (~60 Hz): emits inputs while buttons are held
        self._jx = 0.0; self._jy = 0.0; self._jz = 0.0; self._je = 0.0
        self._jog_tick = QTimer(self); self._jog_tick.setInterval(16)
        self._jog_tick.timeout.connect(self._tick_emit_inputs)
        self._jog_tick.start()

# ---------- soft "work home" (user-set home) ----------
        self._home = None             # dict like {"x":..., "y":..., "z":..., "e":...}
        self._last = {"x":0.0,"y":0.0,"z":0.0,"e":0.0}
        self._goto_active = False
        self._goto_tol = 0.01         # mm tolerance to stop at home

        self._goto_timer = QTimer(self)
        self._goto_timer.setInterval(16)   # ~60 Hz
        self._goto_timer.timeout.connect(self._goto_home_tick)


    # ------------------------------ jog wiring --------------------------------
    def _wire_jog_buttons(self):
        # helper for XY press/hold
        def press_xy(dx, dy):
            self._jx = float(dx); self._jy = float(dy)
            # optional: send an immediate nudge so taps feel responsive
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

        # Z press/hold
        self.btn_zp.pressed.connect(lambda: self._set_z(+1))
        self.btn_zp.released.connect(lambda: self._set_z(0))
        self.btn_zm.pressed.connect(lambda: self._set_z(-1))
        self.btn_zm.released.connect(lambda: self._set_z(0))

        # E press/hold (we map to trigger pair (lt, rt))
        self.btn_ep.pressed.connect(lambda: self._set_e(+1))
        self.btn_ep.released.connect(lambda: self._set_e(0))
        self.btn_em.pressed.connect(lambda: self._set_e(-1))
        self.btn_em.released.connect(lambda: self._set_e(0))

        # Center dot → Home
        # Center dot → Go to saved Home
        self.btn_ct.clicked.connect(self._goto_home_start)


    # ------------------------------ emit helpers ------------------------------
    def _emit_input(self, cmd: str, value):
        if not cmd or cmd == "none":
            return
        self.q_ctrl_to_gantry.put({"type": "input", "cmd": cmd, "value": value})

    def _tick_emit_inputs(self):
        # Convert held directions into periodic inputs, like a joystick
        if self._jx or self._jy:
            self._emit_input("xy_motion", (self._jx, self._jy))
        if self._jz:
            # gantry expects z_motion as (0.0, jy)
            self._emit_input("z_motion", (0.0, self._jz))
        if self._je:
            # e_motion uses (lt, rt) in 0..1; map -1/0/+1 to a pair
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


def _on_estop(self):
    """Emergency stop: tell gantry, and clear any ongoing motions/timers."""
    self.q_gui_to_gantry.put({"type": "btn_estop"})
    self._jx = self._jy = self._jz = self._je = 0.0
    self._goto_active = False
    self._goto_timer.stop()
    # send zero inputs to ensure motion stops
    self._emit_input("xy_motion", (0.0, 0.0))
    self._emit_input("z_motion", (0.0, 0.0))
    self._emit_input("e_motion", (0.0, 0.0))
    self.lab_msg.setText("E-STOP sent to gantry!")

def _set_home(self):
    """Save the current position as 'work home'."""
    self._home = dict(self._last)  # copy
    self.lab_msg.setText(f"Home set at (X{self._home['x']:.3f}, Y{self._home['y']:.3f}, Z{self._home['z']:.3f})")

def _goto_home_start(self):
    """Begin soft go-home towards saved work home using jog inputs."""
    if not self._home:
        self.lab_msg.setText("No Home set yet. Click 'Set Home' at your desired position.")
        return
    self._goto_active = True
    self._goto_timer.start()

def _goto_home_tick(self):
    """Drive axes toward saved home by emitting jog-like inputs until within tolerance."""
    if not (self._goto_active and self._home):
        return
    # Read current position
    cx, cy, cz = self._last["x"], self._last["y"], self._last["z"]
    tx, ty, tz = self._home["x"], self._home["y"], self._home["z"]

    # Compute small direction steps (-1, 0, +1) per axis
    def sgn(delta, tol):
        if abs(delta) <= tol: return 0.0
        return 1.0 if delta > 0 else -1.0

    dx = sgn(tx - cx, self._goto_tol)
    dy = sgn(ty - cy, self._goto_tol)
    dz = sgn(tz - cz, self._goto_tol)

    # If all within tolerance, stop
    if dx == dy == dz == 0.0:
        self._goto_active = False
        self._goto_timer.stop()
        # stop any residual motion
        self._emit_input("xy_motion", (0.0, 0.0))
        self._emit_input("z_motion", (0.0, 0.0))
        self.lab_msg.setText("Arrived at Home.")
        return

    # Emit jog-like inputs toward the target
    if dx or dy:
        self._emit_input("xy_motion", (dx, dy))
    if dz:
        self._emit_input("z_motion", (0.0, dz))



    # ----------------------------- controls/events ----------------------------
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

    # ----------------------------- gantry -> GUI ------------------------------
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
        x = float(s.get("x", 0.0)); y = float(s.get("y", 0.0))
        z = float(s.get("z", 0.0)); e = float(s.get("e", 0.0))
        self._xy_point.setData([x], [y])
        self.lab_x.setText(f"{x:.3f}")
        self.lab_y.setText(f"{y:.3f}")
        self.lab_z.setText(f"{z:.3f}")
        self.lab_e.setText(f"{e:.3f}")
        # keep UI fields in sync, if gantry publishes them
        if "xy_step" in s: self.in_xy.setText(f"{float(s['xy_step']):.3f}")
        if "z_step"  in s: self.in_z.setText(f"{float(s['z_step']):.3f}")
        if "e_step"  in s: self.in_e.setText(f"{float(s['e_step']):.3f}")
        self._last["x"] = x
        self._last["y"] = y
        self._last["z"] = z
        self._last["e"] = e

    # -------------------------------- shutdown --------------------------------
    def closeEvent(self, ev):
        try:
            self._poll.stop(); self._jog_tick.stop()
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

#    def btn_estop(self):
 #       """Emergency stop handler — sends E-STOP to gantry."""
  #      self.q_gui_to_gantry.put({"type": "btn_estop"})
   #     self.lab_msg.setText("E-STOP sent to gantry!")
     #   # Optionally reset all jog variables
    #    self._jx = self._jy = self._jz = self._je = 0.0
