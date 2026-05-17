# ESP32-P4 OV5647 Color JPEG Streaming

Real-time **color JPEG streaming** over USB serial from an OV5647 camera on the **Waveshare ESP32-P4 Nano** (chip rev v1.3).

The full hardware pipeline runs entirely on-chip:

```
OV5647 RAW8 (MIPI CSI-2, 2 lanes)
  → ISP: Bayer GBRG demosaic → RGB565
  → ISP: Color Correction Matrix (CCM)
  → ISP: Gamma correction (γ = 0.55)
  → CPU: 4× downsample → 200×320 RGB565
  → JPEG hardware encoder → ~3 KB / frame
  → Base64 → USB JTAG serial
  → Python / Web UI: ~2 fps live stream
```

---

## Hardware

| Component | Details |
|-----------|---------|
| Board | Waveshare ESP32-P4 Nano (chip rev **v1.3**) |
| Camera | OV5647 module (Waveshare ESP32-P4 camera board) |
| MCLK | On-board 24 MHz crystal on camera board — `xclk_pin = -1` |
| I2C | SDA = GPIO 7, SCL = GPIO 8 |
| MIPI | 2 lanes, 200 Mbps/lane |
| MIPI PHY LDO | Channel 3, 2500 mV |

---

## Performance

| Metric | Value |
|--------|-------|
| Sensor capture rate | ~15.4 fps (800×1280 RAW8) |
| JPEG sent every | 3 valid frames |
| JPEG resolution | **200×320 RGB565** |
| JPEG size | ~2–4 KB (quality 45) |
| USB JTAG throughput | ~9 KB/s (macOS, USB FS CDC) |
| **End-to-end stream fps** | **~2 fps** (USB bottleneck) |

---

## Quick Start

```bash
git clone https://github.com/efloresaraya/esp32p4-ov5647-color-stream.git
cd esp32p4-ov5647-color-stream
chmod +x setup.sh && ./setup.sh
```

`setup.sh` installs ESP-IDF v6.1, the Enki web UI and all Python dependencies automatically.

Start every new terminal session with:

```bash
source .venv/bin/activate
source esp-idf/export.sh
```

---

## Prerequisites

