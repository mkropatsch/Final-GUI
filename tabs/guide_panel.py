from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QWidget,
    QHBoxLayout,
    QFrame,
)
from PyQt5.QtCore import Qt


class GuideWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("System Setup & Guided Tour")
        self.resize(760, 560)
        self.setModal(False)

        self.setStyleSheet("""
            QDialog {
                background-color: #0f1b2b;
            }

            QLabel#TitleLabel {
                color: #ffffff;
                font-size: 26px;
                font-weight: 800;
            }

            QLabel#SubtitleLabel {
                color: #c7d7ea;
                font-size: 15px;
            }

            QFrame#SectionCard {
                background-color: #142233;
                border: 1px solid #2a3d55;
                border-left: 4px solid #4fc3f7;
                border-radius: 10px;
            }

            QLabel#SectionTitle {
                color: #f1f4f8;
                font-size: 18.5px;
                font-weight: 700;
            }

            QLabel#SectionBody {
                color: #d8e2ee;
                font-size: 15.5px;
                line-height: 1.6em;
            }

            QPushButton#PrimaryButton {
                background-color: #2f6ea3;
                color: white;
                border: 1px solid #4b86b8;
                border-radius: 6px;
                padding: 8px 14px;
                font-weight: 600;
            }

            QPushButton#PrimaryButton:hover {
                background-color: #3c7db3;
            }

            QPushButton#SecondaryButton {
                background-color: #2a3442;
                color: #d9e2ec;
                border: 1px solid #465364;
                border-radius: 6px;
                padding: 8px 14px;
                font-weight: 600;
            }

            QPushButton#SecondaryButton:hover {
                background-color: #364152;
            }

            QScrollArea {
                border: none;
                background: transparent;
            }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)

        # ---------------- header card ----------------
        header = QFrame()
        header.setStyleSheet("""
            QFrame {
                background-color: #1a2d44;
                border: 2px solid #4fc3f7;
                border-radius: 12px;
            }
        """)

        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(18, 16, 18, 16)
        header_layout.setSpacing(6)

        badge = QLabel("HELP")
        badge.setStyleSheet("""
            QLabel {
                background-color: #2f6ea3;
                color: white;
                border-radius: 8px;
                padding: 3px 10px;
                font-size: 11px;
                font-weight: 700;
                max-width: 50px;
            }
        """)

        title = QLabel("System Setup & Guided Tour")
        title.setObjectName("TitleLabel")

        subtitle = QLabel("Quick reference for setup, navigation, and basic operation.")
        subtitle.setObjectName("SubtitleLabel")
        subtitle.setWordWrap(True)

        header_layout.addWidget(badge, alignment=Qt.AlignLeft)
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)

        content_layout.addWidget(header)

        # ---------------- setup card ----------------
        setup_body = (
            "1. Select <b>Simulator</b> for testing or <b>Board</b> mode for real hardware.<br>"
            "2. Choose serial ports if using hardware.<br>"
            "3. Click <b>Connect</b> to initialize the system.<br>"
            "4. Select the motion target (<b>Needle</b>, <b>Camera</b>, or <b>Both</b>).<br>"
            "5. Use manual jog controls to verify movement and make small adjustments.<br>"
            "6. Move to your starting position and click <b>Set Home</b>.<br>"
            "7. Only start routines after home has been set.<br><br>"
            "<b>Tip:</b> If movement is not enabled, check that the board is properly connected."
        )
        content_layout.addWidget(self._make_section_card("System Setup", setup_body))

        # ---------------- overview card ----------------
        overview_body = (
            "<b>Gantry:</b> Connect system, control motion, and view camera.<br>"
            "<b>Sensors:</b> Monitor environmental and incubator data.<br>"
            "<b>Microscope:</b> Capture images, record video, and run detection.<br>"
            "<b>Automation:</b> Configure well plates, calibration, and routines.<br><br>"
            "<b>Typical workflow:</b><br>"
            "Gantry → Set Home → Sensors → Connect sensors → Microscope (optional) → Automation"
        )
        content_layout.addWidget(self._make_section_card("Tab Overview", overview_body))

        # ---------------- notes card ----------------
        notes_body = (
            "• <b>Machine Home</b> moves the system to its physical home using endstops.<br>"
            "• <b>Set Home</b> defines the current working origin in software.<br>"
            "• Use <b>Emergency Stop</b> if motion must be halted immediately.<br><br>"
            "<b>Common issues:</b><br>"
            "• No movement → system may not be connected<br>"
            "• Wrong position → home may not be set correctly<br>"
            "• Blank camera view → preview may not be started"
        )
        content_layout.addWidget(self._make_section_card("Notes", notes_body))

        # ---------------- buttons ----------------
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.btn_start_tour = QPushButton("Start Guided Tour")
        self.btn_start_tour.setObjectName("PrimaryButton")

        self.btn_close = QPushButton("Close")
        self.btn_close.setObjectName("SecondaryButton")

        btn_row.addWidget(self.btn_start_tour)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_close)

        content_layout.addLayout(btn_row)
        content_layout.addStretch()

        scroll.setWidget(content)
        root.addWidget(scroll)

        self.btn_close.clicked.connect(self.close)

    def _make_section_card(self, title_text: str, body_html: str) -> QFrame:
        card = QFrame()
        card.setObjectName("SectionCard")

        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title = QLabel(title_text)
        title.setObjectName("SectionTitle")

        body = QLabel(body_html)
        body.setObjectName("SectionBody")
        body.setWordWrap(True)
        body.setTextFormat(Qt.RichText)
        body.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        layout.addWidget(title)
        layout.addWidget(body)

        return card

    def closeEvent(self, event):
        event.accept()