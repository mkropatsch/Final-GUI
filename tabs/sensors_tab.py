from __future__ import annotations

import math
import queue # communication between worker thread and GUI
import random # for simulation mode
import re
import threading # sensor reading happens in the background so GUI doesn't lock up
import time
from collections import deque #used for live plotting (keeps a number of "recent items")

from PyQt5.QtCore import Qt, QTimer # all just GUI building pieces
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

HAS_SERIAL = True # if pyserial isn't downloaded, simulation should still work
try:
    import serial
    import serial.tools.list_ports
except Exception:
    HAS_SERIAL = False

HAS_MPL = True # matplotlib should be downloaded, if not it just won't show plots
try:
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
except Exception:
    HAS_MPL = False


PATTERN = re.compile( # goal is to pull the readings, looks for specific text
    r"CO2:\s*(\d+)\s*ppm\s*\|\s*Temp:\s*([-\d\.]+)\s*°C\s*\|\s*RH:\s*([-\d\.]+)\s*%\s*\|\s*\[(?:O₂|O2|O)\]\s*([-\d\.]+)",
    re.IGNORECASE, #case difference won't matter (temp vs Temp)
)


class SerialReader(threading.Thread): # runs independently from the GUI and reads sensor data continuously
    def __init__(self, port: str, baud: int = 115200, out_queue: queue.Queue | None = None):
        super().__init__(daemon=True) #initializes the thread
        self.port = port # port and baud stores connection info
        self.baud = baud
        self.q = out_queue or queue.Queue() #if queue is passed in -> use it, otherwise create a new one (the GUI passes in a shared queue, so both thread and GUI use the same one)
        self._stop_event = threading.Event() #used to stop the loop
        self.ser = None #placeholder for serial connection

    def stop(self) -> None: #sets the flag so the loop can stop without killing threads
        self._stop_event.set()

    def run(self) -> None:
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=1)
            time.sleep(1.2) #many arduino boards reset when serial opens, so it gives it some time to reboot
            self.ser.reset_input_buffer() #clears any junk data that may have been sent during startup
        except Exception as e:
            self.q.put(("status", f"Could not open {self.port}: {e}"))
            return #sends this message instead of crashing basically

        self.q.put(("status", f"Connected to {self.port}"))

        while not self._stop_event.is_set(): #basically this loop runs forever until it's stopped
            try:
                raw = self.ser.readline() #waits for a full line
                if not raw:
                    continue
                line = raw.decode(errors="replace").strip() #converts to a string
                match = PATTERN.search(line) #if it matches the pattern, it sends the data
                if match:
                    self.q.put((
                        "data",
                        {
                            "co2": float(match.group(1)),
                            "temp": float(match.group(2)),
                            "rh": float(match.group(3)),
                            "o2": float(match.group(4)),
                        },
                    ))
                elif line:
                    self.q.put(("raw", line))
            except Exception as e:
                self.q.put(("status", f"Serial error: {e}"))
                break

        try:
            if self.ser and self.ser.is_open: #closes the port safely
                self.ser.close()
        except Exception:
            pass
        self.q.put(("status", "Disconnected")) #tells the gui it's done


class SimReader(threading.Thread):
    def __init__(self, out_queue: queue.Queue | None = None, rate_hz: float = 1.0):
        super().__init__(daemon=True)
        self.q = out_queue or queue.Queue()
        self._stop_event = threading.Event()
        self.rate = max(0.1, float(rate_hz))
        self.t0 = time.time()
        self.base_co2 = 650 + random.uniform(-40, 40)
        self.base_temp = 22.5 + random.uniform(-1, 1)
        self.base_rh = 42 + random.uniform(-5, 5)
        self.base_o2 = 20.7 + random.uniform(-0.2, 0.2)

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        self.q.put(("status", "Simulation running"))
        dt = 1.0 / self.rate
        while not self._stop_event.is_set():
            t = time.time() - self.t0
            co2 = self.base_co2 + 30 * math.sin(2 * math.pi * 0.015 * t) + 8 * math.sin(2 * math.pi * 0.21 * t) + random.gauss(0, 3)
            co2 = max(380, co2)
            temp = self.base_temp + 0.25 * math.sin(2 * math.pi * 0.01 * t) + random.gauss(0, 0.03)
            rh = self.base_rh - 0.35 * (temp - self.base_temp) + random.gauss(0, 0.2)
            rh = max(15, min(85, rh))
            o2 = self.base_o2 + 0.02 * math.sin(2 * math.pi * 0.02 * t) + random.gauss(0, 0.01)
            self.q.put(("data", {"co2": float(co2), "temp": float(temp), "rh": float(rh), "o2": float(o2)}))
            time.sleep(dt)
        self.q.put(("status", "Simulation stopped"))


