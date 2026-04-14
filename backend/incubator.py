from __future__ import annotations

# incubator.py
# Bidirectional serial thread for the Arduino incubator board.
#
# The Arduino outputs one line every ~5 seconds:
#   CO2: 412 ppm | Temp: 36.85 C | RH: 72.34 % | Setpoint: 37.00 C | Heater PWM: 47
#
# Commands we send TO the Arduino (all lowercase — Arduino calls cmd.toLowerCase()):
#   pump1 forward <ms>   — run pump 1 forward for <ms> milliseconds
#   pump1 reverse <ms>   — run pump 1 reverse for <ms> milliseconds
#   pump2 forward <ms>   — run pump 2 forward for <ms> milliseconds
#   pump2 reverse <ms>   — run pump 2 reverse for <ms> milliseconds
#   pump3 <ms>           — run pump 3 for <ms> milliseconds (one direction only)
#   pump4 <ms>           — run pump 4 for <ms> milliseconds (one direction only)
#   stop1                — stop pump 1
#   stop2                — stop pump 2
#   stop3                — stop pump 3
#   stop4                — stop pump 4
#   stopall              — stop all pumps
#   setpoint <temp>      — update heater target temperature
#
# TODO: wire pump commands into the routine (RoutineController._dispense).
#       Decide which pump(s) the routine should trigger at each well and
#       whether a reverse/aspirate step is needed after dispensing.

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


# Matches the Arduino's output line format (no Error or Pump fields in new firmware)
INCUBATOR_PATTERN = re.compile(
    r"CO2:\s*(\d+)\s*ppm"
    r"\s*\|\s*Temp:\s*([-\d\.]+)\s*C"
    r"\s*\|\s*RH:\s*([-\d\.]+)\s*%"
    r"\s*\|\s*Setpoint:\s*([-\d\.]+)\s*C"
    r"\s*\|\s*Heater PWM:\s*(\d+)",
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
        self.q = out_queue or queue.Queue()
        self._cmd_queue: queue.Queue[str] = queue.Queue()
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

    # ---- Pump 1 (bidirectional H-bridge) ----
    def pump1_forward(self, duration_ms: int) -> None:
        self.send_command(f"pump1 forward {int(duration_ms)}")

    def pump1_reverse(self, duration_ms: int) -> None:
        self.send_command(f"pump1 reverse {int(duration_ms)}")

    def stop1(self) -> None:
        self.send_command("stop1")

    # ---- Pump 2 (bidirectional H-bridge) ----
    def pump2_forward(self, duration_ms: int) -> None:
        self.send_command(f"pump2 forward {int(duration_ms)}")

    def pump2_reverse(self, duration_ms: int) -> None:
        self.send_command(f"pump2 reverse {int(duration_ms)}")

    def stop2(self) -> None:
        self.send_command("stop2")

    # ---- Pump 3 (single direction) ----
    def pump3_run(self, duration_ms: int) -> None:
        self.send_command(f"pump3 {int(duration_ms)}")

    def stop3(self) -> None:
        self.send_command("stop3")

    # ---- Pump 4 (single direction) ----
    def pump4_run(self, duration_ms: int) -> None:
        self.send_command(f"pump4 {int(duration_ms)}")

    def stop4(self) -> None:
        self.send_command("stop4")

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
                match = INCUBATOR_PATTERN.search(line)
                if match:
                    self.q.put((
                        "data",
                        {
                            "co2":        float(match.group(1)),
                            "temp":       float(match.group(2)),
                            "rh":         float(match.group(3)),
                            "setpoint":   float(match.group(4)),
                            "heater_pwm": int(match.group(5)),
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
