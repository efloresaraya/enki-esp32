"""Local web UI for Enki."""

from __future__ import annotations

import asyncio
import base64
import json
import struct
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import serial

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from enki.board_detect import list_serial_ports, resolve_serial_port
from enki.config import AppConfig, load_config, write_config
from enki.doctor import run_doctor
from enki.idf_wrapper import find_idf
from enki.sdkconfig_defaults import read_effective_settings, update_defaults
from enki.utils import EnkiError

STATIC_DIR = Path(__file__).resolve().parent / "ui_static"

SKIP_DIRS = {
    ".git",
    ".venv",
    ".venv311",
    "__pycache__",
    ".pytest_cache",
    "build",
    "esp-idf",
    ".enki",
    "enki",
    "enki_esp.egg-info",
    "runtime",
}
TEXT_SUFFIXES = {
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cc",
    ".cxx",
    ".json",
    ".txt",
    ".md",
    ".cmake",
    ".yml",
    ".yaml",
    ".ini",
    ".cfg",
    ".csv",
    ".m",
    ".py",
}
REGISTRY_FILE = ".enki/ui_projects.json"

BOARD_PROFILES: list[dict[str, Any]] = [
    {
        "id": "generic_esp32",
        "name": "ESP32 DevKit / generica",
        "target": "esp32",
        "psram": False,
        "flash_size": "4MB",
        "notes": "Perfil seguro para DevKitC, NodeMCU-32S y placas ESP32-WROOM comunes.",
        "features": ["wifi_native", "bluetooth_native", "usb_serial_jtag"],
    },
    {
        "id": "esp32_wrover",
        "name": "ESP32-WROVER / PSRAM",
        "target": "esp32",
        "psram": True,
        "flash_size": "4MB",
        "notes": "Activa PSRAM para placas WROVER o modulos con RAM externa.",
        "features": ["wifi_native", "bluetooth_native", "usb_serial_jtag"],
    },
    {
        "id": "esp32s3_devkit",
        "name": "ESP32-S3 DevKit",
        "target": "esp32s3",
        "psram": False,
        "flash_size": "8MB",
        "notes": "Base para ESP32-S3 sin asumir PSRAM.",
        "features": ["wifi_native", "bluetooth_native", "usb_serial_jtag", "usb_otg"],
    },
    {
        "id": "esp32s3_psram",
        "name": "ESP32-S3 con PSRAM",
        "target": "esp32s3",
        "psram": True,
        "flash_size": "8MB",
        "notes": "Para S3 con RAM externa. Verifica el tipo exacto de PSRAM si el build avisa.",
        "features": ["wifi_native", "bluetooth_native", "usb_serial_jtag", "usb_otg"],
    },
    {
        "id": "esp32c3_devkit",
        "name": "ESP32-C3 DevKit",
        "target": "esp32c3",
        "psram": False,
        "flash_size": "4MB",
        "notes": "RISC-V compacto, sin PSRAM en la mayoria de placas comunes.",
        "features": ["wifi_native", "bluetooth_le_native", "usb_serial_jtag"],
    },
    {
        "id": "esp32c6_devkit",
        "name": "ESP32-C6 DevKit",
        "target": "esp32c6",
        "psram": False,
        "flash_size": "4MB",
        "notes": "Perfil base para ESP32-C6.",
        "features": ["wifi_native", "bluetooth_le_native", "usb_serial_jtag"],
    },
    {
        "id": "esp32p4_nano_32r16f",
        "name": "ESP32-P4 Nano · 32MB PSRAM · 16MB Flash",
        "target": "esp32p4",
        "psram": True,
        "flash_size": "16MB",
        "notes": "Perfil para ESP32-P4 Nano con PSRAM HEX y flash grande. Recomendado para proyectos con buffers, camara, audio o UI.",
        "features": [
            "usb_serial_jtag",
            "usb_otg",
            "ethernet",
            "wifi_remote",
            "camera_mipi",
            "display_mipi",
            "sdmmc",
            "i2s_audio",
        ],
    },
]

