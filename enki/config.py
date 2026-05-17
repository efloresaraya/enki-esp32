"""Project configuration validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from enki.utils import ConfigError


Language = Literal["c", "cpp"]
Target = Literal["esp32", "esp32s2", "esp32s3", "esp32c3", "esp32c6", "esp32h2", "esp32p4"]


class AppConfig(BaseModel):
    project: str
    target: Target = "esp32"
    language: Language = "c"
    framework: Literal["esp-idf"] = "esp-idf"
    rtos: Literal["freertos"] = "freertos"
    serial_port: str = "auto"
    baudrate: int = Field(default=115200, gt=0)
    template: str
    entry: str
    board_profile: str = "generic_esp32"
    psram: bool = False
    flash_size: str = "4MB"
    partition_scheme: str = "single_factory"
    log_level: str = "info"
    features: dict[str, bool] = Field(default_factory=dict)


def config_path(project_dir: Path | None = None) -> Path:
    return (project_dir or Path.cwd()).resolve() / "app_config.json"


def load_config(project_dir: Path | None = None) -> AppConfig:
    path = config_path(project_dir)
    if not path.exists():
        raise ConfigError(f"Missing app_config.json in {path.parent}", command="config")
    try:
        return AppConfig.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise ConfigError(f"Invalid app_config.json: {exc}", command="config") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in app_config.json: {exc}", command="config") from exc


def write_config(config: AppConfig, path: Path) -> None:
    path.write_text(config.model_dump_json(indent=2) + "\n", encoding="utf-8")