- Waveshare ESP32-P4 Nano board with OV5647 camera module
- macOS or Linux
- Python 3.9+ — [python.org](https://www.python.org/downloads/)
- Git

Everything else (ESP-IDF, toolchains, Python packages) is installed by `setup.sh`.

---

## Build & Flash

```bash
# Inside the cloned directory, after running setup.sh and activating the session:
idf.py build
idf.py -p /dev/cu.usbmodemXXXX flash
```

Replace `/dev/cu.usbmodemXXXX` with your actual USB JTAG port (`ls /dev/cu.usbmodem*`).

---

## Viewing the Stream

### Option A — Enki Web UI (installed by setup.sh)

```bash
enki ui
open http://127.0.0.1:8765
```

In the browser, click **Start Camera**, enter your serial port, and hit Start.

### Option B — Standalone Python viewer

```python
import serial, base64, io
from PIL import Image

ser = serial.Serial('/dev/cu.usbmodemXXXX', 115200, timeout=2)
buf = bytearray()
while True:
    buf.extend(ser.read(8192))
    while b'\n' in buf:
        line, buf = buf.split(b'\n', 1)
        if line.startswith(b'ENKIB64:'):
            _, size, b64 = line.split(b':', 2)
            img = Image.open(io.BytesIO(base64.b64decode(b64)))
            img.show()
```

---

## Serial Protocol

All lines end with `\n`. The `\n` triggers `usb_serial_jtag_ll_txfifo_flush()` automatically — no `fflush()` or `fsync()` needed.

| Line prefix | Frequency | Content |
|-------------|-----------|---------|
| `ENKIB64:<size>:<base64>` | Every 3 valid frames | Full color JPEG, base64-encoded |
| `ENKIMETRIC:total=N:valid=V:rx=R:fps=F:ms=T` | Every 3 frames | Capture metrics |
| `ENKIMETRIC:jpeg:frame=N:sent=S:encoded=E:send=D:eoi=X` | Each JPEG | JPEG metadata |
| `ENKIMETRIC:skip:expected=E:got=G` | Invalid frame | Wrong DMA size |
| `ENKIMETRIC:timeout:...` | After 5 s silence | No DMA frame received |
| `ENKISTAT:frame=N:format=rgb565:size=S:...` | Every 30 frames | Full pixel stats |
| `ENKIID:...` | Boot | OV5647 I2C register readback |

---

## Tunable Constants

In `main/main.c`:

```c
#define CAM_HRES              800    // sensor capture resolution
#define CAM_VRES              1280
#define PREVIEW_HRES          200    // JPEG output resolution
#define PREVIEW_VRES          320
#define JPEG_QUALITY          45     // JPEG quality (1–100)
#define JPEG_EVERY_N_FRAMES   3      // send JPEG every N valid frames
#define METRIC_EVERY_N_FRAMES 3      // print ENKIMETRIC every N frames
#define PIXEL_STATS_EVERY_N   30     // full pixel scan every N valid frames
#define CAM_WARMUP_MS         1500   // sensor stabilization delay (ms)
#define CAM_LANE_BITRATE_MBPS 200    // MIPI CSI-2 lane bitrate
```

**To increase fps**: reduce `JPEG_EVERY_N_FRAMES` or lower `JPEG_QUALITY` (smaller JPEG → faster USB transfer).

---

## ISP Color Pipeline

### Bayer Order

The OV5647 native Bayer pattern is **GBRG** — not BGGR as the datasheet suggests for default readout. The IDF driver crops the frame in a way that shifts the Bayer phase. This was confirmed by the official Espressif `esp_cam_sensor` driver (fixed in v0.7.1) and by the `esp-video-components` source:

```c
.bayer_order = COLOR_RAW_ELEMENT_ORDER_GBRG,  // OV5647 on ESP32-P4
```

Using the wrong Bayer order causes severe color artifacts (green or blue cast).

### Color Correction Matrix (CCM)

Applied in ISP hardware after demosaic. Corrects the OV5647 sensor spectral response for indoor lighting:

```c
// Gentle indoor correction: slight R boost, mild B reduction
{ 1.4f, -0.2f, -0.2f },   // R_out
{-0.1f,  1.1f,  0.0f },   // G_out
{ 0.0f, -0.1f,  0.9f },   // B_out
```

### Gamma Correction

Applied in ISP hardware with γ = 0.55 to lift shadows and produce a natural-looking image:

```c
static uint32_t s_gamma_brighten(uint32_t x) {
    return (uint32_t)(powf((float)x / 255.0f, 0.55f) * 255.0f + 0.5f);
}
```

---

## ESP32-P4 v1.3 Quirks

These are hardware/driver bugs specific to **chip rev v1.3**. Document them for your own sanity:

### 1. ISP is mandatory — `set_color_mode_bypass()` is a NO-OP

Without ISP active, the CSI bridge (`MIPI_CSI_BRG_USER_ISP`) never activates and DMA receives no data. **Always initialize ISP before the CSI controller**, even if you don't need color processing.

```c
// Correct order:
esp_isp_new_processor(...);   esp_isp_enable(isp_proc);
// then:
esp_cam_new_csi_ctlr(...);
esp_cam_ctlr_enable(cam);
```

### 2. Demosaic is NOT enabled by default after `esp_isp_enable()`

Calling `esp_isp_enable()` alone does not enable the demosaic submodule. Without it, the ISP outputs flat/striped data. You must call:

```c
esp_isp_demosaic_configure(isp_proc, &demosaic_cfg);
esp_isp_demosaic_enable(isp_proc);
```

### 3. `received_size` is reported in RAW8 bytes regardless of ISP output format

`trans->received_size` always equals `CAM_HRES × CAM_VRES` (RAW8 size), even though the ISP writes RGB565 (2× larger) into the DMA buffer. Use `RAW_FRAME_BYTES` for validation, but sync and process `RGB_FRAME_BYTES`:

```c
if (trans->received_size != RAW_FRAME_BYTES) { /* invalid — skip */ }
esp_cache_msync(frame_buf, RGB_FRAME_BYTES, ESP_CACHE_MSYNC_FLAG_DIR_M2C);
```

### 4. `fflush()` and `fsync()` are NO-OPs for binary data over USB JTAG

`fflush()` does nothing on `_IONBF` streams. `fsync(STDOUT_FILENO)` is unreliable for binary over USB JTAG. The correct approach: end every transmission with `\n`, which triggers `usb_serial_jtag_ll_txfifo_flush()` automatically in the VFS driver.

### 5. USB JTAG VFS `write()` does short writes silently

`write(STDOUT_FILENO, buf, large_size)` may return after writing only one TX FIFO worth of data (64 bytes) without an error. Always loop `write()` until all bytes are consumed, or use `fwrite()` in chunks ≤ 512 bytes per call which is confirmed reliable.

### 6. WBG (White Balance Gain) hardware not available on v1.3

`esp_isp_wbg_configure()` returns `ESP_ERR_NOT_SUPPORTED` on chip revisions below v3.0. Use the CCM matrix for color/white balance correction instead.

---

## Initialization Sequence

```
1.  esp_ldo_acquire_channel(chan=3, 2500 mV)     // MIPI PHY power
2.  heap_caps_aligned_alloc(SPIRAM) × 3          // frame_buf (2MB), preview_buf (128KB), jbuf (500KB)
3.  jpeg_new_encoder_engine()
4.  xSemaphoreCreateBinary()                     // frame-ready signal
5.  esp_isp_new_processor(RAW8→RGB565, 80 MHz)
6.  esp_isp_enable()
7.  esp_isp_demosaic_configure() + esp_isp_demosaic_enable()
8.  esp_isp_ccm_configure() + esp_isp_ccm_enable()
9.  esp_isp_gamma_configure(R/G/B) + esp_isp_gamma_enable()
10. esp_cam_new_csi_ctlr(RAW8 in, RAW8 out, 2 lanes)
11. esp_cam_ctlr_register_event_callbacks(on_get_new_trans, on_trans_finished)
12. esp_cam_ctlr_enable() + esp_cam_ctlr_start()
13. example_sensor_init()                        // I2C + OV5647 registers
14. sccb: stream stop → 0x4800=0x14 → delay 5ms → stream start
15. vTaskDelay(CAM_WARMUP_MS)
16. Loop: xSemaphoreTake → ENKIMETRIC → msync(RGB) → ENKISTAT → JPEG → ENKIB64
```

---

## OV5647 Key Registers

| Register | Value | Description |
|----------|-------|-------------|
| `0x0100` | `0x01` | Streaming on |
| `0x4800` | `0x14` | MIPI continuous clock lane |
| `0x3036` | `0x80` | PLL multiplier |
| `0x3018` | `0x44` | Lane config (2 lanes) |
| `0x503D` | `0x00` | Test pattern off (`0x80` = color bars on) |
| `0x3503` | `0x00` | AEC + AGC automatic |

---

## Component Dependencies

`main/CMakeLists.txt`:
```cmake
REQUIRES esp_hw_support esp_driver_cam sensor_init esp_driver_jpeg
         esp_driver_isp esp_sccb_intf esp_driver_usb_serial_jtag
         esp_driver_uart vfs esp_timer
```

`idf_component.yml`: OV5647 sensor via `espressif/esp_cam_sensor`

- ESP-IDF target: **esp32p4**
- Tested version: **ESP-IDF v6.1**

---

## License

Apache License 2.0 — see [LICENSE](../../LICENSE) for details.