FEATURE_CATALOG: list[dict[str, Any]] = [
    {"id": "usb_serial_jtag", "name": "USB Serial/JTAG", "kind": "integrated", "sdkconfig": True, "default": True, "notes": "Consola y programación por USB cuando la placa lo expone."},
    {"id": "usb_otg", "name": "USB 2.0 OTG", "kind": "integrated", "sdkconfig": False, "default": False, "notes": "Disponible en ESP32-P4; requiere código/controlador USB en firmware."},
    {"id": "ethernet", "name": "Ethernet MAC", "kind": "integrated_needs_phy", "sdkconfig": True, "default": False, "notes": "El MAC existe en ESP32-P4; necesitas PHY, pines y configuración de board."},
    {"id": "wifi_native", "name": "WiFi nativo", "kind": "integrated", "sdkconfig": False, "default": False, "notes": "Disponible en ESP32 clásicos/S3/C3/C6, no en ESP32-P4."},
    {"id": "wifi_remote", "name": "WiFi remoto/hosted", "kind": "external", "sdkconfig": True, "default": False, "notes": "ESP32-P4 puede usar WiFi mediante coprocesador/módulo externo."},
    {"id": "bluetooth_native", "name": "Bluetooth nativo", "kind": "integrated", "sdkconfig": False, "default": False, "notes": "No nativo en ESP32-P4."},
    {"id": "bluetooth_le_native", "name": "Bluetooth LE nativo", "kind": "integrated", "sdkconfig": False, "default": False, "notes": "No nativo en ESP32-P4."},
    {"id": "bluetooth_external", "name": "Bluetooth externo", "kind": "external", "sdkconfig": False, "default": False, "notes": "Reservado para integración con módulo externo."},
    {"id": "camera_mipi", "name": "Cámara / MIPI CSI", "kind": "integrated", "sdkconfig": False, "default": False, "notes": "Disponible en ESP32-P4; requiere pines, sensor y driver."},
    {"id": "display_mipi", "name": "Display / MIPI DSI", "kind": "integrated", "sdkconfig": False, "default": False, "notes": "Disponible en ESP32-P4; requiere panel y driver."},
    {"id": "sdmmc", "name": "SD/MMC", "kind": "integrated", "sdkconfig": False, "default": False, "notes": "Host SD/MMC disponible; requiere pines y código."},
    {"id": "i2s_audio", "name": "I2S audio", "kind": "integrated", "sdkconfig": False, "default": False, "notes": "I2S/LP-I2S disponibles; requiere codec o micrófono."},
]


_FRAME_MAGIC = b"\xbe\xef\xca\xfe"
_B64_PREFIX = b"ENKIB64:"


