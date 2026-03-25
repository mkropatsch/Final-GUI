# z_tester.py
# Single-axis tester locked to Z axis (works with dual-Z motor slots).
# Jog Z up/down, set zero, and return to Z=0.
# Place next to gantry.py and run:  python z_tester.py

import sys
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QApplication, QWidget, QGridLayout, QGroupBox, QFormLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QSpinBox
)

# Import your board backends
from gantry import StepperControlBoard, StepperControlBoardSimulator

def try_board():
    """Try to open the real board; if that fails, use simulator."""
    try:
        return StepperControlBoard()
    except Exception as e:
        print(f"[z_tester] No board detected, using simulator: {e}")
        return StepperControlBoardSimulator()

class ZTester(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Z Tester (Jog / Zero / Go to 0)")
        self.resize(400, 240)

        self.board = try_board()
        self.step_mm = 0.20
        self.feed = 1200

        # ---------- UI ----------
        grid = QGridLayout(self)

        # Controls
        box = QGroupBox("Controls")
        form = QFormLayout(box)

        self.in_step = QLineEdit(f"{self.step_mm:.3f}")
        self.in_feed = QSpinBox()
        self.in_feed.setRange(100, 20000)
        self.in_feed.setValue(self.feed)
        self.in_feed.valueChanged.connect(self._on_feed)

        row = QHBoxLayout()
        self.btn_zm = QPushButton("Z −")
        self.btn_zp = QPushButton("Z +")
        self.btn_zm.clicked.connect(lambda: self._jog(sign=-1))
        self.btn_zp.clicked.connect(lambda: self._jog(sign=+1))
        row.addWidget(self.btn_zm)
        row.addWidget(self.btn_zp)

        row2 = QHBoxLayout()
        self.btn_zero = QPushButton("Zero here (G92 Z0)")
        self.btn_go0 = QPushButton("Go to Z=0")
        self.btn_zero.clicked.connect(self._zero_here)
        self.btn_go0.clicked.connect(self._goto_zero)
        row2.addWidget(self.btn_zero)
        row2.addWidget(self.btn_go0)

        form.addRow("Step (mm)", self.in_step)
        form.addRow("Feed (mm/min)", self.in_feed)
        form.addRow(row)
        form.addRow(row2)

        # Position readout
        rd = QGroupBox("Position (mm)")
        rform = QFormLayout(rd)
        self.lab_z = QLabel("0.000")
        rform.addRow("Z", self.lab_z)

        grid.addWidget(box, 0, 0)
        grid.addWidget(rd, 0, 1)

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

    def _jog(self, sign: int):
        """Relative jog of Z by step size."""
        step = self._get_step() * float(sign)
        self.board.jog({"Z": step}, self.feed)

    def _zero_here(self):
        """Soft-zero Z axis where it is now."""
        if hasattr(self.board, "_send"):
            self.board._send("G92 Z0")
            self.board._send("G91")
        else:
            if hasattr(self.board, "z"):
                self.board.z = 0.0

    def _goto_zero(self):
        """Absolute move to Z=0, then back to relative mode."""
        if hasattr(self.board, "_send"):
            self.board._send("G90")
            self.board._send(f"G1 F{self.feed} Z0")
            self.board._send("G91")
        else:
            if hasattr(self.board, "z"):
                self.board.z = 0.0

    def _tick(self):
        """Poll for position and update label."""
        try:
            self.board.request_data()
        except Exception:
            pass
        z = getattr(self.board, "z", 0.0)
        self.lab_z.setText(f"{z:.3f}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = ZTester()
    w.show()
    sys.exit(app.exec_())
