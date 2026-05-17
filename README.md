# Enki ESP Environment

Enki ESP Environment is a minimal, reproducible and agent-friendly command line layer for programming ESP32 devices on macOS.

It does not replace ESP-IDF or FreeRTOS. Enki wraps the normal ESP-IDF workflow behind predictable Python commands for creating projects, checking the environment, detecting serial ports, building, flashing and monitoring firmware.

## Problem

ESP32 development on macOS often means juggling VS Code extensions, ESP-IDF activation, CMake/Ninja output, serial ports, flashing commands and monitor sessions. Enki keeps ESP-IDF underneath, but gives humans and code agents one simple CLI:

```bash
enki doctor
enki boards
enki new blink --target esp32 --lang c
cd blink
enki build
enki flash
enki monitor
```

## Requirements

- macOS recommended for v1.
- Python 3.11 or newer.
- ESP-IDF installed and activated when building/flashing.
- `idf.py` available through `IDF_PATH` or `PATH`.
- CMake, Ninja and the ESP-IDF toolchain from your ESP-IDF installation.

Enki does not install ESP-IDF automatically in v1.
If ESP-IDF lives inside this repository as `esp-idf/`, Enki can detect it, but you should still run `. ./esp-idf/export.sh` in the shell before `enki build`, `enki flash` or `enki run`.

## Installation

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
```

The main command is:

```bash
enki
```

## Basic Usage

Check your environment:

```bash
. ./esp-idf/export.sh
enki doctor
enki doctor --json
```

List serial ports:

```bash
enki boards
enki boards --json
```

Create a project:

```bash
enki new blink --target esp32 --lang c
enki new blink_cpp --target esp32 --lang cpp
enki new tasks --target esp32 --lang c --template freertos_two_tasks
enki new echo --target esp32 --lang c --template uart_echo
```

Build, flash and monitor:

```bash
cd blink
enki status
enki build
enki flash
enki monitor --port auto --baudrate 115200
enki monitor --seconds 10
enki run --monitor-seconds 10
```

Open the local writing environment:

```bash
enki ui
```

Then visit `http://127.0.0.1:8765`. The UI includes local project search/load, a project file tree, Monaco editor, autosave, Save, Save As, board/target/port selection, PSRAM, flash size, partition presets, log level, Doctor, Boards, Build, Flash, Clean and a small serial terminal.

The board presets include an `ESP32-P4 Nano · 32MB PSRAM · 16MB Flash` profile. It writes `esp32p4`, 16 MB flash and ESP32-P4 PSRAM defaults into `app_config.json` and `sdkconfig.defaults`.

Project recognition is intentionally folder-based and portable. An Enki project is any folder with a valid `app_config.json`. A normal ESP-IDF folder with `CMakeLists.txt` and `main/` can be loaded from the UI and adopted by creating that `app_config.json`, so copied folders do not depend on hidden VS Code workspace state.

## ESP32-P4 Camera Example

The `examples/ov5647_color_stream/` project targets **ESP32-P4 Nano** with an OV5647 MIPI CSI-2 camera. It captures RAW8 at 800×1280, runs the full ISP pipeline on-chip (Bayer demosaic → CCM → gamma), downsamples to 200×320 and streams color JPEG frames over USB JTAG serial at ~2 fps.

The Enki UI Camera tab (`/ws/camera`) can render the live stream. Start the UI with `enki ui`, click **Start Camera** and select your serial port.

See [`examples/ov5647_color_stream/README.md`](examples/ov5647_color_stream/README.md) for full hardware pinout, build instructions, serial protocol and ESP32-P4 v1.3 quirks.

Clean:

```bash
enki clean
```

## app_config.json

Each generated project includes an `app_config.json` file validated with Pydantic:

```json
{
  "project": "blink",
  "target": "esp32",
  "language": "c",
  "framework": "esp-idf",
  "rtos": "freertos",
  "serial_port": "auto",
  "baudrate": 115200,
  "template": "blink_c",
  "entry": "main/main.c"
}
```

Agents should prefer `--json` for diagnosis and read `.enki/logs/` after failed build, flash, run or timed monitor commands.

## Templates

v1 includes:

- `blink_c`
- `blink_cpp`
- `freertos_two_tasks`
- `uart_echo`

Generated projects include a local `components/enki_runtime` copy so examples using `#include "enki.h"` can build as regular ESP-IDF projects.

## MATLAB Foundation

The `matlab/` folder contains starter functions for a future JSON Lines serial bridge:

- `enkiConnect.m`
- `enkiPing.m`
- `enkiReadLine.m`
- `enkiWriteJson.m`
- `examples/demo_serial_read.m`

The `uart_echo` template is the v1 firmware base for that direction.

## Roadmap

- v1: CLI, templates, build, flash, monitor and minimal runtime.
- v2: More complete C runtime.
- v3: Camera and serial protocol stabilization for ESP32-P4.
- v4: JSON serial protocol.
- v5: MATLAB integration.
- v6: Local mini IDE or web interface.
- v7: Stronger code-agent integration.
