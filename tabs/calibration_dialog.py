from __future__ import annotations

import random as _random
from typing import Callable, Optional, Tuple

import cv2
import numpy as np

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
)


# ── RANSAC circle fitting ─────────────────────────────────────────────────────

def _fit_circle_3pts(
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    p3: Tuple[float, float],
) -> Optional[Tuple[float, float, float]]:
    ax, ay = p1; bx, by = p2; cx, cy = p3
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-10:
        return None
    ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)) / d
    uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)) / d
    r = float(np.sqrt((ax - ux) ** 2 + (ay - uy) ** 2))
    return ux, uy, r


def ransac_circle(
    points: list,
    n_iter: int = 400,
    inlier_thresh: float = 8.0,
) -> Optional[Tuple[float, float, float]]:
    if len(points) < 3:
        return None
    pts = np.array(points, dtype=float)
    max_dim = float(max(pts[:, 0].max(), pts[:, 1].max()))
    best: Optional[Tuple[float, float, float]] = None
    best_count = 0
    for _ in range(n_iter):
        sample = _random.sample(points, 3)
        res = _fit_circle_3pts(*sample)
        if res is None:
            continue
        cx, cy, r = res
        if r < 5.0 or r > max_dim:
            continue
        dists = np.sqrt((pts[:, 0] - cx) ** 2 + (pts[:, 1] - cy) ** 2)
        count = int(np.sum(np.abs(dists - r) < inlier_thresh))
        if count > best_count:
            best_count = count
            best = (cx, cy, r)
    return best


# ── Clickable feed label ──────────────────────────────────────────────────────

class _ClickableLabel(QLabel):
    label_clicked = pyqtSignal(int, int)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.label_clicked.emit(event.x(), event.y())
        super().mousePressEvent(event)


# ── Calibration dialog ────────────────────────────────────────────────────────

