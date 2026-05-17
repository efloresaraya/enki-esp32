"""Environment checks for Enki."""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path
from typing import Any

from enki.board_detect import list_serial_ports
from enki.config import load_config
from enki.idf_wrapper import find_idf
from enki.utils import command_exists, python_info


TOOLCHAINS = {
    "esp32": ["xtensa-esp32-elf-gcc", "xtensa-esp-elf-gcc"],
    "esp32s2": ["xtensa-esp32s2-elf-gcc", "xtensa-esp-elf-gcc"],
    "esp32s3": ["xtensa-esp32s3-elf-gcc", "xtensa-esp-elf-gcc"],
    "esp32c3": ["riscv32-esp-elf-gcc"],
    "esp32c6": ["riscv32-esp-elf-gcc"],
    "esp32h2": ["riscv32-esp-elf-gcc"],
    "esp32p4": ["riscv32-esp-elf-gcc"],
}


def _tool_roots() -> list[Path]:
    roots = []
    for candidate in (Path.home() / ".espressif" / "tools",):
        if candidate.exists():
            roots.append(candidate)
    return roots


def _find_bundled_tool(name: str) -> str | None:
    for root in _tool_roots():
        patterns = []
        if name == "cmake":
            patterns.append("cmake/*/CMake.app/Contents/bin/cmake")
            patterns.append("cmake/*/bin/cmake")
        elif name == "ninja":
            patterns.append("ninja/*/ninja")
        else:
            patterns.append(f"**/{name}")
        for pattern in patterns:
            matches = sorted(path for path in root.glob(pattern) if path.is_file())
            if matches:
                return str(matches[-1])
    return None


def _tool_path(name: str) -> str | None:
    return command_exists(name) or _find_bundled_tool(name)


def _version_command(command: list[str]) -> dict[str, Any]:
    executable = _tool_path(command[0])
    if not executable:
        return {"ok": False, "path": None, "version": None}
    try:
        proc = subprocess.run([executable, *command[1:]], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=8)
        first_line = (proc.stdout or "").strip().splitlines()[0] if proc.stdout else None
        return {"ok": proc.returncode == 0, "path": executable, "version": first_line}
    except Exception as exc:
        return {"ok": False, "path": executable, "version": None, "error": str(exc)}


def _project_context(project_dir: Path | None = None) -> dict[str, Any] | None:
    try:
        config = load_config(project_dir)
    except Exception:
        return None
    return {
        "project": config.project,
        "target": config.target,
        "serial_port": config.serial_port,
        "baudrate": config.baudrate,
        "board_profile": config.board_profile,
    }


def _toolchain_checks(target: str | None) -> dict[str, Any]:
    checks = {
        "xtensa_esp32": _version_command(["xtensa-esp32-elf-gcc", "--version"]),
        "xtensa_esp32s2_s3": _version_command(["xtensa-esp32s3-elf-gcc", "--version"]),
        "riscv32": _version_command(["riscv32-esp-elf-gcc", "--version"]),
    }
    if target:
        candidates = TOOLCHAINS.get(target, [])
        checks["required_for_target"] = {
            "target": target,
            "candidates": candidates,
            "ok": any(_version_command([candidate, "--version"])["ok"] for candidate in candidates),
        }
    return checks


def _port_check(configured_port: str | None, ports: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not configured_port:
        return None
    devices = {str(port.get("device")) for port in ports}
    if configured_port == "auto":
        recommended = next((str(port.get("device")) for port in ports if port.get("preferred")), None)
        return {"configured": "auto", "ok": recommended is not None, "resolved": recommended, "message": None if recommended else "No preferred USB serial port detected"}
    ok = configured_port in devices
    return {
        "configured": configured_port,
        "ok": ok,
        "resolved": configured_port if ok else None,
        "message": None if ok else "Configured serial port is not currently present",
    }


def run_doctor(project_dir: Path | None = None) -> dict[str, Any]:
    idf = find_idf()
    project = _project_context(project_dir)
    ports = list_serial_ports()
    target = project.get("target") if project else None
    checks = {
        "python": python_info(),
        "system": {
            "os": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "macos": platform.mac_ver()[0] if platform.system() == "Darwin" else None,
        },
        "esp_idf": idf.to_dict(),
        "idf_py": {"ok": bool(idf.available), "path": idf.idf_py},
        "cmake": _version_command(["cmake", "--version"]),
        "ninja": _version_command(["ninja", "--version"]),
        "toolchains": _toolchain_checks(str(target) if target else None),
        "ports": ports,
    }
    if project:
        checks["project"] = project
        checks["configured_port"] = _port_check(str(project.get("serial_port")), ports)
    required = [
        checks["python"]["ok"],
        checks["esp_idf"]["available"],
        checks["cmake"]["ok"],
        checks["ninja"]["ok"],
    ]
    if target:
        required.append(bool(checks["toolchains"].get("required_for_target", {}).get("ok")))
    return {"ok": all(required), "checks": checks}
