/*
 * camera_sensor_stack_probe_RGB -- main.c
 *
 * OV5647 MIPI CSI-2 RAW8 -> RGB565 color JPEG -> USB-serial
 * Target: Waveshare ESP32-P4 Nano (chip rev v1.3)  SDA=GPIO7  SCL=GPIO8
 *
 * Pipeline:
 *   CSI PHY -> CSI bridge RAW8 DMA -> ISP demosaic BGGR->RGB565 -> PSRAM
 *   -> 2x downsample RGB565 -> JPEG color encode -> USB serial
 *
 * WHY v5 BLOCKED ON SECOND FRAME:
 *   When on_get_new_trans is registered, csi_dma_trans_done_callback gets the
 *   next buffer from that callback and NEVER reads from trans_que (line 392 in
 *   esp_cam_ctlr_csi.c is inside an else-branch).  esp_cam_ctlr_receive() only
 *   does xQueueSend(trans_que, MAX_DELAY) -- returns after enqueue, not after DMA
 *   completes.  First call fills the 1-item queue and returns.  Second call blocks
 *   forever because the queue is never drained.
 *
 * FIX: remove esp_cam_ctlr_receive() entirely.  Signal the main task via a binary
 * semaphore from on_trans_finished (called from DMA ISR each time a user-buffer
 * frame completes, because user_buf != backup_buf is always TRUE).
 *
 * MCLK: Waveshare ESP32-P4 Nano has a 24 MHz crystal on the camera interface
 * board driving pin 11 (XVCLK) directly.  xclk_pin = -1 is correct.
 *
 * Serial frame format:
 *   ENKIB64:<jpeg_size>:<base64-jpeg>\n
 */

#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include "esp_err.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "esp_ldo_regulator.h"
#include "esp_cam_ctlr.h"
#include "esp_cam_ctlr_csi.h"
#include "esp_cam_sensor.h"
#include "esp_sccb_intf.h"
#include "driver/i2c_master.h"
#include "esp_private/esp_cache_private.h"
#include "esp_cache.h"
#include <math.h>
#include "driver/isp.h"
#include "driver/isp_demosaic.h"
#include "driver/isp_ccm.h"
#include "driver/isp_gamma.h"
#include "driver/jpeg_encode.h"
#include "driver/uart_vfs.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_timer.h"
#include "example_sensor_init.h"

static const char *TAG = "cam_stream";

/* -- Hardware constants ---------------------------------------------------- */
#define MIPI_PHY_LDO_CHAN_ID      3
#define MIPI_PHY_LDO_VOLTAGE_MV   2500
#define CAM_SDA_IO                7
#define CAM_SCL_IO                8
#define CAM_HRES                  800
#define CAM_VRES                  1280
#define PREVIEW_HRES              200
#define PREVIEW_VRES              320
#define CAM_LANE_BITRATE_MBPS     200  /* Matches ESP-IDF OV5647 RAW8 example timing */

#define FB_HRES                   CAM_HRES
#define FB_VRES                   CAM_VRES
#define FB_BPP                    16           /* RGB565 */
#define RAW_FRAME_BYTES           (CAM_HRES * CAM_VRES)
#define RGB_FRAME_BYTES           (CAM_HRES * CAM_VRES * 2)
#define PREVIEW_BPP               2                                  /* RGB565 */
#define PREVIEW_FRAME_BYTES       (PREVIEW_HRES * PREVIEW_VRES * PREVIEW_BPP)

/* -- Tuning constants ------------------------------------------------------ */
#define CAM_WARMUP_MS             1500
#define CAM_BLACK_FRAME_LIMIT     3
#define JPEG_QUALITY              45
#define JPEG_OUT_BUF_SIZE         (500 * 1024)
#define FRAME_TIMEOUT_MS          5000
#define PIXEL_STATS_EVERY_N       30
#define JPEG_EVERY_N_FRAMES       3
#define METRIC_EVERY_N_FRAMES     3   /* print ENKIMETRIC every N total frames */

/* -- ISR / semaphore state ------------------------------------------------- */
static SemaphoreHandle_t     s_frame_sem;
static esp_cam_ctlr_trans_t  s_finished_trans;
static esp_cam_ctlr_trans_t  s_cam_trans;  /* always re-armed by on_get_new_trans */
static volatile uint32_t     s_new_trans_count  = 0;  /* diagnostic: on_get_new_trans fires */
static volatile uint32_t     s_trans_done_count = 0;  /* diagnostic: on_trans_finished fires */

