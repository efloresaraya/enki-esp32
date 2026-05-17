"""Small, human-facing sdkconfig.defaults manager."""

from __future__ import annotations

from pathlib import Path
from typing import Any

FLASH_SIZE_KEYS = {
    "2MB": "CONFIG_ESPTOOLPY_FLASHSIZE_2MB",
    "4MB": "CONFIG_ESPTOOLPY_FLASHSIZE_4MB",
    "8MB": "CONFIG_ESPTOOLPY_FLASHSIZE_8MB",
    "16MB": "CONFIG_ESPTOOLPY_FLASHSIZE_16MB",
}

PARTITION_KEYS = {
    "single_factory": "CONFIG_PARTITION_TABLE_SINGLE_APP",
    "factory_ota": "CONFIG_PARTITION_TABLE_TWO_OTA",
}

LOG_LEVELS = {
    "none": ("CONFIG_LOG_DEFAULT_LEVEL_NONE", 0),
    "error": ("CONFIG_LOG_DEFAULT_LEVEL_ERROR", 1),
    "warn": ("CONFIG_LOG_DEFAULT_LEVEL_WARN", 2),
    "info": ("CONFIG_LOG_DEFAULT_LEVEL_INFO", 3),
    "debug": ("CONFIG_LOG_DEFAULT_LEVEL_DEBUG", 4),
    "verbose": ("CONFIG_LOG_DEFAULT_LEVEL_VERBOSE", 5),
}

MANAGED_KEYS = {
    "CONFIG_SPIRAM",
    "CONFIG_SPIRAM_USE_MALLOC",
    "CONFIG_SPIRAM_MODE_HEX",
    "CONFIG_SPIRAM_MODE_OCT",
    "CONFIG_SPIRAM_SPEED_200M",
    "CONFIG_SPIRAM_SPEED_120M",
    "CONFIG_SPIRAM_SPEED_80M",
    "CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL",
    "CONFIG_PM_SLP_SPIRAM_HALFSLEEP_ENABLED",
    "CONFIG_PARTITION_TABLE_CUSTOM",
    "CONFIG_PARTITION_TABLE_CUSTOM_FILENAME",
    "CONFIG_PARTITION_TABLE_FILENAME",
    "CONFIG_PARTITION_TABLE_OFFSET",
    "CONFIG_LOG_DEFAULT_LEVEL",
    "CONFIG_USJ_ENABLE_USB_SERIAL_JTAG",
    "CONFIG_ETH_ENABLED",
    "CONFIG_ETH_USE_ESP32_EMAC",
    "CONFIG_ETH_USE_SPI_ETHERNET",
    "CONFIG_ESP_HOST_WIFI_ENABLED",
    "CONFIG_ESP_WIFI_REMOTE_ENABLED",
    # ESP32-P4 chip revision (pre-v3 vs v3+)
    "CONFIG_ESP32P4_SELECTS_REV_LESS_V3",
    "CONFIG_ESP32P4_REV_MIN_0",
    "CONFIG_ESP32P4_REV_MIN_1",
    "CONFIG_ESP32P4_REV_MIN_100",
    "CONFIG_ESP32P4_REV_MIN_300",
    "CONFIG_ESP32P4_REV_MIN_301",
    *FLASH_SIZE_KEYS.values(),
    *PARTITION_KEYS.values(),
    *(level[0] for level in LOG_LEVELS.values()),
}


def defaults_path(project_dir: Path) -> Path:
    return project_dir / "sdkconfig.defaults"


def parse_defaults(project_dir: Path) -> dict[str, str]:
    path = defaults_path(project_dir)
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("# CONFIG_") and line.endswith(" is not set"):
            values[line.removeprefix("# ").removesuffix(" is not set")] = "n"
            continue
        if line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def read_effective_settings(project_dir: Path, config: Any) -> dict[str, Any]:
    values = parse_defaults(project_dir)
    flash_size = next((name for name, key in FLASH_SIZE_KEYS.items() if values.get(key) == "y"), getattr(config, "flash_size", "4MB"))
    partition_scheme = next((name for name, key in PARTITION_KEYS.items() if values.get(key) == "y"), getattr(config, "partition_scheme", "single_factory"))
    log_level = next((name for name, (key, _) in LOG_LEVELS.items() if values.get(key) == "y"), getattr(config, "log_level", "info"))
    return {
        "psram": values.get("CONFIG_SPIRAM") == "y" or bool(getattr(config, "psram", False)),
        "flash_size": flash_size,
        "partition_scheme": partition_scheme,
        "log_level": log_level,
        "features": dict(getattr(config, "features", {}) or {}),
        "raw": values,
    }


