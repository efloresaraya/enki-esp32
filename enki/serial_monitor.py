"""Basic pyserial monitor."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import serial

from enki.board_detect import auto_detect_port
from enki.logs import new_log_file, write_log
from enki.utils import MonitorError


def resolve_port(port: str) -> str:
    if port != "auto":
        return port
    detected = auto_detect_port()
    if not detected:
        raise MonitorError("No serial port found for auto detection", command="monitor")
    return detected


def monitor_serial(
    *,
    port: str,
    baudrate: int,
    seconds: float | None = None,
    as_json: bool = False,
    project_dir: Path | None = None,
) -> dict[str, Any]:
    resolved = resolve_port(port)
    lines: list[str] = []
    log_file = new_log_file("monitor", project_dir) if seconds else None
    started = time.time()
    try:
        with serial.Serial(resolved, baudrate=baudrate, timeout=0.2) as ser:
            while True:
                raw = ser.readline()
                if raw:
                    text = raw.decode("utf-8", errors="replace").rstrip()
                    lines.append(text)
                    if not as_json:
                        print(text)
                if seconds is not None and time.time() - started >= seconds:
                    break
                if seconds is None:
                    time.sleep(0.01)
    except KeyboardInterrupt:
        pass
    except serial.SerialException as exc:
        raise MonitorError(f"Serial monitor failed: {exc}", command="monitor", log_file=log_file) from exc

    if log_file:
        write_log(log_file, "\n".join(lines) + ("\n" if lines else ""))
    return {
        "ok": True,
        "port": resolved,
        "baudrate": baudrate,
        "seconds": seconds,
        "lines": lines if as_json else None,
        "log_file": str(log_file) if log_file else None,
    }


def parse_json_lines(lines: list[str]) -> list[Any]:
    parsed = []
    for line in lines:
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            parsed.append({"raw": line})
    return parsed