def list_ports() -> list[str]:
    if not HAS_SERIAL:
        return []
    return [p.device for p in serial.tools.list_ports.comports()]


class SensorCard(QFrame):
    def __init__(self, title: str, value: str, accent: str):
        super().__init__()
        self.accent = accent
        self.setObjectName("SensorCard")
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            f"""
            QFrame#SensorCard {{
                background-color: #182433;
                border: 1px solid #31465f;
                border-left: 5px solid {accent};
                border-radius: 10px;
            }}
            QLabel {{ border: none; }}
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("color: #cfd8e3; font-size: 12px;")

        self.value_label = QLabel(value)
        self.value_label.setStyleSheet(f"color: {accent}; font-size: 24px; font-weight: 700;")

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addStretch()

    def set_value(self, text: str) -> None:
        self.value_label.setText(text)


class SensorsTab(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self.reader: SerialReader | SimReader | None = None
        self.q: queue.Queue = queue.Queue()
        self.t0 = time.time()
        self.is_recording = True

        self.T_GOOD = 800
        self.T_WARN = 1500

        self.ts = deque(maxlen=600)
        self.co2_series = deque(maxlen=600)
        self.temp_series = deque(maxlen=600)
        self.rh_series = deque(maxlen=600)
        self.o2_series = deque(maxlen=600)

        self._build_ui()
        self.refresh_ports()

        self.timer = QTimer(self)
        self.timer.setInterval(60)
        self.timer.timeout.connect(self._pump_queue)
        self.timer.start()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        conn_box = QGroupBox("Sensor Connection")
        conn_layout = QHBoxLayout(conn_box)

        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(260)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_connect = QPushButton("Connect")
        self.btn_record = QPushButton("Stop Recording")
        self.btn_clear = QPushButton("Clear Graphs")

        self.conn_status = QLabel("Not connected")
        self.conn_status.setMinimumWidth(240)
        self.conn_status.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        conn_layout.addWidget(QLabel("Port"))
        conn_layout.addWidget(self.port_combo)
        conn_layout.addWidget(self.btn_refresh)
        conn_layout.addWidget(self.btn_connect)
        conn_layout.addWidget(self.btn_record)
        conn_layout.addWidget(self.btn_clear)
        conn_layout.addSpacing(12)
        conn_layout.addWidget(self.conn_status)
        conn_layout.addStretch()

        root.addWidget(conn_box)

        cards_box = QWidget()
        cards_layout = QGridLayout(cards_box)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setHorizontalSpacing(12)
        cards_layout.setVerticalSpacing(12)

        self.card_co2 = SensorCard("CO₂ (ppm)", "—", "#88e06b")
        self.card_temp = SensorCard("Temp (°C)", "—", "#ff8a70")
        self.card_rh = SensorCard("Humidity (%)", "—", "#c68cff")
        self.card_o2 = SensorCard("O₂ (%vol)", "—", "#85c7ff")

        cards_layout.addWidget(self.card_co2, 0, 0)
        cards_layout.addWidget(self.card_temp, 0, 1)
        cards_layout.addWidget(self.card_rh, 0, 2)
        cards_layout.addWidget(self.card_o2, 0, 3)
        for col in range(4):
            cards_layout.setColumnStretch(col, 1)

        root.addWidget(cards_box)

        self.air_status = QLabel("Status: —")
        self.air_status.setStyleSheet("font-size: 16px; font-weight: 700; color: #d8e2ee; padding: 2px 4px;")
        root.addWidget(self.air_status)

        self.tip_label = QLabel("Connect a sensor port or choose simulation mode.")
        self.tip_label.setWordWrap(True)
        self.tip_label.setStyleSheet("color: #aeb9c8; padding-left: 4px;")
        root.addWidget(self.tip_label)

        plots_box = QGroupBox("Live Sensor Plots")
        plots_box.setStyleSheet("""
        QGroupBox {
            font-size: 20px;
            font-weight: bold;
            color: #dddddd;
        }
        """)
        
        plots_layout = QVBoxLayout(plots_box)

        if HAS_MPL:
            self.fig = Figure(figsize=(8.5, 5.2), dpi=100, facecolor="#122033")
            self.ax_co2 = self.fig.add_subplot(221)
            self.ax_temp = self.fig.add_subplot(222)
            self.ax_rh = self.fig.add_subplot(223)
            self.ax_o2 = self.fig.add_subplot(224)

            self._style_axes(self.ax_co2, "CO₂ (ppm)")
            self._style_axes(self.ax_temp, "Temp (°C)")
            self._style_axes(self.ax_rh, "RH (%)", xlabel="Time (s)")
            self._style_axes(self.ax_o2, "O₂ (%vol)", xlabel="Time (s)")

            (self.l_co2,) = self.ax_co2.plot([], [], lw=1.8, color="#88e06b")
            (self.l_temp,) = self.ax_temp.plot([], [], lw=1.8, color="#ff8a70")
            (self.l_rh,) = self.ax_rh.plot([], [], lw=1.8, color="#c68cff")
            (self.l_o2,) = self.ax_o2.plot([], [], lw=1.8, color="#85c7ff")

            self.canvas = FigureCanvas(self.fig)
            self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            plots_layout.addWidget(self.canvas)
        else:
            self.canvas = None
            msg = QLabel("matplotlib is not installed, so live plots are unavailable.")
            msg.setAlignment(Qt.AlignCenter)
            msg.setMinimumHeight(280)
            msg.setStyleSheet("color: #aeb9c8;")
            plots_layout.addWidget(msg)

        root.addWidget(plots_box, 1)

        self.btn_refresh.clicked.connect(self.refresh_ports)
        self.btn_connect.clicked.connect(self.toggle_connect)
        self.btn_record.clicked.connect(self.toggle_recording)
        self.btn_clear.clicked.connect(self._clear_graphs)

        self.setStyleSheet(
            """
            QGroupBox {
                font-weight: bold;
                border: 1px solid #31465f;
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 10px;
                color: #e5edf7;
                background-color: #122033;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QLabel {
                color: #d8e2ee;
            }
            QPushButton {
                background-color: #4b617b;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
            }
            QPushButton:hover { background-color: #5d7694; }
            QPushButton:pressed { background-color: #40546a; }
            QComboBox {
                background-color: #102030;
                color: #d8e2ee;
                border: 1px solid #48607d;
                border-radius: 4px;
                padding: 6px 8px;
            }
            """
        )

    def _style_axes(self, ax, ylabel: str, xlabel: str = "") -> None:
        ax.set_facecolor("#16283b")
        ax.grid(True, linestyle="--", alpha=0.25)
        ax.tick_params(colors="#d8e2ee", labelsize=8)
        ax.set_ylabel(ylabel, color="#d8e2ee")
        if xlabel:
            ax.set_xlabel(xlabel, color="#d8e2ee")
        for spine in ax.spines.values():
            spine.set_color("#55708f")

    def refresh_ports(self) -> None:
        ports = ["SIMULATE (no hardware)"] + (list_ports() if HAS_SERIAL else [])
        current = self.port_combo.currentText()

        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        self.port_combo.addItems(ports)
        if current in ports:
            self.port_combo.setCurrentText(current)
        else:
            self.port_combo.setCurrentIndex(0)
        self.port_combo.blockSignals(False)

        if not self.reader:
            self.conn_status.setText("Select a port and click Connect.")

    def toggle_connect(self) -> None:
        if self.reader is not None:
            self.reader.stop()
            self.reader = None
            self.btn_connect.setText("Connect")
            self.conn_status.setText("Disconnecting…")
            return

        port = self.port_combo.currentText().strip()
        if not port:
            QMessageBox.warning(self, "Port", "Pick a port or simulation mode.")
            return

        if port.startswith("SIMULATE"):
            self.reader = SimReader(out_queue=self.q, rate_hz=1.0)
        else:
            if not HAS_SERIAL:
                QMessageBox.critical(self, "pyserial missing", "Install pyserial or choose simulation mode.")
                return
            self.reader = SerialReader(port=port, baud=115200, out_queue=self.q)

        self.reader.start()
        self.btn_connect.setText("Disconnect")
        self.conn_status.setText(f"Connecting to {port}…")


    def toggle_recording(self) -> None:
        # warning if nothing is connected:
        if self.reader is None:
            QMessageBox.warning(self, "Not Connected", "Please connect first.")
            return
        
        self.is_recording = not self.is_recording
        
        if self.is_recording:
            self.btn_record.setText("Stop Recording")
            self.conn_status.setText("Recording resumed.")
        else:
            self.btn_record.setText("Start Recording")
            self.conn_status.setText("Recording paused.")


    def _pump_queue(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "status":
                    self.conn_status.setText(str(payload))
                    if str(payload).lower() in {"disconnected", "simulation stopped"}:
                        self.btn_connect.setText("Connect")
                elif kind == "raw":
                    pass
                elif kind == "data":
                    self._update_readings(payload)
        except queue.Empty:
            pass
        except Exception as e:
            self.conn_status.setText(f"Queue error: {e}")

    def _update_readings(self, d: dict) -> None:
        co2 = d.get("co2")
        temp = d.get("temp")
        rh = d.get("rh")
        o2 = d.get("o2")

        if co2 is not None:
            self.card_co2.set_value(f"{co2:.0f}")
        if temp is not None:
            self.card_temp.set_value(f"{temp:.2f}")
        if rh is not None:
            self.card_rh.set_value(f"{rh:.1f}")
        if o2 is not None:
            self.card_o2.set_value(f"{o2:.2f}")

        if HAS_MPL and self.is_recording and co2 is not None:
            t = time.time() - self.t0
            self.ts.append(t)
            self.co2_series.append(co2)
            if temp is not None:
                self.temp_series.append(temp)
            if rh is not None:
                self.rh_series.append(rh)
            if o2 is not None:
                self.o2_series.append(o2)

            xt = list(self.ts)
            y_co2 = list(self.co2_series)
            y_temp = list(self.temp_series)
            y_rh = list(self.rh_series)
            y_o2 = list(self.o2_series)

            self.l_co2.set_data(xt, y_co2)
            if y_temp:
                self.l_temp.set_data(xt[: len(y_temp)], y_temp)
            if y_rh:
                self.l_rh.set_data(xt[: len(y_rh)], y_rh)
            if y_o2:
                self.l_o2.set_data(xt[: len(y_o2)], y_o2)

            self._autoscale(self.ax_co2, xt, y_co2)
            if y_temp:
                self._autoscale(self.ax_temp, xt[: len(y_temp)], y_temp)
            if y_rh:
                self._autoscale(self.ax_rh, xt[: len(y_rh)], y_rh)
            if y_o2:
                self._autoscale(self.ax_o2, xt[: len(y_o2)], y_o2)

            if self.canvas is not None:
                self.canvas.draw_idle()

        if co2 is not None:
            self._apply_air_status(float(co2))

    def _autoscale(self, ax, x, y, pad_frac: float = 0.08, pad_abs: float = 0.02) -> None:
        if len(x) < 2 or len(y) < 2:
            return
        ax.set_xlim(x[0], x[-1] + 0.1)
        ymin, ymax = min(y), max(y)
        if ymin == ymax:
            ymin -= 1
            ymax += 1
        pad = max((ymax - ymin) * pad_frac, pad_abs * max(abs(ymin), abs(ymax), 1))
        ax.set_ylim(ymin - pad, ymax + pad)

    def _apply_air_status(self, co2: float) -> None:
        if co2 < self.T_GOOD:
            self.air_status.setText("Status: Fresh air")
            self.air_status.setStyleSheet("font-size: 16px; font-weight: 700; color: #88e06b; padding: 2px 4px;")
            self.tip_label.setText("Air looks good right now.")
        elif co2 < self.T_WARN:
            self.air_status.setText("Status: Getting stuffy")
            self.air_status.setStyleSheet("font-size: 16px; font-weight: 700; color: #ffcb6b; padding: 2px 4px;")
            self.tip_label.setText("Consider opening a window or increasing ventilation.")
        else:
            self.air_status.setText("Status: High CO₂ — ventilate")
            self.air_status.setStyleSheet("font-size: 16px; font-weight: 700; color: #ff7b72; padding: 2px 4px;")
            self.tip_label.setText("Open doors/windows or increase airflow to bring CO₂ down.")

   
    def _clear_graphs(self) -> None:
        self.t0 = time.time()
        
        self.ts.clear()
        self.co2_series.clear()
        self.temp_series.clear()
        self.rh_series.clear()
        self.o2_series.clear()
        
        if HAS_MPL:
            self.l_co2.set_data([], [])
            self.l_temp.set_data([], [])
            self.l_rh.set_data([], [])
            self.l_o2.set_data([], [])
            
            # Reset axes to a clean blank view
            self.ax_co2.set_xlim(0, 10)
            self.ax_co2.set_ylim(0, 2000)
            
            self.ax_temp.set_xlim(0, 10)
            self.ax_temp.set_ylim(15, 35)
            
            self.ax_rh.set_xlim(0, 10)
            self.ax_rh.set_ylim(0, 100)
            
            self.ax_o2.set_xlim(0, 10)
            self.ax_o2.set_ylim(15, 25)
            
            if self.canvas is not None:
                self.canvas.draw_idle()
        self.conn_status.setText("Graph data cleared.")
            
            
   
    def shutdown(self) -> None:
        if self.reader is not None:
            try:
                self.reader.stop()
            except Exception:
                pass
            self.reader = None

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)


if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication
    import sys

    app = QApplication(sys.argv)
    w = SensorsTab()
    w.resize(1200, 760)
    w.show()
    sys.exit(app.exec_())