def update_defaults(project_dir: Path, settings: dict[str, Any]) -> Path:
    path = defaults_path(project_dir)
    existing_lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
    kept = []
    for line in existing_lines:
        stripped = line.strip()
        if stripped == "# Managed by Enki ESP Environment":
            continue
        key = stripped.split("=", 1)[0] if "=" in stripped else None
        if stripped.startswith("# CONFIG_") and stripped.endswith(" is not set"):
            key = stripped.removeprefix("# ").removesuffix(" is not set")
        if key and key in MANAGED_KEYS:
            continue
        kept.append(line)

    generated = ["", "# Managed by Enki ESP Environment"]
    if bool(settings.get("psram", False)):
        generated.extend([
            "CONFIG_SPIRAM=y",
            "CONFIG_SPIRAM_USE_MALLOC=y",
        ])
        if str(settings.get("target")) == "esp32p4":
            generated.extend([
                "CONFIG_SPIRAM_MODE_HEX=y",
                "CONFIG_SPIRAM_SPEED_200M=y",
                "CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=0",
                "CONFIG_PM_SLP_SPIRAM_HALFSLEEP_ENABLED=n",
            ])

    # ESP32-P4 Nano (rev 1.x) requires pre-v3 chip revision support.
    # Pre-v3 and v3+ P4 revisions are not ABI-compatible; the Nano board
    # uses rev 1.x hardware so we always select the pre-v3 revision range.
    if str(settings.get("target")) == "esp32p4":
        generated.extend([
            "CONFIG_ESP32P4_SELECTS_REV_LESS_V3=y",
            "CONFIG_ESP32P4_REV_MIN_100=y",
        ])

    flash_size = str(settings.get("flash_size") or "4MB")
    for name, key in FLASH_SIZE_KEYS.items():
        if name == flash_size:
            generated.append(f"{key}=y")

    partition_scheme = str(settings.get("partition_scheme") or "single_factory")
    for name, key in PARTITION_KEYS.items():
        if name == partition_scheme:
            generated.append(f"{key}=y")
    if partition_scheme == "factory_ota":
        generated.extend([
            'CONFIG_PARTITION_TABLE_FILENAME="partitions_two_ota.csv"',
            "CONFIG_PARTITION_TABLE_OFFSET=0x8000",
        ])

    log_level = str(settings.get("log_level") or "info")
    level_key, level_value = LOG_LEVELS.get(log_level, LOG_LEVELS["info"])
    generated.extend([
        f"{level_key}=y",
        f"CONFIG_LOG_DEFAULT_LEVEL={level_value}",
    ])

    features = settings.get("features") or {}
    if bool(features.get("usb_serial_jtag", True)):
        generated.append("CONFIG_USJ_ENABLE_USB_SERIAL_JTAG=y")
    else:
        generated.append("# CONFIG_USJ_ENABLE_USB_SERIAL_JTAG is not set")

    if bool(features.get("ethernet", False)):
        generated.extend([
            "CONFIG_ETH_ENABLED=y",
            "CONFIG_ETH_USE_ESP32_EMAC=y",
        ])
    else:
        generated.extend([
            "# CONFIG_ETH_ENABLED is not set",
            "# CONFIG_ETH_USE_ESP32_EMAC is not set",
            "# CONFIG_ETH_USE_SPI_ETHERNET is not set",
        ])

    if bool(features.get("wifi_remote", False)):
        generated.extend([
            "CONFIG_ESP_HOST_WIFI_ENABLED=y",
            "CONFIG_ESP_WIFI_REMOTE_ENABLED=y",
        ])
    else:
        generated.append("# CONFIG_ESP_HOST_WIFI_ENABLED is not set")

    content = "\n".join([*kept, *generated]).strip() + "\n"
    path.write_text(content, encoding="utf-8")
    return path
