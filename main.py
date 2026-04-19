# main.py

# Changing layout
    # Graph/camera sizing
    # Moved camera connection settings to the left


from __future__ import annotations

import sys
import multiprocessing as mp
from typing import Dict, Optional
import cv2
import pygame

from PyQt5.QtCore import Qt, QTimer, QDateTime, pyqtSignal
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
    QPlainTextEdit,
    QStackedWidget,
)
import pyqtgraph as pg

from tabs.guide_panel import GuideWindow
from tabs.guided_tour import GuidedTour
from tabs.sensors_tab import SensorsTab

from tabs.automation_tab import AutomationTab

from tabs.microscope_tab import MicroscopeTab
from tabs.calibration_dialog import CalibrationDialog

try:
    import serial.tools.list_ports as list_ports
except Exception:
    list_ports = None

try:
    sys.path.append(r'C:\Users\macke\Desktop\Project Code\camera_light_test')
    from DNX64 import DNX64 as _DNX64Class
    _DNX64_DLL = r'C:\Users\macke\Desktop\Project Code\camera_light_test\DNX64.dll'
except Exception:
    _DNX64Class = None
    _DNX64_DLL = None


pg.setConfigOption("background", "#1e1e1e") #dark gray
pg.setConfigOption("foreground", "#dddddd") #soft white

from backend.routine import RoutineController
from backend.incubator import IncubatorSerial
from backend.camera_manager import CameraManager

# --------------------------- child (gantry) entry ----------------------------
def gantry_process_main(q_to_gui, q_from_gui, q_from_controller,
                        simulate: bool,
                        ports: dict[str, str | None] | None = None) -> None:
    from backend.gantry import GantrySystem

    g = GantrySystem(
        q_to_gui=q_to_gui,
        q_from_gui=q_from_gui,
        q_from_controller=q_from_controller,
        simulate=simulate,
        ports=ports,
    )
    g.run()

# ---- Adding Xbox controller -----

def controller_process_main(q_from_gui_to_ctrl, q_to_gantry,
                            joystick_index: int = 0) -> None:
    from backend.controller import XboxController
    
    ctrl = XboxController(
        q_from_gui_to_ctrl=q_from_gui_to_ctrl,
        q_to_gantry=q_to_gantry,
        joystick_index=joystick_index,
    )
    ctrl.read_controller()


