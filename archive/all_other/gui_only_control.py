"""
GUI-Only Gantry Controller
--------------------------
- Spawns your existing GantrySystem in a child process.
- Uses on-screen jog buttons to send motion commands:
    * XY: 8-way pad (↖ ↑ ↗, ← • →, ↙ ↓ ↘)
    * Z: Z↑, Z↓
    * E (extruder/feeder): E+, E−
- Also exposes: step sizes, feed (mm/min), G28 (Home), E-STOP.

Run:
    python gui_only_control.py [--simulate]

Requires:
    PyQt5, pyqtgraph
    Your existing gantry module: `gantry_keyboard_friendly.GantrySystem`
"""

import argparse
import multiprocessing as mp
import queue
import sys
from typing import Dict, Tuple, Set

from PyQt5.QtCore import Qt, QTimer, QDateTime
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGridLayout, QVBoxLayout,
    QGroupBox, QFormLayout, QLabel, QSlider, QLineEdit, QSpinBox,
    QPushButton, QHBoxLayout, QMessageBox
)
import pyqtgraph as pg


# ------------------------- Child process entrypoint ---------------------------

def gantry_process_main(q_to_gui, q_from_gui, q_from_controller, simulate: bool) -> None:
    """
    Child entry point: import and run your GantrySystem in the child.
    """
    from gantry_keyboard_friendly import GantrySystem  # keep same import as your existing code
    g = GantrySystem(
        q_to_gui=q_to_gui,
        q_from_gui=q_from_gui,
        q_from_controller=q_from_controller,
        simulate=simulate,
    )
    g.run()


# ------------------------------- Main Window ---------------------------------

