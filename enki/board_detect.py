"""Serial board and port detection."""

from __future__ import annotations

import glob
import platform
from pathlib import Path
from typing import Any

from serial.tools import list_ports

MAC_PREFERRED_PREFIXES = (
    "/dev/cu.usbserial",
    "/dev/cu.SLAB_USBtoUART",
    "/dev/cu.wchusbserial",
    "/dev/cu.usbmodem",
)
MAC_LOW_CONFIDENCE_NAMES = ("Bluetooth", "debug-console")


def port_exists(port: str | None) -> bool:
    """Return true when a configured serial device is currently present."""
    if not port or port == "auto":
        return False
    return any(str(item.get("device")) == port for item in list_serial_ports())


def _port_priority(device: str) -> tuple[int, str]:
    if platform.system() == "Darwin":
        for index, prefix in enumerate(MAC_PREFERRED_PREFIXES):
            if device.startswith(prefix):
                return (index, device)
        if device.startswith("/dev/cu."):
            return (20, device)
        if device.startswith("/dev/tty."):
            return (50, device)
    return (100, device)


def _is_preferred_port(device: str) -> bool:
    if platform.system() != "Darwin":
        return True
    if any(name in device for name in MAC_LOW_CONFIDENCE_NAMES):
        return False
    return any(device.startswith(prefix) for prefix in MAC_PREFERRED_PREFIXES)


def list_serial_ports() -> list[dict[str, Any]]:
    ports = []
    seen = set()
    for item in list_ports.comports():
        seen.add(item.device)
        ports.append(
            {
                "device": item.device,
                "name": item.name,
                "description": item.description,
                "hwid": item.hwid,
                "vid": item.vid,
                "pid": item.pid,
                "serial_number": item.serial_number,
                "manufacturer": item.manufacturer,
                "product": item.product,
                "interface": item.interface,
                "preferred": _is_preferred_port(item.device),
            }
        )

    if platform.system() == "Darwin":
        patterns = ["/dev/cu.usbserial*", "/dev/cu.SLAB_USBtoUART*", "/dev/cu.wchusbserial*", "/dev/cu.usbmodem*", "/dev/tty.*"]
        for pattern in patterns:
            for match in glob.glob(pattern):
                if match not in seen and Path(match).exists():
                    ports.append(
                        {
                            "device": match,
                            "name": Path(match).name,
                            "description": "Serial device",
                            "hwid": None,
                            "vid": None,
                            "pid": None,
                            "serial_number": None,
                            "manufacturer": None,
                            "product": None,
                            "interface": None,
                            "preferred": _is_preferred_port(match),
                        }
                    )
                    seen.add(match)
    return sorted(ports, key=lambda p: _port_priority(p["device"]))


def auto_detect_port() -> str | None:
    ports = list_serial_ports()
    preferred = [p for p in ports if p.get("preferred")]
    if preferred:
        return str(preferred[0]["device"])
    if platform.system() == "Darwin":
        cu_ports = [
            p
            for p in ports
            if str(p.get("device", "")).startswith("/dev/cu.")
            and not any(name in str(p.get("device", "")) for name in MAC_LOW_CONFIDENCE_NAMES)
        ]
        if cu_ports:
            return str(cu_ports[0]["device"])
        return None
    if ports:
        return str(ports[0]["device"])
    return None


def resolve_serial_port(configured: str = "auto", *, allow_fallback: bool = True) -> dict[str, Any]:
    """Resolve a configured serial port into a currently usable device.

    The return payload is intentionally JSON-friendly so CLI and UI callers can
    show whether Enki used the configured port or had to fall back to auto.
    """
    ports = list_serial_ports()
    devices = {str(port.get("device")) for port in ports}

    if configured != "auto" and configured in devices:
        return {
            "ok": True,
            "port": configured,
            "source": "configured",
            "configured_port": configured,
            "fallback_used": False,
            "ports": ports,
            "message": None,
        }

    auto = auto_detect_port()
    if configured == "auto":
        return {
            "ok": auto is not None,
            "port": auto,
            "source": "auto" if auto else None,
            "configured_port": configured,
            "fallback_used": False,
            "ports": ports,
            "message": None if auto else "No serial port found",
        }

    if allow_fallback and auto:
        return {
            "ok": True,
            "port": auto,
            "source": "auto_fallback",
            "configured_port": configured,
            "fallback_used": True,
            "ports": ports,
            "message": f"Configured serial port is not present: {configured}. Using {auto}.",
        }

    return {
        "ok": False,
        "port": None,
        "source": None,
        "configured_port": configured,
        "fallback_used": False,
        "ports": ports,
        "message": f"Configured serial port is not present: {configured}",
    }