class CameraStream:
    """Reads camera frames from a serial port and fans them out to WebSocket clients.

    Supported formats:
    - [0xBE 0xEF 0xCA 0xFE][uint32_le size][JPEG bytes]
    - ENKIB64:<jpeg_size>:<base64-jpeg>\n
    """

    def __init__(self, port: str, baudrate: int) -> None:
        self.port = port
        self.baudrate = baudrate
        self._lock = threading.Lock()
        self._queues: list[asyncio.Queue] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def add_client(self, q: asyncio.Queue, loop: asyncio.AbstractEventLoop) -> None:
        with self._lock:
            self._queues.append(q)
            if self._loop is None:
                self._loop = loop
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._read_loop, daemon=True)
                self._thread.start()

    def remove_client(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._queues = [x for x in self._queues if x is not q]
            if not self._queues:
                self._stop.set()

    def _push(self, item: str | None) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        with self._lock:
            for q in self._queues:
                try:
                    loop.call_soon_threadsafe(q.put_nowait, item)
                except Exception:
                    pass

    def _read_loop(self) -> None:
        try:
            ser = serial.Serial(self.port, self.baudrate, timeout=0.5)
        except serial.SerialException:
            self._push(None)
            return

        # Line-based parser: handles ENKIB64 frames and ENKIMETRIC/ENKISTAT metrics.
        # Reads in large chunks (8 KB) for efficiency at 115200 baud.
        buf = bytearray()
        try:
            while not self._stop.is_set():
                chunk = ser.read(8192)
                if not chunk:
                    continue
                buf.extend(chunk)

                # Drain all complete newline-terminated lines from the buffer.
                while True:
                    nl = buf.find(b"\n")
                    if nl < 0:
                        # Incomplete line; guard against buffer explosion.
                        if len(buf) > 512 * 1024:
                            buf.clear()
                        break

                    line = bytes(buf[:nl]).strip()
                    del buf[:nl + 1]

                    if not line:
                        continue

                    if line.startswith(_B64_PREFIX):
                        # ENKIB64:<size>:<base64-jpeg>
                        parts = line.split(b":", 2)
                        if len(parts) == 3:
                            try:
                                expected = int(parts[1])
                                jpeg = base64.b64decode(parts[2], validate=True)
                                if expected == len(jpeg):
                                    # Push raw base64 string; WS endpoint wraps it.
                                    self._push(parts[2].decode("ascii"))
                            except Exception:
                                pass

                    elif line.startswith(b"ENKIMETRIC:") or line.startswith(b"ENKISTAT:"):
                        # Forward metric lines to all WebSocket clients as structured JSON.
                        try:
                            self._push(
                                '{"type":"metric","line":'
                                + json.dumps(line.decode("ascii", errors="replace"))
                                + "}"
                            )
                        except Exception:
                            pass

                    elif line.startswith(_FRAME_MAGIC) and len(line) >= 8:
                        # Legacy binary frame: [MAGIC 4B][size 4B LE][jpeg ...]
                        size = struct.unpack("<I", line[4:8])[0]
                        if 0 < size <= 500 * 1024:
                            payload = line[8:]
                            while len(payload) < size and not self._stop.is_set():
                                more = ser.read(size - len(payload))
                                if more:
                                    payload += more
                            if len(payload) == size:
                                self._push(base64.b64encode(payload).decode())

        except Exception:
            self._push(None)
        finally:
            try:
                ser.close()
            except Exception:
                pass


def create_app(workspace: Path | None = None) -> FastAPI:
    root = (workspace or Path.cwd()).resolve()
    home = Path.home().resolve()
    temp_root = Path("/private/tmp").resolve()
    app = FastAPI(title="Enki ESP UI")
    camera_streams: dict[str, CameraStream] = {}
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def is_under(path: Path, base: Path) -> bool:
        return path == base or base in path.parents

    def safe_path(raw_path: str = "") -> Path:
        raw_path = raw_path or "."
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve()
        if not (is_under(candidate, root) or is_under(candidate, home) or is_under(candidate, temp_root)):
            raise HTTPException(400, "Path outside allowed local roots")
        return candidate

    def project_dir(project: str) -> Path:
        path = safe_path(project)
        if not (path / "app_config.json").exists():
            raise HTTPException(404, "Enki project not found")
        return path

    def registry_path() -> Path:
        return root / REGISTRY_FILE

    def load_registry() -> list[str]:
        path = registry_path()
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return [str(item) for item in data.get("projects", []) if isinstance(item, str)]

    def save_registry(paths: list[Path]) -> None:
        unique = sorted({str(path.resolve()) for path in paths})
        path = registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"projects": unique}, indent=2) + "\n", encoding="utf-8")

    def register_project(path: Path) -> None:
        existing = [safe_path(item) for item in load_registry()]
        existing.append(path.resolve())
        save_registry(existing)

    def has_esp_idf_shape(path: Path) -> bool:
        return (
            (path / "CMakeLists.txt").is_file()
            and (path / "main" / "CMakeLists.txt").is_file()
            and any((path / "main" / name).is_file() for name in ("main.c", "main.cpp", "app_main.c", "app_main.cpp"))
        )

    def infer_entry(path: Path) -> tuple[str, str]:
        for name, language in (
            ("main/main.c", "c"),
            ("main/app_main.c", "c"),
            ("main/main.cpp", "cpp"),
            ("main/app_main.cpp", "cpp"),
        ):
            if (path / name).is_file():
                return name, language
        return "main/main.c", "c"

    def project_info(path: Path) -> dict[str, Any] | None:
        path = path.resolve()
        cfg = path / "app_config.json"
        if cfg.exists():
            try:
                config = load_config(path)
                return {
                    "name": config.project or path.name,
                    "folder_name": path.name,
                    "path": "." if path == root else str(path.relative_to(root)) if is_under(path, root) else str(path),
                    "absolute_path": str(path),
                    "target": config.target,
                    "language": config.language,
                    "template": config.template,
                    "entry": config.entry,
                    "kind": "enki",
                    "portable": True,
                    "importable": False,
                }
            except EnkiError:
                return {
                    "name": path.name,
                    "folder_name": path.name,
                    "path": "." if path == root else str(path.relative_to(root)) if is_under(path, root) else str(path),
                    "absolute_path": str(path),
                    "target": None,
                    "language": None,
                    "template": None,
                    "entry": None,
                    "kind": "invalid_enki",
                    "portable": False,
                    "importable": False,
                }
        if has_esp_idf_shape(path):
            entry, language = infer_entry(path)
            return {
                "name": path.name,
                "folder_name": path.name,
                "path": "." if path == root else str(path.relative_to(root)) if is_under(path, root) else str(path),
                "absolute_path": str(path),
                "target": None,
                "language": language,
                "template": "imported_esp_idf",
                "entry": entry,
                "kind": "esp_idf",
                "portable": False,
                "importable": True,
            }
        return None

    def write_import_config(path: Path, target: str = "esp32") -> AppConfig:
        entry, language = infer_entry(path)
        config = AppConfig(
            project=path.name,
            target=target,
            language=language,  # type: ignore[arg-type]
            template="imported_esp_idf",
            entry=entry,
        )
        write_config(config, path / "app_config.json")
        return config

    def discover_projects(base: Path, query: str = "", max_depth: int = 4) -> list[dict[str, Any]]:
        query = query.lower().strip()
        results: list[dict[str, Any]] = []
        stack: list[tuple[Path, int]] = [(base, 0)]
        seen: set[Path] = set()
        while stack and len(results) < 80:
            folder, depth = stack.pop()
            try:
                folder = folder.resolve()
            except OSError:
                continue
            if folder in seen:
                continue
            seen.add(folder)
            if folder.name in SKIP_DIRS or (folder.name.startswith(".") and folder != base):
                continue

            info = project_info(folder)
            if info and (not query or query in folder.name.lower() or query in str(folder).lower()):
                results.append(info)
                continue

            if depth >= max_depth:
                continue
            try:
                children = [item for item in folder.iterdir() if item.is_dir()]
            except OSError:
                continue
            for child in sorted(children, key=lambda p: p.name.lower(), reverse=True):
                if child.name not in SKIP_DIRS:
                    stack.append((child, depth + 1))
        return sorted(results, key=lambda item: (item["kind"] != "enki", item["name"].lower()))

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/workspace")
    async def workspace_info() -> dict[str, Any]:
        return {
            "root": str(root),
            "home": str(home),
            "idf": find_idf().to_dict(),
        }

    @app.get("/api/board-profiles")
    async def board_profiles() -> dict[str, Any]:
        return {
            "ok": True,
            "profiles": BOARD_PROFILES,
            "features": FEATURE_CATALOG,
            "targets": ["esp32", "esp32s2", "esp32s3", "esp32c3", "esp32c6", "esp32h2", "esp32p4"],
            "flash_sizes": ["2MB", "4MB", "8MB", "16MB"],
            "partition_schemes": [
                {"id": "single_factory", "name": "Single factory app"},
                {"id": "factory_ota", "name": "Factory + OTA slots"},
            ],
            "log_levels": ["none", "error", "warn", "info", "debug", "verbose"],
        }

    @app.get("/api/projects")
    async def projects() -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        candidates = [root]
        candidates.extend(safe_path(item) for item in load_registry())
        candidates.extend(p for p in root.iterdir() if p.is_dir() and p.name not in SKIP_DIRS and not p.name.startswith("."))
        seen: set[Path] = set()
        for path in candidates:
            path = path.resolve()
            if path in seen:
                continue
            seen.add(path)
            info = project_info(path)
            if info and info["kind"] in {"enki", "invalid_enki"}:
                result.append(info)
        return sorted(result, key=lambda item: item["name"].lower())

    @app.get("/api/projects/discover")
    async def discover(base: str = "", q: str = "", depth: int = 4) -> dict[str, Any]:
        base_path = safe_path(base or str(root))
        if not base_path.is_dir():
            raise HTTPException(404, "Search folder not found")
        return {
            "ok": True,
            "base": str(base_path),
            "projects": discover_projects(base_path, q, max(0, min(depth, 7))),
        }

    @app.post("/api/projects/load")
    async def load_or_adopt_project(data: dict[str, Any]) -> dict[str, Any]:
        path = safe_path(str(data.get("path", "")).strip())
        if not path.is_dir():
            raise HTTPException(404, "Project folder not found")

        info = project_info(path)
        if not info:
            raise HTTPException(400, "Folder is not an Enki or ESP-IDF project")

        if info["kind"] == "esp_idf":
            if not bool(data.get("adopt", False)):
                return {
                    "ok": False,
                    "needs_adoption": True,
                    "message": "ESP-IDF folder found. Create app_config.json to make it portable in Enki.",
                    "project": info,
                }
            config = write_import_config(path, str(data.get("target") or "esp32"))
            register_project(path)
            return {
                "ok": True,
                "adopted": True,
                "project": project_info(path),
                "config": config.model_dump(),
            }

        if info["kind"] == "invalid_enki":
            raise HTTPException(400, "app_config.json exists but is invalid")

        register_project(path)
        return {"ok": True, "adopted": False, "project": info}

    @app.get("/api/projects/{project:path}/status")
    async def status(project: str) -> dict[str, Any]:
        path = project_dir(project)
        config = load_config(path)
        return {
            "ok": True,
            "project": config.project,
            "target": config.target,
            "language": config.language,
            "serial_port": config.serial_port,
            "baudrate": config.baudrate,
            "template": config.template,
            "entry": config.entry,
            "project_dir": str(path),
            "idf_available": find_idf().available,
            "serial_port_available": resolve_serial_port(config.serial_port, allow_fallback=False)["ok"] if config.serial_port != "auto" else None,
            "resolved_auto_port": resolve_serial_port("auto").get("port"),
        }

    @app.get("/api/projects/{project:path}/settings")
    async def get_settings(project: str) -> dict[str, Any]:
        path = project_dir(project)
        config = load_config(path)
        effective = read_effective_settings(path, config)
        return {
            "ok": True,
            "config": config.model_dump(),
            "sdkconfig_defaults": effective,
            "defaults_file": str(path / "sdkconfig.defaults"),
        }

    @app.put("/api/projects/{project:path}/settings")
    async def save_settings(project: str, data: dict[str, Any]) -> dict[str, Any]:
        path = project_dir(project)
        current = load_config(path).model_dump()
        allowed = {
            "target",
            "serial_port",
            "baudrate",
            "board_profile",
            "psram",
            "flash_size",
            "partition_scheme",
            "log_level",
            "features",
        }
        merged = {**current, **{key: value for key, value in data.items() if key in allowed}}

        profile_id = str(merged.get("board_profile") or "")
        profile = next((item for item in BOARD_PROFILES if item["id"] == profile_id), None)
        if profile and bool(data.get("apply_profile_defaults", False)):
            merged["target"] = profile["target"]
            merged["psram"] = profile["psram"]
            merged["flash_size"] = profile["flash_size"]

        config = AppConfig.model_validate(merged)
        write_config(config, path / "app_config.json")
        defaults_file = update_defaults(path, config.model_dump())
        return {
            "ok": True,
            "config": config.model_dump(),
            "defaults_file": str(defaults_file),
            "message": "Settings saved. Next build will apply target and sdkconfig.defaults.",
        }

    @app.get("/api/projects/{project:path}/tree")
    async def tree(project: str, path: str = "") -> list[dict[str, Any]]:
        base = project_dir(project)
        folder = (base / path).resolve() if path else base
        if folder != base and base not in folder.parents:
            raise HTTPException(400, "Path outside project")
        if not folder.is_dir():
            raise HTTPException(404, "Folder not found")

        entries: list[dict[str, Any]] = []
        for item in sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if item.name.startswith(".") or item.name in SKIP_DIRS or item.name.endswith(".old"):
                continue
            rel = str(item.relative_to(base))
            if item.is_dir():
                entries.append({"type": "dir", "name": item.name, "path": rel})
            elif item.is_file():
                entries.append(
                    {
                        "type": "file",
                        "name": item.name,
                        "path": rel,
                        "size": item.stat().st_size,
                        "editable": item.suffix.lower() in TEXT_SUFFIXES or item.name in {"CMakeLists.txt", "app_config.json"},
                    }
                )
        return entries

    @app.get("/api/projects/{project:path}/files/{file_path:path}")
    async def read_file(project: str, file_path: str) -> dict[str, Any]:
        base = project_dir(project)
        target = (base / file_path).resolve()
        if target != base and base not in target.parents:
            raise HTTPException(400, "Path outside project")
        if not target.is_file():
            raise HTTPException(404, "File not found")
        if target.suffix.lower() not in TEXT_SUFFIXES and target.name not in {"CMakeLists.txt", "app_config.json"}:
            raise HTTPException(415, "File is not editable as text")
        return {"path": file_path, "content": target.read_text(encoding="utf-8", errors="replace")}

    @app.put("/api/projects/{project:path}/files/{file_path:path}")
    async def write_file(project: str, file_path: str, data: dict[str, Any]) -> dict[str, Any]:
        base = project_dir(project)
        target = (base / file_path).resolve()
        if target != base and base not in target.parents:
            raise HTTPException(400, "Path outside project")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(data.get("content", "")), encoding="utf-8")
        return {"ok": True, "path": file_path}

    @post_save_as(app, project_dir)
    def _save_as(project: str, data: dict[str, Any]) -> dict[str, Any]:
        base = project_dir(project)
        target_name = str(data.get("target_path", "")).strip()
        if not target_name:
            raise HTTPException(400, "target_path required")
        target = (base / target_name).resolve()
        if target != base and base not in target.parents:
            raise HTTPException(400, "Path outside project")
        if target.exists() and not bool(data.get("overwrite", False)):
            raise HTTPException(409, "Target file already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(data.get("content", "")), encoding="utf-8")
        return {"ok": True, "path": str(target.relative_to(base))}

    @app.get("/api/doctor")
    async def doctor() -> dict[str, Any]:
        return run_doctor(root)

    @app.get("/api/boards")
    async def boards() -> dict[str, Any]:
        ports = list_serial_ports()
        recommended = next((p for p in ports if p.get("preferred")), None)
        return {"ok": True, "ports": ports, "count": len(ports), "recommended_port": recommended}

    @app.post("/api/projects/{project:path}/command/{command}")
    async def command(
        project: str,
        command: str,
        data: dict[str, Any] = Body(default={}),
    ) -> dict[str, Any]:
        if command not in {"status", "build", "flash", "clean"}:
            raise HTTPException(400, "Unsupported command")
        path = project_dir(project)
        if command == "status":
            config = load_config(path)
            return {"ok": True, "project": config.model_dump(), "idf": find_idf().to_dict()}

        cmd = [sys.executable, "-m", "enki.cli", command, "--json"]
        if command == "flash":
            port = str(data.get("port") or "").strip()
            if port and port != "auto":
                cmd += ["--port", port]

        proc = await asyncio.to_thread(
            subprocess.run,
            cmd,
            cwd=path,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            parsed = {"ok": proc.returncode == 0, "stdout": proc.stdout, "stderr": proc.stderr}
        parsed["returncode"] = proc.returncode
        if proc.stderr:
            parsed["stderr"] = proc.stderr
        return parsed

    @app.websocket("/ws/serial")
    async def serial_terminal(websocket: WebSocket) -> None:
        await websocket.accept()
        port = websocket.query_params.get("port", "auto")
        baudrate = int(websocket.query_params.get("baudrate", "115200"))
        resolved_info = resolve_serial_port(port)
        if not resolved_info["ok"] or not resolved_info["port"]:
            await websocket.send_json({"type": "error", "message": resolved_info.get("message") or "No serial port found"})
            await websocket.close()
            return
        port = str(resolved_info["port"])

        try:
            ser = serial.Serial(port, baudrate=baudrate, timeout=0.1)
        except serial.SerialException as exc:
            await websocket.send_json({"type": "error", "message": str(exc)})
            await websocket.close()
            return

        await websocket.send_json({"type": "open", "port": port, "baudrate": baudrate, "port_resolution": {key: value for key, value in resolved_info.items() if key != "ports"}})

        async def reader() -> None:
            try:
                while True:
                    raw = await asyncio.to_thread(ser.readline)
                    if raw:
                        await websocket.send_json(
                            {
                                "type": "data",
                                "text": raw.decode("utf-8", errors="replace"),
                            }
                        )
            except Exception:
                pass

        task = asyncio.create_task(reader())
        try:
            while True:
                message = await websocket.receive_json()
                if message.get("type") == "send":
                    text = str(message.get("text", ""))
                    if not text.endswith("\n"):
                        text += "\n"
                    await asyncio.to_thread(ser.write, text.encode("utf-8"))
                elif message.get("type") == "close":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            task.cancel()
            await asyncio.to_thread(ser.close)

    @app.websocket("/ws/camera")
    async def camera_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        port = websocket.query_params.get("port", "auto")
        baudrate_str = websocket.query_params.get("baudrate", "115200")
        try:
            baudrate = int(baudrate_str)
        except ValueError:
            baudrate = 115200

        resolved = resolve_serial_port(port)
        if not resolved["ok"] or not resolved["port"]:
            await websocket.send_json({"type": "error", "message": resolved.get("message") or "No serial port found"})
            await websocket.close()
            return
        port = str(resolved["port"])

        stream_key = f"{port}:{baudrate}"
        if stream_key not in camera_streams:
            camera_streams[stream_key] = CameraStream(port, baudrate)
        stream = camera_streams[stream_key]

        q: asyncio.Queue = asyncio.Queue(maxsize=16)
        loop = asyncio.get_event_loop()
        stream.add_client(q, loop)
        await websocket.send_json({"type": "open", "port": port, "baudrate": baudrate})

        try:
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    await websocket.send_json({"type": "keepalive"})
                    continue

                if item is None:
                    await websocket.send_json({"type": "error", "message": "Serial stream ended or port unavailable"})
                    break

                if item.startswith("{"):
                    # Pre-formatted JSON (metric or other structured message).
                    await websocket.send_text(item)
                else:
                    # Raw base64 frame data.
                    await websocket.send_text(json.dumps({"type": "frame", "data": item}))
        except WebSocketDisconnect:
            pass
        finally:
            stream.remove_client(q)

    return app


def post_save_as(app: FastAPI, _project_dir):
    """Small decorator helper to keep the save-as route visually named."""

    def decorator(func):
        app.post("/api/projects/{project:path}/save-as")(func)
        return func

    return decorator


def serve_ui(host: str = "127.0.0.1", port: int = 8765, workspace: Path | None = None) -> None:
    import uvicorn

    uvicorn.run(create_app(workspace), host=host, port=port, log_level="info")
