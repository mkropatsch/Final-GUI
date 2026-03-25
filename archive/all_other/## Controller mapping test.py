# axis_tester.py
# Minimal single-axis tester: jog, zero-here, and go-to-zero.
# Place next to gantry.py (so it can import) and run:  python axis_tester.py

import sys
from typing import Dict

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QApplication, QWidget, QGridLayout, QGroupBox, QFormLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QSpinBox, QComboBox
)

# Import your board backends
from gantry import StepperControlBoard, StepperControlBoardSimulator

def try_board():
    """Try to open the real board; if that fails, use simulator."""
    try:
        return StepperControlBoard()
    except Exception as e:
        print(f"[axis_tester] No board detected, using simulator: {e}")
        return StepperControlBoardSimulator()

class AxisTester(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Single-Axis Tester (Jog / Zero / Go-to-0)")
        self.resize(520, 260)

        self.board = try_board()
        self.curr_axis = "X"
        self.step_mm = 0.20
        self.feed = 1500

        # ---------- UI ----------
        grid = QGridLayout(self)

        # Controls
        box = QGroupBox("Controls")
        form = QFormLayout(box)

        self.sel_axis = QComboBox()
        self.sel_axis.addItems(["X", "Y", "Z", "E"])
        self.sel_axis.currentTextChanged.connect(self._on_axis)

        self.in_step = QLineEdit(f"{self.step_mm:.3f}")
        self.in_feed = QSpinBox()
        self.in_feed.setRange(100, 12000)
        self.in_feed.setValue(self.feed)
        self.in_feed.valueChanged.connect(self._on_feed)

        row = QHBoxLayout()
        self.btn_jog_minus = QPushButton("⟵  Jog −")
        self.btn_jog_plus  = QPushButton("Jog +  ⟶")
        self.btn_jog_minus.clicked.connect(lambda: self._jog(sign=-1))
        self.btn_jog_plus.clicked.connect(lambda: self._jog(sign=+1))
        row.addWidget(self.btn_jog_minus)
        row.addWidget(self.btn_jog_plus)

        row2 = QHBoxLayout()
        self.btn_zero = QPushButton("Zero here (G92)")
        self.btn_goto0 = QPushButton("Go to 0")
        self.btn_zero.clicked.connect(self._zero_here)
        self.btn_goto0.clicked.connect(self._goto_zero)
        row2.addWidget(self.btn_zero)
        row2.addWidget(self.btn_goto0)

        form.addRow("Axis", self.sel_axis)
        form.addRow("Step (mm)", self.in_step)
        form.addRow("Feed (mm/min)", self.in_feed)
        form.addRow(row)
        form.addRow(row2)

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

        grid.addWidget(box, 0, 0)
        grid.addWidget(rd, 0, 1)

        # Timer to refresh positions
        self.t = QTimer(self)
        self.t.setInterval(200)  # 5 Hz
        self.t.timeout.connect(self._tick)
        self.t.start()

    # ---------- Helpers ----------
    def _on_axis(self, text: str):
        self.curr_axis = text.strip().upper()

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

    def _jog(self, sign: int):
        """Relative jog along selected axis by step size at current feed."""
        step = self._get_step() * float(sign)
        axes: Dict[str, float] = {}
        axes[self.curr_axis] = step
        self.board.jog(axes, self.feed)

    def _zero_here(self):
        """Soft-zero this axis: G92 Axis0 on real board; set attr to 0 in sim."""
        axis = self.curr_axis
        if hasattr(self.board, "_send"):
            self.board._send(f"G92 {axis}0")
            self.board._send("G91")  # back to relative
        else:
            if axis == "X": self.board.x = 0.0
            elif axis == "Y": self.board.y = 0.0
            elif axis == "Z": self.board.z = 0.0
            elif axis == "E": self.board.e = 0.0

    def _goto_zero(self):
        """Go to absolute 0 on this axis, then return to relative mode."""
        axis = self.curr_axis
        if hasattr(self.board, "_send"):
            self.board._send("G90")
            self.board._send(f"G1 F{self.feed} {axis}0")
            self.board._send("G91")
        else:
            if axis == "X": self.board.x = 0.0
            elif axis == "Y": self.board.y = 0.0
            elif axis == "Z": self.board.z = 0.0
            elif axis == "E": self.board.e = 0.0

    def _tick(self):
        """Poll the board for latest position; refresh labels."""
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
    w = AxisTester()
    w.show()
    sys.exit(app.exec_())
