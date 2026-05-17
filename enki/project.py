"""Project generation from bundled templates."""

from __future__ import annotations

from pathlib import Path
import shutil

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import ValidationError

from enki.config import AppConfig
from enki.sdkconfig_defaults import update_defaults
from enki.utils import ProjectError

TEMPLATE_NAMES = {"blink_c", "blink_cpp", "freertos_two_tasks", "uart_echo", "camera_probe_imx219"}


def templates_dir() -> Path:
    return Path(__file__).resolve().parent / "templates"


def bundled_runtime_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "runtime" / "enki_runtime"


def default_template(lang: str) -> str:
    return "blink_cpp" if lang == "cpp" else "blink_c"


def create_project(name: str, *, target: str, lang: str, template: str | None, output_dir: Path | None = None) -> dict[str, str]:
    selected = template or default_template(lang)
    if selected not in TEMPLATE_NAMES:
        raise ProjectError(f"Unknown template '{selected}'. Available: {', '.join(sorted(TEMPLATE_NAMES))}", command="new")
    if selected == "blink_cpp" and lang != "cpp":
        raise ProjectError("Template blink_cpp requires --lang cpp", command="new")
    if selected != "blink_cpp" and lang == "cpp" and template is not None:
        raise ProjectError(f"Template {selected} is C-only in v1; use --lang c or template blink_cpp", command="new")

    root = (output_dir or Path.cwd()).resolve() / name
    if root.exists():
        raise ProjectError(f"Project directory already exists: {root}", command="new")

    source = templates_dir() / selected
    env = Environment(loader=FileSystemLoader(str(source)), undefined=StrictUndefined, keep_trailing_newline=True)
    entry = "main/main.cpp" if lang == "cpp" else "main/main.c"
    try:
        context = AppConfig(
            project=name,
            target=target,  # type: ignore[arg-type]
            language=lang,  # type: ignore[arg-type]
            template=selected,
            entry=entry,
        ).model_dump()
    except ValidationError as exc:
        raise ProjectError(f"Invalid project options: {exc}", command="new") from exc

    root.mkdir(parents=True)

    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if path.is_dir():
            (root / relative).mkdir(exist_ok=True)
            continue
        destination_name = relative.name.removesuffix(".j2")
        destination = root / relative.parent / destination_name
        template_text = path.read_text(encoding="utf-8")
        rendered = env.from_string(template_text).render(**context)
        destination.write_text(rendered, encoding="utf-8")

    runtime = bundled_runtime_dir()
    if runtime.exists():
        shutil.copytree(runtime, root / "components" / "enki_runtime")

    try:
        rendered_config = AppConfig.model_validate_json((root / "app_config.json").read_text(encoding="utf-8"))
    except (ValidationError, OSError) as exc:
        raise ProjectError(f"Generated app_config.json is invalid: {exc}", command="new") from exc
    update_defaults(root, rendered_config.model_dump())

    return {"project_dir": str(root), "template": selected, "target": target, "language": lang}
