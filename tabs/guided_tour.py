from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QDialog,
)


class TourHighlight(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.target = None
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.hide()

    def set_target(self, widget):
        self.target = widget
        if widget is None:
            self.hide()
            return
        self.setGeometry(self.parent().rect())
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, event):
        if not self.target or not self.target.isVisible():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        top_left = self.target.mapTo(self.parent(), self.target.rect().topLeft())
        rect = self.target.geometry()
        rect.moveTopLeft(top_left)
        rect = rect.adjusted(-6, -6, 6, 6)

        # outer glow
        glow_pen = QPen(QColor(79, 195, 247, 120))
        glow_pen.setWidth(10)
        painter.setPen(glow_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect, 10, 10)

        # main border
        border_pen = QPen(QColor("#4fc3f7"))
        border_pen.setWidth(4)
        painter.setPen(border_pen)
        painter.drawRoundedRect(rect, 10, 10)


class TourPopup(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Guided Tour")
        self.setModal(False)
        self.resize(470, 250)

        self.setStyleSheet("""
            QDialog {
                background-color: #15263a;
                border: 1px solid #35506d;
                border-radius: 10px;
            }
            QLabel#StepTitle {
                color: #f2f5f9;
                font-size: 20px;
                font-weight: 700;
            }
            QLabel#StepText {
                color: #d8e2ee;
                font-size: 17px;
            }
            QPushButton {
                background-color: #2a3442;
                color: #d9e2ec;
                border: 1px solid #465364;
                border-radius: 6px;
                padding: 7px 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #364152;
            }
            QPushButton#NextButton {
                background-color: #2f6ea3;
                border: 1px solid #4b86b8;
                color: white;
            }
            QPushButton#NextButton:hover {
                background-color: #3c7db3;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.lab_step = QLabel("Step")
        self.lab_step.setObjectName("StepTitle")

        self.lab_text = QLabel("")
        self.lab_text.setObjectName("StepText")
        self.lab_text.setWordWrap(True)

        btn_row = QHBoxLayout()
        self.btn_back = QPushButton("Back")
        self.btn_next = QPushButton("Next")
        self.btn_next.setObjectName("NextButton")
        self.btn_close = QPushButton("Close Tour")

        btn_row.addWidget(self.btn_back)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_close)
        btn_row.addWidget(self.btn_next)

        layout.addWidget(self.lab_step)
        layout.addWidget(self.lab_text)
        layout.addStretch()
        layout.addLayout(btn_row)


class GuidedTour:
    def __init__(self, main_window):
        self.main = main_window
        self.steps = []
        self.index = 0

        self.highlight = TourHighlight(self.main)
        self.popup = TourPopup(self.main)

        self.popup.btn_next.clicked.connect(self.next_step)
        self.popup.btn_back.clicked.connect(self.prev_step)
        self.popup.btn_close.clicked.connect(self.finish)

    def start(self):
        if not self.steps:
            return
        self.index = 0
        self.show_step()

    def show_step(self):
        if not (0 <= self.index < len(self.steps)):
            self.finish()
            return

        step = self.steps[self.index]

        if "tab" in step:
            self.main.tabs.setCurrentIndex(step["tab"])

        widget = step["widget"]
        title = step.get("title", f"Step {self.index + 1}")
        text = step["text"]

        self.highlight.set_target(widget)

        self.popup.lab_step.setText(title)
        self.popup.lab_text.setText(text)

        self.popup.btn_back.setEnabled(self.index > 0)
        if self.index == len(self.steps) - 1:
            self.popup.btn_next.setText("Finish")
        else:
            self.popup.btn_next.setText("Next")

        self.popup.show()
        self.popup.raise_()
        self.popup.activateWindow()

        # place popup near highlighted widget
        self._position_popup_near_widget(widget)

    def _position_popup_near_widget(self, widget):
        if widget is None or not widget.isVisible():
            return

        main_geom = self.main.frameGeometry()

        # target widget rect in main-window coordinates
        top_left = widget.mapTo(self.main, widget.rect().topLeft())
        rect = widget.geometry()
        rect.moveTopLeft(top_left)

        popup_w = self.popup.width()
        popup_h = self.popup.height()
        margin = 18

        # Try right side first
        x = main_geom.x() + rect.right() + margin
        y = main_geom.y() + rect.top()

        screen = self.main.screen().availableGeometry()

        # If it would go off the right edge, place on the left
        if x + popup_w > screen.right():
            x = main_geom.x() + rect.left() - popup_w - margin

        # If that still goes too far left, clamp
        if x < screen.left() + 10:
            x = screen.left() + 10

        # Keep vertically aligned with widget, but clamp to screen
        if y + popup_h > screen.bottom():
            y = screen.bottom() - popup_h - 10
        if y < screen.top() + 10:
            y = screen.top() + 10

        self.popup.move(x, y)
    
    def next_step(self):
        if self.index >= len(self.steps) - 1:
            self.finish()
            return
        self.index += 1
        self.show_step()

    def prev_step(self):
        if self.index <= 0:
            return
        self.index -= 1
        self.show_step()

    def finish(self):
        self.highlight.set_target(None)
        self.popup.hide()