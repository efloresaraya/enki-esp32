"""Log file helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def log_dir(project_dir: Path | None = None) -> Path:
    path = (project_dir or Path.cwd()).resolve() / ".enki" / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_log_file(prefix: str, project_dir: Path | None = None) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return log_dir(project_dir) / f"{prefix}_{stamp}.log"


def write_log(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", errors="replace")
    return path
