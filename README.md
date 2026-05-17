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

## Quick Start

```bash
git clone https://github.com/efloresaraya/enki-esp32.git
cd enki-esp32
chmod +x setup.sh && ./setup.sh
```

`setup.sh` installs Enki into a local `.venv` and, if ESP-IDF is not already present, clones and installs **ESP-IDF v6.1** with the ESP32-P4 toolchain (downloads ~1–2 GB).

Start every new terminal session with:

```bash
source .venv/bin/activate
source esp-idf/export.sh
```

Then verify the environment:

```bash
enki doctor
```

## Requirements

- macOS (recommended for v1)
- Python 3.9 or newer — [python.org](https://www.python.org/downloads/)
- Git
- ESP-IDF v6.1 — installed automatically by `setup.sh`, or manually from [docs.espressif.com](https://docs.espressif.com/projects/esp-idf/en/v6.1/esp32p4/get-started/index.html)

## Basic Usage

Check your environment:

```bash
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

## OV5647 UVC Webcam (ESP32-P4 Nano)

The `ov5647_uvc_webcam/` project turns an **ESP32-P4 Nano + OV5647** camera module into a plug-and-play USB UVC webcam — no drivers needed on macOS, Linux or Windows.

**Hardware**: ESP32-P4 Nano · OV5647 MIPI CSI-2 · USB-A OTG HS port  
**Output**: 640×480 MJPEG @ 15 fps over USB UVC (isochronous)  
**Sensor mode**: RAW10 1280×960 2:1 binning — covers ~99 % of the full 2592×1944 sensor area for maximum field of view  
**Pipeline**: MIPI RAW10 → ISP (Bayer GBRG demosaic + CCM + γ0.55) → RGB565 → 2× CPU downscale → HW JPEG encoder → TinyUSB UVC  

```bash
cd ov5647_uvc_webcam
idf.py build
idf.py -p /dev/cu.usbmodem* flash
python3 view_webcam.py          # live preview (OpenCV)
```

The device appears as **"OV5647 UVC Webcam"** (VID 0x303A / PID 0x8000) in any UVC-compatible application (QuickTime, OBS, Zoom, etc.).

Key ESP32-P4 v1.3 quirks documented and worked around:
- TinyUSB PR #1820 `wLength` fix for macOS UVC probe/commit
- ISP demosaic must be explicitly enabled after `esp_isp_enable()`
- DMA `received_size` reported in MIPI input bytes, not RGB565 bytes
- ISOC transfer required (BULK causes AVFoundation C++ exception on macOS)
- Ping-pong DMA buffers prevent frame tearing during CPU downscale

See [`ov5647_uvc_webcam/sdkconfig.defaults`](ov5647_uvc_webcam/sdkconfig.defaults) for all Kconfig settings.

---

## ESP32-P4 Camera Serial Example

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
