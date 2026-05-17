"""Shared utilities for Enki."""

from __future__ import annotations

import json
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from rich.console import Console

console = Console()


class EnkiError(Exception):
    """Base exception for expected Enki failures."""

    error_type = "EnkiError"

    def __init__(
        self,
        message: str,
        *,
        command: str | None = None,
        returncode: int | None = None,
        log_file: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.command = command
        self.returncode = returncode
        self.log_file = log_file

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "ok": False,
            "error_type": self.error_type,
            "message": self.message,
        }
        if self.command:
            data["command"] = self.command
        if self.log_file:
            data["log_file"] = str(self.log_file)
        if self.returncode is not None:
            data["returncode"] = self.returncode
        return data


class ConfigError(EnkiError):
    error_type = "ConfigError"


class IdfError(EnkiError):
    error_type = "IdfError"


class BuildError(EnkiError):
    error_type = "BuildError"


class FlashError(EnkiError):
    error_type = "FlashError"


class MonitorError(EnkiError):
    error_type = "MonitorError"


class ProjectError(EnkiError):
    error_type = "ProjectError"


def print_json(data: dict[str, Any] | list[Any]) -> None:
    console.print_json(json.dumps(data, indent=2, default=str))


def python_info() -> dict[str, Any]:
    return {
        "executable": sys.executable,
        "version": platform.python_version(),
        "ok": sys.version_info >= (3, 9),
    }


def command_exists(name: str) -> str | None:
    return shutil.which(name)


def project_root_from(path: Path | None = None) -> Path:
    return (path or Path.cwd()).resolve()
