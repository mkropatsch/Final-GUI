## Two axes control - XY

# xy_tester.py
# Two-axis tester for X & Y: jog, zero-here (both axes), and go-to (0,0).
# Place next to gantry.py and run:  python xy_tester.py

import sys
from typing import Dict

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QApplication, QWidget, QGridLayout, QGroupBox, QFormLayout, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QLineEdit, QSpinBox
)

# Import your board backends
from gantry import StepperControlBoard, StepperControlBoardSimulator

def try_board():
    """Try to open the real board; if that fails, use simulator."""
    try:
        return StepperControlBoard()
    except Exception as e:
        print(f"[xy_tester] No board detected, using simulator: {e}")
        return StepperControlBoardSimulator()

class XYTester(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("XY Tester (Jog / Zero / Go to (0,0))")
        self.resize(600, 320)

        self.board = try_board()
        self.step_mm = 0.20   # per-axis step for jogs
        self.feed = 1500      # mm/min

        # ---------- UI ----------
        root = QGridLayout(self)

        # Controls
        box = QGroupBox("Controls")
        f = QFormLayout(box)

        self.in_step = QLineEdit(f"{self.step_mm:.3f}")
        self.in_feed = QSpinBox()
        self.in_feed.setRange(100, 20000)
        self.in_feed.setValue(self.feed)
        self.in_feed.valueChanged.connect(self._on_feed)

        # Jog rows
        row_x = QHBoxLayout()
        self.btn_xm = QPushButton("X −")
        self.btn_xp = QPushButton("X +")
        self.btn_xm.clicked.connect(lambda: self._jog(dx=-1, dy=0))
        self.btn_xp.clicked.connect(lambda: self._jog(dx=+1, dy=0))
        row_x.addWidget(self.btn_xm)
        row_x.addWidget(self.btn_xp)

        row_y = QHBoxLayout()
        self.btn_ym = QPushButton("Y −")
        self.btn_yp = QPushButton("Y +")
        self.btn_ym.clicked.connect(lambda: self._jog(dx=0, dy=-1))
        self.btn_yp.clicked.connect(lambda: self._jog(dx=0, dy=+1))
        row_y.addWidget(self.btn_ym)
        row_y.addWidget(self.btn_yp)

        # Diagonals (optional but handy)
        row_d = QHBoxLayout()
        self.btn_d1 = QPushButton("↖ X− Y+")
        self.btn_d2 = QPushButton("↗ X+ Y+")
        self.btn_d3 = QPushButton("↙ X− Y−")
        self.btn_d4 = QPushButton("↘ X+ Y−")
        self.btn_d1.clicked.connect(lambda: self._jog(dx=-1, dy=+1))
        self.btn_d2.clicked.connect(lambda: self._jog(dx=+1, dy=+1))
        self.btn_d3.clicked.connect(lambda: self._jog(dx=-1, dy=-1))
        self.btn_d4.clicked.connect(lambda: self._jog(dx=+1, dy=-1))
        row_d.addWidget(self.btn_d1)
        row_d.addWidget(self.btn_d2)
        row_d.addWidget(self.btn_d3)
        row_d.addWidget(self.btn_d4)

        # Zero & Go-to
        row_cmd = QHBoxLayout()
        self.btn_zero_xy = QPushButton("Zero here (G92 X0 Y0)")
        self.btn_go_00   = QPushButton("Go to (0,0)")
        self.btn_zero_xy.clicked.connect(self._zero_xy)
        self.btn_go_00.clicked.connect(self._go_to_00)
        row_cmd.addWidget(self.btn_zero_xy)
        row_cmd.addWidget(self.btn_go_00)

        f.addRow("Step (mm)", self.in_step)
        f.addRow("Feed (mm/min)", self.in_feed)
        f.addRow(row_x)
        f.addRow(row_y)
        f.addRow(row_d)
        f.addRow(row_cmd)

        # Readout
        rd = QGroupBox("Position (mm)")
        rform = QFormLayout(rd)
        self.lab_x = QLabel("0.000")
        self.lab_y = QLabel("0.000")
        self.lab_z = QLabel("0.000")
        self.lab_e = QLabel("0.000")
        rform.addRow("X", self.lab_x)
        rform.addRow("Y", self.lab_y)
        rform.addRow("Z", self.lab_z)
        rform.addRow("E", self.lab_e)

        root.addWidget(box, 0, 0)
        root.addWidget(rd, 0, 1)

        # Timer to refresh positions
        self.t = QTimer(self)
        self.t.setInterval(200)  # 5 Hz
        self.t.timeout.connect(self._tick)
        self.t.start()

    # ---------- Helpers ----------
    def _on_feed(self, val: int):
        self.feed = int(val)

    def _get_step(self) -> float:
        try:
            v = float(self.in_step.text())
            if v <= 0:
                raise ValueError
            return v
        except Exception:
            self.in_step.setText(f"{self.step_mm:.3f}")
            return self.step_mm

    def _jog(self, dx: int, dy: int):
        """Relative jog in G91: move X and/or Y by ±step at current feed."""
        step = self._get_step()
        axes: Dict[str, float] = {}
        if dx != 0:
            axes["X"] = float(dx) * step
        if dy != 0:
            axes["Y"] = float(dy) * step
        if axes:
            self.board.jog(axes, self.feed)

    def _zero_xy(self):
        """Soft-zero both axes where they are now (G92 X0 Y0), then return to relative mode."""
        if hasattr(self.board, "_send"):
            self.board._send("G92 X0 Y0")
            self.board._send("G91")  # ensure relative after
        else:
            # Simulator path
            if hasattr(self.board, "x"): self.board.x = 0.0
            if hasattr(self.board, "y"): self.board.y = 0.0

    def _go_to_00(self):
        """Absolute move to X=0, Y=0, then return to relative mode."""
        if hasattr(self.board, "_send"):
            self.board._send("G90")
            self.board._send(f"G1 F{self.feed} X0 Y0")
            self.board._send("G91")
        else:
            # Simulator: snap to 0,0
            if hasattr(self.board, "x"): self.board.x = 0.0
            if hasattr(self.board, "y"): self.board.y = 0.0

    def _tick(self):
        """Poll firmware/sim for position; refresh labels."""
        try:
            self.board.request_data()
        except Exception:
            pass
        x = getattr(self.board, "x", 0.0)
        y = getattr(self.board, "y", 0.0)
        z = getattr(self.board, "z", 0.0)
        e = getattr(self.board, "e", 0.0)
        self.lab_x.setText(f"{x:.3f}")
        self.lab_y.setText(f"{y:.3f}")
        self.lab_z.setText(f"{z:.3f}")
        self.lab_e.setText(f"{e:.3f}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = XYTester()
    w.show()
    sys.exit(app.exec_())