static bool IRAM_ATTR s_on_get_new_trans(esp_cam_ctlr_handle_t handle,
                                         esp_cam_ctlr_trans_t *trans,
                                         void *user_data)
{
    s_new_trans_count++;
    *trans = *(const esp_cam_ctlr_trans_t *)user_data;
    return false;
}

/*
 * Called from DMA ISR whenever a frame completes into a non-backup buffer.
 * Since on_get_new_trans always supplies the user buffer (user_buf != backup_buf
 * is always true), this fires on every real DMA completion.
 * Driver already did M2C cache sync before calling us (csi.c line 419).
 */
static bool IRAM_ATTR s_on_trans_finished(esp_cam_ctlr_handle_t handle,
                                          esp_cam_ctlr_trans_t *trans,
                                          void *user_data)
{
    s_trans_done_count++;
    s_finished_trans = *trans;
    BaseType_t woken = pdFALSE;
    xSemaphoreGiveFromISR(s_frame_sem, &woken);
    return woken == pdTRUE;
}

/* -- OV5647 colour-bar test pattern (reg 0x503D bit7) --------------------- */
static void ov5647_set_test_pattern(esp_sccb_io_handle_t sccb, bool en)
{
    esp_sccb_transmit_reg_a16v8(sccb, 0x503D, en ? 0x80 : 0x00);
}

static inline uint8_t rgb565_to_luma(uint16_t px)
{
    uint32_t r5 = (px >> 11) & 0x1F;
    uint32_t g6 = (px >> 5) & 0x3F;
    uint32_t b5 = px & 0x1F;
    uint32_t lum = (r5 * 630 + g6 * 608 + b5 * 240) >> 8;
    return lum > 255 ? 255 : (uint8_t)lum;
}

static const char *probe_result_name(esp_err_t err)
{
    return err == ESP_OK ? "ok" : "fail";
}

static uint8_t sccb_read_or_zero(esp_sccb_io_handle_t sccb, uint16_t reg, esp_err_t *out_err)
{
    uint8_t value = 0;
    esp_err_t err = esp_sccb_transmit_receive_reg_a16v8(sccb, reg, &value);
    if (out_err) {
        *out_err = err;
    }
    return value;
}

static void print_sensor_probe(const example_sensor_handle_t *sensor)
{
    if (!sensor || !sensor->i2c_bus_handle || !sensor->sccb_handle) {
        printf("ENKIID:error=no_sensor_handle\n");
        fflush(stdout);
        return;
    }

    esp_err_t ack_ov5647 = i2c_master_probe(sensor->i2c_bus_handle, 0x36, 100);
    esp_err_t ack_imx219 = i2c_master_probe(sensor->i2c_bus_handle, 0x10, 100);

    esp_err_t e_id_h = ESP_FAIL;
    esp_err_t e_id_l = ESP_FAIL;
    esp_err_t e_stream = ESP_FAIL;
    esp_err_t e_mipi = ESP_FAIL;
    esp_err_t e_test = ESP_FAIL;
    esp_err_t e_aec = ESP_FAIL;
    esp_err_t e_fmt = ESP_FAIL;

    uint8_t id_h = sccb_read_or_zero(sensor->sccb_handle, 0x300A, &e_id_h);
    uint8_t id_l = sccb_read_or_zero(sensor->sccb_handle, 0x300B, &e_id_l);
    uint8_t stream = sccb_read_or_zero(sensor->sccb_handle, 0x0100, &e_stream);
    uint8_t mipi = sccb_read_or_zero(sensor->sccb_handle, 0x4800, &e_mipi);
    uint8_t test = sccb_read_or_zero(sensor->sccb_handle, 0x503D, &e_test);
    uint8_t aec = sccb_read_or_zero(sensor->sccb_handle, 0x3503, &e_aec);
    uint8_t fmt = sccb_read_or_zero(sensor->sccb_handle, 0x3034, &e_fmt);

    printf("ENKIID:i2c_0x36=%s:i2c_0x10=%s:pid=%s:%02X%02X:stream=%s:%02X:mipi_4800=%s:%02X:test_503d=%s:%02X:aec_3503=%s:%02X:fmt_3034=%s:%02X\n",
           probe_result_name(ack_ov5647),
           probe_result_name(ack_imx219),
           (e_id_h == ESP_OK && e_id_l == ESP_OK) ? "ok" : "fail",
           id_h,
           id_l,
           probe_result_name(e_stream),
           stream,
           probe_result_name(e_mipi),
           mipi,
           probe_result_name(e_test),
           test,
           probe_result_name(e_aec),
           aec,
           probe_result_name(e_fmt),
           fmt);
    fflush(stdout);
}

