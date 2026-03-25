# gui_preview.py
# Preview-only GUI for Stage Control (no hardware, no multiprocessing).
# Requires: pip install PyQt5 pyqtgraph qdarkstyle (qdarkstyle optional)

from __future__ import annotations
import sys
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGridLayout, QGroupBox, QFormLayout,
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QComboBox, QSlider
)
import pyqtgraph as pg

class StagePreview(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Stage Control — GUI Preview")
        self.resize(1100, 720)

        # ----------------------------- root layout -----------------------------
        root = QWidget(self)
        self.setCentralWidget(root)
        grid = QGridLayout(root)
        grid.setColumnStretch(0, 3)   # big XY plot
        grid.setColumnStretch(1, 2)   # controls
        grid.setColumnStretch(2, 2)   # jog panel

        # --------------------------- XY plot group -----------------------------
        self.xy_group = QGroupBox("XY Position (mm)")
        xy_v = QVBoxLayout(self.xy_group)
        self.xy_plot = pg.PlotWidget()
        self.xy_plot.setAspectLocked(True)
        self.xy_plot.showGrid(x=True, y=True, alpha=0.25)
        self.xy_plot.setLabel("left", "Y (mm)")
        self.xy_plot.setLabel("bottom", "X (mm)")
       # self.xy_plot.invertY(True)  # so +Y is up visually
        self._xy_point = self.xy_plot.plot([0], [0], pen=None, symbol="o", symbolSize=10)
        xy_v.addWidget(self.xy_plot)
        grid.addWidget(self.xy_group, 0, 0, 3, 1)

        # --------------------------- State group -------------------------------
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

        # --------------------------- Controls group ----------------------------
        self.ctrl_group = QGroupBox("Step & Speed")
        form = QFormLayout(self.ctrl_group)

        # presets
        row_presets = QHBoxLayout()
        self.cmb_preset = QComboBox()
        self.cmb_preset.addItems(["Fine", "Medium", "Coarse"])
        row_presets.addWidget(QLabel("Preset"))
        row_presets.addWidget(self.cmb_preset)
        row_presets.addStretch()

        # custom sizes
        self.in_xy = QLineEdit("0.10")
        self.in_z  = QLineEdit("0.05")
        self.in_e  = QLineEdit("0.02")

        # feed slider (safe preview range)
        self.sld_feed = QSlider(Qt.Horizontal)
        self.sld_feed.setMinimum(100)
        self.sld_feed.setMaximum(1200)
        self.sld_feed.setValue(400)
        self.sld_feed.setSingleStep(50)
        self.sld_feed.setPageStep(100)
        self.lab_feed = QLabel(f"{self.sld_feed.value()} mm/min")
        self.lab_feed.setAlignment(Qt.AlignRight)

        form.addRow(row_presets)
        form.addRow("XY step (mm/tick)", self.in_xy)
        form.addRow("Z step (mm/tick)",  self.in_z)
        form.addRow("E step (mm/tick)",  self.in_e)

        row_feed = QHBoxLayout()
        row_feed.addWidget(self.sld_feed, stretch=1)
        row_feed.addWidget(self.lab_feed)
        form.addRow("Feed", row_feed)

        # Home & E-stop
        row_act = QHBoxLayout()
        self.btn_home = QPushButton("Home all (G28)")
        self.btn_estop = QPushButton("E-STOP")
        self.btn_estop.setStyleSheet("QPushButton{background:#b51f1f;color:white;font-weight:bold}")
        row_act.addWidget(self.btn_home)
        row_act.addStretch()
        row_act.addWidget(self.btn_estop)
        form.addRow(row_act)

        # --------------------------- Jog panel --------------------------------
        self.jog_group = QGroupBox("Manual Control")
        j = QGridLayout(self.jog_group)

        def mk(txt, w=48, h=36):
            b = QPushButton(txt); b.setFixedSize(w, h); return b

        # XY pad
        self.btn_ul = mk("↖"); self.btn_up = mk("↑"); self.btn_ur = mk("↗")
        self.btn_le = mk("←"); self.btn_ct = mk("•"); self.btn_ri = mk("→")
        self.btn_dl = mk("↙"); self.btn_dn = mk("↓"); self.btn_dr = mk("↘")
        # Z and E
        self.btn_zp = mk("Z↑"); self.btn_zm = mk("Z↓")
        self.btn_ep = mk("E+"); self.btn_em = mk("E−")

        # place pad (3x3)
        j.addWidget(self.btn_ul, 0, 0); j.addWidget(self.btn_up, 0, 1); j.addWidget(self.btn_ur, 0, 2)
        j.addWidget(self.btn_le, 1, 0); j.addWidget(self.btn_ct, 1, 1); j.addWidget(self.btn_ri, 1, 2)
        j.addWidget(self.btn_dl, 2, 0); j.addWidget(self.btn_dn, 2, 1); j.addWidget(self.btn_dr, 2, 2)

        # Z column
        j.addWidget(self.btn_zp, 0, 3)
        j.addWidget(self.btn_zm, 2, 3)

        # E column
        j.addWidget(self.btn_ep, 0, 4)
        j.addWidget(self.btn_em, 2, 4)

        # hint
        hint = QLabel("Click = step   |   Hold = continuous (preview only)")
        hint.setStyleSheet("color: #8aa; font-size: 11px;")
        j.addWidget(hint, 3, 0, 1, 5)

        # --------------------------- Messages ---------------------------------
        self.msg_group = QGroupBox("Messages")
        vmsg = QVBoxLayout(self.msg_group)
        self.lab_msg = QLabel("Ready.")
        self.lab_msg.setWordWrap(True)
        vmsg.addWidget(self.lab_msg)

        # Place right-side column
        right_col = QVBoxLayout()
        right_col.addWidget(self.state_group)
        right_col.addWidget(self.ctrl_group)
        right_col.addWidget(self.msg_group, stretch=1)
        grid.addLayout(right_col, 0, 1, 3, 1)
        grid.addWidget(self.jog_group, 0, 2, 2, 1)

        # --------------------------- Preview "sim" -----------------------------
        self.x = 0.0; self.y = 0.0; self.z = 0.0; self.e = 0.0
        self._jx = 0.0; self._jy = 0.0; self._jz = 0.0; self._je = 0.0
        self._tick = QTimer(self); self._tick.setInterval(16)   # ~60 Hz
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

        # events
        self.sld_feed.valueChanged.connect(lambda v: self.lab_feed.setText(f"{v} mm/min"))
        self.cmb_preset.currentTextChanged.connect(self._apply_preset)

        # connect jog buttons for preview motion
        self._wire_jog()

        # apply initial preset
        self._apply_preset(self.cmb_preset.currentText())

    # --------------------------- jog wiring (preview) --------------------------
    def _wire_jog(self):
        # XY directions: note: plot is inverted Y so up is negative delta visually;
        # we keep convention that "UP button" moves +Y in machine coordinates.
        def press_xy(dx, dy):
            self._jx = float(dx); self._jy = float(dy)
            self._step_xy(dx, dy)

        def release_xy():
            self._jx = self._jy = 0.0

        map_xy = [
            (self.btn_up,  (0, +1)), (self.btn_dn,  (0, -1)),
            (self.btn_le, (-1,  0)), (self.btn_ri,  (+1, 0)),
            (self.btn_ul, (-1, +1)), (self.btn_ur, (+1, +1)),
            (self.btn_dl, (-1, -1)), (self.btn_dr, (+1, -1)),
        ]
        for btn, (dx, dy) in map_xy:
            btn.pressed.connect(lambda dx=dx, dy=dy: press_xy(dx, dy))
            btn.released.connect(release_xy)

        # Z buttons
        self.btn_zp.pressed.connect(lambda: self._start_z(+1))
        self.btn_zp.released.connect(lambda: self._stop_z())
        self.btn_zm.pressed.connect(lambda: self._start_z(-1))
        self.btn_zm.released.connect(lambda: self._stop_z())

        # E buttons
        self.btn_ep.pressed.connect(lambda: self._start_e(+1))
        self.btn_ep.released.connect(lambda: self._stop_e())
        self.btn_em.pressed.connect(lambda: self._start_e(-1))
        self.btn_em.released.connect(lambda: self._stop_e())

        # Home & E-stop
        self.btn_ct.clicked.connect(self._go_home)   # center dot -> home
        self.btn_home.clicked.connect(self._go_home) # toolbar home -> home
        self.btn_estop.clicked.connect(self._estop)

    # --------------------------- preview helpers -------------------------------
    def _apply_preset(self, name: str):
        if name == "Fine":
            self.in_xy.setText("0.01"); self.in_z.setText("0.005"); self.in_e.setText("0.005")
        elif name == "Medium":
            self.in_xy.setText("0.10"); self.in_z.setText("0.05");  self.in_e.setText("0.02")
        else:
            self.in_xy.setText("1.00"); self.in_z.setText("0.20");  self.in_e.setText("0.10")

    def _step_xy(self, dx, dy):
        try:
            step = float(self.in_xy.text())
        except ValueError:
            step = 0.1
        self.x += dx * step
        self.y += dy * step

    def _start_z(self, sgn):
        try:
            step = float(self.in_z.text())
        except ValueError:
            step = 0.05
        self._jz = float(sgn)
        self.z += sgn * step   # immediate bump

    def _stop_z(self):
        self._jz = 0.0

    def _start_e(self, sgn):
        try:
            step = float(self.in_e.text())
        except ValueError:
            step = 0.02
        self._je = float(sgn)
        self.e += sgn * step

    def _stop_e(self):
        self._je = 0.0

    def _estop(self):
        self._jx = self._jy = self._jz = self._je = 0.0
        self._set_msg("E-STOP pressed (preview only)")

    def _set_msg(self, text: str):
        self.lab_msg.setText(text)

    def _refresh_state(self):
        """Update the plot and labels to current coordinates."""
        self._xy_point.setData([self.x], [self.y])
        self.lab_x.setText(f"{self.x:.3f}")
        self.lab_y.setText(f"{self.y:.3f}")
        self.lab_z.setText(f"{self.z:.3f}")
        self.lab_e.setText(f"{self.e:.3f}")

    def _go_home(self):
        """Snap all axes to 0 in the preview (like homing)."""
        self.x = self.y = self.z = self.e = 0.0
        self._refresh_state()
        self._set_msg("Homed to (0,0,0,0) — preview")

    # --------------------------- preview tick ----------------------------------
    def _on_tick(self):
        # continuous jogging while holding buttons
        try:
            xy_step = float(self.in_xy.text())
            z_step  = float(self.in_z.text())
            e_step  = float(self.in_e.text())
        except ValueError:
            xy_step, z_step, e_step = 0.1, 0.05, 0.02

        # scale by a modest factor tied to feed slider (still safe)
        speed_scale = self.sld_feed.value() / 600.0  # ~0.17..2.0
        if self._jx or self._jy:
            self.x += self._jx * xy_step * 0.25 * speed_scale
            self.y += self._jy * xy_step * 0.25 * speed_scale
        if self._jz:
            self.z += self._jz * z_step * 0.3 * speed_scale
        if self._je:
            self.e += self._je * e_step * 0.3 * speed_scale

        # update plot + labels
        self._refresh_state()


def main():
    app = QApplication(sys.argv)
    # Dark style is optional; comment out if you don't have qdarkstyle.
    try:
        import qdarkstyle
        app.setStyleSheet(qdarkstyle.load_stylesheet_pyqt5())
    except Exception:
        pass
    win = StagePreview()
    win.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
