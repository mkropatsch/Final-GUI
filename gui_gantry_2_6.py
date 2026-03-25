# gui_gantry_2_6.py

### gui_gantry_2.6
# ------ Adding microscope tab


from __future__ import annotations

import sys
import multiprocessing as mp
from typing import Dict, Optional
import cv2
import pygame

from PyQt5.QtCore import Qt, QTimer, QDateTime
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTabBar,
    QVBoxLayout,
    QWidget,
    QSlider,
    QPlainTextEdit,
    QStackedWidget
)
import pyqtgraph as pg

from tabs.sensors_tab import SensorsTab

from tabs.automation_tab import AutomationTab

from tabs.microscope_tab import MicroscopeTab

try:
    import serial.tools.list_ports as list_ports
except Exception:
    list_ports = None


pg.setConfigOption("background", "#1e1e1e") #dark gray
pg.setConfigOption("foreground", "#dddddd") #soft white


# --------------------------- child (gantry) entry ----------------------------
def gantry_process_main(q_to_gui, q_from_gui, q_from_controller,
                        simulate: bool, port: str | None = None) -> None:
    from gantry_gui_new_automate_2_2 import GantrySystem

    g = GantrySystem(
        q_to_gui=q_to_gui,
        q_from_gui=q_from_gui,
        q_from_controller=q_from_controller,
        simulate=simulate,
        port=port,
    )
    g.run()

# ---- Adding Xbox controller -----

def controller_process_main(q_from_gui_to_ctrl, q_to_gantry,
                            joystick_index: int = 0) -> None:
    from controller_2_0 import XboxController
    
    ctrl = XboxController(
        q_from_gui_to_ctrl=q_from_gui_to_ctrl,
        q_to_gantry=q_to_gantry,
        joystick_index=joystick_index,
    )
    ctrl.read_controller()


