from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QComboBox,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QCheckBox,
    QSizePolicy,
)


@dataclass
class DetectionResult:
    found: bool
    centroid: Optional[Tuple[int, int]] = None
    area: float = 0.0
    contour_count: int = 0


class MicroscopeTab(QWidget):
    """Dedicated microscope/imaging tab.

    Features in this first version:
    - camera refresh / connect / disconnect
    - start / stop live preview
    - snapshot saving
    - simple contour-based detection
    - raw / mask / overlay display modes
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self.camera_cap = None
        self.camera_connected = False
        self.preview_live = False
        self.current_camera_index = None
        self.last_frame_bgr = None
        self.last_display_frame = None
        self.snapshot_dir = os.path.join(os.getcwd(), "microscope_snapshots")
        os.makedirs(self.snapshot_dir, exist_ok=True)

        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._update_frame)

        self._build_ui()
        self.refresh_cameras()
        self._update_placeholder("No camera connected")
        self.btn_view.setEnabled(False)
        self.btn_snapshot.setEnabled(False)

    # -------------------------- UI --------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(12)

        # ----- Left: preview -----
        preview_group = QGroupBox("Microscope View")
        preview_group.setStyleSheet(
            """
            QGroupBox::title {
                font-size: 18px;
                font-weight: bold;
                color: #f0f0f0;
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 6px;
            }
            """
        )
        preview_layout = QVBoxLayout(preview_group)
        preview_layout.setSpacing(8)

        top_preview_row = QHBoxLayout()
        self.display_mode = QComboBox()
        self.display_mode.addItems(["Raw View", "Mask View", "Overlay View"])
        top_preview_row.addWidget(QLabel("Display"))
        top_preview_row.addWidget(self.display_mode)
        top_preview_row.addStretch()

        self.preview_label = QLabel("No camera connected")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(700, 520)
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview_label.setStyleSheet(
            """
            QLabel {
                background-color: #0c0c0c;
                border: 1px solid #404040;
                border-radius: 8px;
                color: #c8cfdb;
                font-size: 16px;
            }
            """
        )

        self.preview_info = QLabel("Preview idle")
        self.preview_info.setStyleSheet("color: #b8c4d9;")

        preview_layout.addLayout(top_preview_row)
        preview_layout.addWidget(self.preview_label, 1)
        preview_layout.addWidget(self.preview_info)

        # ----- Right: controls -----
        controls = QVBoxLayout()
        controls.setSpacing(10)

        cam_group = QGroupBox("Camera")
        cam_form = QGridLayout(cam_group)

        self.camera_combo = QComboBox()
        self.camera_combo.setMinimumWidth(160)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_connect = QPushButton("Connect")
        self.btn_view = QPushButton("Start View")

        self.camera_status = QLabel("Disconnected")
        self.camera_status.setStyleSheet("color: #ffb347; font-weight: bold;")

        cam_form.addWidget(QLabel("Select Camera"), 0, 0)
        cam_form.addWidget(self.camera_combo, 0, 1, 1, 2)
        cam_form.addWidget(self.btn_refresh, 1, 0)
        cam_form.addWidget(self.btn_connect, 1, 1)
        cam_form.addWidget(self.btn_view, 1, 2)
        cam_form.addWidget(QLabel("Status"), 2, 0)
        cam_form.addWidget(self.camera_status, 2, 1, 1, 2)

        capture_group = QGroupBox("Capture")
        capture_layout = QVBoxLayout(capture_group)
        capture_layout.setSpacing(8)

        folder_row = QHBoxLayout()
        self.btn_folder = QPushButton("Choose Folder")
        self.btn_snapshot = QPushButton("Save Snapshot")
        folder_row.addWidget(self.btn_folder)
        folder_row.addWidget(self.btn_snapshot)

        self.folder_label = QLabel(self.snapshot_dir)
        self.folder_label.setWordWrap(True)
        self.folder_label.setStyleSheet("color: #b8c4d9;")
        self.capture_status = QLabel("Ready to save snapshots")
        self.capture_status.setStyleSheet("color: #b8c4d9;")

        capture_layout.addLayout(folder_row)
        capture_layout.addWidget(self.folder_label)
        capture_layout.addWidget(self.capture_status)

        detect_group = QGroupBox("Detection")
        detect_form = QFormLayout(detect_group)
        detect_form.setSpacing(8)

        self.chk_detection = QCheckBox("Enable Detection")
        self.chk_detection.setChecked(False)

        self.sld_threshold = QSlider(Qt.Horizontal)
        self.sld_threshold.setRange(0, 255)
        self.sld_threshold.setValue(120)
        self.lab_threshold = QLabel("120")
        threshold_row = QHBoxLayout()
        threshold_row.addWidget(self.sld_threshold, 1)
        threshold_row.addWidget(self.lab_threshold)

        self.spin_min_area = QSpinBox()
        self.spin_min_area.setRange(1, 100000)
        self.spin_min_area.setValue(500)

        self.chk_invert = QCheckBox("Invert Threshold")
        self.chk_invert.setChecked(False)

        detect_form.addRow(self.chk_detection)
        detect_form.addRow("Threshold", threshold_row)
        detect_form.addRow("Min Area", self.spin_min_area)
        detect_form.addRow(self.chk_invert)

        readout_group = QGroupBox("Readout")
        readout_form = QFormLayout(readout_group)
        readout_form.setSpacing(8)

        self.lab_detect_status = QLabel("No detection")
        self.lab_centroid = QLabel("(-, -)")
        self.lab_area = QLabel("0")
        self.lab_contours = QLabel("0")

        readout_form.addRow("Detection", self.lab_detect_status)
        readout_form.addRow("Centroid", self.lab_centroid)
        readout_form.addRow("Area", self.lab_area)
        readout_form.addRow("Contours", self.lab_contours)

        controls.addWidget(cam_group)
        controls.addWidget(capture_group)
        controls.addWidget(detect_group)
        controls.addWidget(readout_group)
        controls.addStretch()

        root.addWidget(preview_group, 3)
        root.addLayout(controls, 2)

        self.setStyleSheet(
            self.styleSheet()
            + """
            QGroupBox {
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton {
                min-height: 32px;
            }
            QLabel {
                font-size: 13px;
            }
            QCheckBox, QComboBox, QSpinBox {
                font-size: 13px;
            }
            """
        )

        # signals
        self.btn_refresh.clicked.connect(self.refresh_cameras)
        self.btn_connect.clicked.connect(self._on_connect_clicked)
        self.btn_view.clicked.connect(self._on_view_clicked)
        self.btn_folder.clicked.connect(self._choose_folder)
        self.btn_snapshot.clicked.connect(self._save_snapshot)
        self.sld_threshold.valueChanged.connect(
            lambda v: self.lab_threshold.setText(str(v))
        )

    # ----------------------- camera management -----------------------
    def refresh_cameras(self) -> None:
        self.camera_combo.clear()

        found_any = False
        for idx in range(5):
            cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            if cap is not None and cap.isOpened():
                self.camera_combo.addItem(f"Camera {idx}", idx)
                found_any = True
                cap.release()

        if not found_any:
            self.camera_combo.addItem("(no cameras found)", None)

    def _on_connect_clicked(self) -> None:
        if self.camera_connected:
            self.disconnect_camera()
            return

        cam_index = self.camera_combo.currentData()
        if cam_index is None:
            QMessageBox.warning(self, "Camera", "Please select a valid camera.")
            return

        cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
        if cap is None or not cap.isOpened():
            QMessageBox.warning(self, "Camera", f"Could not open camera {cam_index}.")
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        self.camera_cap = cap
        self.camera_connected = True
        self.current_camera_index = cam_index

        self.camera_status.setText(f"Connected: Camera {cam_index}")
        self.camera_status.setStyleSheet("color: #66dd88; font-weight: bold;")
        self.btn_connect.setText("Disconnect")
        self.btn_view.setEnabled(True)
        self.btn_snapshot.setEnabled(True)
        self._update_placeholder("Camera connected - preview off")
        self.preview_info.setText("Camera ready")

    def _on_view_clicked(self) -> None:
        if not self.camera_connected or self.camera_cap is None:
            QMessageBox.warning(self, "Camera", "Connect a camera first.")
            return

        if self.preview_live:
            self.timer.stop()
            self.preview_live = False
            self.btn_view.setText("Start View")
            self._update_placeholder("Camera connected - preview off")
            self.preview_info.setText("Preview stopped")
            return

        self.timer.start()
        self.preview_live = True
        self.btn_view.setText("Stop View")
        self.preview_info.setText("Preview running")

    def disconnect_camera(self) -> None:
        self.timer.stop()
        self.preview_live = False

        if self.camera_cap is not None:
            try:
                self.camera_cap.release()
            except Exception:
                pass

        self.camera_cap = None
        self.camera_connected = False
        self.current_camera_index = None
        self.last_frame_bgr = None
        self.last_display_frame = None

        self.btn_connect.setText("Connect")
        self.btn_view.setText("Start View")
        self.btn_view.setEnabled(False)
        self.btn_snapshot.setEnabled(False)
        self.camera_status.setText("Disconnected")
        self.camera_status.setStyleSheet("color: #ffb347; font-weight: bold;")
        self._update_placeholder("No camera connected")
        self.preview_info.setText("Preview idle")
        self.lab_detect_status.setText("No detection")
        self.lab_centroid.setText("(-, -)")
        self.lab_area.setText("0")
        self.lab_contours.setText("0")

    # --------------------------- frame update ---------------------------
    def _update_frame(self) -> None:
        if not self.camera_connected or self.camera_cap is None:
            return

        ret, frame = self.camera_cap.read()
        if not ret or frame is None:
            self.timer.stop()
            self.preview_live = False
            self.btn_view.setText("Start View")
            self._update_placeholder("Preview unavailable")
            self.preview_info.setText("Failed to read frame")
            return

        self.last_frame_bgr = frame.copy()

        display_bgr, result = self._process_frame(frame)
        self.last_display_frame = display_bgr.copy()
        self._apply_readout(result)
        self._show_bgr_frame(display_bgr)

    def _process_frame(self, frame_bgr):
        if not self.chk_detection.isChecked():
            mode = self.display_mode.currentText()
            if mode == "Mask View":
                gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
                thr_type = cv2.THRESH_BINARY_INV if self.chk_invert.isChecked() else cv2.THRESH_BINARY
                _, mask = cv2.threshold(gray, self.sld_threshold.value(), 255, thr_type)
                display = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
            else:
                display = frame_bgr.copy()
            return display, DetectionResult(found=False)

        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        thr_type = cv2.THRESH_BINARY_INV if self.chk_invert.isChecked() else cv2.THRESH_BINARY
        _, mask = cv2.threshold(blur, self.sld_threshold.value(), 255, thr_type)

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        min_area = float(self.spin_min_area.value())
        valid = [c for c in contours if cv2.contourArea(c) >= min_area]

        mode = self.display_mode.currentText()
        if mode == "Mask View":
            display = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        else:
            display = frame_bgr.copy()

        if not valid:
            return display, DetectionResult(found=False, contour_count=len(contours))

        largest = max(valid, key=cv2.contourArea)
        area = float(cv2.contourArea(largest))
        moments = cv2.moments(largest)

        cx = cy = None
        if moments["m00"] != 0:
            cx = int(moments["m10"] / moments["m00"])
            cy = int(moments["m01"] / moments["m00"])

        if mode == "Overlay View":
            cv2.drawContours(display, valid, -1, (0, 255, 255), 2)
            if cx is not None and cy is not None:
                cv2.circle(display, (cx, cy), 7, (255, 0, 255), -1)
                cv2.line(display, (cx - 15, cy), (cx + 15, cy), (255, 0, 255), 2)
                cv2.line(display, (cx, cy - 15), (cx, cy + 15), (255, 0, 255), 2)

        return display, DetectionResult(
            found=True,
            centroid=(cx, cy) if cx is not None and cy is not None else None,
            area=area,
            contour_count=len(valid),
        )

    def _show_bgr_frame(self, frame_bgr) -> None:
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        h, w, ch = frame_rgb.shape
        bytes_per_line = ch * w
        qimg = QImage(frame_rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
        pix = QPixmap.fromImage(qimg)
        scaled = pix.scaled(
            self.preview_label.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.preview_label.setPixmap(scaled)
        self.preview_label.setText("")

    def _update_placeholder(self, text: str) -> None:
        self.preview_label.clear()
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText(text)
        self.preview_label.setAlignment(Qt.AlignCenter)

    def _apply_readout(self, result: DetectionResult) -> None:
        if result.found:
            self.lab_detect_status.setText("Object detected")
            if result.centroid is None:
                self.lab_centroid.setText("(-, -)")
            else:
                self.lab_centroid.setText(f"({result.centroid[0]}, {result.centroid[1]})")
            self.lab_area.setText(f"{result.area:.1f}")
            self.lab_contours.setText(str(result.contour_count))
            self.preview_info.setText("Detection active")
        else:
            self.lab_detect_status.setText("No object detected")
            self.lab_centroid.setText("(-, -)")
            self.lab_area.setText("0")
            self.lab_contours.setText(str(result.contour_count))
            if self.chk_detection.isChecked():
                self.preview_info.setText("Detection enabled - no valid contour")

    # --------------------------- capture ---------------------------
    def _choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Snapshot Folder", self.snapshot_dir)
        if not path:
            return
        self.snapshot_dir = path
        self.folder_label.setText(self.snapshot_dir)

    def _save_snapshot(self) -> None:
        if self.last_frame_bgr is None:
            QMessageBox.warning(self, "Snapshot", "No frame available yet.")
            return

        os.makedirs(self.snapshot_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        filename = os.path.join(self.snapshot_dir, f"microscope_{stamp}.png")

        ok = cv2.imwrite(filename, self.last_frame_bgr)
        if ok:
            self.capture_status.setText(f"Saved: {filename}")
        else:
            self.capture_status.setText("Failed to save snapshot")
            QMessageBox.warning(self, "Snapshot", "Failed to save snapshot.")

    # --------------------------- cleanup ---------------------------
    def shutdown(self) -> None:
        self.disconnect_camera()


if __name__ == "__main__":
    import sys
    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv)
    w = MicroscopeTab()
    w.resize(1400, 900)
    w.show()
    sys.exit(app.exec_())