static void print_rgb565_frame_stats(uint32_t frame_no, const uint8_t *buf, size_t size)
{
    uint8_t min_v = 255;
    uint8_t max_v = 0;
    uint64_t sum = 0;
    uint32_t zeros = 0;
    uint32_t full = 0;
    uint64_t hdiff = 0;
    uint64_t vdiff = 0;
    uint32_t hcnt = 0;
    uint32_t vcnt = 0;
    const uint16_t *pixels = (const uint16_t *)buf;
    size_t pixel_count = size / sizeof(uint16_t);

    for (size_t i = 0; i < pixel_count; i++) {
        uint8_t v = rgb565_to_luma(pixels[i]);
        if (v < min_v) {
            min_v = v;
        }
        if (v > max_v) {
            max_v = v;
        }
        sum += v;
        zeros += (v == 0);
        full += (v == 255);
    }

    for (int y = 0; y < CAM_VRES; y += 8) {
        const uint16_t *row = pixels + y * CAM_HRES;
        for (int x = 0; x < CAM_HRES - 1; x += 8) {
            uint8_t a = rgb565_to_luma(row[x]);
            uint8_t b = rgb565_to_luma(row[x + 1]);
            hdiff += a > b ? a - b : b - a;
            hcnt++;
        }
    }
    for (int y = 0; y < CAM_VRES - 1; y += 8) {
        const uint16_t *row0 = pixels + y * CAM_HRES;
        const uint16_t *row1 = row0 + CAM_HRES;
        for (int x = 0; x < CAM_HRES; x += 8) {
            uint8_t a = rgb565_to_luma(row0[x]);
            uint8_t b = rgb565_to_luma(row1[x]);
            vdiff += a > b ? a - b : b - a;
            vcnt++;
        }
    }

    printf("ENKISTAT:frame=%lu:format=rgb565:size=%lu:min=%u:max=%u:mean=%lu:zeros=%lu:full=%lu:hdiff=%lu:vdiff=%lu\n",
           (unsigned long)frame_no,
           (unsigned long)size,
           min_v,
           max_v,
           (unsigned long)(sum / pixel_count),
           (unsigned long)zeros,
           (unsigned long)full,
           (unsigned long)(hcnt ? hdiff / hcnt : 0),
           (unsigned long)(vcnt ? vdiff / vcnt : 0));
    fflush(stdout);
}

/* 4× downsample manteniendo color RGB565.
 * Promedia un bloque 4×4 (16 píxeles) canal por canal y reempaqueta.
 * src: 800×1280 RGB565  dst: 200×320 RGB565
 * Reduce la resolución preview a ¼ para que el JPEG sea ≤3 KB y quepa en
 * los ~9 KB/s del USB JTAG, logrando ~2 fps en el navegador.            */
static void downsample_rgb565_4x(const uint8_t *src, uint8_t *dst)
{
    const uint16_t *pixels = (const uint16_t *)src;
    uint16_t       *out    = (uint16_t *)dst;
    for (int y = 0; y < PREVIEW_VRES; y++) {      /* 0 .. 319 */
        for (int x = 0; x < PREVIEW_HRES; x++) {  /* 0 .. 199 */
            uint32_t r = 0, g = 0, b = 0;
            for (int dy = 0; dy < 4; dy++) {
                const uint16_t *row = pixels + (y * 4 + dy) * CAM_HRES + x * 4;
                for (int dx = 0; dx < 4; dx++) {
                    uint16_t p = row[dx];
                    r += (p >> 11) & 0x1F;
                    g += (p >>  5) & 0x3F;
                    b += p & 0x1F;
                }
            }
            /* divide by 16 (>> 4) for each channel */
            out[y * PREVIEW_HRES + x] = (uint16_t)(((r >> 4) << 11) |
                                                    ((g >> 4) <<  5) |
                                                     (b >> 4));
        }
    }
}

/* -- Gamma correction (γ=0.55) -------------------------------------------- */
/* Applied in ISP hardware.  γ<1 brightens shadows; 0.55 is between sRGB
 * (0.45) and linear (1.0) — enough to lift the dark image without blowing
 * out highlights.  Input x and output are in [0, 255].                     */
