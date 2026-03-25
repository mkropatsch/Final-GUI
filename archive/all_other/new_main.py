"""
Core-XY Gantry GUI (Windows-spawn safe) — Keyboard-only controller
==================================================================

This GUI launches one child process using Windows-safe 'spawn':
  • Gantry process (hardware/sim control)

Mapping persistence:
  - The controller mapping is stored in config/controller_map.json
  - GUI preloads that file (if present) to prefill the mapping dialog

Run:
    python new_main.py [--simulate]

Deps:
    pip install PyQt5 pyqtgraph pygame pyserial qdarkstyle
"""

from __future__ import annotations

import json
import os
import sys
import argparse
import multiprocessing as mp
from typing import Dict

from PyQt5.QtCore import Qt, QTimer, QDateTime
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGridLayout, QGroupBox, QFormLayout,
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QSpinBox,
    QComboBox, QDialog, QTableWidget, QTableWidgetItem, QMessageBox, QSlider
)
import pyqtgraph as pg
import qdarkstyle


# --------------------------- Child process entry points -----------------------
# IMPORTANT: These must be top-level functions so Windows 'spawn' can import them.

def gantry_process_main(q_to_gui, q_from_gui, q_from_controller, simulate: bool) -> None:
    """Child entry point: construct and run the GantrySystem *in the child*."""
    # Import here (inside the child) so parent never touches heavy libs.
    from gantry_keyboard_friendly import GantrySystem
    g = GantrySystem(
        q_to_gui=q_to_gui,
        q_from_gui=q_from_gui,
        q_from_controller=q_from_controller,
        simulate=simulate,
    )
    g.run()


