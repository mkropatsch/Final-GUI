from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterator, Optional, Tuple

from PyQt5.QtCore import QObject, QTimer, pyqtSignal


@dataclass
class RoutineConfig:
    plate_type: str = "24"
    rows: int = 4
    cols: int = 6
    dx: float = 1.0
    dy: float = 1.0
    dz: float = 0.0
    wait_s: float = 1.0
    serpentine: bool = True
    dispense_ms: int = 0
    retract_ms: int = 0
    total_run_s: float = 0.0


class RoutineController(QObject):
    status_changed = pyqtSignal(dict)
    log_message = pyqtSignal(str)

    def __init__(
        self,
        command_sink: Callable[[dict], None],
        dispense_sink: Callable[[int], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)

        self._send_command = command_sink
        self._dispense = dispense_sink

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)

        self._config = RoutineConfig()
        self._running = False
        self._run_start_time: float = 0.0

        self._row = 0
        self._col = 0
        self._move_iter: Optional[Iterator[Tuple[int, int, int, int]]] = None
        self._phase = "idle"

    @property
    def is_running(self) -> bool:
        return self._running

    def update_config(self, raw: dict) -> None:
        self._config = self._parse_config(raw)
        self.log_message.emit(
            f"Routine config updated: {self._config.rows}x{self._config.cols}, "
            f"dx={self._config.dx}, dy={self._config.dy}, dz={self._config.dz}, "
            f"wait={self._config.wait_s}s, serpentine={self._config.serpentine}"
        )

    def start(self, raw: Optional[dict] = None) -> None:
        if raw is not None:
            self._config = self._parse_config(raw)

        if self._running:
            self.log_message.emit("Routine is already running.")
            return

        if self._config.rows < 1 or self._config.cols < 1:
            self.log_message.emit("Routine start failed: rows and cols must both be >= 1.")
            self._emit_status("Error", phase="Invalid config")
            return

        self._running = True
        self._run_start_time = time.time()
        self._row = 0
        self._col = 0
        self._move_iter = iter(
            self._grid_iter(
                rows=self._config.rows,
                cols=self._config.cols,
                serpentine=self._config.serpentine,
            )
        )
        self._phase = "z_down"

        self.log_message.emit(
            f"Routine started: {self._config.rows}x{self._config.cols}, "
            f"dx={self._config.dx}, dy={self._config.dy}, dz={self._config.dz}, "
            f"wait={self._config.wait_s}s"
        )
        self._emit_status("Running", phase="Z down")
        self._timer.start(0)

    def stop(self) -> None:
        if not self._running and self._phase == "idle":
            return

        self._timer.stop()
        self._running = False
        self._move_iter = None
        self._phase = "idle"

        self.log_message.emit("Routine stopped.")
        self._emit_status("Stopped", phase="Stopped")

    def _tick(self) -> None:
        if not self._running:
            return

        if self._phase == "z_down":
            self._do_z_down()
            return

        if self._phase == "z_wait":
            self._do_z_wait()
            return

        if self._phase == "z_up":
            self._do_z_up()
            return

        if self._phase == "xy_move":
            self._do_xy_move()
            return

        self.log_message.emit(f"Routine entered unknown phase '{self._phase}'. Stopping.")
        self.stop()

    def _do_z_down(self) -> None:
        dz = self._config.dz
        self._emit_status("Running", phase="Z down")

        if abs(dz) > 1e-9:
            self._send_command({
                "type": "gantry_cmd",
                "cmd": "move_rel",
                "dx": 0.0,
                "dy": 0.0,
                "dz": -abs(dz),
                "de": 0.0,
            })
            self._send_motion_barrier()

        self._phase = "z_wait"
        self._timer.start(0)

    def _do_z_wait(self) -> None:
        self._emit_status("Running", phase="Wait")
        
        if self._dispense and self._config.dispense_ms > 0:
            self._dispense(self._config.dispense_ms)
            
        wait_ms = max(0, int(self._config.wait_s * 1000))
        self._phase = "z_up"
        self._timer.start(wait_ms)

    def _do_z_up(self) -> None:
        dz = self._config.dz
        self._emit_status("Running", phase="Z up")

        if abs(dz) > 1e-9:
            self._send_command({
                "type": "gantry_cmd",
                "cmd": "move_rel",
                "dx": 0.0,
                "dy": 0.0,
                "dz": abs(dz),
                "de": 0.0,
            })
            self._send_motion_barrier()

        self._phase = "xy_move"
        self._timer.start(0)

    def _do_xy_move(self) -> None:
        if self._move_iter is None:
            self.log_message.emit("Routine move iterator missing. Stopping.")
            self.stop()
            return

        try:
            next_row, next_col, _dcol, _drow = next(self._move_iter)
        except StopIteration:
            self.log_message.emit("Routine pass complete. Returning to A1.")
            self._emit_status(
                "Running",
                phase="Returning home",
                current_well=self._well_name(self._row, self._col),
                highlight_row=self._row,
                highlight_col=self._col,
            )
            self._send_command({
                "type": "gantry_cmd",
                "cmd": "move_abs",
                "X": 0.0,
                "Y": 0.0,
                "Z": 0.0,
                "E": 0.0,
            })
            self._send_motion_barrier()

            elapsed = time.time() - self._run_start_time
            if self._config.total_run_s > 0 and elapsed < self._config.total_run_s:
                remaining = self._config.total_run_s - elapsed
                self.log_message.emit(
                    f"Repeating routine ({remaining:.0f}s remaining)."
                )
                self._row = 0
                self._col = 0
                self._move_iter = iter(
                    self._grid_iter(
                        rows=self._config.rows,
                        cols=self._config.cols,
                        serpentine=self._config.serpentine,
                    )
                )
                self._phase = "z_down"
                self._timer.start(0)
            else:
                self.log_message.emit("Routine complete.")
                self.status_changed.emit({
                    "status": "Complete",
                    "phase": "Done",
                    "current_well": None,
                    "highlight_row": None,
                    "highlight_col": None,
                })
                self._running = False
                self._phase = "idle"
            return

        abs_x = float(next_col * self._config.dx)
        abs_y = float(next_row * self._config.dy)

        self._send_command({
            "type": "gantry_cmd",
            "cmd": "move_abs",
            "X": abs_x,
            "Y": abs_y,
            "Z": 0.0,
            "E": 0.0,
        })
        self._send_motion_barrier()

        self._row = next_row
        self._col = next_col

        self.log_message.emit(
            f"Moving to {self._well_name(self._row, self._col)} "
            f"(X={abs_x:.3f}, Y={abs_y:.3f})."
        )

        self._phase = "z_down"
        self._emit_status("Running", phase="Move next")
        self._timer.start(0)

    def _parse_config(self, raw: dict) -> RoutineConfig:
        def _f(key: str, default: float) -> float:
            val = raw.get(key, default)
            if val in ("", None):
                return float(default)
            return float(val)

        return RoutineConfig(
            plate_type=str(raw.get("plate_type", "24")),
            rows=int(raw.get("rows", 4)),
            cols=int(raw.get("cols", 6)),
            dx=_f("dx", 1.0),
            dy=_f("dy", 1.0),
            dz=_f("dz", 0.0),
            wait_s=_f("wait_s", 1.0),
            serpentine=bool(raw.get("serpentine", True)),
            dispense_ms=int(_f("dispense_ms", 0)),
            retract_ms=int(_f("retract_ms", 0)),
            total_run_s=_f("total_run_s", 0.0),
        )

    def _grid_iter(
        self,
        rows: int,
        cols: int,
        serpentine: bool,
    ) -> Iterator[Tuple[int, int, int, int]]:
        cur_col = 0

        for r in range(rows):
            forward = (r % 2 == 0) or (not serpentine)

            if forward:
                for c in range(1, cols):
                    yield r, c, +1, 0
                    cur_col = c
            else:
                for c in range(cols - 2, -1, -1):
                    yield r, c, -1, 0
                    cur_col = c

            if r < rows - 1:
                yield r + 1, cur_col, 0, +1

    def _well_name(self, row: int, col: int) -> str:
        return f"{chr(65 + row)}{col + 1}"

    def _send_motion_barrier(self) -> None:
        self._send_command({"type": "gcode", "cmd": "M400"})

    def _emit_status(
        self,
        status: str,
        phase: str,
        current_well: Optional[str] = None,
        highlight_row: Optional[int] = None,
        highlight_col: Optional[int] = None,
    ) -> None:
        payload = {
            "status": status,
            "phase": phase,
            "current_well": current_well or self._well_name(self._row, self._col),
            "highlight_row": self._row if highlight_row is None else highlight_row,
            "highlight_col": self._col if highlight_col is None else highlight_col,
        }
        self.status_changed.emit(payload)