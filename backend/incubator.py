from __future__ import annotations

# incubator.py
# Bidirectional serial thread for the Marlin-based incubator board.
#
# Marlin outputs temperature auto-reports (enabled via M155 S5) every 5 seconds:
#   T:22.87 /37.00 @:127
#
# CO2/RH sensor lines arrive on the same serial stream (separate from Marlin):
#   CO2: 412 ppm | RH: 72.34 %
#
# Commands we send TO the board:
#   M999               — clear any halted/error state (sent once on connect)
#   M155 S5            — enable auto temperature report every 5 seconds (sent once on connect)
#   M104 S<temp>       — set heater target temperature
#   pump1 forward <ms> — run pump 1 forward for <ms> milliseconds
#   pump1 reverse <ms> — run pump 1 reverse for <ms> milliseconds
#   stop1              — stop pump 1
#   pump2 <ms>         — run pump 2 for <ms> milliseconds (single direction)
#   stop2              — stop pump 2
#   stopall            — stop all pumps

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


# Marlin auto-report temperature line: T:22.87 /37.00 @:127
# Groups: (current_temp, setpoint, heater_power 0-255)
MARLIN_TEMP_PATTERN = re.compile(
    r"T:([-\d\.]+)\s*/\s*([-\d\.]+)\s*@:(\d+)",
    re.IGNORECASE,
)

# CO2/RH sensor line (same serial stream, separate from Marlin temperature reporting)
# Tolerates extra fields between CO2 and RH.
# Groups: (co2_ppm, rh_percent)
CO2_RH_PATTERN = re.compile(
    r"CO2:\s*(\d+)\s*ppm"
    r".*?RH:\s*([-\d\.]+)\s*%",
    re.IGNORECASE,
)

# Legacy alias kept so any code importing INCUBATOR_PATTERN still compiles
INCUBATOR_PATTERN = CO2_RH_PATTERN


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
        self.send_command(f"M104 S{temp:.2f}")

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

        # Clear any halted/error state, then enable auto temperature reporting every 5 s
        self.ser.write(b"M999\n")
        self.ser.flush()
        time.sleep(0.1)
        self.ser.write(b"M155 S5\n")
        self.ser.flush()

        # Accumulate partial readings across line types so each emission carries
        # the most recent values for all fields (temp lines don't include CO2/RH
        # and CO2/RH lines don't include temp).
        _last: dict = {}

        while not self._stop_event.is_set():

            # --- drain any outbound commands first ---
            try:
                while True:
                    cmd = self._cmd_queue.get_nowait()
                    self.ser.write((cmd + "\n").encode("utf-8"))
                    self.ser.flush()
            except queue.Empty:
                pass

            # --- read one line from Marlin ---
            try:
                raw = self.ser.readline()
                if not raw:
                    continue
                line = raw.decode(errors="replace").strip()

                temp_match = MARLIN_TEMP_PATTERN.search(line)
                co2_match = CO2_RH_PATTERN.search(line)

                if temp_match:
                    _last["temp"]       = float(temp_match.group(1))
                    _last["setpoint"]   = float(temp_match.group(2))
                    _last["heater_pwm"] = int(temp_match.group(3))
                    self.q.put(("data", dict(_last)))
                elif co2_match:
                    _last["co2"] = float(co2_match.group(1))
                    _last["rh"]  = float(co2_match.group(2))
                    self.q.put(("data", dict(_last)))
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
