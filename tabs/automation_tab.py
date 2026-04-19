from __future__ import annotations

import random as _random
from dataclasses import dataclass

import cv2
import numpy as np

from PyQt5.QtCore import Qt, QDateTime, QRectF, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QBrush, QPixmap, QImage
from tabs.calibration_dialog import ransac_circle
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
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QDialog,
    QTextEdit,
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


class WellInfoDialog(QDialog):
    info_saved = pyqtSignal(str, str)  # well_name, text

    def __init__(self, well_name: str, existing_text: str = "", parent=None):
        super().__init__(parent)
        self.well_name = well_name
        self.setWindowTitle(f"{well_name} — Information")
        self.resize(420, 320)
        self.setStyleSheet("""
            QDialog { background-color: #1a1f2b; }
            QLabel#title {
                color: #d8e2ee;
                font-size: 25px;
                font-weight: 700;
            }
            QTextEdit {
                background-color: #0e1117;
                color: #c8d0dc;
                border: 1px solid #3a4a5e;
                border-radius: 5px;
                font-size: 20px;
                padding: 6px;
            }
            QPushButton {
                background-color: #2a3445;
                color: #d0d7e2;
                border: 1px solid #3a4a5e;
                border-radius: 5px;
                padding: 6px 16px;
                font-size: 18px;
            }
            QPushButton:hover { background-color: #354055; }
            QPushButton#save {
                background-color: #1e4a2e;
                border-color: #3a8a5e;
                color: #aaeebb;
            }
            QPushButton#save:hover { background-color: #265a38; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        title = QLabel(f"{well_name}  —  Information")
        title.setObjectName("title")
        layout.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #3a4a5e;")
        layout.addWidget(sep)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("No information recorded for this well.")
        self.text_edit.setText(existing_text)
        self.text_edit.setReadOnly(True)
        layout.addWidget(self.text_edit, 1)

        btn_row = QHBoxLayout()
        self.btn_edit = QPushButton("Edit")
        self.btn_save = QPushButton("Save")
        self.btn_save.setObjectName("save")
        self.btn_save.setVisible(False)
        btn_close = QPushButton("Close")
        btn_row.addWidget(self.btn_edit)
        btn_row.addWidget(self.btn_save)
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        self.btn_edit.clicked.connect(self._on_edit)
        self.btn_save.clicked.connect(self._on_save)
        btn_close.clicked.connect(self.close)

    def _on_edit(self) -> None:
        self.text_edit.setReadOnly(False)
        self.text_edit.setFocus()
        self.btn_edit.setVisible(False)
        self.btn_save.setVisible(True)

    def _on_save(self) -> None:
        self.info_saved.emit(self.well_name, self.text_edit.toPlainText())
        self.text_edit.setReadOnly(True)
        self.btn_save.setVisible(False)
        self.btn_edit.setVisible(True)


class WellPopup(QFrame):
    move_to_requested = pyqtSignal()
    info_requested = pyqtSignal()

    def __init__(self, well_name: str, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setStyleSheet("""
            QFrame {
                background-color: #1e2530;
                border: 1px solid #4fc3f7;
                border-radius: 8px;
            }
            QLabel#title {
                color: #4fc3f7;
                font-size: 16px;
                font-weight: 700;
            }
            QPushButton {
                background-color: #2a3445;
                color: #d0d7e2;
                border: 1px solid #3a4a5e;
                border-radius: 5px;
                padding: 6px 12px;
                font-size: 13px;
                text-align: left;
            }
            QPushButton:hover { background-color: #354055; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        title = QLabel(well_name)
        title.setObjectName("title")
        layout.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #3a4a5e;")
        layout.addWidget(sep)

        self.btn_move = QPushButton("  Move To")
        self.btn_info = QPushButton("  Information")
        self.btn_move.clicked.connect(self.move_to_requested)
        self.btn_info.clicked.connect(self.info_requested)
        layout.addWidget(self.btn_move)
        layout.addWidget(self.btn_info)


class WellPlatePreview(QWidget):
    well_hovered = pyqtSignal(int, int)
    well_unhovered = pyqtSignal()
    well_clicked = pyqtSignal(int, int, int, int)  # row, col, global_x, global_y

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.rows = 4
        self.cols = 6
        self.highlight_index: tuple[int, int] | None = None
        self.hover_index: tuple[int, int] | None = None
        self.setMinimumSize(380, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

    def set_plate_layout(self, rows: int, cols: int) -> None:
        self.rows = max(1, int(rows))
        self.cols = max(1, int(cols))
        self.update()

    def set_highlight(self, row: int | None = None, col: int | None = None) -> None:
        self.highlight_index = None if row is None or col is None else (row, col)
        self.update()

    def _grid_geometry(self):
        w, h = self.width(), self.height()
        outer_margin = 28
        plate_rect = QRectF(outer_margin, outer_margin,
                            max(10, w - 2*outer_margin), max(10, h - 2*outer_margin))
        inner_margin = 18
        inner_rect = plate_rect.adjusted(inner_margin, inner_margin, -inner_margin, -inner_margin)
        grid_margin = 26
        grid_rect = inner_rect.adjusted(grid_margin, grid_margin, -grid_margin, -grid_margin)
        step_x = grid_rect.width() / self.cols if self.cols > 0 else 1
        step_y = grid_rect.height() / self.rows if self.rows > 0 else 1
        diameter = min(step_x, step_y) * 0.68
        start_x = grid_rect.left() + step_x / 2
        start_y = grid_rect.top() + step_y / 2
        return start_x, start_y, step_x, step_y, diameter

    def _well_at(self, px: float, py: float) -> tuple[int, int] | None:
        start_x, start_y, step_x, step_y, diameter = self._grid_geometry()
        radius = diameter / 2
        for r in range(self.rows):
            for c in range(self.cols):
                cx = start_x + c * step_x
                cy = start_y + r * step_y
                if (px - cx)**2 + (py - cy)**2 <= radius**2:
                    return r, c
        return None

    def mouseMoveEvent(self, event):
        well = self._well_at(event.x(), event.y())
        if well != self.hover_index:
            self.hover_index = well
            if well is not None:
                self.well_hovered.emit(well[0], well[1])
            else:
                self.well_unhovered.emit()
            self.update()

    def leaveEvent(self, _event):
        if self.hover_index is not None:
            self.hover_index = None
            self.well_unhovered.emit()
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            well = self._well_at(event.x(), event.y())
            if well is not None:
                gp = self.mapToGlobal(event.pos())
                self.well_clicked.emit(well[0], well[1], gp.x(), gp.y())

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
                elif self.hover_index == (r, c):
                    painter.setBrush(QBrush(QColor("#2a4a5e")))
                    painter.setPen(QPen(QColor("#4fc3f7"), 1.6))
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
    # Pump 1 & 2 are bidirectional; Pump 3 & 4 are single direction
    pump1_forward_requested = pyqtSignal(int)
    pump1_reverse_requested = pyqtSignal(int)
    pump1_stop_requested = pyqtSignal()
    pump2_forward_requested = pyqtSignal(int)
    pump2_reverse_requested = pyqtSignal(int)
    pump2_stop_requested = pyqtSignal()
    pump3_requested = pyqtSignal(int)
    pump3_stop_requested = pyqtSignal()
    pump4_requested = pyqtSignal(int)
    pump4_stop_requested = pyqtSignal()
    stop_all_requested = pyqtSignal()
    cam_view_toggle_requested = pyqtSignal()
    calibration_requested = pyqtSignal()
    move_to_well_requested = pyqtSignal(float, float)  # abs X, Y in mm

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._calib_center: tuple | None = None
        self._calib_radius: float | None = None
        self._circle_history: list = []
        self._well_notes: dict[str, str] = {}
        self._smooth_window: int = 8
        self._build_ui()
        self._apply_plate_preset("24")

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        # ---------------- left column: preview + camera feed + messages ----------------
        left_col = QVBoxLayout()
        left_col.setSpacing(10)

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

        left_col.addWidget(preview_group, 2)

        # Camera feed panel
        cam_feed_group = QGroupBox("Camera Feed")
        cam_feed_group.setStyleSheet(
            "QGroupBox::title { font-size: 16px; font-weight: bold; color: #ffffff; padding: 0 5px; }"
        )
        cam_feed_layout = QVBoxLayout(cam_feed_group)
        cam_feed_layout.setContentsMargins(8, 8, 8, 8)

        self.cam_feed_label = QLabel("No camera connected")
        self.cam_feed_label.setAlignment(Qt.AlignCenter)
        self.cam_feed_label.setMinimumHeight(160)
        self.cam_feed_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.cam_feed_label.setStyleSheet(
            "QLabel { background-color: #0c0c0c; border: 1px solid #404040;"
            " border-radius: 6px; color: #c8cfdb; font-size: 14px; }"
        )
        cam_feed_layout.addWidget(self.cam_feed_label)

        self.btn_cam_view = QPushButton("Start View")
        self.btn_cam_view.setEnabled(False)
        self.btn_cam_view.clicked.connect(self.cam_view_toggle_requested)
        cam_feed_layout.addWidget(self.btn_cam_view)

        left_col.addWidget(cam_feed_group, 1)

        root.addLayout(left_col, 2)

        # ---------------- right: controls ----------------
        right_col = QVBoxLayout()
        right_col.setSpacing(10)
        root.addLayout(right_col, 2)

        # Header row: title + instructions button
        hdr_row = QHBoxLayout()
        hdr_title = QLabel("Automation Controls")
        hdr_title.setStyleSheet("font-size: 15px; font-weight: 700; color: #d0d7e2;")
        self.btn_routine_instructions = QPushButton("?")
        self.btn_routine_instructions.setFixedSize(22, 22)
        self.btn_routine_instructions.setToolTip("Routine Instructions")
        self.btn_routine_instructions.setStyleSheet("""
            QPushButton {
                background-color: #2a2f3a;
                color: #4fc3f7;
                border: 1px solid #4fc3f7;
                border-radius: 11px;
                font-weight: 700;
                font-size: 13px;
            }
            QPushButton:hover { background-color: #353b48; }
        """)
        hdr_row.addWidget(hdr_title)
        hdr_row.addSpacing(2)
        hdr_row.addWidget(self.btn_routine_instructions)
        hdr_row.addStretch()
        right_col.addLayout(hdr_row)
        right_col.addSpacing(2)

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
        
        pump_group = QGroupBox("Pump Test")
        pump_group.setStyleSheet(
            "QGroupBox::title { font-size: 16px; font-weight: bold; color: #ffffff; padding: 0 5px; }"
        )
        pump_form = QFormLayout(pump_group)
        pump_form.setSpacing(8)

        stop_style = "QPushButton { background-color: #6a3030; color: white; font-weight: 600; } QPushButton:hover { background-color: #814040; }"

        # Pump 1 — bidirectional
        self.in_pump1_ms = QLineEdit("1000")
        self.in_pump1_ms.setPlaceholderText("ms")
        p1_row = QHBoxLayout()
        self.btn_pump1_fwd = QPushButton("Forward")
        self.btn_pump1_rev = QPushButton("Reverse")
        self.btn_pump1_stop = QPushButton("Stop")
        self.btn_pump1_stop.setStyleSheet(stop_style)
        self.btn_pump1_fwd.clicked.connect(self._on_pump1_forward)
        self.btn_pump1_rev.clicked.connect(self._on_pump1_reverse)
        self.btn_pump1_stop.clicked.connect(self.pump1_stop_requested)
        p1_row.addWidget(self.in_pump1_ms)
        p1_row.addWidget(self.btn_pump1_fwd)
        p1_row.addWidget(self.btn_pump1_rev)
        p1_row.addWidget(self.btn_pump1_stop)
        pump_form.addRow("Pump 1", p1_row)

        # Pump 2 — bidirectional
        self.in_pump2_ms = QLineEdit("1000")
        self.in_pump2_ms.setPlaceholderText("ms")
        p2_row = QHBoxLayout()
        self.btn_pump2_fwd = QPushButton("Forward")
        self.btn_pump2_rev = QPushButton("Reverse")
        self.btn_pump2_stop = QPushButton("Stop")
        self.btn_pump2_stop.setStyleSheet(stop_style)
        self.btn_pump2_fwd.clicked.connect(self._on_pump2_forward)
        self.btn_pump2_rev.clicked.connect(self._on_pump2_reverse)
        self.btn_pump2_stop.clicked.connect(self.pump2_stop_requested)
        p2_row.addWidget(self.in_pump2_ms)
        p2_row.addWidget(self.btn_pump2_fwd)
        p2_row.addWidget(self.btn_pump2_rev)
        p2_row.addWidget(self.btn_pump2_stop)
        pump_form.addRow("Pump 2", p2_row)

        # Pump 3 — single direction
        self.in_pump3_ms = QLineEdit("1000")
        self.in_pump3_ms.setPlaceholderText("ms")
        p3_row = QHBoxLayout()
        self.btn_pump3_run = QPushButton("Run")
        self.btn_pump3_stop = QPushButton("Stop")
        self.btn_pump3_stop.setStyleSheet(stop_style)
        self.btn_pump3_run.clicked.connect(self._on_pump3_run)
        self.btn_pump3_stop.clicked.connect(self.pump3_stop_requested)
        p3_row.addWidget(self.in_pump3_ms)
        p3_row.addWidget(self.btn_pump3_run)
        p3_row.addWidget(self.btn_pump3_stop)
        pump_form.addRow("Pump 3", p3_row)

        # Pump 4 — single direction
        self.in_pump4_ms = QLineEdit("1000")
        self.in_pump4_ms.setPlaceholderText("ms")
        p4_row = QHBoxLayout()
        self.btn_pump4_run = QPushButton("Run")
        self.btn_pump4_stop = QPushButton("Stop")
        self.btn_pump4_stop.setStyleSheet(stop_style)
        self.btn_pump4_run.clicked.connect(self._on_pump4_run)
        self.btn_pump4_stop.clicked.connect(self.pump4_stop_requested)
        p4_row.addWidget(self.in_pump4_ms)
        p4_row.addWidget(self.btn_pump4_run)
        p4_row.addWidget(self.btn_pump4_stop)
        pump_form.addRow("Pump 4", p4_row)

        self.btn_stop_all = QPushButton("Stop All Pumps")
        self.btn_stop_all.setStyleSheet(stop_style)
        self.btn_stop_all.clicked.connect(self.stop_all_requested)
        pump_form.addRow(self.btn_stop_all)

        right_col.addWidget(pump_group)


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

        self._btn_calibrate = QPushButton("Calibrate Well")
        self._btn_calibrate.setToolTip("Open the well edge calibration dialog")
        self._btn_calibrate.clicked.connect(self.calibration_requested)
        self._lab_calibration = QLabel("Not calibrated")
        self._lab_calibration.setStyleSheet("color: #ffaa00; font-size: 14px;")

        self.lab_home_required = QLabel("Home not set — use Set Home on Gantry tab")
        self.lab_home_required.setStyleSheet("color: #ffaa00; font-size: 14px;")

        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("Start Routine")
        self.btn_start.setEnabled(False)
        self.btn_stop = QPushButton("Stop Routine")
        self.btn_stop.setStyleSheet(
            "QPushButton { background-color: #6a3030; color: white; font-weight: 600; }"
            "QPushButton:hover { background-color: #814040; }"
        )
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)

        run_dur_row = QHBoxLayout()
        run_dur_row.addWidget(QLabel("Run for (min):"))
        self.in_run_duration = QLineEdit("0")
        self.in_run_duration.setFixedWidth(60)
        self.in_run_duration.setToolTip("Total run time in minutes. 0 = run once.")
        run_dur_row.addWidget(self.in_run_duration)
        run_dur_row.addStretch()

        status_layout.addWidget(self.lab_status)
        status_layout.addWidget(self.lab_current)
        status_layout.addWidget(self.lab_phase)
        status_layout.addWidget(self.lab_home_required)
        status_layout.addLayout(btn_row)
        status_layout.addLayout(run_dur_row)
        status_layout.addWidget(self._btn_calibrate)
        status_layout.addWidget(self._lab_calibration)

        right_col.addWidget(status_group)

        # Messages panel at the bottom of the right column
        msg_top = QHBoxLayout()
        msg_top.addWidget(QLabel("Messages"))
        msg_top.addStretch()
        self.btn_clear_messages = QPushButton("Clear")
        self.btn_clear_messages.setFixedWidth(80)
        msg_top.addWidget(self.btn_clear_messages)

        self.msg_box = QPlainTextEdit()
        self.msg_box.setReadOnly(True)
        self.msg_box.setMinimumHeight(80)
        self.msg_box.setMaximumHeight(130)
        self.msg_box.setStyleSheet("background-color: #0e1117; color: #c8d0dc; font-size: 12px;")

        right_col.addLayout(msg_top)
        right_col.addWidget(self.msg_box)
        right_col.addStretch()

        self.btn_clear_messages.clicked.connect(self.msg_box.clear)

        self.preview.well_clicked.connect(self._on_well_clicked)
        self.btn_plate_update.clicked.connect(self._on_update_clicked)
        self.cmb_plate.currentTextChanged.connect(self._on_plate_changed)
        self.spn_rows.valueChanged.connect(self._on_geometry_changed)
        self.spn_cols.valueChanged.connect(self._on_geometry_changed)
        self.btn_start.clicked.connect(self._on_start_clicked)
        self.btn_stop.clicked.connect(self._on_stop_clicked)
        self.btn_routine_instructions.clicked.connect(self._show_routine_instructions)

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

    def _on_well_clicked(self, row: int, col: int, gx: int, gy: int) -> None:
        well_name = f"{chr(65 + row)}{col + 1}"
        popup = WellPopup(well_name, self)
        popup.move_to_requested.connect(lambda: self._move_to_well(row, col, popup))
        popup.info_requested.connect(lambda: self._open_well_info(row, col, popup))
        popup.move(gx + 8, gy + 8)
        popup.show()

    def _open_well_info(self, row: int, col: int, popup: "WellPopup") -> None:
        popup.close()
        well_name = f"{chr(65 + row)}{col + 1}"
        dlg = WellInfoDialog(well_name, self._well_notes.get(well_name, ""), self)
        dlg.info_saved.connect(lambda name, text: self._save_well_note(name, text))
        dlg.exec_()

    def _save_well_note(self, well_name: str, text: str) -> None:
        self._well_notes[well_name] = text
        self.post_message(f"Information saved for well {well_name}.")

    def _move_to_well(self, row: int, col: int, popup: "WellPopup") -> None:
        popup.close()
        try:
            dx = float(self.in_dx.text().strip())
            dy = float(self.in_dy.text().strip())
        except ValueError:
            self.post_message("Move To failed: ΔX / ΔY inputs are not valid numbers.")
            return
        well_name = f"{chr(65 + row)}{col + 1}"
        x_mm = col * dx
        y_mm = row * dy
        self.post_message(f"Moving to {well_name} (X={x_mm:.2f} mm, Y={y_mm:.2f} mm)...")
        self.move_to_well_requested.emit(x_mm, y_mm)

    def _show_routine_instructions(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Automated Routine Instructions")
        dialog.resize(700, 520)

        layout = QVBoxLayout(dialog)

        title = QLabel("Automated Routine Instructions")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: white;")

        text = QLabel(
            "<b>Before Starting</b><br><br>"
            "1. Connect the gantry system.<br>"
            "2. Set the working home position.<br>"
            "3. Confirm plate layout and spacing.<br>"
            "4. Verify calibration if needed.<br><br>"

            "<b>Routine Behavior</b><br><br>"
            "• The routine moves through wells based on the layout.<br>"
            "• Serpentine mode alternates direction each row.<br>"
            "• Z movement and wait times are applied per well.<br><br>"

            "<b>Important Notes</b><br><br>"
            "• Do not start until home is set.<br>"
            "• Use Stop to interrupt the routine.<br>"
            "• Watch the status panel for progress."
            
            "Camera well calibration is not implemented into the routine yet, this is a preview!"
        )
        text.setWordWrap(True)
        text.setTextFormat(Qt.RichText)
        text.setStyleSheet("font-size: 15px; color: #d8e2ee;")

        layout.addWidget(title)
        layout.addWidget(text)

        btn = QPushButton("Close")
        btn.clicked.connect(dialog.close)
        layout.addWidget(btn, alignment=Qt.AlignRight)

        dialog.exec_()
        
    def _ms(self, field: QLineEdit) -> int:
        return int(field.text().strip() or 0)

    def _on_pump1_forward(self) -> None:
        self.pump1_forward_requested.emit(self._ms(self.in_pump1_ms))

    def _on_pump1_reverse(self) -> None:
        self.pump1_reverse_requested.emit(self._ms(self.in_pump1_ms))

    def _on_pump2_forward(self) -> None:
        self.pump2_forward_requested.emit(self._ms(self.in_pump2_ms))

    def _on_pump2_reverse(self) -> None:
        self.pump2_reverse_requested.emit(self._ms(self.in_pump2_ms))

    def _on_pump3_run(self) -> None:
        self.pump3_requested.emit(self._ms(self.in_pump3_ms))

    def _on_pump4_run(self) -> None:
        self.pump4_requested.emit(self._ms(self.in_pump4_ms))

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

    def set_cam_view_state(self, camera_connected: bool, view_running: bool) -> None:
        """Update the Start/Stop View button to reflect microscope tab state."""
        self.btn_cam_view.setEnabled(camera_connected)
        self.btn_cam_view.setText("Stop View" if view_running else "Start View")
        self._cam_connected = camera_connected

    def update_camera_frame(self, pixmap) -> None:
        """Receive a QPixmap from MicroscopeTab and show it in the feed label.
        Pass None to reset to the placeholder text."""
        if pixmap is None:
            self.cam_feed_label.clear()
            connected = getattr(self, "_cam_connected", False)
            self.cam_feed_label.setText(
                "Preview stopped" if connected else "No camera connected"
            )
            return
        scaled = pixmap.scaled(
            self.cam_feed_label.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.cam_feed_label.setPixmap(scaled)
        self.cam_feed_label.setText("")

    def receive_raw_frame(self, frame_bgr) -> None:
        if frame_bgr is None:
            return

        if self._calib_radius is None:
            self._show_bgr_in_feed(frame_bgr)
            return

        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blur, 50, 150)

        ys, xs = np.where(edges > 0)
        display = frame_bgr.copy()
        display[edges > 0] = [0, 180, 0]  # highlight edges in green

        if len(xs) >= 3:
            pts = list(zip(xs.tolist(), ys.tolist()))
            if len(pts) > 150:
                pts = _random.sample(pts, 150)

            result = ransac_circle(pts, n_iter=100, inlier_thresh=8.0)
            if result is not None:
                cx, cy, r = result
                if abs(r - self._calib_radius) / max(self._calib_radius, 1.0) < 0.25:
                    self._circle_history.append((cx, cy, r))
                    if len(self._circle_history) > self._smooth_window:
                        self._circle_history.pop(0)

        if self._circle_history:
            cx = sum(c[0] for c in self._circle_history) / len(self._circle_history)
            cy = sum(c[1] for c in self._circle_history) / len(self._circle_history)
            cv2.circle(display, (int(cx), int(cy)), int(self._calib_radius), (0, 200, 255), 2)
            cv2.circle(display, (int(cx), int(cy)), 5, (0, 200, 255), -1)

        self._show_bgr_in_feed(display)

    def _show_bgr_in_feed(self, frame_bgr) -> None:
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        h, w, ch = frame_rgb.shape
        qimg = QImage(frame_rgb.data, w, h, ch * w, QImage.Format_RGB888)
        pix = QPixmap.fromImage(qimg)
        scaled = pix.scaled(
            self.cam_feed_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.cam_feed_label.setPixmap(scaled)
        self.cam_feed_label.setText("")

    def post_message(self, text: str) -> None:
        stamp = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        self.msg_box.appendPlainText(f"[{stamp}] {text}")
        self.msg_box.verticalScrollBar().setValue(
            self.msg_box.verticalScrollBar().maximum()
        )

    def set_calibration_result(self, center_px: tuple, radius_px: float) -> None:
        self._calib_center = center_px
        self._calib_radius = radius_px
        self._circle_history.clear()
        cx, cy = center_px
        self._lab_calibration.setText(
            f"Calibrated — center ({cx:.1f}, {cy:.1f}) px, r={radius_px:.1f} px"
        )
        self._lab_calibration.setStyleSheet("color: #66dd88; font-size: 12px;")

    def on_home_set_changed(self, home_set: bool) -> None:
        self.btn_start.setEnabled(home_set)
        if home_set:
            self.lab_home_required.setText("")
        else:
            self.lab_home_required.setText("Home not set — use Set Home on Gantry tab")

    def get_config(self) -> dict:
        try:
            run_min = float(self.in_run_duration.text().strip())
        except ValueError:
            run_min = 0.0
        return {
            "plate_type": self.cmb_plate.currentText(),
            "rows": self.spn_rows.value(),
            "cols": self.spn_cols.value(),
            "dx": self.in_dx.text().strip(),
            "dy": self.in_dy.text().strip(),
            "dz": self.in_dz.text().strip(),
            "wait_s": self.in_wait.text().strip(),
            "serpentine": self.chk_serpentine.isChecked(),
            "total_run_s": run_min * 60.0,
        }


if __name__ == "__main__":
    import sys
    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv)
    w = AutomationTab()
    w.resize(1200, 760)
    w.show()
    sys.exit(app.exec_())
