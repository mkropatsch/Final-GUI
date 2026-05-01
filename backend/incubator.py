from __future__ import annotations

# incubator.py
# Bidirectional serial thread for the Arduino incubator board.
#
# The Arduino outputs two line types:
#
#   Every ~250 ms (thermistor / heater):
#     Thermistor Temp: 36.85 C | Setpoint: 37.00 C | PWM: 47 | SafetyHold: OFF
#
#   Every ~5 s (SCD41 CO2 sensor):
#     CO2: 412 ppm | SCD41 Temp: 25.30 C | RH: 72.34 %
#
# Commands we send TO the Arduino (all lowercase):
#   pump1 forward <ms>   — run pump 1 forward for <ms> milliseconds
#   pump1 reverse <ms>   — run pump 1 reverse for <ms> milliseconds
#   stop1                — stop pump 1
#   pump2 <ms>           — run pump 2 for <ms> milliseconds (single direction)
#   stop2                — stop pump 2
#   stopall              — stop all pumps
#   setpoint <temp>      — update heater target temperature (36.5–37.3 °C)
#   offset <val>         — adjust thermistor reading by <val> °C

import queue
import re
import threading
import time

HAS_SERIAL = True
try:
    import serial
    import serial.tools.list_ports
except Exception:
    HAS_SERIAL = False


# Thermistor / heater line (~250 ms cadence)
PATTERN_THERM = re.compile(
    r"Thermistor Temp:\s*([-\d\.]+)\s*C"
    r"\s*\|\s*Setpoint:\s*([-\d\.]+)\s*C"
    r"\s*\|\s*PWM:\s*(\d+)"
    r"\s*\|\s*SafetyHold:\s*(\w+)",
    re.IGNORECASE,
)

# SCD41 CO2/RH line (~5 s cadence); SCD41 Temp is ignored — thermistor is used for control
PATTERN_SCD41 = re.compile(
    r"CO2:\s*(\d+)\s*ppm"
    r"\s*\|\s*SCD41 Temp:\s*[-\d\.]+\s*C"
    r"\s*\|\s*RH:\s*([-\d\.]+)\s*%",
    re.IGNORECASE,
)

INCUBATOR_PATTERN = PATTERN_SCD41  # legacy alias


class IncubatorSerial(threading.Thread):
    """Bidirectional serial thread for the Arduino incubator board.

    Inbound data is parsed and placed on `out_queue` as tuples:
        ("status", str)          — connection status messages
        ("data",   dict)         — parsed sensor + control state
        ("raw",    str)          — unparsed lines (errors, boot messages, etc.)

    Outbound commands are sent via send_command(str), which is thread-safe.
    """

    def __init__(
        self,
        port: str,
        baud: int = 115200,
        out_queue: queue.Queue | None = None,
    ):
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.q = out_queue or queue.Queue()
        self._cmd_queue: queue.Queue[str] = queue.Queue()
        self._stop_event = threading.Event()
        self.ser = None
        self._state: dict = {}  # merged state from both line types

    # ------------------------------------------------------------------
    # Public API (call from GUI thread — thread-safe)
    # ------------------------------------------------------------------

    def stop(self) -> None:
        self._stop_event.set()

    def send_command(self, cmd: str) -> None:
        """Queue a command string to be sent to the Arduino."""
        self._cmd_queue.put(cmd)

    # ---- Pump 1 (bidirectional H-bridge) ----
    def pump1_forward(self, duration_ms: int) -> None:
        self.send_command(f"pump1 forward {int(duration_ms)}")

    def pump1_reverse(self, duration_ms: int) -> None:
        self.send_command(f"pump1 reverse {int(duration_ms)}")

    def stop1(self) -> None:
        self.send_command("stop1")

    # ---- Pump 2 (single direction) ----
    def pump2_run(self, duration_ms: int) -> None:
        self.send_command(f"pump2 {int(duration_ms)}")

    def stop2(self) -> None:
        self.send_command("stop2")

    # ---- All pumps ----
    def stop_all(self) -> None:
        self.send_command("stopall")

    # ---- Heater ----
    def set_setpoint(self, temp: float) -> None:
        self.send_command(f"setpoint {temp:.2f}")

    # ------------------------------------------------------------------
    # Thread run loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=1)
            time.sleep(1.2)
            self.ser.reset_input_buffer()
        except Exception as e:
            self.q.put(("status", f"Could not open {self.port}: {e}"))
            return

        self.q.put(("status", f"Connected to {self.port}"))

        while not self._stop_event.is_set():

            # --- drain any outbound commands first ---
            try:
                while True:
                    cmd = self._cmd_queue.get_nowait()
                    self.ser.write((cmd + "\n").encode("utf-8"))
                    self.ser.flush()
            except queue.Empty:
                pass

            # --- read one line from Arduino ---
            try:
                raw = self.ser.readline()
                if not raw:
                    continue
                line = raw.decode(errors="replace").strip()

                therm = PATTERN_THERM.search(line)
                scd = PATTERN_SCD41.search(line)

                if therm:
                    self._state.update({
                        "temp":        float(therm.group(1)),
                        "setpoint":    float(therm.group(2)),
                        "heater_pwm":  int(therm.group(3)),
                        "safety_hold": therm.group(4).upper() == "ON",
                    })
                    self.q.put(("data", dict(self._state)))
                elif scd:
                    self._state.update({
                        "co2": float(scd.group(1)),
                        "rh":  float(scd.group(2)),
                    })
                    self.q.put(("data", dict(self._state)))
                elif line:
                    self.q.put(("raw", line))

            except Exception as e:
                self.q.put(("status", f"Serial error: {e}"))
                break

        # --- cleanup ---
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass
        self.q.put(("status", "Disconnected"))
