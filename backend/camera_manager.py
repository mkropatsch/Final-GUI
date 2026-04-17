from __future__ import annotations

from typing import Optional

import cv2
from PyQt5.QtCore import QObject, QTimer, pyqtSignal


class CameraManager(QObject):
    frame_ready = pyqtSignal(object)          # raw BGR numpy frame
    camera_state_changed = pyqtSignal(bool)   # connected
    preview_state_changed = pyqtSignal(bool)  # preview running
    error_occurred = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        self._cap = None
        self._connected = False
        self._preview_live = False
        self._camera_index: Optional[int] = None
        self._latest_frame = None

        self._timer = QTimer(self)
        self._timer.setInterval(33)  # ~30 FPS
        self._timer.timeout.connect(self._grab_frame)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_preview_live(self) -> bool:
        return self._preview_live

    @property
    def camera_index(self) -> Optional[int]:
        return self._camera_index

    @property
    def latest_frame(self):
        return self._latest_frame

    def connect_camera(self, cam_index: int) -> bool:
        if self._connected:
            self.disconnect_camera()

        cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
        if cap is None or not cap.isOpened():
            self.error_occurred.emit(f"Could not open camera {cam_index}.")
            return False

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        self._cap = cap
        self._connected = True
        self._camera_index = cam_index
        self.camera_state_changed.emit(True)
        return True

    def disconnect_camera(self) -> None:
        self.stop_preview()

        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass

        self._cap = None
        self._connected = False
        self._camera_index = None
        self._latest_frame = None
        self.camera_state_changed.emit(False)

    def start_preview(self) -> bool:
        if not self._connected or self._cap is None:
            self.error_occurred.emit("Connect a camera first.")
            return False

        if self._preview_live:
            return True

        self._timer.start()
        self._preview_live = True
        self.preview_state_changed.emit(True)
        return True

    def stop_preview(self) -> None:
        if self._preview_live:
            self._timer.stop()
            self._preview_live = False
            self.preview_state_changed.emit(False)

    def toggle_preview(self) -> bool:
        if self._preview_live:
            self.stop_preview()
            return False

        self.start_preview()
        return self._preview_live

    def _grab_frame(self) -> None:
        if not self._connected or self._cap is None:
            return

        ret, frame = self._cap.read()
        if not ret or frame is None:
            self.stop_preview()
            self._latest_frame = None
            self.error_occurred.emit("Failed to read camera frame.")
            return

        self._latest_frame = frame
        self.frame_ready.emit(frame)