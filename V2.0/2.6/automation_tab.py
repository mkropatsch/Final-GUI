from __future__ import annotations

from dataclasses import dataclass

from PyQt5.QtCore import Qt, QRectF, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QBrush
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class PlatePreset:
    name: str
    rows: int
    cols: int


PLATE_PRESETS = {
    "12": PlatePreset("12", 3, 4),
    "24": PlatePreset("24", 4, 6),
    "48": PlatePreset("48", 6, 8),
    "96": PlatePreset("96", 8, 12),
    "Custom": PlatePreset("Custom", 4, 6),
}


class WellPlatePreview(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.rows = 4
        self.cols = 6
        self.highlight_index: tuple[int, int] | None = None
        self.setMinimumSize(520, 420)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_plate_layout(self, rows: int, cols: int) -> None:
        self.rows = max(1, int(rows))
        self.cols = max(1, int(cols))
        self.update()

    def set_highlight(self, row: int | None = None, col: int | None = None) -> None:
        self.highlight_index = None if row is None or col is None else (row, col)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        bg = QColor("#121821")
        panel = QColor("#182433")
        panel_edge = QColor("#31465f")
        inner_edge = QColor("#46627f")
        well_fill = QColor("#223247")
        well_edge = QColor("#6c89a8")
        highlight_fill = QColor("#4fc3f7")
        text_color = QColor("#d8e2ee")

        painter.fillRect(self.rect(), bg)

        w = self.width()
        h = self.height()
        outer_margin = 28

        plate_rect = QRectF(
            outer_margin,
            outer_margin,
            max(10, w - 2 * outer_margin),
            max(10, h - 2 * outer_margin),
        )

        painter.setPen(QPen(panel_edge, 2.2))
        painter.setBrush(QBrush(panel))
        painter.drawRoundedRect(plate_rect, 18, 18)

        inner_margin = 18
        inner_rect = plate_rect.adjusted(inner_margin, inner_margin, -inner_margin, -inner_margin)
        painter.setPen(QPen(inner_edge, 1.4))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(inner_rect, 12, 12)

        grid_margin = 26
        grid_rect = inner_rect.adjusted(grid_margin, grid_margin, -grid_margin, -grid_margin)

        if self.rows < 1 or self.cols < 1 or grid_rect.width() <= 0 or grid_rect.height() <= 0:
            return

        step_x = grid_rect.width() / self.cols
        step_y = grid_rect.height() / self.rows
        diameter = min(step_x, step_y) * 0.68

        start_x = grid_rect.left() + step_x / 2
        start_y = grid_rect.top() + step_y / 2

        painter.setPen(QPen(well_edge, 1.2))

        for r in range(self.rows):
            for c in range(self.cols):
                cx = start_x + c * step_x
                cy = start_y + r * step_y
                well_rect = QRectF(cx - diameter / 2, cy - diameter / 2, diameter, diameter)

                if self.highlight_index == (r, c):
                    painter.setBrush(QBrush(highlight_fill))
                    painter.setPen(QPen(QColor("#bcecff"), 1.6))
                else:
                    painter.setBrush(QBrush(well_fill))
                    painter.setPen(QPen(well_edge, 1.2))

                painter.drawEllipse(well_rect)

        # small row/column labels
        label_font = QFont()
        label_font.setPointSize(10)
        painter.setFont(label_font)
        painter.setPen(QPen(text_color))

        for c in range(self.cols):
            cx = start_x + c * step_x
            painter.drawText(QRectF(cx - 12, grid_rect.top() - 22, 24, 18), Qt.AlignCenter, str(c + 1))

        for r in range(self.rows):
            cy = start_y + r * step_y
            painter.drawText(QRectF(grid_rect.left() - 24, cy - 9, 18, 18), Qt.AlignCenter, chr(65 + (r % 26)))


class AutomationTab(QWidget):
    update_requested = pyqtSignal(dict)
    start_requested = pyqtSignal(dict)
    stop_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._build_ui()
        self._apply_plate_preset("24")

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        # ---------------- left: preview ----------------
        preview_group = QGroupBox("Well Plate Preview")
        preview_group.setStyleSheet(
            """
            QGroupBox::title {
                font-size: 18px;
                font-weight: bold;
                color: #ffffff;
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 6px;
            }
            """
        )
        preview_layout = QVBoxLayout(preview_group)
        preview_layout.setContentsMargins(12, 12, 12, 12)
        preview_layout.setSpacing(10)

        self.preview = WellPlatePreview()
        preview_layout.addWidget(self.preview, 1)

        self.preview_info = QLabel("4 rows × 6 columns  •  24 wells")
        self.preview_info.setStyleSheet("color: #cfd8e3; font-size: 14px; font-weight: 600;")
        self.preview_info.setAlignment(Qt.AlignCenter)
        preview_layout.addWidget(self.preview_info)

        root.addWidget(preview_group, 3)

        # ---------------- right: controls ----------------
        right_col = QVBoxLayout()
        right_col.setSpacing(10)
        root.addLayout(right_col, 2)

        plate_group = QGroupBox("Plate Setup")
        plate_group.setStyleSheet(
            "QGroupBox::title { font-size: 16px; font-weight: bold; color: #ffffff; padding: 0 5px; }"
        )
        plate_form = QFormLayout(plate_group)
        plate_form.setSpacing(8)

        top_row = QHBoxLayout()
        self.cmb_plate = QComboBox()
        self.cmb_plate.addItems(["12", "24", "48", "96", "Custom"])
        self.cmb_plate.setMinimumWidth(140)
        self.btn_plate_update = QPushButton("Update")
        top_row.addWidget(self.cmb_plate, 1)
        top_row.addWidget(self.btn_plate_update)

        self.spn_rows = QSpinBox()
        self.spn_rows.setRange(1, 26)
        self.spn_cols = QSpinBox()
        self.spn_cols.setRange(1, 24)

        self.lab_total = QLabel("24")
        self.lab_total.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lab_plate_note = QLabel("Select a preset or choose Custom to enter your own rows/columns.")
        self.lab_plate_note.setWordWrap(True)
        self.lab_plate_note.setStyleSheet("color: #aeb9c8;")

        plate_form.addRow("Well plate", top_row)
        plate_form.addRow("Rows", self.spn_rows)
        plate_form.addRow("Columns", self.spn_cols)
        plate_form.addRow("Total wells", self.lab_total)
        plate_form.addRow(self.lab_plate_note)

        right_col.addWidget(plate_group)

        auto_group = QGroupBox("Automation Inputs")
        auto_group.setStyleSheet(
            "QGroupBox::title { font-size: 16px; font-weight: bold; color: #ffffff; padding: 0 5px; }"
        )
        auto_form = QFormLayout(auto_group)
        auto_form.setSpacing(8)

        self.in_dx = QLineEdit()
        self.in_dy = QLineEdit()
        self.in_dz = QLineEdit()
        self.in_wait = QLineEdit()

        self.in_dx.setPlaceholderText("ΔX")
        self.in_dy.setPlaceholderText("ΔY")
        self.in_dz.setPlaceholderText("ΔZ")
        self.in_wait.setPlaceholderText("Wait time")

        self.chk_serpentine = QCheckBox("Serpentine")
        self.chk_serpentine.setChecked(True)

        auto_form.addRow("ΔX", self.in_dx)
        auto_form.addRow("ΔY", self.in_dy)
        auto_form.addRow("ΔZ", self.in_dz)
        auto_form.addRow("Wait (s)", self.in_wait)
        auto_form.addRow(self.chk_serpentine)

        right_col.addWidget(auto_group)

        status_group = QGroupBox("Routine Status")
        status_group.setStyleSheet(
            "QGroupBox::title { font-size: 16px; font-weight: bold; color: #ffffff; padding: 0 5px; }"
        )
        status_layout = QVBoxLayout(status_group)
        status_layout.setSpacing(8)

        self.lab_status = QLabel("Idle")
        self.lab_status.setStyleSheet("font-size: 18px; font-weight: 700; color: #d8e2ee;")

        self.lab_current = QLabel("Current well: —")
        self.lab_current.setStyleSheet("color: #cfd8e3;")

        self.lab_phase = QLabel("Phase: —")
        self.lab_phase.setStyleSheet("color: #cfd8e3;")

        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("Start Routine")
        self.btn_stop = QPushButton("Stop Routine")
        self.btn_stop.setStyleSheet(
            "QPushButton { background-color: #6a3030; color: white; font-weight: 600; }"
            "QPushButton:hover { background-color: #814040; }"
        )
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)

        status_layout.addWidget(self.lab_status)
        status_layout.addWidget(self.lab_current)
        status_layout.addWidget(self.lab_phase)
        status_layout.addLayout(btn_row)

        right_col.addWidget(status_group)

        right_col.addStretch()

        self.btn_plate_update.clicked.connect(self._on_update_clicked)
        self.cmb_plate.currentTextChanged.connect(self._on_plate_changed)
        self.spn_rows.valueChanged.connect(self._on_geometry_changed)
        self.spn_cols.valueChanged.connect(self._on_geometry_changed)
        self.btn_start.clicked.connect(self._on_start_clicked)
        self.btn_stop.clicked.connect(self._on_stop_clicked)

    def _on_plate_changed(self, plate_name: str) -> None:
        custom = plate_name == "Custom"
        self.spn_rows.setEnabled(custom)
        self.spn_cols.setEnabled(custom)

        if not custom:
            self._apply_plate_preset(plate_name)

    def _apply_plate_preset(self, plate_name: str) -> None:
        preset = PLATE_PRESETS.get(plate_name, PLATE_PRESETS["24"])

        self.spn_rows.blockSignals(True)
        self.spn_cols.blockSignals(True)
        self.spn_rows.setValue(preset.rows)
        self.spn_cols.setValue(preset.cols)
        self.spn_rows.blockSignals(False)
        self.spn_cols.blockSignals(False)

        custom = plate_name == "Custom"
        self.spn_rows.setEnabled(custom)
        self.spn_cols.setEnabled(custom)

        self._update_preview_and_labels()

    def _on_geometry_changed(self) -> None:
        if self.cmb_plate.currentText() == "Custom":
            self._update_preview_and_labels()

    def _update_preview_and_labels(self) -> None:
        rows = self.spn_rows.value()
        cols = self.spn_cols.value()
        total = rows * cols

        self.preview.set_plate_layout(rows, cols)
        self.preview_info.setText(f"{rows} rows × {cols} columns  •  {total} wells")
        self.lab_total.setText(str(total))

    def _on_update_clicked(self) -> None:
        plate_name = self.cmb_plate.currentText()
        if plate_name != "Custom":
            self._apply_plate_preset(plate_name)
        else:
            self._update_preview_and_labels()

        self.lab_status.setText("Preset updated")
        self.lab_phase.setText("Phase: Ready")
        self.update_requested.emit(self.get_config())

    def _on_start_clicked(self) -> None:
        self.lab_status.setText("Running")
        self.lab_phase.setText("Phase: Starting")
        self.start_requested.emit(self.get_config())

    def _on_stop_clicked(self) -> None:
        self.lab_status.setText("Stopped")
        self.lab_phase.setText("Phase: Stopped")
        self.stop_requested.emit()

    def set_runtime_status(
        self,
        status: str,
        current_well: str | None = None,
        phase: str | None = None,
        highlight_row: int | None = None,
        highlight_col: int | None = None,
    ) -> None:
        self.lab_status.setText(status)
        self.lab_current.setText(f"Current well: {current_well or '—'}")
        self.lab_phase.setText(f"Phase: {phase or '—'}")
        self.preview.set_highlight(highlight_row, highlight_col)

    def get_config(self) -> dict:
        return {
            "plate_type": self.cmb_plate.currentText(),
            "rows": self.spn_rows.value(),
            "cols": self.spn_cols.value(),
            "dx": self.in_dx.text().strip(),
            "dy": self.in_dy.text().strip(),
            "dz": self.in_dz.text().strip(),
            "wait_s": self.in_wait.text().strip(),
            "serpentine": self.chk_serpentine.isChecked(),
        }


if __name__ == "__main__":
    import sys
    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv)
    w = AutomationTab()
    w.resize(1200, 760)
    w.show()
    sys.exit(app.exec_())
