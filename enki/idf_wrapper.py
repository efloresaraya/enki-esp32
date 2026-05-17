"""ESP-IDF discovery and subprocess wrapper."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from enki.logs import new_log_file, write_log
from enki.utils import BuildError, FlashError, IdfError, command_exists


@dataclass(frozen=True)
class IdfInfo:
    available: bool
    idf_py: str | None
    idf_path: str | None
    source: str | None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "idf_py": self.idf_py,
            "idf_path": self.idf_path,
            "source": self.source,
            "message": self.message,
        }


def find_idf() -> IdfInfo:
    idf_path = os.environ.get("IDF_PATH")
    if idf_path:
        candidate = Path(idf_path) / "tools" / "idf.py"
        if candidate.exists():
            return IdfInfo(True, str(candidate), idf_path, "IDF_PATH")

    in_path = command_exists("idf.py")
    if in_path:
        return IdfInfo(True, in_path, idf_path, "PATH")

    for base in [Path.cwd().resolve(), *Path.cwd().resolve().parents]:
        candidate_root = base / "esp-idf"
        candidate = candidate_root / "tools" / "idf.py"
        if candidate.exists():
            return IdfInfo(
                True,
                str(candidate),
                str(candidate_root),
                "workspace",
                "Found local esp-idf directory. Run '. ./esp-idf/export.sh' before building/flashing so tools are on PATH.",
            )

    return IdfInfo(
        False,
        None,
        idf_path,
        None,
        "ESP-IDF not found. Activate/export ESP-IDF so IDF_PATH is set or idf.py is available in PATH.",
    )


def require_idf(command: str = "idf") -> IdfInfo:
    info = find_idf()
    if not info.available or not info.idf_py:
        raise IdfError(info.message or "ESP-IDF not found", command=command)
    return info


def _existing_tool_dirs() -> list[str]:
    """Find ESP-IDF tool directories installed under ~/.espressif.

    This is a conservative fallback for agent/GUI runs where ESP-IDF exists but
    export.sh has not been sourced in the current shell.
    """
    roots = [Path.home() / ".espressif"]
    dirs: list[Path] = []
    for root in roots:
        try:
            if not root.exists():
                continue
            dirs.extend(path for path in (root / "python_env").glob("*/bin") if path.is_dir())
        except PermissionError:
            continue
        tools = root / "tools"
        if tools.is_dir():
            dirs.extend(path for path in tools.glob("cmake/*/CMake.app/Contents/bin") if path.is_dir())
            dirs.extend(path for path in tools.glob("ninja/*") if path.is_dir())
            dirs.extend(path for path in tools.glob("*/**/bin") if path.is_dir())

    unique: list[str] = []
    seen: set[str] = set()
    for path in dirs:
        value = str(path)
        if value not in seen:
            unique.append(value)
            seen.add(value)
    return unique


def _prepare_idf_env(info: IdfInfo) -> dict[str, str]:
    env = os.environ.copy()
    if info.idf_path:
        env["IDF_PATH"] = info.idf_path

    path_dirs = env.get("PATH", "").split(":") if env.get("PATH") else []
    preferred = _existing_tool_dirs()
    idf_py_dirs = [p for p in path_dirs if ".espressif" in p and "python_env" in p]
    front = preferred + idf_py_dirs
    seen: set[str] = set()
    ordered = []
    for item in [*front, *path_dirs]:
        if item and item not in seen:
            ordered.append(item)
            seen.add(item)
    env["PATH"] = ":".join(ordered)

    python_dirs = [item for item in ordered if ".espressif" in item and "python_env" in item]
    if python_dirs:
        python_env = str(Path(python_dirs[0]).parent)
        env["VIRTUAL_ENV"] = python_env
        env.setdefault("IDF_PYTHON_ENV_PATH", python_env)
    if info.idf_path and "ESP_IDF_VERSION" not in env:
        try:
            proc = subprocess.run(
                ["git", "-C", info.idf_path, "describe", "--tags", "--dirty", "--always"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
            version = proc.stdout.strip()
            if proc.returncode == 0 and version:
                env["ESP_IDF_VERSION"] = version[1:] if version.startswith("v") else version
        except Exception:
            pass
    if "ESP_ROM_ELF_DIR" not in env:
        rom_elf_dir = Path.home() / ".espressif" / "tools" / "esp-rom-elfs"
        try:
            if rom_elf_dir.is_dir():
                env["ESP_ROM_ELF_DIR"] = str(rom_elf_dir)
        except PermissionError:
            pass
    return env


def run_idf(args: list[str], *, project_dir: Path, log_prefix: str) -> dict[str, Any]:
    info = require_idf(log_prefix)
    log_file = new_log_file(log_prefix, project_dir)
    command = [info.idf_py or "idf.py", *args]
    env = _prepare_idf_env(info)
    proc = subprocess.run(
        command,
        cwd=project_dir,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    combined = (
        f"$ {' '.join(command)}\n"
        f"cwd: {project_dir}\n"
        f"returncode: {proc.returncode}\n\n"
        f"--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}\n"
    )
    write_log(log_file, combined)
    result = {
        "ok": proc.returncode == 0,
        "command": " ".join(command),
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "log_file": str(log_file),
    }
    if proc.returncode != 0:
        command_name = next((a for a in args if not a.startswith("-") and not a.startswith("/")), args[-1] if args else "idf")
        if "flash" in args:
            raise FlashError("idf.py flash failed", command="flash", returncode=proc.returncode, log_file=log_file)
        if "build" in args:
            raise BuildError("idf.py build failed", command="build", returncode=proc.returncode, log_file=log_file)
        raise IdfError(f"idf.py {command_name} failed", command=command_name, returncode=proc.returncode, log_file=log_file)
    return result


def build(project_dir: Path) -> dict[str, Any]:
    return run_idf(["build"], project_dir=project_dir, log_prefix="build")


def flash(project_dir: Path, port: str) -> dict[str, Any]:
    return run_idf(["-p", port, "flash"], project_dir=project_dir, log_prefix="flash")


def clean(project_dir: Path, full: bool = True) -> dict[str, Any]:
    return run_idf(["fullclean" if full else "clean"], project_dir=project_dir, log_prefix="clean")


def set_target(project_dir: Path, target: str) -> dict[str, Any]:
    # idf.py set-target depends on fullclean, which refuses to run if the build dir
    # exists but has no CMakeCache.txt (e.g. after a fullclean that left an empty dir).
    # Delete the directory manually in that case so set-target can proceed.
    import shutil
    build_dir = project_dir / "build"
    if build_dir.is_dir() and not (build_dir / "CMakeCache.txt").is_file():
        shutil.rmtree(build_dir, ignore_errors=True)
    return run_idf(["set-target", target], project_dir=project_dir, log_prefix="set_target")


def configured_target(project_dir: Path) -> str | None:
    sdkconfig = project_dir / "sdkconfig"
    if not sdkconfig.exists():
        return None
    for line in sdkconfig.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("CONFIG_IDF_TARGET="):
            return line.split("=", 1)[1].strip().strip('"')
    return None