static uint32_t s_gamma_brighten(uint32_t x)
{
    if (x == 0) return 0;
    return (uint32_t)(powf((float)x / 255.0f, 0.55f) * 255.0f + 0.5f);
}

/* -- Serial frame sender --------------------------------------------------- */
/*
 * Chunk buffer for base64 encoding.  512 bytes = 128 base64 groups per fwrite
 * call, reducing syscall count from ~5000 (4-byte original) to ~34.
 * fwrite() through stdio is the confirmed-working path on USB JTAG VFS;
 * direct write() silently drops the trailing '\n' when the FIFO is full.
 */
#define B64_CHUNK 512

static void write_base64_payload(const uint8_t *data, uint32_t size)
{
    static const char alphabet[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    char     buf[B64_CHUNK];
    int      pos = 0;
    uint32_t i   = 0;

    while (i + 2 < size) {
        uint32_t v = ((uint32_t)data[i] << 16) | ((uint32_t)data[i + 1] << 8) | data[i + 2];
        buf[pos++] = alphabet[(v >> 18) & 0x3F];
        buf[pos++] = alphabet[(v >> 12) & 0x3F];
        buf[pos++] = alphabet[(v >>  6) & 0x3F];
        buf[pos++] = alphabet[v & 0x3F];
        i += 3;
        if (pos >= B64_CHUNK - 4) {
            fwrite(buf, 1, (size_t)pos, stdout);
            pos = 0;
        }
    }
    /* Padding */
    if (i < size) {
        uint32_t v = (uint32_t)data[i] << 16;
        buf[pos++] = alphabet[(v >> 18) & 0x3F];
        if (i + 1 < size) {
            v |= (uint32_t)data[i + 1] << 8;
            buf[pos++] = alphabet[(v >> 12) & 0x3F];
            buf[pos++] = alphabet[(v >>  6) & 0x3F];
            buf[pos++] = '=';
        } else {
            buf[pos++] = alphabet[(v >> 12) & 0x3F];
            buf[pos++] = '=';
            buf[pos++] = '=';
        }
    }
    if (pos > 0) {
        fwrite(buf, 1, (size_t)pos, stdout);
    }
}

static bool send_jpeg_frame_b64(const uint8_t *data, uint32_t size)
{
    printf("ENKIB64:%lu:", (unsigned long)size);
    write_base64_payload(data, size);
    printf("\n");   /* '\n' triggers USB JTAG TX FIFO auto-flush */
    return true;
}

/* ========================================================================== */
void app_main(void)
{
    esp_log_level_set("*", ESP_LOG_WARN);
    esp_log_level_set(TAG,  ESP_LOG_INFO);
    ESP_LOGI(TAG, "OV5647 CSI RAW8 -> JPEG grayscale stream  v27 csi-first-800x1280");
    ESP_LOGI(TAG, "SDA=%d SCL=%d  MCLK=on-board crystal (xclk_pin=-1)  %dx%d  2-lane  %d Mbps",
             CAM_SDA_IO, CAM_SCL_IO, CAM_HRES, CAM_VRES, CAM_LANE_BITRATE_MBPS);

    /* -- MIPI PHY LDO ------------------------------------------------------ */
    esp_ldo_channel_handle_t ldo_mipi = NULL;
    esp_ldo_channel_config_t ldo_cfg  = {
        .chan_id    = MIPI_PHY_LDO_CHAN_ID,
        .voltage_mv = MIPI_PHY_LDO_VOLTAGE_MV,
    };
    ESP_ERROR_CHECK(esp_ldo_acquire_channel(&ldo_cfg, &ldo_mipi));
    vTaskDelay(pdMS_TO_TICKS(10));

    /* -- Sensor config; stream starts after CSI is ready on this board. ----- */
    example_sensor_handle_t sensor = {.sccb_handle = NULL, .i2c_bus_handle = NULL};
    example_sensor_config_t scfg   = {
        .i2c_port_num   = I2C_NUM_0,
        .i2c_sda_io_num = CAM_SDA_IO,
        .i2c_scl_io_num = CAM_SCL_IO,
        .port           = ESP_CAM_SENSOR_MIPI_CSI,
        .format_name    = "MIPI_2lane_24Minput_RAW8_800x1280_50fps",
    };

    /* -- DMA Memory ---------------------------------------------------- */
    size_t align = 0;
    esp_cache_get_alignment(MALLOC_CAP_SPIRAM, &align);

    /* ESP32-P4 rev v1.3 rejects CSI RAW8->RGB565 DMA conversion. Capture RAW8
     * and encode a grayscale preview, with RGB-sized slack matching IDF tests. */
    size_t FRAME_BYTES = RAW_FRAME_BYTES;
    size_t frame_buf_len = RGB_FRAME_BYTES;
    uint8_t *frame_buf = heap_caps_aligned_alloc(align, frame_buf_len, MALLOC_CAP_SPIRAM);
    if (!frame_buf) {
        ESP_LOGE(TAG, "Failed to allocate frame buffer");
        while (1) vTaskDelay(pdMS_TO_TICKS(5000));
    }
    ESP_LOGI(TAG, "Frame buf %zu B @ %p  raw8_frame=%u B  align=%u",
             frame_buf_len, frame_buf, (unsigned)FRAME_BYTES, (unsigned)align);

    uint8_t *preview_buf = heap_caps_aligned_alloc(align, PREVIEW_FRAME_BYTES, MALLOC_CAP_SPIRAM);
    if (!preview_buf) {
        ESP_LOGE(TAG, "Failed to allocate preview buffer");
        while (1) vTaskDelay(pdMS_TO_TICKS(5000));
    }
    ESP_LOGI(TAG, "Preview buf %u B  %dx%d RAW8 grayscale",
             (unsigned)PREVIEW_FRAME_BYTES, PREVIEW_HRES, PREVIEW_VRES);

    s_cam_trans.buffer = frame_buf;
    s_cam_trans.buflen = frame_buf_len;

    /* -- JPEG encoder ------------------------------------------------------- */
    jpeg_encode_engine_cfg_t jeng = {.intr_priority = 0, .timeout_ms = 700};
    jpeg_encoder_handle_t jpeg_enc = NULL;
    ESP_ERROR_CHECK(jpeg_new_encoder_engine(&jeng, &jpeg_enc));

    jpeg_encode_memory_alloc_cfg_t jmem = {.buffer_direction = JPEG_ENC_ALLOC_OUTPUT_BUFFER};
    size_t   jbuf_sz = 0;
    uint8_t *jbuf    = (uint8_t *)jpeg_alloc_encoder_mem(JPEG_OUT_BUF_SIZE, &jmem, &jbuf_sz);
    if (!jbuf) {
        ESP_LOGE(TAG, "JPEG buf alloc failed");
        while (1) vTaskDelay(pdMS_TO_TICKS(5000));
    }
    ESP_LOGI(TAG, "JPEG buf: %u B", (unsigned)jbuf_sz);

    /* -- Frame-ready semaphore (binary) ------------------------------------- */
    s_frame_sem = xSemaphoreCreateBinary();
    if (!s_frame_sem) {
        ESP_LOGE(TAG, "Semaphore create failed");
        while (1) vTaskDelay(pdMS_TO_TICKS(5000));
    }

    /* -- ISP (RAW8->RGB565) ------------------------------------------------ */
    /* On chip rev v1.3, set_color_mode_bypass() in the CSI bridge is a NO-OP,
     * so the bridge never routes data to DMA without ISP active.  ISP claims
     * the bridge via MIPI_CSI_BRG_USER_ISP and establishes the data path. */
    isp_proc_handle_t isp_proc = NULL;
    /* bayer_order: OV5647 datasheet = BGGR, but the 800×1280 crop in the IDF
     * driver offsets the start row by 1 → effective pattern is GRBG.
     * Symptoms of wrong order: face appears green (R↔G swapped).
     * Try in order: GRBG → GBRG → RGGB → BGGR until colors look correct. */
    esp_isp_processor_cfg_t isp_cfg = {
        .clk_hz                 = 80 * 1000 * 1000,
        .input_data_source      = ISP_INPUT_DATA_SOURCE_CSI,
        .input_data_color_type  = ISP_COLOR_RAW8,
        .output_data_color_type = ISP_COLOR_RGB565,
        .has_line_start_packet  = false,
        .has_line_end_packet    = false,
        .h_res                  = CAM_HRES,
        .v_res                  = CAM_VRES,
        .bayer_order            = COLOR_RAW_ELEMENT_ORDER_GBRG,  /* OV5647 native — fixed in esp_cam_sensor v0.7.1 */
    };
    ESP_ERROR_CHECK(esp_isp_new_processor(&isp_cfg, &isp_proc));
    ESP_ERROR_CHECK(esp_isp_enable(isp_proc));
    ESP_LOGI(TAG, "ISP enabled (RAW8->RGB565)");

    /* Demosaic must be explicitly enabled — default state is off even after
     * esp_isp_enable().  Without it the Bayer->RGB conversion does not run
     * and the ISP outputs flat/striped colour data. */
    esp_isp_demosaic_config_t demosaic_cfg = {
        .grad_ratio  = { .val = 0 },
        .padding_mode = ISP_DEMOSAIC_EDGE_PADDING_MODE_SRND_DATA,
        .padding_data = 0,
        .padding_line_tail_valid_start_pixel = 0,
        .padding_line_tail_valid_end_pixel   = 0,
    };
    ESP_ERROR_CHECK(esp_isp_demosaic_configure(isp_proc, &demosaic_cfg));
    ESP_ERROR_CHECK(esp_isp_demosaic_enable(isp_proc));
    ESP_LOGI(TAG, "ISP demosaic enabled (BGGR->RGB565)");

    /* -- ISP CCM: OV5647 color correction ---------------------------------- */
    /* Raw Bayer demosaic gives a greenish/cool result because:
     *   · 2× green pixels in Bayer array → green dominant
     *   · No sensor spectral correction → blue/cyan cast under indoor light
     * Matrix: boost R, keep G, reduce B → warmer, more natural skin tones.
     * Row sums for neutral grey: R≈1.0, G≈0.9, B≈0.65 (warm white balance). */
    /* CCM: gentle indoor correction — assumes Bayer is now correct.
     * Slight R boost + mild B reduction for warm indoor light.            */
    esp_isp_ccm_config_t ccm_cfg = {
        .matrix = {
            { 1.4f, -0.2f, -0.2f },   /* R_out: slight boost             */
            {-0.1f,  1.1f,  0.0f },   /* G_out: nearly flat              */
            { 0.0f, -0.1f,  0.9f },   /* B_out: slight reduction         */
        },
        .saturation = true,
        .flags = { .update_once_configured = true },
    };
    ESP_ERROR_CHECK(esp_isp_ccm_configure(isp_proc, &ccm_cfg));
    ESP_ERROR_CHECK(esp_isp_ccm_enable(isp_proc));
    ESP_LOGI(TAG, "ISP CCM enabled (warm color correction)");

    /* -- ISP Gamma: γ=0.55 to lift shadows --------------------------------- */
    isp_gamma_curve_points_t gamma_pts = {};
    ESP_ERROR_CHECK(esp_isp_gamma_fill_curve_points(s_gamma_brighten, &gamma_pts));
    ESP_ERROR_CHECK(esp_isp_gamma_configure(isp_proc, COLOR_COMPONENT_R, &gamma_pts));
    ESP_ERROR_CHECK(esp_isp_gamma_configure(isp_proc, COLOR_COMPONENT_G, &gamma_pts));
    ESP_ERROR_CHECK(esp_isp_gamma_configure(isp_proc, COLOR_COMPONENT_B, &gamma_pts));
    ESP_ERROR_CHECK(esp_isp_gamma_enable(isp_proc));
    ESP_LOGI(TAG, "ISP gamma enabled (γ=0.55)");

    /* -- CSI controller: sensor RAW8, DMA output RAW8 ---------------------- */
    esp_cam_ctlr_csi_config_t csi_cfg = {
        .ctlr_id                = 0,
        .h_res                  = CAM_HRES,
        .v_res                  = CAM_VRES,
        .lane_bit_rate_mbps     = CAM_LANE_BITRATE_MBPS,
        .input_data_color_type  = CAM_CTLR_COLOR_RAW8,
        .output_data_color_type = CAM_CTLR_COLOR_RAW8,
        .data_lane_num          = 2,
        .byte_swap_en           = false,
        .queue_items            = 1,
    };
    esp_cam_ctlr_handle_t cam = NULL;
    ESP_ERROR_CHECK(esp_cam_new_csi_ctlr(&csi_cfg, &cam));

    esp_cam_ctlr_evt_cbs_t cbs = {
        .on_get_new_trans  = s_on_get_new_trans,
        .on_trans_finished = s_on_trans_finished,
    };
    ESP_ERROR_CHECK(esp_cam_ctlr_register_event_callbacks(cam, &cbs, &s_cam_trans));
    ESP_ERROR_CHECK(esp_cam_ctlr_enable(cam));

    ESP_ERROR_CHECK(esp_cam_ctlr_start(cam));
    ESP_LOGI(TAG, "CSI started -- initializing sensor stream...");

    example_sensor_init(&scfg, &sensor);
    if (!sensor.sccb_handle) {
        ESP_LOGE(TAG, "No sensor -- halting");
        while (1) vTaskDelay(pdMS_TO_TICKS(5000));
    }
    ESP_LOGI(TAG, "Sensor OK -- restarting OV5647 MIPI stream after CSI/ISP init");
    esp_sccb_transmit_reg_a16v8(sensor.sccb_handle, 0x0100, 0x00);
    esp_sccb_transmit_reg_a16v8(sensor.sccb_handle, 0x4800, 0x14);
    vTaskDelay(pdMS_TO_TICKS(5));
    esp_sccb_transmit_reg_a16v8(sensor.sccb_handle, 0x0100, 0x01);
    ESP_LOGI(TAG, "OV5647 MIPI stream restarted (0x4800=0x14); warming up %d ms", CAM_WARMUP_MS);
    vTaskDelay(pdMS_TO_TICKS(CAM_WARMUP_MS));

    /* -- I2C register readback: verify OV5647 state ----------------------- */
    {
        uint8_t v = 0;
        struct { uint16_t reg; const char *name; uint8_t expect; } regs[] = {
            {0x0100, "stream",    0x01},
            {0x4800, "mipi_ctrl", 0x14},
            {0x3018, "lane_cfg",  0x44},
            {0x3036, "pll_mult",  0x80},
            {0x3037, "pll_div",   0xFF},  /* FF = don't check */
            {0x4837, "pclk_period", 0xFF},
        };
        for (int i = 0; i < (int)(sizeof(regs)/sizeof(regs[0])); i++) {
            esp_err_t re = esp_sccb_transmit_receive_reg_a16v8(sensor.sccb_handle, regs[i].reg, &v);
            if (re == ESP_OK) {
                ESP_LOGI(TAG, "OV5647 reg 0x%04X (%s) = 0x%02X%s",
                         regs[i].reg, regs[i].name, v,
                         (regs[i].expect != 0xFF && v != regs[i].expect) ? "  <-- MISMATCH" : "");
            } else {
                ESP_LOGE(TAG, "OV5647 reg 0x%04X read fail: 0x%x", regs[i].reg, re);
            }
        }
    }
    ov5647_set_test_pattern(sensor.sccb_handle, false);
    ESP_LOGW(TAG, "OV5647 internal test pattern disabled; using sensor driver exposure/gain defaults");
    print_sensor_probe(&sensor);

    /* -- Wait for frames -- */
    ESP_LOGI(TAG, "Waiting for frames...");

    /* RGB565 color JPEG: el ISP ya entregó RGB565; el encoder acepta RGB565
     * directamente como entrada.  YUV422 = mejor relación calidad/tamaño. */
    jpeg_encode_cfg_t enc_cfg = {
        .height        = PREVIEW_VRES,
        .width         = PREVIEW_HRES,
        .src_type      = JPEG_ENCODE_IN_FORMAT_RGB565,
        .sub_sample    = JPEG_DOWN_SAMPLING_YUV422,
        .image_quality = JPEG_QUALITY,
        .pixel_reverse = false,
    };

    setvbuf(stdout, NULL, _IONBF, 0);
    uart_vfs_dev_port_set_tx_line_endings(CONFIG_ESP_CONSOLE_UART_NUM, ESP_LINE_ENDINGS_LF);
    usb_serial_jtag_vfs_set_tx_line_endings(ESP_LINE_ENDINGS_LF);
    ESP_LOGI(TAG, "Console VFS set to LF-only; camera frames use ENKIB64 text transport");
    esp_log_level_set(TAG, ESP_LOG_WARN);

    /* -- Main streaming loop ------------------------------------------------ */
    uint32_t frame_count   = 0;   /* valid frames (passed received_size check) */
    uint32_t sent_count    = 0;   /* JPEG frames transmitted                   */
    uint32_t total_frames  = 0;   /* every DMA completion, incl. bad frames    */
    int64_t  last_frame_us = 0;   /* timestamp of previous frame (µs)          */

    while (1) {
        /* -- Wait for DMA frame completion ---------------------------------- */
        if (xSemaphoreTake(s_frame_sem, pdMS_TO_TICKS(FRAME_TIMEOUT_MS)) != pdTRUE) {
            printf("ENKIMETRIC:timeout:valid=%lu:total=%lu:new_trans=%lu:trans_done=%lu\n",
                   (unsigned long)frame_count,
                   (unsigned long)total_frames,
                   (unsigned long)s_new_trans_count,
                   (unsigned long)s_trans_done_count);
            /* \n triggers USB JTAG FIFO auto-flush — no fflush needed */
            continue;
        }

        /* -- Fast per-frame metric (throttled to METRIC_EVERY_N_FRAMES) ------- */
        total_frames++;
        int64_t now_us  = esp_timer_get_time();
        int64_t elapsed = (last_frame_us > 0) ? (now_us - last_frame_us) : 0;
        last_frame_us   = now_us;
        float   fps     = (elapsed > 10000) ? (1e6f / (float)elapsed) : 0.0f;

        if ((total_frames % METRIC_EVERY_N_FRAMES) == 0) {
            printf("ENKIMETRIC:total=%lu:valid=%lu:rx=%u:fps=%.1f:ms=%lu\n",
                   (unsigned long)total_frames,
                   (unsigned long)frame_count,
                   (unsigned)s_finished_trans.received_size,
                   fps,
                   (unsigned long)(elapsed / 1000));
            /* \n triggers USB JTAG FIFO auto-flush — no fflush needed */
        }

        /* -- Validate DMA transfer size ------------------------------------- */
        /* received_size is reported in RAW8 units regardless of ISP output
         * format (chip-rev v1.3 driver quirk).  FRAME_BYTES == RAW_FRAME_BYTES
         * is the correct sentinel.                                             */
        if (s_finished_trans.received_size != FRAME_BYTES) {
            printf("ENKIMETRIC:skip:expected=%u:got=%u\n",
                   (unsigned)FRAME_BYTES,
                   (unsigned)s_finished_trans.received_size);
            continue;
        }

        /* -- Cache sync: ISP writes RGB565; sync the full RGB565 extent ----- */
        /* frame_buf holds RGB565 (ISP output).  received_size reports only the
         * RAW8-equivalent bytes (driver quirk), so we must sync the full
         * RGB565 extent manually.                                              */
        esp_cache_msync(frame_buf, RGB_FRAME_BYTES, ESP_CACHE_MSYNC_FLAG_DIR_M2C);

        /* -- Pixel stats: heavy scan, every PIXEL_STATS_EVERY_N valid frames - */
        if ((frame_count % PIXEL_STATS_EVERY_N) == 0) {
            print_rgb565_frame_stats(frame_count, frame_buf, RGB_FRAME_BYTES);
        }

        /* -- JPEG encode + send: every JPEG_EVERY_N_FRAMES valid frames ----- */
        if ((frame_count % JPEG_EVERY_N_FRAMES) == 0) {
            downsample_rgb565_4x(frame_buf, preview_buf);

            uint32_t  jpeg_size = 0;
            esp_err_t enc_err   = jpeg_encoder_process(jpeg_enc, &enc_cfg,
                                                       preview_buf, PREVIEW_FRAME_BYTES,
                                                       jbuf, jbuf_sz, &jpeg_size);
            if (enc_err == ESP_OK && jpeg_size > 0) {
                bool has_eoi = (jpeg_size >= 2 &&
                                jbuf[jpeg_size - 2] == 0xFF &&
                                jbuf[jpeg_size - 1] == 0xD9);
                uint32_t send_size = jpeg_size;
                if (!has_eoi && (jpeg_size + 2 <= jbuf_sz)) {
                    jbuf[jpeg_size]     = 0xFF;
                    jbuf[jpeg_size + 1] = 0xD9;
                    send_size += 2;
                    /* CPU writes land in D-cache; write_base64_payload reads
                     * the same cache lines via CPU — no C2M sync needed.     */
                }
                printf("ENKIMETRIC:jpeg:frame=%lu:sent=%lu:encoded=%lu:send=%lu:eoi=%s\n",
                       (unsigned long)frame_count,
                       (unsigned long)sent_count,
                       (unsigned long)jpeg_size,
                       (unsigned long)send_size,
                       has_eoi ? "native" : "patched");
                if (send_jpeg_frame_b64(jbuf, send_size)) {
                    sent_count++;
                }
            } else {
                printf("ENKIMETRIC:jpeg_fail:frame=%lu:err=0x%x\n",
                       (unsigned long)frame_count, enc_err);
            }
        }

        frame_count++;
    }
}
