## Simple GUI for message display

#!/usr/bin/env python3
"""
PyQt5 log viewer that tails a log file and displays new lines live.

Usage:
  python message_viewer_qt.py gantry_messages.log
If omitted, defaults to gantry_messages.log in the same folder.
"""

import os
import sys
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QCheckBox, QPushButton, QTextEdit
)

DEFAULT_LOG = "gantry_messages.log"


class LogViewer(QMainWindow):
    def __init__(self, logfile: str):
        super().__init__()
        self.logfile = os.path.abspath(logfile)
        self.setWindowTitle("Gantry Messages")
        self.resize(980, 560)

        self._fp = None
        self._pos = 0

        # --- UI ---
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        top = QHBoxLayout()
        layout.addLayout(top)

        self.path_label = QLabel(f"File: {self.logfile}")
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(self.path_label, stretch=1)

        self.auto_scroll = QCheckBox("Auto-scroll")
        self.auto_scroll.setChecked(True)
        top.addWidget(self.auto_scroll)

        self.pause = QCheckBox("Pause")
        self.pause.setChecked(False)
        top.addWidget(self.pause)

        self.clear_btn = QPushButton("Clear view")
        self.clear_btn.clicked.connect(self._clear_view)
        top.addWidget(self.clear_btn)

        self.text = QTextEdit()
        self.text.setReadOnly(True)
        # (optional) a little nicer for logs:
        self.text.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(self.text, stretch=1)

        self.status = QLabel("Starting…")
        layout.addWidget(self.status)

        # --- Timer to poll file ---
        self.timer = QTimer(self)
        self.timer.setInterval(200)  # ms
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def _clear_view(self):
        self.text.clear()

    def _ensure_open(self):
        # Create file if missing so viewer never errors
        os.makedirs(os.path.dirname(self.logfile), exist_ok=True)
        if not os.path.exists(self.logfile):
            open(self.logfile, "a", encoding="utf-8").close()

        if self._fp is None:
            self._fp = open(self.logfile, "r", encoding="utf-8", errors="replace")
            # Start at end (tail behavior)
            self._fp.seek(0, os.SEEK_END)
            self._pos = self._fp.tell()

    def _tick(self):
        try:
            if self.pause.isChecked():
                self.status.setText("Paused.")
                return

            self._ensure_open()

            # handle truncation (log cleared / rotated)
            try:
                size = os.path.getsize(self.logfile)
            except OSError:
                size = 0

            if size < self._pos:
                # file was truncated; reopen from start
                try:
                    self._fp.close()
                except Exception:
                    pass
                self._fp = None
                self._pos = 0
                self.status.setText("Log truncated; reopened.")
                return

            self._fp.seek(self._pos)
            new_text = self._fp.read()
            if new_text:
                self.text.moveCursor(self.text.textCursor().End)
                self.text.insertPlainText(new_text)
                if self.auto_scroll.isChecked():
                    self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())

            self._pos = self._fp.tell()
            self.status.setText("Watching…")

        except Exception as e:
            self.status.setText(f"Error: {e}")


def main():
    logfile = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LOG
    app = QApplication(sys.argv)
    w = LogViewer(logfile)
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()