# (Kept for reference, but NOT used any more.)
def controller_process_main(q_from_gui_to_ctrl, q_to_gantry) -> None:
    try:
        from controller_keyboard import KeyboardController
    except ModuleNotFoundError:
        # fallback if Windows spawn can’t find it
        import importlib.util, os
        here = os.path.dirname(os.path.abspath(__file__))
        kb_path = os.path.join(here, "controller_keyboard.py")
        spec = importlib.util.spec_from_file_location("controller_keyboard", kb_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore
        KeyboardController = mod.KeyboardController

    c = KeyboardController(
        q_from_gui_to_ctrl=q_from_gui_to_ctrl,
        q_to_gantry=q_to_gantry,
    )
    c.read_controller()


# ------------------------------ Mapping dialog --------------------------------

class MappingDialog(QDialog):
    """
    Simple mapping editor: controller control -> command.
    """
    COMMANDS = [
        "none",
        "xy_motion",
        "z_motion",
        "e_motion",
        "xy_step_size_inc",
        "xy_step_size_dec",
        "z_step_size_inc",
        "z_step_size_dec",
        "e_step_size_inc",
        "e_step_size_dec",
        "home_all",
    ]
    ORDER = [
        "joyL", "joyR", "trig",
        "a", "b", "x", "y", "lb", "rb",
        "back", "start",
        "dpad_U", "dpad_D", "dpad_L", "dpad_R",
    ]

    def __init__(self, parent, current_map: Dict[str, str]):
        super().__init__(parent)
        self.setWindowTitle("Configure Controller Mapping")
        self.resize(520, 520)

        self.table = QTableWidget(self)
        self.table.setRowCount(len(self.ORDER))
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Control", "Command"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)

        for r, key in enumerate(self.ORDER):
            self.table.setItem(r, 0, QTableWidgetItem(key))
            combo = QComboBox()
            combo.addItems(self.COMMANDS)
            combo.setCurrentText(current_map.get(key, "none"))
            self.table.setCellWidget(r, 1, combo)

        btn_ok = QPushButton("Apply")
        btn_cancel = QPushButton("Cancel")
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(btn_cancel)
        buttons.addWidget(btn_ok)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addLayout(buttons)

    def result_mapping(self) -> Dict[str, str]:
        out = {}
        for r, key in enumerate(self.ORDER):
            combo: QComboBox = self.table.cellWidget(r, 1)  # type: ignore
            out[key] = combo.currentText()
            # NOTE: Mapping dialog is still present; it's harmless even without a
            # separate controller process, and you can repurpose it later if you like.
        return out


# -------------------------------- Main window ---------------------------------

class GantryGUI(QMainWindow):
    def __init__(self, simulate: bool = True):
        super().__init__()

        # ---- Create a spawn context and plain Queues (no Manager) -------------
        ctx = mp.get_context("spawn")
        self.q_gantry_to_gui = ctx.Queue(maxsize=1000)
        self.q_gui_to_gantry = ctx.Queue(maxsize=1000)
        self.q_gui_to_ctrl = ctx.Queue(maxsize=1000)
        self.q_ctrl_to_gantry = ctx.Queue(maxsize=1000)

        # ---- Start child process (gantry only) --------------------------------
        self.p_gantry = ctx.Process(
            target=gantry_process_main,
            args=(self.q_gantry_to_gui, self.q_gui_to_gantry, self.q_ctrl_to_gantry, simulate),
            daemon=True,
        )
        self.p_gantry.start()

        # ---- Build GUI --------------------------------------------------------
        self.setWindowTitle("Core-XY Gantry Control (spawn-safe) — Controller: keyboard")
        self.resize(1000, 720)
        root = QWidget()
        self.setCentralWidget(root)
        grid = QGridLayout(root)

        # XY map
        self.xy_group = QGroupBox("XY Position (mm)")
        xy_layout = QVBoxLayout(self.xy_group)
        self.xy_plot = pg.PlotWidget()
        self.xy_plot.setAspectLocked(True)
        self.xy_plot.showGrid(x=True, y=True, alpha=0.25)
        self.xy_plot.setLabel("left", "Y (mm)")
        self.xy_plot.setLabel("bottom", "X (mm)")
        self._xy_point = self.xy_plot.plot([0], [0], pen=None, symbol="o", symbolSize=10)
        xy_layout.addWidget(self.xy_plot)

        # State labels + Z visual
        self.state_group = QGroupBox("State")
        st = QFormLayout(self.state_group)
        self.lab_x = QLabel("0.000")
        self.lab_y = QLabel("0.000")
        self.lab_z = QLabel("0.000")
        self.lab_e = QLabel("0.000")
        st.addRow("X", self.lab_x)
        st.addRow("Y", self.lab_y)
        st.addRow("Z", self.lab_z)
        st.addRow("E", self.lab_e)

        self.z_group = QGroupBox("Z (qualitative)")
        zlay = QVBoxLayout(self.z_group)
        self.z_bar = QSlider(Qt.Vertical)
        self.z_bar.setEnabled(False)
        self.z_bar.setRange(0, 1000)
        self.z_bar.setValue(500)
        zlay.addWidget(self.z_bar)

        # Controls
        self.ctrl_group = QGroupBox("Controls")
        form = QFormLayout(self.ctrl_group)
        self.in_xy = QLineEdit("0.20")
        self.in_z = QLineEdit("0.05")
        self.in_e = QLineEdit("0.02")
        self.in_feed = QSpinBox()
        self.in_feed.setRange(100, 12000)
        self.in_feed.setValue(3000)
        form.addRow("XY step (mm/tick)", self.in_xy)
        form.addRow("Z step (mm/tick)", self.in_z)
        form.addRow("E step (mm/tick)", self.in_e)
        form.addRow("Feed (mm/min)", self.in_feed)

        self.btn_apply = QPushButton("Apply step sizes")
        self.btn_home = QPushButton("Home all (G28)")
        self.btn_map = QPushButton("Configure controller…")
        row = QHBoxLayout()
        row.addWidget(self.btn_apply)
        row.addWidget(self.btn_home)
        row.addStretch()
        row.addWidget(self.btn_map)
        form.addRow(row)

        self.msg_group = QGroupBox("Messages")
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        msglay = QVBoxLayout(self.msg_group)
        msglay.addWidget(self.msg)

        grid.addWidget(self.xy_group,   0, 0, 2, 1)
        grid.addWidget(self.z_group,    0, 1, 1, 1)
        grid.addWidget(self.state_group,1, 1, 1, 1)
        grid.addWidget(self.ctrl_group, 2, 0, 1, 2)
        grid.addWidget(self.msg_group,  3, 0, 1, 2)

        # Signals
        self.btn_apply.clicked.connect(self._apply_steps)
        self.btn_home.clicked.connect(self._home_all)
        self.btn_map.clicked.connect(self._open_mapping)
        self.in_feed.valueChanged.connect(self._apply_feed_change)

        # Poll incoming state/messages from gantry child
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(100)

        # -------------- Load last-saved mapping to prefill dialog --------------
        self._mapping = self._load_saved_mapping()

        # ---- Inline keyboard controller (Qt-based; replaces the pygame window) ----
        self.setFocusPolicy(Qt.StrongFocus)   # allow this window to receive key events
        self._pressed = set()                 # currently held keys
        self._kb_timer = QTimer(self)
        self._kb_timer.timeout.connect(self._tick_keyboard)
        self._kb_timer.start(16)              # ~60 Hz

        # Optional: hide the old "Configure controller…" button (no external controller now)
        try:
            self.btn_map.hide()
        except Exception:
            pass

        # Give focus to this window on startup (helpful on Windows)
        self.activateWindow()
        self.raise_()
        self.setFocus()

    # ------------------------------- helpers ----------------------------------

    @staticmethod
    def _config_path() -> str:
        """Return the absolute path to config/controller_map.json next to this file."""
        base = os.path.dirname(os.path.abspath(__file__))
        cfg_dir = os.path.join(base, "config")
        os.makedirs(cfg_dir, exist_ok=True)
        return os.path.join(cfg_dir, "controller_map.json")

    def _load_saved_mapping(self) -> Dict[str, str]:
        """
        Load mapping from disk if present; otherwise fallback to defaults.
        This is used to prefill the dialog so it reflects the last known setup.
        """
        default_map = {
            "joyL": "xy_motion", "joyR": "z_motion", "trig": "e_motion",
            "a": "z_step_size_inc", "b": "z_step_size_dec",
            "x": "e_step_size_dec", "y": "e_step_size_inc",
            "lb": "xy_step_size_dec", "rb": "xy_step_size_inc",
            "back": "home_all", "start": "home_all",
            "dpad_U": "none", "dpad_D": "none", "dpad_L": "none", "dpad_R": "none",
        }
        path = self._config_path()
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    disk = json.load(f)
                # only keep keys we know
                for k in list(disk.keys()):
                    if k not in default_map:
                        disk.pop(k, None)
                # fill in any missing keys
                merged = {**default_map, **disk}
                return merged
        except Exception:
            pass
        return default_map

    def _save_mapping_atomic(self, mapping: Dict[str, str]) -> None:
        """Write mapping atomically: write to .tmp then replace the final file."""
        path = self._config_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(mapping, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    # ------------------------------- UI handlers ------------------------------

    def _apply_steps(self) -> None:
        try:
            xy = float(self.in_xy.text())
            z = float(self.in_z.text())
            e = float(self.in_e.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid", "Enter numeric step sizes.")
            return
        self.q_gui_to_gantry.put({"type": "set_steps", "xy_step": xy, "z_step": z, "e_step": e})

    def _apply_feed_change(self, val: int) -> None:
        self.q_gui_to_gantry.put({"type": "set_feed", "feed_mm_min": int(val)})

    def _home_all(self) -> None:
        self.q_gui_to_gantry.put({"type": "home_all"})

    def _open_mapping(self) -> None:
        """
        (Dialog still exists; you can remove it later if you don’t need it.)
        """
        dlg = MappingDialog(self, self._mapping)
        if dlg.exec_() == QDialog.Accepted:
            newmap = dlg.result_mapping()
            try:
                self._save_mapping_atomic(newmap)
                self._mapping = newmap
            except Exception as e:
                QMessageBox.warning(self, "Save failed", f"Couldn't save mapping: {e}")
            # No external controller to notify; keep the saved file for future use.
            self.q_gui_to_gantry.put({"type": "save_mapping"})

    # ------------------------------ polling loop ------------------------------

    def _poll(self) -> None:
        while not self.q_gantry_to_gui.empty():
            msg = self.q_gantry_to_gui.get()
            if not isinstance(msg, dict):
                continue
            typ = msg.get("type")
            if typ == "state":
                self._apply_state(msg)
            elif typ == "message":
                stamp = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
                self.msg.setText(f"[{stamp}] {msg.get('text','')}")
            elif typ == "controller_state":
                mapping = msg.get("mapping")
                if isinstance(mapping, dict):
                    self._mapping = {**self._mapping, **mapping}
                    stamp = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
                    self.msg.setText(f"[{stamp}] Controller mapping updated and persisted.")

    def _apply_state(self, s: Dict) -> None:
        x = float(s.get("x", 0.0))
        y = float(s.get("y", 0.0))
        z = float(s.get("z", 0.0))
        e = float(s.get("e", 0.0))
        self._xy_point.setData([x], [y])
        self.lab_x.setText(f"{x:.3f}")
        self.lab_y.setText(f"{y:.3f}")
        self.lab_z.setText(f"{z:.3f}")
        self.lab_e.setText(f"{e:.3f}")
        # qualitative Z viz
        self.z_bar.setValue(int((z % 10.0) / 10.0 * 1000))
        # reflect step sizes/feed back to inputs to keep UI in sync
        self.in_xy.setText(f"{float(s.get('xy_step', 0.2)):.3f}")
        self.in_z.setText(f"{float(s.get('z_step', 0.05)):.3f}")
        self.in_e.setText(f"{float(s.get('e_step', 0.02)):.3f}")
        self.in_feed.blockSignals(True)
        self.in_feed.setValue(int(s.get("feed", 3000)))
        self.in_feed.blockSignals(False)

    # ---------------------------- Qt keyboard control -------------------------

    def _emit_input(self, cmd: str, value):
        """Send the same dict messages the gantry already understands."""
        if not cmd or cmd == "none":
            return
        self.q_ctrl_to_gantry.put({"type": "input", "cmd": cmd, "value": value})

    def _tick_keyboard(self):
        """Runs ~60 Hz to convert held keys into joystick-like motion."""
        kd = self._pressed
        held = kd.__contains__

        # XY from WASD
        jx = float(held(Qt.Key_D)) - float(held(Qt.Key_A))
        jy = float(held(Qt.Key_S)) - float(held(Qt.Key_W))
        if jx or jy:
            self._emit_input("xy_motion", (jx, jy))

        # Z from Up/Down arrows
        z_jy = float(held(Qt.Key_Up)) - float(held(Qt.Key_Down))
        if z_jy:
            self._emit_input("z_motion", (0.0, z_jy))

        # E from [ and ]
        lt = float(held(Qt.Key_BracketLeft))
        rt = float(held(Qt.Key_BracketRight))
        if lt or rt:
            self._emit_input("e_motion", (lt, rt))

    def keyPressEvent(self, e):
        """Track pressed keys and fire debounced step-size/actions on key down."""
        self._pressed.add(e.key())
        if e.key() == Qt.Key_Comma:   self._emit_input("xy_step_size_dec", 1)
        if e.key() == Qt.Key_Period:  self._emit_input("xy_step_size_inc", 1)
        if e.key() == Qt.Key_K:       self._emit_input("z_step_size_dec", 1)
        if e.key() == Qt.Key_L:       self._emit_input("z_step_size_inc", 1)
        if e.key() == Qt.Key_N:       self._emit_input("e_step_size_dec", 1)
        if e.key() == Qt.Key_M:       self._emit_input("e_step_size_inc", 1)
        if e.key() == Qt.Key_H:       self._emit_input("home_all", 1)

    def keyReleaseEvent(self, e):
        """Stop considering the key as held when released."""
        self._pressed.discard(e.key())

    # -------------------------------- shutdown --------------------------------

    def closeEvent(self, ev) -> None:
        try:
            self.timer.stop()
        except Exception:
            pass
        # Best-effort terminate (child has its own loop)
        for p in (self.p_gantry,):
            try:
                p.terminate()
                p.join(timeout=1.0)
            except Exception:
                pass
        ev.accept()


# ----------------------------------- entry -----------------------------------
def _launch_gui(simulate: bool) -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    try:
        app.setStyleSheet(qdarkstyle.load_stylesheet_pyqt5())
    except Exception:
        pass
    win = GantryGUI(simulate=simulate)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    # On Windows, always use spawn. Also helps on macOS with PyQt.
    mp.freeze_support()
    try:
        mp.set_start_method("spawn")
    except RuntimeError:
        pass

    parser = argparse.ArgumentParser(description="Core-XY Gantry GUI (keyboard-only)")
    parser.add_argument("--simulate", action="store_true",
                        help="Run with simulator backend (no serial board).")
    args = parser.parse_args()

    _launch_gui(simulate=args.simulate)
