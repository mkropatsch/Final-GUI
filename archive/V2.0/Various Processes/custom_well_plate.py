# Draws a custom well plate

import sys
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QSpinBox, QPushButton, QGroupBox
)
from PyQt5.QtGui import QPainter, QPen
from PyQt5.QtCore import Qt, QRectF


class WellPlatePreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = 4
        self.cols = 6
        self.setMinimumSize(340, 260)

    def set_plate_layout(self, rows, cols):
        self.rows = max(1, rows)
        self.cols = max(1, cols)
        self.update()  # trigger repaint

    def paintEvent(self, event):
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        # Background
        painter.fillRect(self.rect(), Qt.white)

        # Outer plate rectangle
        outer_margin = 20
        plate_rect = QRectF(
            outer_margin,
            outer_margin,
            w - 2 * outer_margin,
            h - 2 * outer_margin
        )

        painter.setPen(QPen(Qt.black, 2))
        painter.drawRoundedRect(plate_rect, 10, 10)

        # Inner plate border
        inner_margin = 8
        inner_rect = plate_rect.adjusted(
            inner_margin, inner_margin,
            -inner_margin, -inner_margin
        )
        painter.setPen(QPen(Qt.black, 1.2))
        painter.drawRoundedRect(inner_rect, 6, 6)

        # Usable area for wells
        grid_margin = 18
        grid_rect = inner_rect.adjusted(
            grid_margin, grid_margin,
            -grid_margin, -grid_margin
        )

        if self.rows < 1 or self.cols < 1:
            return

        # Step size between well centers
        step_x = grid_rect.width() / self.cols
        step_y = grid_rect.height() / self.rows

        # Diameter limited by whichever direction is tighter
        diameter = min(step_x, step_y) * 0.78

        # Center positions
        start_x = grid_rect.left() + step_x / 2
        start_y = grid_rect.top() + step_y / 2

        painter.setPen(QPen(Qt.black, 1.5))

        for r in range(self.rows):
            for c in range(self.cols):
                cx = start_x + c * step_x
                cy = start_y + r * step_y

                well_rect = QRectF(
                    cx - diameter / 2,
                    cy - diameter / 2,
                    diameter,
                    diameter
                )
                painter.drawEllipse(well_rect)


class WellPlateDemo(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Well Plate Preview Demo")
        self.resize(420, 380)

        main_layout = QVBoxLayout(self)

        # Controls group
        controls_group = QGroupBox("Custom Well Plate")
        controls_layout = QHBoxLayout()

        self.rows_spin = QSpinBox()
        self.rows_spin.setRange(1, 20)
        self.rows_spin.setValue(4)

        self.cols_spin = QSpinBox()
        self.cols_spin.setRange(1, 24)
        self.cols_spin.setValue(6)

        self.update_button = QPushButton("Update Preview")

        controls_layout.addWidget(QLabel("Rows:"))
        controls_layout.addWidget(self.rows_spin)
        controls_layout.addSpacing(10)
        controls_layout.addWidget(QLabel("Columns:"))
        controls_layout.addWidget(self.cols_spin)
        controls_layout.addSpacing(15)
        controls_layout.addWidget(self.update_button)

        controls_group.setLayout(controls_layout)

        # Preview widget
        self.preview = WellPlatePreview()

        main_layout.addWidget(controls_group)
        main_layout.addWidget(self.preview)

        # Button connection
        self.update_button.clicked.connect(self.update_preview)

    def update_preview(self):
        rows = self.rows_spin.value()
        cols = self.cols_spin.value()
        self.preview.set_plate_layout(rows, cols)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = WellPlateDemo()
    window.show()
    sys.exit(app.exec_())