class CalibrationDialog(QDialog):
    """Pop-up dialog for RANSAC well-edge calibration.

    command_sink  — callable that sends a gantry command dict (targets camera stage)
    return_pos    — camera stage position dict {x, y, z, e} to return to on close
    """

    def __init__(
        self,
        command_sink: Callable[[dict], None],
        return_pos: dict,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Well Edge Calibration")
        self.resize(1150, 740)
        self.setModal(True)

        self._command_sink = command_sink
        self._return_pos = dict(return_pos)

        self._points: list[Tuple[float, float]] = []
        self._circle: Optional[Tuple[float, float, float]] = None
        self._last_frame: Optional[np.ndarray] = None
        self._display_scale: float = 1.0
        self._display_offset: Tuple[int, int] = (0, 0)

        # Result written on accept
        self.well_center_px: Optional[Tuple[float, float]] = None
        self.well_radius_px: Optional[float] = None

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(12, 12, 12, 12)

        instr = QLabel(
            "<b>Instructions:</b> Use the jog buttons to move the camera until you can see "
            "part of the well edge. Then <b>click on the edge</b> to place points — aim for "
            "<b>6–8 points spread as far apart as possible</b> along the arc. "
            "Click <b>Fit Circle</b> when done. If the fit looks wrong, click <b>Fit Circle</b> "
            "again (RANSAC is random — a second attempt may give a better result). "
            "Click <b>Accept</b> when satisfied, or <b>Cancel</b> to discard."
        )
        instr.setWordWrap(True)
        instr.setStyleSheet(
            "background: #1e2a3a; color: #c8d8ee; font-size: 16px; "
            "padding: 10px; border-radius: 4px;"
        )
        root.addWidget(instr)

        body = QHBoxLayout()
        body.setSpacing(12)

        # ── Live feed ──────────────────────────────────────────────────────
        self._feed_label = _ClickableLabel("No camera feed — connect camera and start preview first")
        self._feed_label.setAlignment(Qt.AlignCenter)
        self._feed_label.setMinimumSize(740, 560)
        self._feed_label.setSizePolicy(
            self._feed_label.sizePolicy().Expanding,
            self._feed_label.sizePolicy().Expanding,
        )
        self._feed_label.setStyleSheet(
            "background: #0c0c0c; border: 1px solid #404040; "
            "border-radius: 6px; color: #888; font-size: 14px;"
        )
        self._feed_label.label_clicked.connect(self._on_feed_clicked)
        body.addWidget(self._feed_label, 3)

        # ── Right controls ─────────────────────────────────────────────────
        ctrl = QVBoxLayout()
        ctrl.setSpacing(10)

        # Jog group
        jog_group = QGroupBox("Camera Jog")
        jog_vbox = QVBoxLayout(jog_group)
        jog_vbox.setSpacing(6)

        dpad = QGridLayout()
        dpad.setSpacing(4)
        self._btn_up    = QPushButton("↑")
        self._btn_down  = QPushButton("↓")
        self._btn_left  = QPushButton("←")
        self._btn_right = QPushButton("→")
        for b in (self._btn_up, self._btn_down, self._btn_left, self._btn_right):
            b.setFixedSize(48, 48)
            b.setStyleSheet("font-size: 20px;")
        dpad.addWidget(self._btn_up,    0, 1)
        dpad.addWidget(self._btn_left,  1, 0)
        dpad.addWidget(self._btn_down,  1, 1)
        dpad.addWidget(self._btn_right, 1, 2)
        jog_vbox.addLayout(dpad)

        step_row = QHBoxLayout()
        step_row.addWidget(QLabel("Step (mm):"))
        self._cmb_step = QComboBox()
        self._cmb_step.addItems(["0.1", "0.5", "1.0", "2.0"])
        self._cmb_step.setCurrentIndex(1)
        step_row.addWidget(self._cmb_step)
        step_row.addStretch()
        jog_vbox.addLayout(step_row)

        self._btn_up.clicked.connect(lambda: self._jog(0, -1))
        self._btn_down.clicked.connect(lambda: self._jog(0, 1))
        self._btn_left.clicked.connect(lambda: self._jog(-1, 0))
        self._btn_right.clicked.connect(lambda: self._jog(1, 0))

        ctrl.addWidget(jog_group)

        # Points group
        pts_group = QGroupBox("Points")
        pts_vbox = QVBoxLayout(pts_group)
        pts_vbox.setSpacing(6)
        self._lab_point_count = QLabel("Points placed: 0")
        self._lab_point_count.setStyleSheet("color: #c8d8ee;")
        self._btn_clear = QPushButton("Clear Points")
        self._btn_clear.clicked.connect(self._clear_points)
        pts_vbox.addWidget(self._lab_point_count)
        pts_vbox.addWidget(self._btn_clear)
        ctrl.addWidget(pts_group)

        # Fit group
        fit_group = QGroupBox("Circle Fit")
        fit_vbox = QVBoxLayout(fit_group)
        fit_vbox.setSpacing(6)
        self._btn_fit = QPushButton("Fit Circle")
        self._btn_fit.clicked.connect(self._fit_circle)
        self._lab_fit_status = QLabel("No fit yet")
        self._lab_fit_status.setWordWrap(True)
        self._lab_fit_status.setStyleSheet("color: #b8c4d9; font-size: 12px;")
        fit_vbox.addWidget(self._btn_fit)
        fit_vbox.addWidget(self._lab_fit_status)

        self._lab_radius_adjust = QLabel("Adjust radius:")
        self._lab_radius_adjust.setStyleSheet("color: #c8d8ee;")
        self._sld_radius = QSlider(Qt.Horizontal)
        self._sld_radius.setMinimum(10)
        self._sld_radius.setMaximum(1000)
        self._sld_radius.valueChanged.connect(self._on_radius_slider)
        self._lab_radius_value = QLabel("")
        self._lab_radius_value.setStyleSheet("color: #c8d8ee; font-size: 12px;")

        self._lab_radius_adjust.setVisible(False)
        self._sld_radius.setVisible(False)
        self._lab_radius_value.setVisible(False)

        fit_vbox.addWidget(self._lab_radius_adjust)
        fit_vbox.addWidget(self._sld_radius)
        fit_vbox.addWidget(self._lab_radius_value)
        ctrl.addWidget(fit_group)

        ctrl.addStretch()

        # Accept / Cancel
        self._btn_accept = QPushButton("Accept")
        self._btn_accept.setEnabled(False)
        self._btn_accept.setStyleSheet(
            "QPushButton { background-color: #2a6a3a; color: white; font-weight: 600; min-height: 36px; }"
            "QPushButton:hover { background-color: #3a8a4e; }"
            "QPushButton:disabled { background-color: #333; color: #666; }"
        )
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setStyleSheet("min-height: 36px;")
        self._btn_accept.clicked.connect(self._on_accept)
        btn_cancel.clicked.connect(self._on_cancel)
        ctrl.addWidget(self._btn_accept)
        ctrl.addWidget(btn_cancel)

        body.addLayout(ctrl, 1)
        root.addLayout(body, 1)

    # ── Frame receiving ───────────────────────────────────────────────────────

    def receive_frame(self, frame_bgr: np.ndarray) -> None:
        self._last_frame = frame_bgr.copy()
        self._redraw()

    def _redraw(self) -> None:
        if self._last_frame is None:
            return
        if self._feed_label.width() < 1 or self._feed_label.height() < 1:
            return
        frame = self._last_frame.copy()

        for px, py in self._points:
            cv2.circle(frame, (int(px), int(py)), 6, (0, 255, 80), -1)
            cv2.circle(frame, (int(px), int(py)), 7, (255, 255, 255), 1)

        if self._circle is not None:
            cx, cy, r = self._circle
            cv2.circle(frame, (int(cx), int(cy)), int(r), (0, 200, 255), 2)
            cv2.circle(frame, (int(cx), int(cy)), 5, (0, 200, 255), -1)
            cv2.line(frame, (int(cx) - 12, int(cy)), (int(cx) + 12, int(cy)), (0, 200, 255), 1)
            cv2.line(frame, (int(cx), int(cy) - 12), (int(cx), int(cy) + 12), (0, 200, 255), 1)

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = frame_rgb.shape
        qimg = QImage(frame_rgb.data, w, h, ch * w, QImage.Format_RGB888)
        pix = QPixmap.fromImage(qimg)

        label_w = self._feed_label.width()
        label_h = self._feed_label.height()
        scaled = pix.scaled(label_w, label_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)

        self._display_scale = min(label_w / w, label_h / h)
        self._display_offset = (
            (label_w - scaled.width()) // 2,
            (label_h - scaled.height()) // 2,
        )
        self._feed_label.setPixmap(scaled)
        self._feed_label.setText("")

    # ── Point placement ───────────────────────────────────────────────────────

    def _on_feed_clicked(self, lx: int, ly: int) -> None:
        if self._last_frame is None or self._display_scale < 1e-6:
            return
        img_x = (lx - self._display_offset[0]) / self._display_scale
        img_y = (ly - self._display_offset[1]) / self._display_scale
        h, w = self._last_frame.shape[:2]
        if 0 <= img_x < w and 0 <= img_y < h:
            self._points.append((img_x, img_y))
            self._circle = None
            self._btn_accept.setEnabled(False)
            self._lab_fit_status.setText("No fit yet")
            self._lab_point_count.setText(f"Points placed: {len(self._points)}")
            self._redraw()

    def _clear_points(self) -> None:
        self._points.clear()
        self._circle = None
        self._btn_accept.setEnabled(False)
        self._lab_point_count.setText("Points placed: 0")
        self._lab_fit_status.setText("No fit yet")
        self._lab_radius_adjust.setVisible(False)
        self._sld_radius.setVisible(False)
        self._lab_radius_value.setVisible(False)
        self._redraw()

    # ── Circle fitting ────────────────────────────────────────────────────────

    def _fit_circle(self) -> None:
        if len(self._points) < 3:
            self._lab_fit_status.setText("Need at least 3 points — click on the well edge first.")
            return
        if len(self._points) < 6:
            self._lab_fit_status.setText(
                f"Only {len(self._points)} points — more points spread across the arc "
                "will give a better fit. Fitting anyway..."
            )

        result = ransac_circle(self._points)
        if result is None:
            self._lab_fit_status.setText("Fit failed — try adding more spread-out points.")
            return

        cx, cy, r = result
        self._circle = result
        self._btn_accept.setEnabled(True)
        self._lab_fit_status.setText(
            f"Center: ({cx:.1f}, {cy:.1f})\nRadius: {r:.1f} px\n\n"
            "Drag the slider to adjust radius if the fit is too small or large."
        )

        self._sld_radius.blockSignals(True)
        self._sld_radius.setMinimum(max(10, int(r * 0.3)))
        self._sld_radius.setMaximum(int(r * 2.5))
        self._sld_radius.setValue(int(r))
        self._sld_radius.blockSignals(False)

        self._lab_radius_adjust.setVisible(True)
        self._sld_radius.setVisible(True)
        self._lab_radius_value.setVisible(True)
        self._lab_radius_value.setText(f"{r:.1f} px")
        self._redraw()

    def _on_radius_slider(self, value: int) -> None:
        if self._circle is None:
            return
        cx, cy, _ = self._circle
        self._circle = (cx, cy, float(value))
        self._lab_radius_value.setText(f"{value} px")
        self._redraw()

    # ── Jog ───────────────────────────────────────────────────────────────────

    def _jog(self, dx_sign: int, dy_sign: int) -> None:
        try:
            step = float(self._cmb_step.currentText())
        except ValueError:
            step = 0.5
        self._command_sink({
            "type": "gantry_cmd",
            "cmd": "move_rel",
            "target": "camera",
            "dx": float(dx_sign) * step,
            "dy": float(dy_sign) * step,
            "dz": 0.0,
            "de": 0.0,
        })

    # ── Accept / Cancel ───────────────────────────────────────────────────────

    def _on_accept(self) -> None:
        if self._circle is None:
            return
        cx, cy, r = self._circle
        self.well_center_px = (cx, cy)
        self.well_radius_px = r
        self._return_camera()
        super().accept()

    def _on_cancel(self) -> None:
        self._return_camera()
        super().reject()

    def _return_camera(self) -> None:
        self._command_sink({
            "type": "gantry_cmd",
            "cmd": "move_abs",
            "target": "camera",
            "X": float(self._return_pos.get("x", 0.0)),
            "Y": float(self._return_pos.get("y", 0.0)),
            "Z": float(self._return_pos.get("z", 0.0)),
            "E": float(self._return_pos.get("e", 0.0)),
        })

    def closeEvent(self, event) -> None:
        self._return_camera()
        super().closeEvent(event)