# ---------------------------------- GUI --------------------------------------
class StageGUI2(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Gantry Control, Sensors, Automation 2.6")
        self.resize(1450, 900)

        # multiprocessing context
        self.ctx = mp.get_context("spawn")

        # backend IPC/process placeholders
        self.q_gantry_to_gui = None
        self.q_gui_to_gantry = None
        self.q_ctrl_to_gantry = None
        self.p_gantry = None
        self.q_gui_to_controller = None

        self.p_gantry = None
        self.p_controller = None

        self._connected = False
        self._controller_connected = False
        self._joystick_index = 0

        # cached state
        self._last_abs = {"x": 0.0, "y": 0.0, "z": 0.0, "e": 0.0}

        ## Camera state
        self.camera_cap = None
        self.camera_connected = False
        self.camera_preview_live = False
        self.current_camera_index = None

        self.camera_timer = QTimer(self)
        self.camera_timer.setInterval(33)
        self.camera_timer.timeout.connect(self._update_camera_frame)

        # --------------------------- root layout ---------------------------
        root = QWidget(self)
        self.setCentralWidget(root)
        main = QVBoxLayout(root)
        main.setContentsMargins(12, 12, 12, 12)
        main.setSpacing(10)

        # --------------------------- top tabs row --------------------------
        self.tabs = QTabBar()
        self.tabs.addTab("Gantry")
        self.tabs.addTab("Sensors")
        self.tabs.addTab("Microscope")
        self.tabs.addTab("Automation")
        self.tabs.setExpanding(False)
        self.tabs.setCurrentIndex(0)
        main.addWidget(self.tabs)
        
        self.pages = QStackedWidget()
        main.addWidget(self.pages, 1)
        
        self.gantry_page = QWidget()
        self.gantry_layout = QVBoxLayout(self.gantry_page)
        self.gantry_layout.setContentsMargins(0, 0, 0, 0)
        self.gantry_layout.setSpacing(10)

        # -------------------------- connection row -------------------------
        conn_box = QGroupBox("Connection")
        conn_layout = QHBoxLayout(conn_box)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Simulator", "Board", "Controller"])
        self.mode_combo.setMinimumWidth(140)

        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(220)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_connect = QPushButton("Connect")

        self.status_label = QLabel("Disconnected")
        self.status_label.setMinimumWidth(180)
        self.status_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.motion_hint = QLabel("Connect to enable movement.")
        self.motion_hint.setStyleSheet("color: #ffaa00; font-weight: bold;")

        conn_layout.addWidget(QLabel("Mode"))
        conn_layout.addWidget(self.mode_combo)
        conn_layout.addSpacing(12)
        conn_layout.addWidget(QLabel("Port"))
        conn_layout.addWidget(self.port_combo)
        conn_layout.addWidget(self.btn_refresh)
        conn_layout.addWidget(self.btn_connect)
        conn_layout.addSpacing(12)
        conn_layout.addWidget(self.status_label)
    
        conn_layout.addStretch()
        conn_layout.addWidget(self.motion_hint)

        self.gantry_layout.addWidget(conn_box)

        # -------------------------- main content ---------------------------
        content = QHBoxLayout()
        content.setSpacing(12)
        self.gantry_layout.addLayout(content)


        # ----- left side: graph + position
        left_box = QGroupBox("Gantry Coordinate View")
        left_layout = QVBoxLayout(left_box)
        left_layout.setSpacing(5)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setAlignment(Qt.AlignTop)

        self.xy_plot = pg.PlotWidget()

        #dark styling
        self.xy_plot.setBackground("#1e1e1e")
        self.xy_plot.setAspectLocked(False)
        self.xy_plot.showGrid(x=True, y=True, alpha=0.25)
        self.xy_plot.setLabel("left", "Y (mm)", color="#cccccc")
        self.xy_plot.setLabel("bottom", "X (mm)", color="#cccccc")
        self.xy_plot.invertY(True)
        #self.xy_plot.setFixedHeight(450)
        self.xy_plot.setMinimumHeight(320)
        self.xy_plot.setMaximumHeight(400)
        self.xy_plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Fixed plot:
        self.xy_plot.setXRange(-10, 110, padding=0)
        self.xy_plot.setYRange(-10, 110, padding=0)
        self.xy_plot.setLimits(xMin=-1000, xMax=1000, yMin=-1000, yMax=1000)


        self._xy_point = self.xy_plot.plot(
            [0], [0],
            pen=None,
            symbol="o",
            symbolSize=10,
            symbolBrush="#4fc3f7", #soft cyan
            symbolPen=None
        )

        axis = self.xy_plot.getAxis("left")
        axis.setPen("#888888")

        axis = self.xy_plot.getAxis("bottom")
        axis.setPen("#888888")


        pos_box = QGroupBox("Position")
        pos_box.setFixedHeight(65)
        pos_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        pos_layout = QHBoxLayout(pos_box)
        pos_layout.setContentsMargins(12, 8, 12, 8)

        self.lab_x = QLabel("0.000")
        self.lab_y = QLabel("0.000")
        self.lab_z = QLabel("0.000")

        for lab in (self.lab_x, self.lab_y, self.lab_z):
            lab.setMinimumWidth(80)
            lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        pos_layout.addWidget(QLabel("X ="))
        pos_layout.addWidget(self.lab_x)
        pos_layout.addSpacing(16)
        pos_layout.addWidget(QLabel("Y ="))
        pos_layout.addWidget(self.lab_y)
        pos_layout.addSpacing(16)
        pos_layout.addWidget(QLabel("Z ="))
        pos_layout.addWidget(self.lab_z)
        pos_layout.addStretch()

        # Camera view
        self.camera_group = QGroupBox("Camera View")
        camera_layout = QVBoxLayout(self.camera_group)
        camera_layout.setSpacing(8)
        camera_layout.setContentsMargins(10, 10, 10, 10)

        self.camera_view = QLabel("No camera connected")
        self.camera_view.setAlignment(Qt.AlignCenter)
        self.camera_view.setMinimumHeight(300)
        self.camera_view.setMaximumHeight(380)
        self.camera_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.camera_view.setStyleSheet("""
            QLabel {
                background-color: #050505;
                border: 1px solid #444444;
                color: #bfc7d5;
                font-size: 15px;
            }
        """)
        camera_ctrl_row = QHBoxLayout()
        camera_ctrl_row.setSpacing(6)

        self.camera_combo = QComboBox()
        self.camera_combo.setMinimumWidth(150)

        self.btn_camera_refresh = QPushButton("Refresh")
        self.btn_camera_connect = QPushButton("Connect")
        self.btn_camera_view = QPushButton("Start View")

        camera_ctrl_row.addWidget(QLabel("Select Camera"))
        camera_ctrl_row.addWidget(self.camera_combo, 1)
        camera_ctrl_row.addWidget(self.btn_camera_refresh)
        camera_ctrl_row.addWidget(self.btn_camera_connect)
        camera_ctrl_row.addWidget(self.btn_camera_view)

        camera_layout.addWidget(self.camera_view)
        camera_layout.addLayout(camera_ctrl_row)

        left_layout.addWidget(self.xy_plot, 1)
        left_layout.addWidget(pos_box)
        left_layout.addWidget(self.camera_group)


        # ----- right side: jog + step/feed
        right_col = QVBoxLayout()
        right_col.setSpacing(8)

        self.manual_group = QGroupBox("Manual Jog")
        mc = QGridLayout(self.manual_group)
        mc.setHorizontalSpacing(6)
        mc.setVerticalSpacing(6)

        def mk(btn_text: str, w: int = 58, h: int = 38) -> QPushButton:
            b = QPushButton(btn_text)
            b.setFixedSize(w, h)
            return b

        self.btn_ul = mk("↖")
        self.btn_up = mk("↑")
        self.btn_ur = mk("↗")
        self.btn_zp = mk("Z↑", 58, 38)

        self.btn_lf = mk("←")
        self.btn_c = mk("•")
        self.btn_rt = mk("→")

        self.btn_dl = mk("↙")
        self.btn_dn = mk("↓")
        self.btn_dr = mk("↘")
        self.btn_zm = mk("Z↓", 58, 38)

        mc.addWidget(self.btn_ul, 0, 0)
        mc.addWidget(self.btn_up, 0, 1)
        mc.addWidget(self.btn_ur, 0, 2)
        mc.addWidget(self.btn_zp, 0, 3)

        mc.addWidget(self.btn_lf, 1, 0)
        mc.addWidget(self.btn_c, 1, 1)
        mc.addWidget(self.btn_rt, 1, 2)

        mc.addWidget(self.btn_dl, 2, 0)
        mc.addWidget(self.btn_dn, 2, 1)
        mc.addWidget(self.btn_dr, 2, 2)
        mc.addWidget(self.btn_zm, 2, 3)

        # ----- Relative move -----
        self.rel_group = QGroupBox()
        rel_outer = QVBoxLayout(self.rel_group)
        rel_outer.setSpacing(4)

        # title row
        rel_title = QHBoxLayout()
        rel_title.setSpacing(4)

        rel_label = QLabel("Relative Move")

        self.rel_help = QPushButton("?")
        self.rel_help.setFixedSize(16, 16)
        self.rel_help.setStyleSheet("""
            QPushButton {
                color: #aaaaaa;
                background-color: transparent;
                border: 1px solid #666666;
                border-radius: 8px;
                font-weight: bold;
                padding: 0px;
            }
            QPushButton:hover {
                color: #ffffff;
                border: 1px solid #aaaaaa;
            }
        """)

        rel_title.addWidget(rel_label)
        rel_title.addWidget(self.rel_help)
        rel_title.addStretch()

        # input row
        rel_layout = QHBoxLayout()
        rel_layout.setSpacing(6)

        self.rel_x = QLineEdit("0")
        self.rel_y = QLineEdit("0")
        self.rel_z = QLineEdit("0")
        self.rel_x.setFixedWidth(55)
        self.rel_y.setFixedWidth(55)
        self.rel_z.setFixedWidth(55)

        self.btn_rel_move = QPushButton("Move")
        self.btn_rel_move.setFixedWidth(70)

        rel_layout.addWidget(QLabel("ΔX"))
        rel_layout.addWidget(self.rel_x)
        rel_layout.addWidget(QLabel("ΔY"))
        rel_layout.addWidget(self.rel_y)
        rel_layout.addWidget(QLabel("ΔZ"))
        rel_layout.addWidget(self.rel_z)
        rel_layout.addWidget(self.btn_rel_move)
        rel_layout.addStretch()

        rel_outer.addLayout(rel_title)
        rel_outer.addLayout(rel_layout)

        self.rel_help.clicked.connect(
            lambda: QMessageBox.information(
                self,
                "Relative Move",
                "Moves the gantry by a specified amount from its current position.\n\n"
                "Example:\n"
                "ΔX = 5 moves 5 mm from where it is now" ### Change because idk how much it actually moves
            )
        )

        # ----- Absolute move -----
        self.abs_group = QGroupBox()
        abs_outer = QVBoxLayout(self.abs_group)
        abs_outer.setSpacing(4)

        # Title row
        abs_title = QHBoxLayout()
        abs_title.setSpacing(4)

        abs_label = QLabel("Absolute Move")

        self.abs_help = QPushButton("?")
        self.abs_help.setFixedSize(16, 16)
        self.abs_help.setStyleSheet("""
            QPushButton {
                color: #aaaaaa;
                background-color: transparent;
                border: 1px solid #666666;
                border-radius: 8px;
                font-weight: bold;
                padding: 0px;
            }
            QPushButton:hover {
                color: #ffffff;
                border: 1px solid #aaaaaa;
            }
        """)

        abs_title.addWidget(abs_label)
        abs_title.addWidget(self.abs_help)
        abs_title.addStretch()

        #input row
        abs_layout = QHBoxLayout()
        abs_layout.setSpacing(6)

        self.abs_x = QLineEdit("0")
        self.abs_y = QLineEdit("0")
        self.abs_z = QLineEdit("0")
        self.abs_x.setFixedWidth(55)
        self.abs_y.setFixedWidth(55)
        self.abs_z.setFixedWidth(55)

        self.btn_abs_move = QPushButton("Go to")
        self.btn_abs_move.setFixedWidth(70)

        abs_layout.addWidget(QLabel("X"))
        abs_layout.addWidget(self.abs_x)
        abs_layout.addWidget(QLabel("Y"))
        abs_layout.addWidget(self.abs_y)
        abs_layout.addWidget(QLabel("Z"))
        abs_layout.addWidget(self.abs_z)
        abs_layout.addWidget(self.btn_abs_move)
        abs_layout.addStretch()

        abs_outer.addLayout(abs_title)
        abs_outer.addLayout(abs_layout)

        self.abs_help.clicked.connect(
            lambda: QMessageBox.information(
                self,
                "Absolute Move",
                "Moves the gantry to the entered coordinate in the current coordinate system.\n\n"
                "Example:\n"
                "X = 10 moves to X = 10 from the current origin."
            )
        )

# ---- Step / Feed -----
        self.ctrl_group = QGroupBox()
        ctrl_outer = QVBoxLayout(self.ctrl_group)
        ctrl_outer.setSpacing(4)

        # title row
        ctrl_title = QHBoxLayout()
        ctrl_title.setSpacing(4)
        
        ctrl_label = QLabel("Step / Feed")

        self.ctrl_help = QPushButton("?")
        self.ctrl_help.setFixedSize(16, 16)
        self.ctrl_help.setStyleSheet("""
            QPushButton {
                color: #aaaaaa;
                background-color: transparent;
                border: 1px solid #666666;
                border-radius: 8px;
                font-weight: bold;
                padding: 0px;
            }
            QPushButton:hover {
                color: #ffffff;
                border: 1px solid #aaaaaa;
            }
        """)

        ctrl_title.addWidget(ctrl_label)
        ctrl_title.addWidget(self.ctrl_help)
        ctrl_title.addStretch()

        # form contents
        ctrl_form = QFormLayout()
        ctrl_form.setSpacing(6)

        self.in_xy = QLineEdit("0.200")
        self.in_z = QLineEdit("0.050")

        self.sld_feed = QSlider(Qt.Horizontal)
        self.sld_feed.setRange(100, 12000)
        self.sld_feed.setValue(3000)

        self.lab_feed = QLabel("3000 mm/min")
        self.lab_feed.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        feed_row = QHBoxLayout()
        feed_row.addWidget(self.sld_feed, 1)
        feed_row.addWidget(self.lab_feed)

        self.btn_apply_steps = QPushButton("Apply Step Sizes")
        self.btn_set_home = QPushButton("Set Home")
        self.btn_home = QPushButton("Machine Home")
        self.btn_estop = QPushButton("Emergency Stop")
        self.btn_estop.setStyleSheet(
            "QPushButton { background:#b51f1f; color:white; font-weight:bold; }"
        )

        action_row = QHBoxLayout()
        #apply step sizes
        action_row.addWidget(self.btn_apply_steps)
        #set home + help
        set_home_layout = QHBoxLayout()
        set_home_layout.setSpacing(2)

        self.set_home_help = QPushButton("?")
        self.set_home_help.setFixedSize(16, 16)
        self.set_home_help.setStyleSheet("""
            QPushButton {
                color: #aaaaaa;
                background-color: transparent;
                border: 1px solid #666666;
                border-radius: 8px;
                font-weight: bold;
                padding: 0px;
            }
            QPushButton:hover {
                color: #ffffff;
                border: 1px solid #aaaaaa;
            }
        """)

        set_home_layout.addWidget(self.btn_set_home)
        set_home_layout.addWidget(self.set_home_help)

        action_row.addLayout(set_home_layout)

        # Machine home + help
        machine_home_layout = QHBoxLayout()
        machine_home_layout.setSpacing(2)

        self.machine_home_help = QPushButton("?")
        self.machine_home_help.setFixedSize(16, 16)
        self.machine_home_help.setStyleSheet(self.set_home_help.styleSheet())

        machine_home_layout.addWidget(self.btn_home)
        machine_home_layout.addWidget(self.machine_home_help)

        action_row.addLayout(machine_home_layout)

        self.set_home_help.clicked.connect(
           lambda: QMessageBox.information(
               self,
               "Set Home",
               "Sets the current position as (0,0,0) without moving the gantry.\n\n"
               "This is a software-defined home.\n\n"
               "Use 'Machine Home' to move to physical endstops."
           )
       )

        self.machine_home_help.clicked.connect(
            lambda: QMessageBox.information(
                self,
                "Machine Home",
                "Moves the gantry to its physical home using endstops (G28).\n\n"
                "This overrides any user-defined home."
            )
        )

        ctrl_form.addRow("XY step (mm)", self.in_xy)
        ctrl_form.addRow("Z step (mm)", self.in_z)
        ctrl_form.addRow("Feed (mm/min)", feed_row)
        ctrl_form.addRow(action_row)
        ctrl_form.addRow(self.btn_estop)

        ctrl_outer.addLayout(ctrl_title)
        ctrl_outer.addLayout(ctrl_form)

        self.ctrl_help.clicked.connect(
            lambda: QMessageBox.information(
                self,
                "Step / Feed",
                "XY step and Z step set how far the gantry moves for each jog input.\n\n"
                "Feed sets how fast the gantry moves in mm/min.\n"
                "Smaller step sizes move farther with each command.\n"
                "Larger step sizes move farther with each command.\n"
                "Higher feed values move faster."
            )
        )

        # ---- Messages box
        self.msg_group = QGroupBox("Messages")
        msg_layout = QVBoxLayout(self.msg_group)
        msg_layout.setSpacing(6)

        msg_top = QHBoxLayout()
        msg_top.addStretch()

        self.btn_clear_messages = QPushButton("Clear Messages")
        self.btn_clear_messages.setFixedWidth(120)
        msg_top.addWidget(self.btn_clear_messages)

        self.msg_box = QPlainTextEdit()
        self.msg_box.setReadOnly(True)
        self.msg_box.setMinimumHeight(90)
        self.msg_box.setMaximumHeight(120)

        msg_layout.addLayout(msg_top)
        msg_layout.addWidget(self.msg_box)

        right_col.addWidget(self.manual_group)
        right_col.addWidget(self.rel_group)
        right_col.addWidget(self.abs_group)
        right_col.addWidget(self.ctrl_group)
        right_col.addWidget(self.msg_group)
        right_col.addStretch()

        content.addWidget(left_box, 3)
        content.addLayout(right_col, 2)


        # ------------------------------- timers ------------------------------
        self._poll = QTimer(self)
        self._poll.setInterval(50)
        self._poll.timeout.connect(self._drain_gantry_messages)
        self._poll.start()

        self._mk_jog_timers()

        # ----------------------------- signals -------------------------------
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        self.btn_refresh.clicked.connect(self.refresh_ports)
        self.btn_connect.clicked.connect(self._on_connect_clicked)

        self.btn_camera_refresh.clicked.connect(self.refresh_cameras)
        self.btn_camera_connect.clicked.connect(self._on_camera_connect_clicked)
        self.btn_camera_view.clicked.connect(self._on_camera_view_clicked)

        self.btn_clear_messages.clicked.connect(self.msg_box.clear)

        self.sld_feed.valueChanged.connect(
            lambda v: self.lab_feed.setText(f"{v} mm/min")
        )
        self.sld_feed.sliderReleased.connect(self._apply_feed_to_gantry)
        self.btn_apply_steps.clicked.connect(self._apply_steps_to_gantry)
        self.btn_set_home.clicked.connect(self._on_set_home)
        self.btn_home.clicked.connect(self._on_home)
        self.btn_estop.clicked.connect(self._on_estop)

        self.btn_c.clicked.connect(lambda: None)

        self.btn_rel_move.clicked.connect(self._on_relative_move)
        self.btn_abs_move.clicked.connect(self._on_absolute_move)

        self._set_motion_controls_enabled(False)

        # optional dark style
        try:
            import qdarkstyle
            self.setStyleSheet(qdarkstyle.load_stylesheet_pyqt5())
        except Exception:
            pass

        self._on_mode_changed(self.mode_combo.currentText())
        self.refresh_ports()

        # Initialize camera UI
        self.refresh_cameras()
        self._update_camera_placeholder("No camera connected")
        self.btn_camera_view.setEnabled(False)

        self._set_motion_controls_enabled(False)


        self.sensors_tab_widget = SensorsTab()
        self.microscope_tab_widget = MicroscopeTab()
        self.automation_tab_widget = AutomationTab()

        self.pages.addWidget(self.gantry_page)          # index 0
        self.pages.addWidget(self.sensors_tab_widget)   # index 1
        self.pages.addWidget(self.microscope_tab_widget)      # index 2
        self.pages.addWidget(self.automation_tab_widget)      # index 3
        
        self.tabs.currentChanged.connect(self.pages.setCurrentIndex)
        self.pages.setCurrentIndex(0)

    # -------------------------- connection logic --------------------------
    def _on_mode_changed(self, mode: str):
        hardware_mode = mode in ("Board", "Controller")
        self.port_combo.setEnabled(hardware_mode)
        self.btn_refresh.setEnabled(hardware_mode)

        if hardware_mode:
            self.refresh_ports()
        else:
            self.port_combo.clear()
            self.port_combo.addItem("(not needed for simulator)")

    def refresh_ports(self):
        self.port_combo.clear()

        if not self.port_combo.isEnabled():
            self.port_combo.addItem("(not needed for simulator)")
            return

        if list_ports is None:
            self.port_combo.addItem("(pyserial not available)")
            return

        ports = list(list_ports.comports())
        if not ports:
            self.port_combo.addItem("(no ports found)")
            return

        for p in ports:
            label = f"{p.device}"
            if p.description:
                label += f" — {p.description}"
            self.port_combo.addItem(label, p.device)

    def _on_connect_clicked(self):
        if self._connected:
            self._disconnect_backend()
            return

        mode = self.mode_combo.currentText()
        simulate = mode == "Simulator"
        selected_port = None

        self.motion_hint.setText("Ready")

        if not simulate:
            if self.port_combo.count() == 0:
                QMessageBox.warning(self, "Connect", "No ports are available.")
                return
            current_text = self.port_combo.currentText().strip()
            if not current_text or current_text.startswith("("):
                QMessageBox.warning(self, "Connect", "Please select a valid port.")
                return
            
            selected_port = self.port_combo.currentData() or self.port_combo.currentText()

        self._start_backend(simulate=simulate, port=selected_port)

        #start controller only for hardware modes
        if mode in ("Board", "Controller"):
            try:
                self._start_controller(joystick_index=self._joystick_index)
            except Exception as e:
                self._post_msg(f"WARNING: Controller failed to start: {e}")

        if mode == "Simulator":
            self.status_label.setText("Connected: Simulator")
        else:
            self.status_label.setText(f"Connected: {mode} ({selected_port})")

        self.btn_connect.setText("Disconnect")
        self.motion_hint.setText("Ready")
        self.motion_hint.setStyleSheet("color: #55ff55; font-weight: bold;")
        self._set_motion_controls_enabled(True)
        self._post_msg(f"{mode} connection started.")

    def _start_backend(self, simulate: bool, port: str | None = None):
        self._disconnect_backend(silent=True)

        self.q_gantry_to_gui = self.ctx.Queue(maxsize=1000)
        self.q_gui_to_gantry = self.ctx.Queue(maxsize=1000)
        self.q_ctrl_to_gantry = self.ctx.Queue(maxsize=1000)

        self.p_gantry = self.ctx.Process(
            target=gantry_process_main,
            args=(
                self.q_gantry_to_gui,
                self.q_gui_to_gantry,
                self.q_ctrl_to_gantry,
                simulate,
                port,
            ),
            daemon=True,
        )
        self.p_gantry.start()
        self._connected = True

    ## adding controller
    def _start_controller(self, joystick_index: int = 0):
        if self.q_ctrl_to_gantry is None:
            raise RuntimeError("Controller queue is not initialized.")
        
        if self.p_controller is not None:
            try:
                if self.p_controller.is_alive():
                    return
            except Exception:
                pass

        self.q_gui_to_controller = self.ctx.Queue(maxsize=200)

        self.p_controller = self.ctx.Process(
            target=controller_process_main,
            args=(
                self.q_gui_to_controller,
                self.q_ctrl_to_gantry,
                joystick_index,
            ),
            daemon=True,
        )
        self.p_controller.start()

        self._controller_connected = True
        self._joystick_index = joystick_index
        self._post_msg(f"Controller process started (joystick index {joystick_index}).")

    def _stop_controller(self):
        if self.p_controller is not None:
            try:
                self.p_controller.terminate()
                self.p_controller.join(timeout=1.0)
            except Exception:
                pass
        
        self.p_controller = None
        self.q_gui_to_controller = None
        self._controller_connected = False


    def _disconnect_backend(self, silent: bool = False):
        self._stop_all_jog_timers()
        self._stop_controller()

        self.motion_hint.setText("Connect to enable gantry movement")

        if self.p_gantry is not None:
            try:
                self.p_gantry.terminate()
                self.p_gantry.join(timeout=1.0)
            except Exception:
                pass

        self.p_gantry = None
        self.q_gantry_to_gui = None
        self.q_gui_to_gantry = None
        self.q_ctrl_to_gantry = None
        was_connected = self._connected
        self._connected = False

        self.btn_connect.setText("Connect")
        self.status_label.setText("Disconnected")
        self.motion_hint.setText("Connect to enable gantry movement")
        self.motion_hint.setStyleSheet("color: #ffaa00; font-weight: bold;")
        self._set_motion_controls_enabled(False)

        if was_connected and not silent:
            self._post_msg("Disconnected.")


    # ---- Camera logic -----
    def refresh_cameras(self):
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

    def _update_camera_placeholder(self, text: str):
        self.camera_view.clear()
        self.camera_view.setPixmap(QPixmap())
        self.camera_view.setText(text)
        self.camera_view.setAlignment(Qt.AlignCenter)

    def _on_camera_connect_clicked(self):
        if self.camera_connected:
            self._disconnect_camera()
            return
        
        cam_index = self.camera_combo.currentData()
        if cam_index is None:
            QMessageBox.warning(self, "Camera", "Please select a valid camera.")
            return
        
        cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
        if cap is None or not cap.isOpened():
            QMessageBox.warning(self, "Camera", f"Could not open camera {cam_index}.")
            return
        
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        
        self.camera_cap = cap
        self.camera_connected = True
        self.current_camera_index = cam_index

        self.btn_camera_connect.setText("Disconnect")
        self.btn_camera_view.setEnabled(True)
        self._update_camera_placeholder("Camera connected - preview off")
        self._post_msg(f"Camera {cam_index} connected.")

    def _on_camera_view_clicked(self):
        if not self.camera_connected or self.camera_cap is None:
            QMessageBox.warning(self, "Camera", "Connect a camera first.")
            return
        
        if self.camera_preview_live:
            self.camera_timer.stop()
            self.camera_preview_live = False
            self.btn_camera_view.setText("Start View")
            self._update_camera_placeholder("Camera connected - preview off")
            self._post_msg("Camera preview stopped.")
            return
        
        self.camera_timer.start()
        self.camera_preview_live = True
        self.btn_camera_view.setText("Stop View")
        self._post_msg("Camera preview started.")

    def _update_camera_frame(self):
        if not self.camera_connected or self.camera_cap is None:
            return
        
        ret, frame = self.camera_cap.read()
        if not ret or frame is None:
            self.camera_timer.stop()
            self.camera_preview_live = False
            self.btn_camera_view.setText("Start View")
            self._update_camera_placeholder("Preview unavailable")
            self._post_msg("WARNING: Failed to read camera frame.")
            return
        
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = frame_rgb.shape
        bytes_per_line = ch * w

        qimg = QImage(frame_rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)

        pix = QPixmap.fromImage(qimg)
        scaled = pix.scaled(
            self.camera_view.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

        self.camera_view.setPixmap(scaled)
        self.camera_view.setText("")

    def _disconnect_camera(self):
        self.camera_timer.stop()
        self.camera_preview_live = False

        if self.camera_cap is not None:
            try:
                self.camera_cap.release()
            except Exception:
                pass

        self.camera_cap = None
        self.camera_connected = False
        self.current_camera_index = None

        self.btn_camera_connect.setText("Connect")
        self.btn_camera_view.setText("Start View")
        self.btn_camera_view.setEnabled(False)
        self._update_camera_placeholder("No camera connected")
        self._post_msg("Camera disconnected.")

    # ---------------------------- jog timers ----------------------------
    def _mk_jog_timers(self):
        self._t = {}

        def mk_timer(name: str, fn):
            t = QTimer(self)
            t.setInterval(50)
            t.timeout.connect(fn)
            self._t[name] = t

        mk_timer("up", lambda: self._jog_xy(0, +1))
        mk_timer("dn", lambda: self._jog_xy(0, -1))
        mk_timer("lf", lambda: self._jog_xy(-1, 0))
        mk_timer("rt", lambda: self._jog_xy(+1, 0))
        mk_timer("ul", lambda: self._jog_xy(-1, +1))
        mk_timer("ur", lambda: self._jog_xy(+1, +1))
        mk_timer("dl", lambda: self._jog_xy(-1, -1))
        mk_timer("dr", lambda: self._jog_xy(+1, -1))
        mk_timer("zp", lambda: self._jog_z(+1))
        mk_timer("zm", lambda: self._jog_z(-1))

        self.btn_up.pressed.connect(self._t["up"].start)
        self.btn_up.released.connect(self._t["up"].stop)

        self.btn_dn.pressed.connect(self._t["dn"].start)
        self.btn_dn.released.connect(self._t["dn"].stop)

        self.btn_lf.pressed.connect(self._t["lf"].start)
        self.btn_lf.released.connect(self._t["lf"].stop)

        self.btn_rt.pressed.connect(self._t["rt"].start)
        self.btn_rt.released.connect(self._t["rt"].stop)

        self.btn_ul.pressed.connect(self._t["ul"].start)
        self.btn_ul.released.connect(self._t["ul"].stop)

        self.btn_ur.pressed.connect(self._t["ur"].start)
        self.btn_ur.released.connect(self._t["ur"].stop)

        self.btn_dl.pressed.connect(self._t["dl"].start)
        self.btn_dl.released.connect(self._t["dl"].stop)

        self.btn_dr.pressed.connect(self._t["dr"].start)
        self.btn_dr.released.connect(self._t["dr"].stop)

        self.btn_zp.pressed.connect(self._t["zp"].start)
        self.btn_zp.released.connect(self._t["zp"].stop)

        self.btn_zm.pressed.connect(self._t["zm"].start)
        self.btn_zm.released.connect(self._t["zm"].stop)

    def _stop_all_jog_timers(self):
        if not hasattr(self, "_t"):
            return
        for t in self._t.values():
            t.stop()

    # ---------------------------- send helpers ----------------------------
    def _send_gui_msg(self, msg: Dict):
        if self.q_gui_to_gantry is not None:
            self.q_gui_to_gantry.put(msg)

    def _send_ctrl_msg(self, msg: Dict):
        if self.q_ctrl_to_gantry is not None:
            self.q_ctrl_to_gantry.put(msg)

    # --- Disable jog until connected
    def _set_motion_controls_enabled(self, enabled: bool):
        widgets = [
            self.btn_ul, self.btn_up, self.btn_ur,
            self.btn_lf, self.btn_c, self.btn_rt,
            self.btn_dl, self.btn_dn, self.btn_dr,
            self.btn_zp, self.btn_zm,
            self.btn_rel_move, self.btn_abs_move,
            self.btn_set_home, self.btn_home,
            self.btn_apply_steps, self.btn_estop,
            self.rel_x, self.rel_y, self.rel_z,
            self.abs_x, self.abs_y, self.abs_z,
            self.in_xy, self.in_z, self.sld_feed,
        ]
        for w in widgets:
            w.setEnabled(enabled)

    # ---------------------------- jog actions ----------------------------
    def _jog_xy(self, sx: int, sy: int):
        if not self._connected:
            self._stop_all_jog_timers()
            self._post_msg("WARNING: Not connected.")
            #QMessageBox.warning(self, "Warning", "Not connected.")
            return
        self._send_ctrl_msg({"type": "input", "cmd": "xy_motion", "value": (sx, sy)})

    def _jog_z(self, s: int):
        if not self._connected:
            self._stop_all_jog_timers()
            self._post_msg("WARNING: Not connected.")
            #QMessageBox.warning(self, "Warning", "Not connected.")
            return
        self._send_ctrl_msg({"type": "input", "cmd": "z_motion", "value": (0, s)})

    # -------------------------- control actions --------------------------
    def _apply_steps_to_gantry(self):
        if not self._connected:
            self._post_msg("WARNING: Not connected.")
            QMessageBox.warning(self, "Warning", "Not connected.")
            return

        try:
            xy = float(self.in_xy.text())
            z = float(self.in_z.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid", "Enter numeric step sizes.")
            return

        self._send_gui_msg({"type": "set_steps", "xy_step": xy, "z_step": z, "e_step": 0.020})
        self._post_msg(f"Applied step sizes: XY={xy:.3f}, Z={z:.3f}")

    def _apply_feed_to_gantry(self):
        if not self._connected:
            self._post_msg("WARNING: Not connected.")
            QMessageBox.warning(self, "Warning", "Not connected.")
            return
        val = int(self.sld_feed.value())
        self._send_gui_msg({"type": "set_feed", "feed_mm_min": val})

    def _on_home(self):
        if not self._connected:
            self._post_msg("WARNING: Not connected.")
            QMessageBox.warning(self, "Warning", "Not connected.")
            return
        self._send_gui_msg({"type": "home_all"})
        self._post_msg("Machine Home requested.")

    def _on_set_home(self):
        if not self._connected:
            self._post_msg("WARNING: Not connected.")
            QMessageBox.warning(self, "Warning", "Not connected.")
            return
        self._send_gui_msg({
            "type": "gantry_cmd",
            "cmd": "set_home",
        })
        self._post_msg("Set home sent. Current position is now (0, 0, 0).")

    def _on_estop(self):
        self._stop_all_jog_timers()
        if not self._connected:
            self._post_msg("WARNING: Not connected.")
            QMessageBox.warning(self, "Warning", "Not connected.")
            return
        self._send_gui_msg({"type": "btn_estop"})
        self._post_msg("Emergency sent.")

    def _on_relative_move(self):
        if not self._connected:
            self._post_msg("WARNING: Not connected.")
            QMessageBox.warning(self, "Warning", "Not connected.")
            return
        
        try:
            dx = float(self.rel_x.text().strip() or "0")
            dy = float(self.rel_y.text().strip() or "0")
            dz = float(self.rel_z.text().strip() or "0")
        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Relative move values must be numeric.")
            return
        
        self._send_gui_msg({
            "type": "gantry_cmd",
            "cmd": "move_rel",
            "dx": dx,
            "dy": dy,
            "dz": dz,
            "feed_mm_min": int(self.sld_feed.value()),
        })
        
        # placeholder behavior for now
        self._post_msg(f"Relative move requested: ΔX={dx:.3f}, ΔY={dy:.3f}, ΔZ={dz:.3f}")

    def _on_absolute_move(self):
        if not self._connected:
            self._post_msg("WARNING: Not connected.")
            QMessageBox.warning(self, "Warning", "Not connected.")
            return
        
        try:
            x = float(self.abs_x.text().strip() or "0")
            y = float(self.abs_y.text().strip() or "0")
            z = float(self.abs_z.text().strip() or "0")
        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Absolute move values must be numeric.")
            return
        
        self._send_gui_msg({
            "type": "gantry_cmd",
            "cmd": "move_abs",
            "X": x,
            "Y": y,
            "Z": z,
            "feed_mm_min": int(self.sld_feed.value()),
        })
        
        # placehold behavior for now
        self._post_msg(f"Absolute move requested: X={x:.3f}, Y={y:.3f}, Z={z:.3f}")


    # --------------------------- backend polling ---------------------------
    def _drain_gantry_messages(self):
        if self.q_gantry_to_gui is None:
            return

        while not self.q_gantry_to_gui.empty():
            msg = self.q_gantry_to_gui.get()

            if not isinstance(msg, dict):
                continue

            typ = msg.get("type")
            if typ == "state":
                self._apply_state(msg)
            elif typ == "message":
                level = str(msg.get("level", "info")).upper()
                text = str(msg.get("text", ""))
                self._post_msg(f"{level}: {text}")

                if level == "WARNING":
                    QMessageBox.warning(self, "Warning", text)
                elif level == "ERROR":
                    QMessageBox.critical(self, "Error", text)
            elif typ == "controller_state":
                mapping = msg.get("mapping", {})
                self._post_msg(f"Controller mapping loaded: {mapping}")

    def _apply_state(self, s: Dict):
        ax = float(s.get("x", 0.0))
        ay = float(s.get("y", 0.0))
        az = float(s.get("z", 0.0))
        ae = float(s.get("e", 0.0))

        self._last_abs.update({"x": ax, "y": ay, "z": az, "e": ae})

        self._xy_point.setData([ax], [ay])
        self.lab_x.setText(f"{ax:.3f}")
        self.lab_y.setText(f"{ay:.3f}")
        self.lab_z.setText(f"{az:.3f}")

        if "xy_step" in s:
            self.in_xy.setText(f"{float(s['xy_step']):.3f}")
        if "z_step" in s:
            self.in_z.setText(f"{float(s['z_step']):.3f}")
        if "feed" in s:
            val = int(s["feed"])
            self.sld_feed.blockSignals(True)
            self.sld_feed.setValue(val)
            self.sld_feed.blockSignals(False)
            self.lab_feed.setText(f"{val} mm/min")

    # ------------------------------ messages ------------------------------
    def _post_msg(self, text: str):
        stamp = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        self.msg_box.appendPlainText(f"[{stamp}] {text}")
        self.msg_box.verticalScrollBar().setValue(
            self.msg_box.verticalScrollBar().maximum()
        )

    # ------------------------------ shutdown ------------------------------
    def closeEvent(self, ev):
        # Prevent closing if camera is still connected
        gantry_cam_connected = getattr(self, "camera_connected", False)
        microscope_cam_connected = (
            hasattr(self, "microscope_tab_widget")
            and self.microscope_tab_widget is not None
            and getattr(self.microscope_tab_widget, "camera_connected", False)
        )
        if gantry_cam_connected or microscope_cam_connected:
            reply = QMessageBox.question(
                self,
                "Camera still connected",
                "Camera is still connected.\nDisconnect and exit?",
                QMessageBox.Yes | QMessageBox.No    
            )
            if reply == QMessageBox.Yes:
                if gantry_cam_connected:
                    self._disconnect_camera()
                if microscope_cam_connected:
                    self.microscope_tab_widget._disconnect_camera()
            else:
                ev.ignore()
                return
            
        # Normal shutdown if safe
        self._disconnect_backend(silent=True)
        self._disconnect_camera()
        
        if hasattr(self, "sensors_tab_widget") and self.sensors_tab_widget is not None:
            self.sensors_tab_widget.shutdown()
            
        ev.accept()


# ----------------------------------- entry -----------------------------------
def main():
    mp.freeze_support()
    try:
        mp.set_start_method("spawn")
    except RuntimeError:
        pass

    app = QApplication(sys.argv)
    win = StageGUI2()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()