class GantryGUI(QMainWindow):
    """
    GUI-only controller: no keyboard reader, no mapping dialog.
    All motion originates from on-screen jog buttons (with press-and-hold).
    """

    def __init__(self, simulate: bool = True):
        super().__init__()

        # ---- IPC queues and child process (spawn-safe on Windows/macOS) -------
        ctx = mp.get_context("spawn")
        self.q_gantry_to_gui = ctx.Queue(maxsize=1000)
        self.q_gui_to_gantry = ctx.Queue(maxsize=1000)
        self.q_ctrl_to_gantry = ctx.Queue(maxsize=1000)  # we use this for GUI "inputs"

        self.p_gantry = ctx.Process(
            target=gantry_process_main,
            args=(self.q_gantry_to_gui, self.q_gui_to_gantry, self.q_ctrl_to_gantry, simulate),
            daemon=True,
        )
        self.p_gantry.start()

        # ------------------------------- UI ------------------------------------
        self.setWindowTitle("Gantry — GUI Jog Controller")
        f = QWidget()
        self.setCentralWidget(f)
        grid = QGridLayout(f)

        # XY plot
        self.xy_group = QGroupBox("XY Position (mm)")
        xy_layout = QVBoxLayout(self.xy_group)
        self.xy_plot = pg.PlotWidget()
        self.xy_plot.setAspectLocked(True)
        self.xy_plot.showGrid(x=True, y=True, alpha=0.25)
        self.xy_plot.setLabel("left", "Y (mm)")
        self.xy_plot.setLabel("bottom", "X (mm)")
        self._xy_point = self.xy_plot.plot([0], [0], pen=None, symbol="o", symbolSize=10)
        xy_layout.addWidget(self.xy_plot)

        # State + Z bar
        self.state_group = QGroupBox("State")
        st = QFormLayout(self.state_group)
        self.lab_x = QLabel("0.000")
        self.lab_y = QLabel("0.000")
        self.lab_z = QLabel("0.000")
        self.lab_e = QLabel("0.000")
        st.addRow("X", self.lab_x)
        st.addRow("Y", self.lab_y)
        st.addRow("Z", self.lab_z)
        st.addRow("E", self.lab_e)

        self.z_group = QGroupBox("Z (qualitative)")
        zlay = QVBoxLayout(self.z_group)
        self.z_bar = QSlider(Qt.Vertical)
        self.z_bar.setEnabled(False)
        self.z_bar.setRange(0, 1000)
        self.z_bar.setValue(500)
        zlay.addWidget(self.z_bar)

        # Controls: step sizes, feed, Home, E-STOP
        self.ctrl_group = QGroupBox("Controls")
        form = QFormLayout(self.ctrl_group)
        self.in_xy = QLineEdit("0.20")
        self.in_z = QLineEdit("0.05")
        self.in_e = QLineEdit("0.02")
        self.in_feed = QSpinBox()
        self.in_feed.setRange(100, 12000)
        self.in_feed.setValue(3000)
        form.addRow("XY step (mm/tick)", self.in_xy)
        form.addRow("Z step (mm/tick)", self.in_z)
        form.addRow("E step (mm/tick)", self.in_e)
        form.addRow("Feed (mm/min)", self.in_feed)

        self.btn_apply = QPushButton("Apply step sizes")
        self.btn_home = QPushButton("Home all (G28)")
        self.btn_estop = QPushButton("E-STOP")
        self.btn_estop.setStyleSheet("QPushButton{background:#b51f1f;color:white;font-weight:bold}")
        row = QHBoxLayout()
        row.addWidget(self.btn_apply)
        row.addWidget(self.btn_home)
        row.addWidget(self.btn_estop)
        row.addStretch(1)
        form.addRow(row)

        # Message line
        self.msg_group = QGroupBox("Messages")
        mlay = QVBoxLayout(self.msg_group)
        self.msg = QLabel("—")
        self.msg.setTextInteractionFlags(Qt.TextSelectableByMouse)
        mlay.addWidget(self.msg)

        # Jog panel
        self.jog_group = QGroupBox("Manual Control")
        j = QGridLayout(self.jog_group)

        def mk(txt, w=48, h=36):
            b = QPushButton(txt)
            b.setFixedSize(w, h)
            return b

        # XY pad
        self.btn_ul = mk("↖"); self.btn_up = mk("↑"); self.btn_ur = mk("↗")
        self.btn_le = mk("←"); self.btn_ct = mk("•"); self.btn_ri = mk("→")
        self.btn_dl = mk("↙"); self.btn_dn = mk("↓"); self.btn_dr = mk("↘")
        j.addWidget(self.btn_ul, 0, 0); j.addWidget(self.btn_up, 0, 1); j.addWidget(self.btn_ur, 0, 2)
        j.addWidget(self.btn_le, 1, 0); j.addWidget(self.btn_ct, 1, 1); j.addWidget(self.btn_ri, 1, 2)
        j.addWidget(self.btn_dl, 2, 0); j.addWidget(self.btn_dn, 2, 1); j.addWidget(self.btn_dr, 2, 2)

        # Z column
        self.btn_zp = mk("Z↑"); self.btn_zm = mk("Z↓")
        j.addWidget(self.btn_zp, 0, 3)
        j.addWidget(self.btn_zm, 2, 3)

        # E column
        self.btn_ep = mk("E+"); self.btn_em = mk("E−")
        j.addWidget(self.btn_ep, 0, 4)
        j.addWidget(self.btn_em, 2, 4)

        # Layout placement
        grid.addWidget(self.xy_group, 0, 0, 2, 1)
        grid.addWidget(self.state_group, 0, 1, 1, 1)
        grid.addWidget(self.z_group, 0, 2, 1, 1)
        grid.addWidget(self.ctrl_group, 1, 1, 1, 2)
        grid.addWidget(self.jog_group, 2, 0, 1, 2)
        grid.addWidget(self.msg_group, 2, 2, 1, 1)

        # -------------------------- Signals & timers ---------------------------
        self.btn_apply.clicked.connect(self._apply_steps)
        self.btn_home.clicked.connect(self._home_all)
        self.btn_estop.clicked.connect(self._estop)
        self.in_feed.valueChanged.connect(self._apply_feed_change)

        # Poll incoming messages from gantry child
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(100)

        # Jog wiring
        self._held_xy: Set[Tuple[int, int]] = set()
        self._z_dir: int = 0     # -1, 0, +1
        self._e_dir: int = 0     # -1, 0, +1
        self._jog_timer = QTimer(self)
        self._jog_timer.timeout.connect(self._tick_jog)

        self._wire_jog()

    # ------------------------------- UI slots ---------------------------------

    def _apply_steps(self) -> None:
        try:
            xy = float(self.in_xy.text())
            z = float(self.in_z.text())
            e = float(self.in_e.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid", "Enter numeric step sizes.")
            return
        self.q_gui_to_gantry.put({"type": "set_steps", "xy_step": xy, "z_step": z, "e_step": e})

    def _apply_feed_change(self, val: int) -> None:
        self.q_gui_to_gantry.put({"type": "set_feed", "feed_mm_min": int(val)})

    def _home_all(self) -> None:
        self.q_gui_to_gantry.put({"type": "home_all"})

    def _estop(self) -> None:
        # Clear held inputs and stop jog generation
        self._held_xy.clear()
        self._z_dir = 0
        self._e_dir = 0
        self._jog_timer.stop()
        stamp = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        self.msg.setText(f"[{stamp}] E-STOP pressed — inputs cleared.")

    # ----------------------------- Jog mechanics ------------------------------

    def _wire_jog(self) -> None:
        # XY
        def bind_xy(btn, dx, dy):
            btn.pressed.connect(lambda dx=dx, dy=dy: self._xy_press(dx, dy))
            btn.released.connect(lambda dx=dx, dy=dy: self._xy_release(dx, dy))

        bind_xy(self.btn_up, 0, +1)
        bind_xy(self.btn_dn, 0, -1)
        bind_xy(self.btn_le, -1, 0)
        bind_xy(self.btn_ri, +1, 0)
        bind_xy(self.btn_ul, -1, +1)
        bind_xy(self.btn_ur, +1, +1)
        bind_xy(self.btn_dl, -1, -1)
        bind_xy(self.btn_dr, +1, -1)

        # Center: Home convenience
        self.btn_ct.clicked.connect(self._home_all)

        # Z
        self.btn_zp.pressed.connect(lambda: self._z_set(+1))
        self.btn_zp.released.connect(lambda: self._z_set(0))
        self.btn_zm.pressed.connect(lambda: self._z_set(-1))
        self.btn_zm.released.connect(lambda: self._z_set(0))

        # E
        self.btn_ep.pressed.connect(lambda: self._e_set(+1))
        self.btn_ep.released.connect(lambda: self._e_set(0))
        self.btn_em.pressed.connect(lambda: self._e_set(-1))
        self.btn_em.released.connect(lambda: self._e_set(0))

    def _xy_press(self, dx: int, dy: int):
        self._held_xy.add((dx, dy))
        if not self._jog_timer.isActive():
            self._jog_timer.start(50)  # ~20 Hz

        # Small bump on press for responsiveness
        self._emit_input("xy_motion", (dx, dy))

    def _xy_release(self, dx: int, dy: int):
        self._held_xy.discard((dx, dy))
        if not self._held_xy and self._z_dir == 0 and self._e_dir == 0:
            self._jog_timer.stop()

    def _z_set(self, sgn: int):
        self._z_dir = sgn
        if sgn != 0 and not self._jog_timer.isActive():
            self._jog_timer.start(50)
        if sgn != 0:
            # small bump on press
            self._emit_input("z_motion", (0.0, float(sgn)))

    def _e_set(self, sgn: int):
        self._e_dir = sgn
        if sgn != 0 and not self._jog_timer.isActive():
            self._jog_timer.start(50)
        if sgn != 0:
            # small bump on press
            lt = float(sgn < 0)  # left trigger
            rt = float(sgn > 0)  # right trigger
            self._emit_input("e_motion", (lt, rt))

    def _tick_jog(self):
        # Aggregate XY
        jx = jy = 0
        for dx, dy in self._held_xy:
            jx += dx
            jy += dy
        if jx or jy:
            self._emit_input("xy_motion", (float(jx), float(jy)))

        # Z
        if self._z_dir:
            self._emit_input("z_motion", (0.0, float(self._z_dir)))

        # E (expects (lt, rt) floats)
        if self._e_dir:
            lt = float(self._e_dir < 0)
            rt = float(self._e_dir > 0)
            self._emit_input("e_motion", (lt, rt))

        # Nothing held? stop timer
        if not (self._held_xy or self._z_dir or self._e_dir):
            self._jog_timer.stop()

    # ------------------------------ IPC helpers -------------------------------

    def _emit_input(self, cmd: str, value):
        if not cmd:
            return
        self.q_ctrl_to_gantry.put({"type": "input", "cmd": cmd, "value": value})

    def _poll(self) -> None:
        # Drain gantry->GUI messages
        try:
            while True:
                msg = self.q_gantry_to_gui.get_nowait()
                if not isinstance(msg, dict):
                    continue
                typ = msg.get("type")
                if typ == "state":
                    self._apply_state(msg)
                elif typ == "message":
                    stamp = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
                    self.msg.setText(f"[{stamp}] {msg.get('text','')}")
                # 'controller_state' not expected here (no external controller), but harmless
        except queue.Empty:
            pass

    def _apply_state(self, s: Dict) -> None:
        x = float(s.get("x", 0.0))
        y = float(s.get("y", 0.0))
        z = float(s.get("z", 0.0))
        e = float(s.get("e", 0.0))
        self._xy_point.setData([x], [y])
        self.lab_x.setText(f"{x:.3f}")
        self.lab_y.setText(f"{y:.3f}")
        self.lab_z.setText(f"{z:.3f}")
        self.lab_e.setText(f"{e:.3f}")

        # quick qualitative Z bar: wrap every 10 mm
        self.z_bar.setValue(int((z % 10.0) / 10.0 * 1000))

        # reflect step sizes/feed into inputs to keep UI in sync
        try:
            self.in_xy.setText(f"{float(s.get('xy_step', float(self.in_xy.text()))):.3f}")
            self.in_z.setText(f"{float(s.get('z_step', float(self.in_z.text()))):.3f}")
            self.in_e.setText(f"{float(s.get('e_step', float(self.in_e.text()))):.3f}")
        except Exception:
            pass
        self.in_feed.blockSignals(True)
        self.in_feed.setValue(int(s.get("feed", self.in_feed.value())))
        self.in_feed.blockSignals(False)

    # ------------------------------ shutdown ----------------------------------

    def closeEvent(self, ev) -> None:
        try:
            self.timer.stop()
            self._jog_timer.stop()
        except Exception:
            pass
        try:
            self.p_gantry.terminate()
        except Exception:
            pass
        super().closeEvent(ev)


# --------------------------------- boot --------------------------------------

def _launch(simulate: bool):
    app = QApplication(sys.argv)
    w = GantryGUI(simulate=simulate)
    w.resize(1000, 600)
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    mp.freeze_support()
    try:
        mp.set_start_method("spawn")
    except RuntimeError:
        pass

    parser = argparse.ArgumentParser(description="GUI-Only Gantry Jog Controller")
    parser.add_argument("--simulate", action="store_true",
                        help="Run with simulator backend (no serial board).")
    args = parser.parse_args()
    _launch(simulate=args.simulate)
