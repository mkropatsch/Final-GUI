from __future__ import annotations

# incubator.py
# Bidirectional serial thread for the Arduino incubator board.
#
# The Arduino outputs one line every ~5 seconds:
#   CO2: 412 ppm | Temp: 36.85 C | RH: 72.34 % | Setpoint: 37.00 C | Error: 0.15 | Heater PWM: 47 | Pump: STOPPED
#
# Commands we send TO the Arduino:
#   FWD <ms>        — run pump forward for <ms> milliseconds
#   REV <ms>        — run pump reverse for <ms> milliseconds
#   STOP            — stop pump immediately
#   SETPOINT <temp> — update heater target temperature

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


# Matches the Arduino's output line format
INCUBATOR_PATTERN = re.compile(
    r"CO2:\s*(\d+)\s*ppm"
    r"\s*\|\s*Temp:\s*([-\d\.]+)\s*C"
    r"\s*\|\s*RH:\s*([-\d\.]+)\s*%"
    r"\s*\|\s*Setpoint:\s*([-\d\.]+)\s*C"
    r"\s*\|\s*Error:\s*([-\d\.]+)"
    r"\s*\|\s*Heater PWM:\s*(\d+)"
    r"\s*\|\s*Pump:\s*(\w+)",
    re.IGNORECASE,
)


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
        self.q = out_queue or queue.Queue()          # inbound → GUI
        self._cmd_queue: queue.Queue[str] = queue.Queue()  # outbound → Arduino
        self._stop_event = threading.Event()
        self.ser = None

    # ------------------------------------------------------------------
    # Public API (call from GUI thread — thread-safe)
    # ------------------------------------------------------------------

    def stop(self) -> None:
        self._stop_event.set()

    def send_command(self, cmd: str) -> None:
        """Queue a command string to be sent to the Arduino."""
        self._cmd_queue.put(cmd)

    # Convenience helpers so callers don't have to format strings
    def pump_forward(self, duration_ms: int) -> None:
        self.send_command(f"FWD {int(duration_ms)}")

    def pump_reverse(self, duration_ms: int) -> None:
        self.send_command(f"REV {int(duration_ms)}")

    def pump_stop(self) -> None:
        self.send_command("STOP")

    def set_setpoint(self, temp: float) -> None:
        self.send_command(f"SETPOINT {temp:.2f}")

    # ------------------------------------------------------------------
    # Thread run loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=1)
            time.sleep(1.2)                  # wait for Arduino reset after serial open
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
                match = INCUBATOR_PATTERN.search(line)
                if match:
                    self.q.put((
                        "data",
                        {
                            "co2":        float(match.group(1)),
                            "temp":       float(match.group(2)),
                            "rh":         float(match.group(3)),
                            "setpoint":   float(match.group(4)),
                            "error":      float(match.group(5)),
                            "heater_pwm": int(match.group(6)),
                            "pump_state": match.group(7).upper(),  # STOPPED / FORWARD / REVERSE
                        },
                    ))
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