# ---------------------------------- GUI --------------------------------------
class StageGUI2(QMainWindow):
    raw_frame_ready = pyqtSignal(object)   # emits raw BGR numpy frame each capture tick
    home_set_changed = pyqtSignal(bool)    # emits True when Set Home is confirmed

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Auto in-vitro v3")
        self.resize(1450, 900)
        self.setMinimumSize(1100, 700)

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
        
        self._active_target = "needle"
        
        #backend controllers
        self.routine = None
        self.incubator: IncubatorSerial | None = None

        # cached state
        self._last_abs = {"x": 0.0, "y": 0.0, "z": 0.0, "e": 0.0}
        self._needle_abs = {"x": 0.0, "y": 0.0, "z": 0.0, "e": 0.0}
        self._camera_stage_abs = {"x": 0.0, "y": 0.0, "z": 0.0, "e": 0.0}

        self._home_set = False

        ## Camera state
        self.camera_manager = CameraManager(self)
        self.camera_connected = False
        self.camera_preview_live = False
        self.current_camera_index = None
        
        
        # --------------------------- root layout ---------------------------
        root = QWidget(self)
        self.setCentralWidget(root)
        main = QVBoxLayout(root)
        main.setContentsMargins(12, 12, 12, 12)
        main.setSpacing(10)

        # --------------------------- top tabs row --------------------------
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)
        
        self.tabs = QTabBar()
        self.tabs.addTab("Gantry")
        self.tabs.addTab("Sensors")
        self.tabs.addTab("Microscope")
        self.tabs.addTab("Automation")
        self.tabs.setExpanding(False)
        self.tabs.setCurrentIndex(0)
        
        self.btn_open_guide =  QPushButton("System Setup & Guided Tour")
        self.btn_open_guide.setFixedHeight(36)
        self.btn_open_guide.setStyleSheet("""
            QPushButton {
                background-color: #2a2f3a;
                color: #d0d7e2;
                border: 2px solid #4fc3f7;
                border-radius: 6px;
                padding: 5px 12px;
                font-weight: 600;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: #353b48;
                border: 2px solid #6fd6ff
            }
        """)
        
        top_row.addWidget(self.tabs)
        top_row.addStretch()
        top_row.addWidget(self.btn_open_guide)
        
        main.addLayout(top_row)
        
        
        self.pages = QStackedWidget()
        main.addWidget(self.pages, 1)
        
        self.gantry_page = QWidget()
        self.gantry_layout = QVBoxLayout(self.gantry_page)
        self.gantry_layout.setContentsMargins(0, 0, 0, 0)
        self.gantry_layout.setSpacing(10)

        self.guide_window = GuideWindow(self)
        self.tour = GuidedTour(self)
        
        # -------------------------- connection row -------------------------
        conn_box = QGroupBox("Connection")
        conn_layout = QVBoxLayout(conn_box)
        conn_layout.setSpacing(8)
        
        # top row
        conn_top = QHBoxLayout()
        conn_top.setSpacing(10)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Simulator", "Board"])
        self.mode_combo.setMinimumWidth(120)

        self.btn_refresh = QPushButton("Refresh Ports")
        self.btn_connect = QPushButton("Connect")

        self.motion_target_combo = QComboBox()
        self.motion_target_combo.addItems(["Both", "Needle", "Camera"])
        self.motion_target_combo.setMinimumWidth(120)

        self.motion_hint = QLabel("Connect to enable movement.")
        self.motion_hint.setStyleSheet("color: #ffaa00; font-weight: bold;")

        conn_top.addWidget(QLabel("Mode"))
        conn_top.addWidget(self.mode_combo)
        conn_top.addSpacing(10)
        conn_top.addWidget(QLabel("Move Target"))
        conn_top.addWidget(self.motion_target_combo)
        conn_top.addSpacing(10)
        conn_top.addWidget(self.btn_refresh)
        conn_top.addWidget(self.btn_connect)
        conn_top.addSpacing(12)
        conn_top.addWidget(self.motion_hint)
        conn_top.addStretch()
        
        # bottom row
        conn_bottom = QGridLayout()
        conn_bottom.setHorizontalSpacing(10)
        conn_bottom.setVerticalSpacing(6)
        conn_bottom.setColumnStretch(0, 0)
        conn_bottom.setColumnStretch(1, 0)
        conn_bottom.setColumnStretch(2, 0)
        conn_bottom.setColumnStretch(3, 1)  # absorb leftover space
        
        self.needle_port_combo = QComboBox()
        self.needle_port_combo.setMinimumWidth(240)
        
        self.camera_stage_port_combo = QComboBox()
        self.camera_stage_port_combo.setMinimumWidth(240)
        
        self.needle_status_label = QLabel("Needle Stage: Disconnected")
        self.needle_status_label.setMinimumWidth(220)
        
        self.camera_stage_status_label = QLabel("Camera Stage: Disconnected")
        self.camera_stage_status_label.setMinimumWidth(220)
        
        conn_bottom.addWidget(QLabel("Needle Stage Port"), 0, 0)
        conn_bottom.addWidget(self.needle_port_combo, 0, 1)
        conn_bottom.addWidget(self.needle_status_label, 0, 2)
        
        conn_bottom.addWidget(QLabel("Camera Stage Port"), 1, 0)
        conn_bottom.addWidget(self.camera_stage_port_combo, 1, 1)
        conn_bottom.addWidget(self.camera_stage_status_label, 1, 2)
    
        conn_layout.addLayout(conn_top)
        conn_layout.addLayout(conn_bottom)

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
        self.xy_plot.setMinimumHeight(480)
        self.xy_plot.setMaximumHeight(550)
        self.xy_plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Fixed-size plot window (will pan when needed):
        self._view_width = 120.0
        self._view_height = 120.0
        self._view_margin = 10.0
        
        self._view_width = 120.0
        self._view_height = 120.0
        self._view_margin = 10.0

        offset = 10.0  # space from edges

        self.xy_plot.setXRange(-offset, self._view_width - offset, padding=0)
        self.xy_plot.setYRange(-offset, self._view_height - offset, padding=0)

        self.xy_plot.setLimits(
            xMin=-100000, xMax=100000,
            yMin=-100000, yMax=100000
        )

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
        
        camera_main_row = QHBoxLayout()
        camera_main_row.setSpacing(14)
        
        # left side: camera controls
        camera_controls_col = QVBoxLayout()
        camera_controls_col.setSpacing(8)
        camera_controls_col.setContentsMargins(0, 8, 0, 0)
        
        camera_controls_col.addWidget(QLabel("Select Camera"))
        
        self.camera_combo = QComboBox()
        self.camera_combo.setMinimumWidth(140)
        camera_controls_col.addWidget(self.camera_combo)
        
        self.btn_camera_refresh = QPushButton("Refresh")
        self.btn_camera_connect = QPushButton("Connect")
        self.btn_camera_view = QPushButton("Start View")
        
        for btn in (self.btn_camera_refresh, self.btn_camera_connect, self.btn_camera_view):
            btn.setFixedWidth(90)
        
        camera_controls_col.addWidget(self.btn_camera_refresh)
        camera_controls_col.addWidget(self.btn_camera_connect)
        camera_controls_col.addWidget(self.btn_camera_view)

        self.lab_camera_status = QLabel("Camera idle")
        self.lab_camera_status.setStyleSheet("color: #bfc7d5;")
        camera_controls_col.addSpacing(8)
        camera_controls_col.addWidget(self.lab_camera_status)
        camera_controls_col.addStretch()
        
        camera_controls_widget = QWidget()
        camera_controls_widget.setLayout(camera_controls_col)
        camera_controls_widget.setFixedWidth(170)
        
        # right side: preview
        self.camera_view = QLabel("No camera connected")
        self.camera_view.setAlignment(Qt.AlignCenter)

        self.camera_view.setFixedSize(570, 570)
        self.camera_view.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.camera_view.setStyleSheet("""
            QLabel {
                background-color: #050505;
                border: 1px solid #444444;
                color: #bfc7d5;
                font-size: 15px;
            }
        """)
       
        # preview container
        preview_container = QWidget()
        preview_layout = QVBoxLayout(preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.addStretch(1)
        preview_layout.addWidget(self.camera_view, alignment=Qt.AlignCenter)
        preview_layout.addStretch(1)
        
        camera_main_row.addWidget(camera_controls_widget, 0, Qt.AlignTop)
        camera_main_row.addWidget(preview_container, 1)
        
        camera_layout.addLayout(camera_main_row)
        
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
        
        
        # ---- Controller box ----
        self.controller_group = QGroupBox("Controller")
        controller_layout = QVBoxLayout(self.controller_group)
        controller_layout.setSpacing(6)
        
        controller_top = QHBoxLayout()
        controller_top.setSpacing(6)
        
        self.controller_status = QLabel("Disconnected")
        self.controller_status.setStyleSheet("color: #bfc7d5;")
        
        self.controller_index_combo = QComboBox()
        self.controller_index_combo.setMinimumWidth(220)
        
        self.btn_refresh_controller = QPushButton("Refresh Controllers")
        self.btn_refresh_controller.setFixedWidth(140)
        
        self.btn_controller_toggle = QPushButton("Connect Controller")
        self.btn_controller_toggle.setFixedWidth(150)
        
        controller_top.addWidget(QLabel("Controller"))
        controller_top.addWidget(self.controller_index_combo)
        controller_top.addWidget(self.btn_refresh_controller)
        controller_top.addWidget(self.btn_controller_toggle)
        controller_top.addStretch()
        
        controller_layout.addLayout(controller_top)
        controller_layout.addWidget(self.controller_status)

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

        self.in_feed = QLineEdit("3000")
        self.in_feed.setFixedWidth(80)

        feed_row = QHBoxLayout()
        feed_row.addWidget(self.in_feed)
        feed_row.addWidget(QLabel("mm/min  (100–12000)"))
        feed_row.addStretch()

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
        right_col.addWidget(self.controller_group)
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
        self.motion_target_combo.currentTextChanged.connect(self._on_motion_target_changed)
        self.btn_refresh.clicked.connect(self.refresh_ports)
        self.btn_connect.clicked.connect(self._on_connect_clicked)

        self.btn_camera_refresh.clicked.connect(self.refresh_cameras)
        self.btn_camera_connect.clicked.connect(self._on_camera_connect_clicked)
        self.btn_camera_view.clicked.connect(self._on_camera_view_clicked)

        self.btn_clear_messages.clicked.connect(self.msg_box.clear)

        self.in_feed.editingFinished.connect(self._apply_feed_to_gantry)
        self.btn_apply_steps.clicked.connect(self._apply_steps_to_gantry)
        self.btn_set_home.clicked.connect(self._on_set_home)
        self.btn_home.clicked.connect(self._on_home)
        self.btn_estop.clicked.connect(self._on_estop)

        self.btn_c.clicked.connect(lambda: None)

        self.btn_rel_move.clicked.connect(self._on_relative_move)
        self.btn_abs_move.clicked.connect(self._on_absolute_move)

        self._set_motion_controls_enabled(False)
        
        self.btn_controller_toggle.clicked.connect(self._on_controller_toggle_clicked)
        self.btn_refresh_controller.clicked.connect(self._refresh_controller_list)
        
        self.btn_open_guide.clicked.connect(self._open_guide_window)
        self.guide_window.btn_close.clicked.connect(self._open_guide_window)

        self.guide_window.btn_start_tour.clicked.connect(self._start_guided_tour)
        
        # optional dark style
        try:
            import qdarkstyle
            self.setStyleSheet(qdarkstyle.load_stylesheet_pyqt5())
        except Exception:
            pass

        self._on_mode_changed(self.mode_combo.currentText())
        
        self._on_motion_target_changed(self.motion_target_combo.currentText())
        
        self.refresh_ports()
        
        self._refresh_controller_list()

        # Initialize camera UI
        self.refresh_cameras()
        self._update_camera_placeholder("No camera connected")
        self.btn_camera_view.setEnabled(False)
        
        # Camera backend wiring
        self.camera_manager.frame_ready.connect(self._on_camera_frame)
        self.camera_manager.camera_state_changed.connect(self._on_camera_state_changed)
        self.camera_manager.preview_state_changed.connect(self._on_camera_preview_changed)
        self.camera_manager.error_occurred.connect(self._on_camera_error)

        self._set_motion_controls_enabled(False)


        self.sensors_tab_widget = SensorsTab()
        self.microscope_tab_widget = MicroscopeTab()
        self.automation_tab_widget = AutomationTab()
        
        self.routine = RoutineController(
            command_sink=self._send_gui_msg,
            dispense_sink=self.incubator.pump_forward if self.incubator else None,
            parent=self,
        )
        self.sensors_tab_widget.incubator_connected.connect(self._on_incubator_connected)
        
        self.routine.log_message.connect(self._post_msg)
        self.routine.log_message.connect(self.automation_tab_widget.post_message)
        self.routine.status_changed.connect(self._on_routine_status_changed)
        
        # automation tab -> routine backend
        self.automation_tab_widget.update_requested.connect(self.routine.update_config)
        self.automation_tab_widget.start_requested.connect(self.routine.start)
        self.automation_tab_widget.stop_requested.connect(self.routine.stop)
        self.automation_tab_widget.pump1_forward_requested.connect(self._on_pump1_forward)
        self.automation_tab_widget.pump1_reverse_requested.connect(self._on_pump1_reverse)
        self.automation_tab_widget.pump1_stop_requested.connect(self._on_pump1_stop)
        self.automation_tab_widget.pump2_forward_requested.connect(self._on_pump2_forward)
        self.automation_tab_widget.pump2_reverse_requested.connect(self._on_pump2_reverse)
        self.automation_tab_widget.pump2_stop_requested.connect(self._on_pump2_stop)
        self.automation_tab_widget.pump3_requested.connect(self._on_pump3_run)
        self.automation_tab_widget.pump3_stop_requested.connect(self._on_pump3_stop)
        self.automation_tab_widget.pump4_requested.connect(self._on_pump4_run)
        self.automation_tab_widget.pump4_stop_requested.connect(self._on_pump4_stop)
        self.automation_tab_widget.stop_all_requested.connect(self._on_stop_all_pumps)

        # Push raw frames to automation tab for live edge detection
        self.raw_frame_ready.connect(self.automation_tab_widget.receive_raw_frame)
        self.microscope_tab_widget.view_state_changed.connect(
            self.automation_tab_widget.set_cam_view_state
        )
        self.automation_tab_widget.cam_view_toggle_requested.connect(
            self._on_camera_view_clicked
        )

        # Push raw BGR frames to MicroscopeTab for display and recording
        self.raw_frame_ready.connect(self.microscope_tab_widget.receive_frame)

        # Notify automation tab when home is set/cleared
        self.home_set_changed.connect(self.automation_tab_widget.on_home_set_changed)

        # Calibration dialog
        self.automation_tab_widget.calibration_requested.connect(self._open_calibration_dialog)
        self.automation_tab_widget.move_to_well_requested.connect(self._on_move_to_well)

        self.pages.addWidget(self.gantry_page)          # index 0
        self.pages.addWidget(self.sensors_tab_widget)   # index 1
        self.pages.addWidget(self.microscope_tab_widget)      # index 2
        self.pages.addWidget(self.automation_tab_widget)      # index 3
        
        self.tabs.currentChanged.connect(self.pages.setCurrentIndex)
        self.pages.setCurrentIndex(0)
        
        self.tour = GuidedTour(self)
        
        self.tour.steps = [
            {
                "tab": 0,
                "widget": self.btn_connect,
                "title": "Connection",
                "text": "Use this button to connect to the gantry system."
            },
            {
                "tab": 0,
                "widget": self.motion_target_combo,
                "title": "Motion Target",
                "text": "Select which stage to move: Needle, Camera, or Both."
            },
            {
                "tab": 0,
                "widget": self.xy_plot,
                "title": "Coordinate View",
                "text": "This shows the current gantry position."
            },
            {
                "tab": 0,
                "widget": self.camera_group,
                "title": "Camera Controls",
                "text": "Camera controls and preview are here."
            },
            {
                "tab": 0,
                "widget": self.manual_group,
                "title": "Manual Jog",
                "text": "These controls let you make small movement adjustments in X, Y, and Z."
            },
            {
                "tab": 0,
                "widget": self.ctrl_group,
                "title": "Step / Feed Settings",
                "text": "Use this section to set jog step size, feed rate, and home-related controls."
            },
            {
                "tab": 1,
                "widget": self.sensors_tab_widget,
                "title": "Sensors Tab",
                "text": "This tab is used for environmental and incubator monitoring."
            },
            {
                "tab": 2,
                "widget": self.microscope_tab_widget,
                "title": "Microscope Tab",
                "text": "This tab handles imaging and detection."
            },
            {
                "tab": 3,
                "widget": self.automation_tab_widget,
                "title": "Automation Tab",
                "text": "This tab controls well plates and routines."
            },
        ]

    def _open_guide_window(self):
        if self.guide_window.isVisible():
            self.guide_window.raise_()
            self.guide_window.activateWindow()
            return
        self.guide_window.show()
        self.guide_window.raise_()
        self.guide_window.activateWindow()
    
    def _start_guided_tour(self):
        self.guide_window.close()
        self.tour.start()    

    # -------------------------- connection logic --------------------------
    def _on_mode_changed(self, mode: str):
        hardware_mode = mode == "Board"
        
        self.needle_port_combo.setEnabled(hardware_mode)
        self.camera_stage_port_combo.setEnabled(hardware_mode)
        self.btn_refresh.setEnabled(hardware_mode)

        if hardware_mode:
            self.refresh_ports()
        else:
            self.needle_port_combo.clear()
            self.camera_stage_port_combo.clear()
            self.needle_port_combo.addItem("(not needed for simulator)")
            self.camera_stage_port_combo.addItem("(not needed for simulator)")

    def _on_motion_target_changed(self, text: str):
        self._active_target = text.strip().lower()
        self._post_msg(f"Motion target set to {text}.")
    
    def refresh_ports(self):
        self.needle_port_combo.clear()
        self.camera_stage_port_combo.clear()
        
        if not self.needle_port_combo.isEnabled():
            self.needle_port_combo.addItem("(not needed for simulator)")
            self.camera_stage_port_combo.addItem("(not needed for simulator)")
            return
        
        if list_ports is None:
            self.needle_port_combo.addItem("(pyserial not available)")
            self.camera_stage_port_combo.addItem("(pyserial not available)")
            return
        
        ports = list(list_ports.comports())
        if not ports:
            self.needle_port_combo.addItem("(no ports found)")
            self.camera_stage_port_combo.addItem("(no ports found)")
            return
        
        # Allow each stage to be skipped independently
        self.needle_port_combo.addItem("-- Not Connected --", None)
        self.camera_stage_port_combo.addItem("-- Not Connected --", None)
        
        for p in ports:
            label = f"{p.device}"
            if p.description:
                label += f" - {p.description}"
                
            self.needle_port_combo.addItem(label, p.device)
            self.camera_stage_port_combo.addItem(label, p.device)
        
    def _on_connect_clicked(self):
        if self._connected:
            self._disconnect_backend()
            return

        mode = self.mode_combo.currentText()
        simulate = mode == "Simulator"
        
        needle_port = None
        camera_port = None

        self.motion_hint.setText("Ready")

        if not simulate:
            if self.needle_port_combo.count() == 0 and self.camera_stage_port_combo.count() == 0:
                QMessageBox.warning(self, "Connect", "No ports are available.")
                return
            
            # currentData() is None for "-- Not Connected --" or placeholder items
            needle_port = self.needle_port_combo.currentData()
            camera_port = self.camera_stage_port_combo.currentData()
                
            if needle_port is None and camera_port is None:
                QMessageBox.warning(self, "Connect", "Please select at least one valid stage port.")
                return
            if needle_port is not None and camera_port is not None and needle_port == camera_port:
                QMessageBox.warning(self, "Connect", "Needle and camera stage cannot use the same serial port.")
                return

        self._start_backend(
            simulate=simulate,
            ports={
                "needle": needle_port,
                "camera": camera_port,
            }
        )

        if simulate:
            self.needle_status_label.setText("Needle: Simulator")
            self.camera_stage_status_label.setText("Camera Stage: Simulator")
        else:
            self.needle_status_label.setText(
                f"Needle Stage: {'Connected' if needle_port else 'Not selected'}"
            )
            self.camera_stage_status_label.setText(
                f"Camera Stage: {'Connected' if camera_port else 'Not selected'}"
            )

        self.btn_connect.setText("Disconnect")
        self.motion_hint.setText("Ready")
        self.motion_hint.setStyleSheet("color: #55ff55; font-weight: bold;")
        self._set_motion_controls_enabled(True)
        self._post_msg(
            f"{mode} backend started. Needle Stage={needle_port or 'None'}, Camera Stage={camera_port or 'None'}"
        )

    def _start_backend(self, simulate: bool, ports: dict[str, str | None] | None = None):
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
                ports,
            ),
            daemon=True,
        )
        self.p_gantry.start()
        self._connected = True

    ## adding controller
    def _refresh_controller_list(self):
        self.controller_index_combo.clear()

        try:
            pygame.init()
            pygame.joystick.init()

            count = pygame.joystick.get_count()

            if count <= 0:
                self.controller_index_combo.addItem("No controllers detected", -1)
                self.btn_controller_toggle.setEnabled(False)
                self.controller_status.setText("No controller detected")
                self.controller_status.setStyleSheet("color: #ffb347; font-weight: bold;")
                self._post_msg("No controllers detected.")
                return

            for i in range(count):
                js = pygame.joystick.Joystick(i)
                js.init()
                name = js.get_name()
                self.controller_index_combo.addItem(f"{i} - {name}", i)

            self.btn_controller_toggle.setEnabled(True)
            self.controller_status.setText("Controller available")
            self.controller_status.setStyleSheet("color: #bfc7d5;")
            self._post_msg(f"Detected {count} controller(s).")

        except Exception as e:
            self.controller_index_combo.clear()
            self.controller_index_combo.addItem("Controller scan failed", -1)
            self.btn_controller_toggle.setEnabled(False)
            self.controller_status.setText("Scan failed")
            self.controller_status.setStyleSheet("color: #ff6b6b; font-weight: bold;")
            self._post_msg(f"Controller scan failed: {e}")
    
    def _controller_available(self, joystick_index: int) -> bool:
        try:
            pygame.init()
            pygame.joystick.init()
            count = pygame.joystick.get_count()
            return 0 <= joystick_index < count
        except Exception:
            return False
            
            
    def _start_controller(self, joystick_index: int = 0):
        if self.q_ctrl_to_gantry is None:
            raise RuntimeError("Controller queue is not initialized.")
        
        if not self._controller_available(joystick_index):
            raise RuntimeError(f"No controller found at joystick index {joystick_index}.")
        
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

    def _on_controller_toggle_clicked(self):
        if not self._connected:
            QMessageBox.warning(self, "Controller", "Connect the gantry first.")
            return
        
        if self._controller_connected:
            self._stop_controller()
            self.btn_controller_toggle.setText("Connect Controller")
            self.controller_status.setText("Disconnected")
            self.controller_status.setStyleSheet("color: #bfc7d5;")
            self._post_msg("Controller disconnected.")
            return
        
        joystick_index = self.controller_index_combo.currentData()
        
        if joystick_index is None or joystick_index < 0:
            QMessageBox.warning(self, "Controller", "No valid controller is selected.")
            return
        
        try:
            self._start_controller(joystick_index=joystick_index)
            self.btn_controller_toggle.setText("Disconnect Controller")
            self.controller_status.setText(f"Connected ({self.controller_index_combo.currentText()})")
            self.controller_status.setStyleSheet("color: #66dd88; font-weight: bold;")
            self._post_msg(f"Controller connected on index {joystick_index}.")
        except Exception as e:
            QMessageBox.warning(self, "Controller", f"Could not start controller: {e}")
            self.controller_status.setText("Connection failed")
            self.controller_status.setStyleSheet("color: #ff6b6b; font-weight: bold;")
            self._post_msg(f"WARNING: Controller failed to start: {e}")


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
        
        if hasattr(self, "btn_controller_toggle"):
            self.btn_controller_toggle.setText("Connect Controller")
        if hasattr(self, "controller_status"):
            self.controller_status.setText("Disconnected")
            self.controller_status.setStyleSheet("color: #bfc7d5;")


    def _disconnect_backend(self, silent: bool = False):
        self._stop_all_jog_timers()
        self._stop_controller()
        
        if self.routine is not None and self.routine.is_running:
            self.routine.stop()
            
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
        if self._home_set:
            self._home_set = False
            self.home_set_changed.emit(False)

        self.btn_connect.setText("Connect")
        self.needle_status_label.setText("Needle Stage: Disconnected")
        self.camera_stage_status_label.setText("Camera Stage: Disconnected")
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
            self.camera_manager.disconnect_camera()
            return

        cam_index = self.camera_combo.currentData()
        if cam_index is None:
            QMessageBox.warning(self, "Camera", "Please select a valid camera.")
            return

        ok = self.camera_manager.connect_camera(cam_index)
        if not ok:
            self.lab_camera_status.setText("Failed to connect")
            return

        self.current_camera_index = cam_index

        if _DNX64Class is not None:
            try:
                dnx = _DNX64Class(_DNX64_DLL)
                dnx.SetVideoDeviceIndex(cam_index)
                dnx.SetLEDState(0, 0)
            except Exception:
                pass

    def _on_camera_view_clicked(self):
        if not self.camera_connected:
            QMessageBox.warning(self, "Camera", "Connect a camera first.")
            return
        self.camera_manager.toggle_preview()

    
    def _open_calibration_dialog(self) -> None:
        if not self.camera_manager.is_preview_live:
            QMessageBox.information(
                self, "Camera Required",
                "Start the camera preview on the Gantry tab before calibrating."
            )
            return
        dlg = CalibrationDialog(
            command_sink=self._send_gui_msg,
            return_pos=dict(self._camera_stage_abs),
            parent=self,
        )
        self.raw_frame_ready.connect(dlg.receive_frame)
        try:
            if dlg.exec_() == CalibrationDialog.Accepted:
                if dlg.well_center_px is not None:
                    self.automation_tab_widget.set_calibration_result(
                        dlg.well_center_px, dlg.well_radius_px
                    )
        finally:
            self.raw_frame_ready.disconnect(dlg.receive_frame)

    def _disconnect_camera(self):
        self.camera_manager.disconnect_camera()
        
    
    def _on_camera_frame(self, frame):
        if frame is None:
            return

        self.raw_frame_ready.emit(frame)

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = frame_rgb.shape
        bytes_per_line = ch * w

        qimg = QImage(frame_rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
        pix = QPixmap.fromImage(qimg)
        scaled = pix.scaled(
            self.camera_view.width(),
            self.camera_view.height(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

        self.camera_view.setPixmap(scaled)
        self.camera_view.setText("") 
        
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
    def _send_gui_msg(self, msg: dict):
        if not self._connected or self.q_gui_to_gantry is None:
            return
        
        if not isinstance(msg, dict):
            return
        
        msg = dict(msg)
        
        # add default target for motion-related / stage-related commands
        if "target" not in msg and msg.get("type") in ("gantry_cmd", "gcode", "home_all", "fan_set"):
            msg["target"] = self._active_target
            
        self.q_gui_to_gantry.put(msg)

    def _send_ctrl_msg(self, msg: Dict):
        if self.q_ctrl_to_gantry is not None:
            self.q_ctrl_to_gantry.put(msg)
            
    def _on_incubator_connected(self, incubator: IncubatorSerial) -> None:
        self.incubator = incubator
        self.routine._dispense = incubator.pump_forward
        self._post_msg("Incubator connected — pump armed for routine.")
    
    def _pump_not_connected(self) -> bool:
        """Returns True and shows warning if incubator board is not connected."""
        if not isinstance(self.incubator, IncubatorSerial):
            msg = "Incubator board not connected — please connect in the Sensors tab."
            self._post_msg(f"WARNING: {msg}")
            self.automation_tab_widget.post_message(f"WARNING: {msg}")
            QMessageBox.warning(self, "Not Connected", msg)
            return True
        return False

    def _on_pump1_forward(self, duration_ms: int) -> None:
        if self._pump_not_connected(): return
        self.incubator.pump1_forward(duration_ms)
        self.automation_tab_widget.post_message(f"Pump 1 forward: {duration_ms} ms")

    def _on_pump1_reverse(self, duration_ms: int) -> None:
        if self._pump_not_connected(): return
        self.incubator.pump1_reverse(duration_ms)
        self.automation_tab_widget.post_message(f"Pump 1 reverse: {duration_ms} ms")

    def _on_pump1_stop(self) -> None:
        if self._pump_not_connected(): return
        self.incubator.stop1()
        self.automation_tab_widget.post_message("Pump 1 stopped.")

    def _on_pump2_forward(self, duration_ms: int) -> None:
        if self._pump_not_connected(): return
        self.incubator.pump2_forward(duration_ms)
        self.automation_tab_widget.post_message(f"Pump 2 forward: {duration_ms} ms")

    def _on_pump2_reverse(self, duration_ms: int) -> None:
        if self._pump_not_connected(): return
        self.incubator.pump2_reverse(duration_ms)
        self.automation_tab_widget.post_message(f"Pump 2 reverse: {duration_ms} ms")

    def _on_pump2_stop(self) -> None:
        if self._pump_not_connected(): return
        self.incubator.stop2()
        self.automation_tab_widget.post_message("Pump 2 stopped.")

    def _on_pump3_run(self, duration_ms: int) -> None:
        if self._pump_not_connected(): return
        self.incubator.pump3_run(duration_ms)
        self.automation_tab_widget.post_message(f"Pump 3 run: {duration_ms} ms")

    def _on_pump3_stop(self) -> None:
        if self._pump_not_connected(): return
        self.incubator.stop3()
        self.automation_tab_widget.post_message("Pump 3 stopped.")

    def _on_pump4_run(self, duration_ms: int) -> None:
        if self._pump_not_connected(): return
        self.incubator.pump4_run(duration_ms)
        self.automation_tab_widget.post_message(f"Pump 4 run: {duration_ms} ms")

    def _on_pump4_stop(self) -> None:
        if self._pump_not_connected(): return
        self.incubator.stop4()
        self.automation_tab_widget.post_message("Pump 4 stopped.")

    def _on_stop_all_pumps(self) -> None:
        if self._pump_not_connected(): return
        self.incubator.stop_all()
        self.automation_tab_widget.post_message("All pumps stopped.")
   
    def _on_routine_status_changed(self, payload: dict) -> None:
        self._post_msg(
            f"Routine: {payload.get('status', '—')} | "
            f"{payload.get('phase', '—')} | "
            f"{payload.get('current_well', '—')}"
        )

        if hasattr(self.automation_tab_widget, "set_runtime_status"):
            self.automation_tab_widget.set_runtime_status(
                status=payload.get("status", "—"),
                current_well=payload.get("current_well"),
                phase=payload.get("phase"),
                highlight_row=payload.get("highlight_row"),
                highlight_col=payload.get("highlight_col"),
            )

    
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
            self.in_xy, self.in_z, self.in_feed,
        ]
        for w in widgets:
            w.setEnabled(enabled)

    # ---------------------------- jog actions ----------------------------
    def _jog_xy(self, sx: int, sy: int):
        if not self._connected:
            self._stop_all_jog_timers()
            self._post_msg("WARNING: Not connected.")
            return
        
        try:
            step = float(self.in_xy.text())
        except ValueError:
            self._post_msg("WARNING: Invalid XY step size.")
            return
        
        self._send_gui_msg({
            "type": "gantry_cmd",
            "cmd": "move_rel",
            "dx": sx * step,
            "dy": sy * step,
            "dz": 0.0,
            "de": 0.0,
        })


    def _jog_z(self, s: int):
        if not self._connected:
            self._stop_all_jog_timers()
            self._post_msg("WARNING: Not connected.")
            return
        
        try:
            step = float(self.in_z.text())
        except ValueError:
            self._post_msg("WARNING: Invalid Z step size.")
            return
        
        self._send_gui_msg({
            "type": "gantry_cmd",
            "cmd": "move_rel",
            "dx": 0.0,
            "dy": 0.0,
            "dz": s * step,
            "de": 0.0,
        })

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

    def _on_move_to_well(self, x_mm: float, y_mm: float) -> None:
        if not self._connected:
            self.automation_tab_widget.post_message("Move To failed: gantry is not connected.")
            return
        self._send_gui_msg({
            "type": "gantry_cmd",
            "cmd": "move_abs",
            "X": x_mm,
            "Y": y_mm,
            "Z": 0.0,
            "E": 0.0,
        })

    def _apply_feed_to_gantry(self):
        try:
            val = max(100, min(12000, int(self.in_feed.text().strip())))
        except ValueError:
            val = 3000
        self.in_feed.setText(str(val))
        if not self._connected:
            return
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
        reply = QMessageBox.question(
            self,
            "Set Home",
            "Make sure the needle is centered on well A1 (top-left well) before setting home.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.Cancel,
        )
        if reply != QMessageBox.Yes:
            return
        self._send_gui_msg({
            "type": "gantry_cmd",
            "cmd": "set_home",
        })
        self._home_set = True
        self.home_set_changed.emit(True)
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
            "feed_mm_min": max(100, min(12000, int(self.in_feed.text().strip() or 3000))),
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
            "feed_mm_min": max(100, min(12000, int(self.in_feed.text().strip() or 3000))),
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
            elif typ == "disconnected":
                channel = msg.get("channel", "unknown")
                reason = msg.get("reason", "unknown error")
                port = msg.get("port", "unknown")
                self._post_msg(f"ERROR: {channel} stage lost connection on {port}: {reason}")

                if self.routine is not None and self.routine.is_running:
                    self.routine.stop()
                    self._post_msg("Routine stopped due to disconnection.")

                if self._home_set:
                    self._home_set = False
                    self.home_set_changed.emit(False)

                label_text = f"{channel.capitalize()} Stage: Lost Connection"
                if channel == "needle":
                    self.needle_status_label.setText(label_text)
                else:
                    self.camera_stage_status_label.setText(label_text)

                QMessageBox.warning(
                    self,
                    "Stage Disconnected",
                    f"The {channel} stage lost its serial connection on {port}.\n\n"
                    f"Reason: {reason}\n\n"
                    "The routine has been stopped. Reconnect and re-home before continuing.",
                )
            elif typ == "controller_state":
                mapping = msg.get("mapping", {})
                self._post_msg(f"Controller mapping loaded: {mapping}")

    def _apply_state(self, s: Dict):
        ax = float(s.get("x", 0.0))
        ay = float(s.get("y", 0.0))
        az = float(s.get("z", 0.0))
        ae = float(s.get("e", 0.0))

        self._last_abs.update({"x": ax, "y": ay, "z": az, "e": ae})
        
        # new separate stage caches
        self._needle_abs = {
            "x": float(s.get("needle_x", ax)),
            "y": float(s.get("needle_y", ay)),
            "z": float(s.get("needle_z", az)),
            "e": float(s.get("needle_e", ae)),
        }
        
        self._camera_stage_abs = {
            "x": float(s.get("camera_x", 0.0)),
            "y": float(s.get("camera_y", 0.0)),
            "z": float(s.get("camera_z", 0.0)),
            "e": float(s.get("camera_e", 0.0))
        }

        self._xy_point.setData([ax], [ay])
        self._autopan_xy_view(ax, ay)
        
        self.lab_x.setText(f"{ax:.3f}")
        self.lab_y.setText(f"{ay:.3f}")
        self.lab_z.setText(f"{az:.3f}")

        if "xy_step" in s:
            self.in_xy.setText(f"{float(s['xy_step']):.3f}")
        if "z_step" in s:
            self.in_z.setText(f"{float(s['z_step']):.3f}")

    def _autopan_xy_view(self, x: float, y: float):
        vb = self.xy_plot.getViewBox()

        x_min, x_max = vb.viewRange()[0]
        y_min, y_max = vb.viewRange()[1]

        changed = False

        # pan X if point gets too close to left/right edge
        if x < x_min + self._view_margin:
            x_min = x - self._view_margin
            x_max = x_min + self._view_width
            changed = True
        elif x > x_max - self._view_margin:
            x_max = x + self._view_margin
            x_min = x_max - self._view_width
            changed = True
            
        # pan Y if point gets too close to top/bottom edge
        if y < y_min + self._view_margin:
            y_min = y - self._view_margin
            y_max = y_min + self._view_height
            changed = True
        elif y > y_max - self._view_margin:
            y_max = y + self._view_margin
            y_min = y_max - self._view_height
            changed = True

        if changed:
            self.xy_plot.setXRange(x_min, x_max, padding=0)
            self.xy_plot.setYRange(y_min, y_max, padding=0)

    # ------------------------------ messages ------------------------------
    def _post_msg(self, text: str):
        stamp = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        self.msg_box.appendPlainText(f"[{stamp}] {text}")
        self.msg_box.verticalScrollBar().setValue(
            self.msg_box.verticalScrollBar().maximum()
        )

    # ------------------------------ shutdown ------------------------------
    def closeEvent(self, ev):
        if getattr(self, "camera_connected", False):
            reply = QMessageBox.question(
                self,
                "Camera still connected",
                "Camera is still connected.\nDisconnect and exit?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self._disconnect_camera()
            else:
                ev.ignore()
                return
            
        # Normal shutdown if safe
        self._disconnect_backend(silent=True)
        self._disconnect_camera()
        
        if hasattr(self, "sensors_tab_widget") and self.sensors_tab_widget is not None:
            self.sensors_tab_widget.shutdown()
            
        ev.accept()

    def _on_camera_state_changed(self, connected: bool) -> None:
        self.camera_connected = connected

        if connected:
            cam_index = self.camera_manager.camera_index
            self.current_camera_index = cam_index
            self.btn_camera_connect.setText("Disconnect")
            self.btn_camera_view.setEnabled(True)
            self.lab_camera_status.setText(f"Camera {cam_index} connected")
            self._update_camera_placeholder("")
            self._post_msg(f"Camera {cam_index} connected.")

            if hasattr(self, "microscope_tab_widget") and self.microscope_tab_widget is not None:
                self.microscope_tab_widget.on_camera_connected(cam_index)
        else:
            self.current_camera_index = None
            self.btn_camera_connect.setText("Connect")
            self.btn_camera_view.setText("Start View")
            self.btn_camera_view.setEnabled(False)
            self.lab_camera_status.setText("Camera disconnected")
            self._update_camera_placeholder("No camera connected")
            self._post_msg("Camera disconnected.")

            if hasattr(self, "microscope_tab_widget") and self.microscope_tab_widget is not None:
                self.microscope_tab_widget.on_camera_disconnected()

            if hasattr(self, "automation_tab_widget") and self.automation_tab_widget is not None:
                self.automation_tab_widget.update_camera_frame(None)

    def _on_camera_preview_changed(self, live: bool) -> None:
        self.camera_preview_live = live

        if live:
            self.btn_camera_view.setText("Stop View")
            self.lab_camera_status.setText("Preview running")
            self._post_msg("Camera preview started.")

            if hasattr(self, "microscope_tab_widget") and self.microscope_tab_widget is not None:
                self.microscope_tab_widget.on_view_started()
        else:
            self.btn_camera_view.setText("Start View")
            if self.camera_connected:
                self.lab_camera_status.setText("Preview stopped")
                self._update_camera_placeholder("Camera connected - preview off")
            else:
                self.lab_camera_status.setText("Camera idle")

            self._post_msg("Camera preview stopped.")

            if hasattr(self, "microscope_tab_widget") and self.microscope_tab_widget is not None:
                self.microscope_tab_widget.on_view_stopped()

            if hasattr(self, "automation_tab_widget") and self.automation_tab_widget is not None:
                self.automation_tab_widget.update_camera_frame(None)

    def _on_camera_error(self, text: str) -> None:
        self.lab_camera_status.setText("Camera error")
        self._update_camera_placeholder("Preview unavailable")
        self._post_msg(f"WARNING: {text}")
        QMessageBox.warning(self, "Camera", text)

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