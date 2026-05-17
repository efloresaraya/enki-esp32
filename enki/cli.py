"""Typer command line interface for Enki ESP Environment."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.table import Table

from enki.board_detect import list_serial_ports, resolve_serial_port
from enki.config import load_config
from enki.doctor import run_doctor
from enki.idf_wrapper import build as idf_build
from enki.idf_wrapper import clean as idf_clean
from enki.idf_wrapper import configured_target
from enki.idf_wrapper import find_idf, flash as idf_flash
from enki.idf_wrapper import set_target as idf_set_target
from enki.project import create_project, default_template
from enki.serial_monitor import monitor_serial
from enki.ui import serve_ui
from enki.utils import EnkiError, console, print_json

app = typer.Typer(help="Enki ESP Environment: minimal ESP32 workflow on top of ESP-IDF.")


def _handle_error(exc: Exception, json_output: bool) -> None:
    if isinstance(exc, EnkiError):
        if json_output:
            print_json(exc.to_json())
        else:
            console.print(f"[red]Error:[/red] {exc.message}")
            if exc.log_file:
                console.print(f"[dim]Log:[/dim] {exc.log_file}")
        raise typer.Exit(code=1)
    raise exc


@app.command()
def doctor(json_output: bool = typer.Option(False, "--json", help="Print structured JSON output.")) -> None:
    """Check Python, ESP-IDF, build tools and serial ports."""
    result = run_doctor(Path.cwd().resolve())
    if json_output:
        print_json(result)
        raise typer.Exit(code=0 if result["ok"] else 1)
    checks = result["checks"]
    table = Table(title="Enki doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    table.add_row("Python", "OK" if checks["python"]["ok"] else "FAIL", checks["python"]["version"])
    table.add_row("ESP-IDF", "OK" if checks["esp_idf"]["available"] else "MISSING", checks["esp_idf"].get("idf_py") or checks["esp_idf"].get("message") or "")
    table.add_row("CMake", "OK" if checks["cmake"]["ok"] else "MISSING", checks["cmake"].get("version") or "")
    table.add_row("Ninja", "OK" if checks["ninja"]["ok"] else "MISSING", checks["ninja"].get("version") or "")
    if checks.get("project"):
        project = checks["project"]
        table.add_row("Project target", str(project.get("target")), str(project.get("board_profile") or ""))
        required = checks["toolchains"].get("required_for_target", {})
        table.add_row("Target toolchain", "OK" if required.get("ok") else "MISSING", ", ".join(required.get("candidates") or []))
    else:
        table.add_row("RISC-V toolchain", "OK" if checks["toolchains"]["riscv32"]["ok"] else "MISSING", checks["toolchains"]["riscv32"].get("version") or "")
    if checks.get("configured_port"):
        port = checks["configured_port"]
        table.add_row("Configured port", "OK" if port.get("ok") else "MISSING", port.get("configured") or "")
    table.add_row("Serial ports", str(len(checks["ports"])), ", ".join(p["device"] for p in checks["ports"]) or "none")
    console.print(table)
    raise typer.Exit(code=0 if result["ok"] else 1)


@app.command()
def boards(json_output: bool = typer.Option(False, "--json", help="Print structured JSON output.")) -> None:
    """List available serial ports."""
    ports = list_serial_ports()
    result = {"ok": True, "ports": ports, "count": len(ports)}
    if json_output:
        print_json(result)
        return
    table = Table(title="Serial boards/ports")
    table.add_column("Port")
    table.add_column("Description")
    table.add_column("VID:PID")
    table.add_column("Preferred")
    for port in ports:
        vidpid = f"{port['vid']:04x}:{port['pid']:04x}" if port.get("vid") is not None and port.get("pid") is not None else ""
        table.add_row(port["device"], port.get("description") or "", vidpid, "yes" if port.get("preferred") else "")
    console.print(table)


@app.command()
def new(
    name: str,
    target: str = typer.Option("esp32", "--target", help="ESP-IDF target."),
    lang: str = typer.Option("c", "--lang", help="Language: c or cpp."),
    template: Optional[str] = typer.Option(None, "--template", help="Template name."),
    json_output: bool = typer.Option(False, "--json", help="Print structured JSON output."),
) -> None:
    """Create a new ESP-IDF compatible project."""
    try:
        result = create_project(name, target=target, lang=lang, template=template)
        payload = {"ok": True, **result}
        if json_output:
            print_json(payload)
        else:
            console.print(f"[green]Created[/green] {result['project_dir']} using {result['template']}")
    except Exception as exc:
        _handle_error(exc, json_output)


@app.command()
def status(json_output: bool = typer.Option(False, "--json", help="Print structured JSON output.")) -> None:
    """Show current Enki project status."""
    try:
        project_dir = Path.cwd().resolve()
        config = load_config(project_dir)
        idf = find_idf()
        payload = {
            "ok": True,
            "project": config.project,
            "target": config.target,
            "language": config.language,
            "serial_port": config.serial_port,
            "serial_port_available": any(str(port.get("device")) == config.serial_port for port in list_serial_ports()) if config.serial_port != "auto" else None,
            "resolved_auto_port": resolve_serial_port("auto").get("port"),
            "baudrate": config.baudrate,
            "template": config.template,
            "entry": config.entry,
            "project_dir": str(project_dir),
            "idf_available": idf.available,
            "last_error": None,
            "paths": {
                "main_dir": (project_dir / "main").exists(),
                "root_cmake": (project_dir / "CMakeLists.txt").exists(),
                "main_cmake": (project_dir / "main" / "CMakeLists.txt").exists(),
                "build_dir": (project_dir / "build").exists(),
            },
        }
        if json_output:
            print_json(payload)
            return
        table = Table(title=f"Enki status: {config.project}")
        table.add_column("Field")
        table.add_column("Value")
        for key in ["target", "language", "serial_port", "baudrate", "template", "entry"]:
            table.add_row(key, str(payload[key]))
        if config.serial_port != "auto":
            table.add_row("serial_port_available", "yes" if payload["serial_port_available"] else "no")
        table.add_row("resolved_auto_port", str(payload["resolved_auto_port"] or "none"))
        table.add_row("project_dir", str(project_dir))
        table.add_row("idf_available", "yes" if idf.available else "no")
        console.print(table)
    except Exception as exc:
        _handle_error(exc, json_output)


@app.command()
def build(json_output: bool = typer.Option(False, "--json", help="Print structured JSON output.")) -> None:
    """Run idf.py build."""
    try:
        config = load_config()
        project_dir = Path.cwd().resolve()
        current_target = configured_target(project_dir)
        if current_target != config.target and (current_target is not None or config.target != "esp32"):
            idf_set_target(project_dir, config.target)
        result = idf_build(project_dir)
        payload = {"ok": True, "command": "build", "log_file": result["log_file"], "returncode": result["returncode"]}
        if json_output:
            print_json(payload)
        else:
            console.print("[green]Build OK[/green]")
            console.print(f"[dim]Log:[/dim] {result['log_file']}")
    except Exception as exc:
        _handle_error(exc, json_output)


@app.command()
def flash(
    port: Optional[str] = typer.Option(None, "--port", "-p", help="Serial port or auto."),
    json_output: bool = typer.Option(False, "--json", help="Print structured JSON output."),
) -> None:
    """Run idf.py flash. Corrects the IDF target if sdkconfig does not match app_config.json."""
    try:
        config = load_config()
        project_dir = Path.cwd().resolve()
        current_target = configured_target(project_dir)
        if current_target != config.target and (current_target is not None or config.target != "esp32"):
            idf_set_target(project_dir, config.target)
        selected = config.serial_port if port is None else port
        resolved_info = resolve_serial_port(selected)
        if not resolved_info["ok"] or not resolved_info["port"]:
            raise EnkiError(resolved_info.get("message") or "No serial port found for flash", command="flash")
        resolved = str(resolved_info["port"])
        result = idf_flash(project_dir, resolved)
        payload = {
            "ok": True,
            "command": "flash",
            "port": resolved,
            "port_resolution": {key: value for key, value in resolved_info.items() if key != "ports"},
            "log_file": result["log_file"],
            "returncode": result["returncode"],
        }
        if json_output:
            print_json(payload)
        else:
            if resolved_info.get("message"):
                console.print(f"[yellow]{resolved_info['message']}[/yellow]")
            console.print(f"[green]Flash OK[/green] on {resolved}")
            console.print(f"[dim]Log:[/dim] {result['log_file']}")
    except Exception as exc:
        _handle_error(exc, json_output)


@app.command()
def monitor(
    port: Optional[str] = typer.Option(None, "--port", "-p", help="Serial port or auto."),
    baudrate: Optional[int] = typer.Option(None, "--baudrate", "-b", help="Serial baudrate."),
    seconds: Optional[float] = typer.Option(None, "--seconds", help="Stop after N seconds."),
    json_output: bool = typer.Option(False, "--json", help="Print structured JSON output."),
) -> None:
    """Open a basic pyserial monitor."""
    try:
        config = load_config()
        selected_port = config.serial_port if port is None else port
        resolved_info = resolve_serial_port(selected_port)
        if not resolved_info["ok"] or not resolved_info["port"]:
            raise EnkiError(resolved_info.get("message") or "No serial port found for monitor", command="monitor")
        selected_baudrate = baudrate or config.baudrate
        result = monitor_serial(port=str(resolved_info["port"]), baudrate=selected_baudrate, seconds=seconds, as_json=json_output, project_dir=Path.cwd().resolve())
        result["port_resolution"] = {key: value for key, value in resolved_info.items() if key != "ports"}
        if json_output:
            print_json(result)
        elif resolved_info.get("message"):
            console.print(f"[yellow]{resolved_info['message']}[/yellow]")
    except Exception as exc:
        _handle_error(exc, json_output)


@app.command()
def run(
    port: Optional[str] = typer.Option(None, "--port", "-p", help="Serial port or auto."),
    monitor_seconds: Optional[float] = typer.Option(None, "--monitor-seconds", help="Stop monitor after N seconds."),
    json_output: bool = typer.Option(False, "--json", help="Print structured JSON output."),
) -> None:
    """Build, flash and monitor. Corrects the IDF target if sdkconfig does not match app_config.json."""
    try:
        config = load_config()
        project_dir = Path.cwd().resolve()
        current_target = configured_target(project_dir)
        if current_target != config.target and (current_target is not None or config.target != "esp32"):
            idf_set_target(project_dir, config.target)
        build_result = idf_build(project_dir)
        selected = config.serial_port if port is None else port
        resolved_info = resolve_serial_port(selected)
        if not resolved_info["ok"] or not resolved_info["port"]:
            raise EnkiError(resolved_info.get("message") or "No serial port found for run", command="run")
        resolved = str(resolved_info["port"])
        flash_result = idf_flash(project_dir, resolved)
        monitor_result = monitor_serial(port=resolved, baudrate=config.baudrate, seconds=monitor_seconds, as_json=json_output, project_dir=project_dir)
        payload = {
            "ok": True,
            "command": "run",
            "port": resolved,
            "port_resolution": {key: value for key, value in resolved_info.items() if key != "ports"},
            "build_log_file": build_result["log_file"],
            "flash_log_file": flash_result["log_file"],
            "monitor_log_file": monitor_result.get("log_file"),
        }
        if json_output:
            print_json(payload)
        else:
            if resolved_info.get("message"):
                console.print(f"[yellow]{resolved_info['message']}[/yellow]")
            console.print(f"[green]Run OK[/green] on {resolved}")
    except Exception as exc:
        _handle_error(exc, json_output)


@app.command()
def clean(
    full: bool = typer.Option(True, "--full/--build-only", help="Use idf.py fullclean or clean."),
    json_output: bool = typer.Option(False, "--json", help="Print structured JSON output."),
) -> None:
    """Clean ESP-IDF build outputs."""
    try:
        load_config()
        result = idf_clean(Path.cwd().resolve(), full=full)
        payload = {"ok": True, "command": "clean", "log_file": result["log_file"], "returncode": result["returncode"]}
        if json_output:
            print_json(payload)
        else:
            console.print("[green]Clean OK[/green]")
            console.print(f"[dim]Log:[/dim] {result['log_file']}")
    except Exception as exc:
        _handle_error(exc, json_output)


@app.command()
def ui(
    host: str = typer.Option("127.0.0.1", "--host", help="Host for the local web UI."),
    port: int = typer.Option(8765, "--port", help="Port for the local web UI."),
    workspace: Optional[Path] = typer.Option(None, "--workspace", help="Workspace root to open."),
) -> None:
    """Start the local Enki writing environment."""
    target = (workspace or Path.cwd()).resolve()
    console.print(f"[green]Enki UI[/green] http://{host}:{port}")
    console.print(f"[dim]Workspace:[/dim] {target}")
    serve_ui(host=host, port=port, workspace=target)


@app.callback()
def main() -> None:
    """Minimal, reproducible ESP32 workflow for humans and code agents."""


if __name__ == "__main__":
    app()
