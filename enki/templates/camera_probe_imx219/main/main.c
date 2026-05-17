#include <stdint.h>
#include <stdio.h>
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "driver/ledc.h"
#include "esp_err.h"
#include "esp_ldo_regulator.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "camera_probe";

#define CAM_I2C_PORT I2C_NUM_0
#define CAM_SDA_IO 7
#define CAM_SCL_IO 8
#define CAM_I2C_FREQ_HZ 100000
#define CAM_CSI_IO0 20
#define CAM_CSI_IO1 21
#define IMX219_XCLK_FREQ_HZ 24000000
#define IMX219_XCLR_MIN_DELAY_MS 7
#define MIPI_PHY_LDO_CHAN_ID 3
#define MIPI_PHY_LDO_VOLTAGE_MV 2500
#define IMX219_ADDR 0x10

static esp_err_t read_reg16(i2c_master_dev_handle_t dev, uint16_t reg, uint8_t *value)
{
    uint8_t reg_buf[2] = {
        (uint8_t)(reg >> 8),
        (uint8_t)(reg & 0xff),
    };
    return i2c_master_transmit_receive(dev, reg_buf, sizeof(reg_buf), value, 1, pdMS_TO_TICKS(100));
}

static bool enable_mipi_phy_ldo(void)
{
    static esp_ldo_channel_handle_t ldo_mipi_phy = NULL;
    if (ldo_mipi_phy != NULL) {
        ESP_LOGI(TAG, "MIPI PHY LDO is already enabled");
        return true;
    }

    esp_ldo_channel_config_t ldo_config = {
        .chan_id = MIPI_PHY_LDO_CHAN_ID,
        .voltage_mv = MIPI_PHY_LDO_VOLTAGE_MV,
    };
    esp_err_t ret = esp_ldo_acquire_channel(&ldo_config, &ldo_mipi_phy);
    if (ret == ESP_OK) {
        ESP_LOGI(TAG, "Enabled MIPI PHY LDO channel %d at %d mV", MIPI_PHY_LDO_CHAN_ID, MIPI_PHY_LDO_VOLTAGE_MV);
        return true;
    } else {
        ESP_LOGW(TAG, "Could not enable MIPI PHY LDO channel %d at %d mV: %s",
                 MIPI_PHY_LDO_CHAN_ID, MIPI_PHY_LDO_VOLTAGE_MV, esp_err_to_name(ret));
        return false;
    }
}

static esp_err_t start_xclk_on_gpio(int gpio_num)
{
    ledc_timer_config_t timer = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = LEDC_TIMER_1_BIT,
        .timer_num = LEDC_TIMER_0,
        .freq_hz = IMX219_XCLK_FREQ_HZ,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    esp_err_t ret = ledc_timer_config(&timer);
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "Could not configure %d Hz XCLK timer: %s", IMX219_XCLK_FREQ_HZ, esp_err_to_name(ret));
        return ret;
    }

    ret = ledc_stop(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, 0);
    if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) {
        ESP_LOGW(TAG, "Could not stop previous XCLK channel: %s", esp_err_to_name(ret));
    }

    ledc_channel_config_t channel = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel = LEDC_CHANNEL_0,
        .timer_sel = LEDC_TIMER_0,
        .intr_type = LEDC_INTR_DISABLE,
        .gpio_num = gpio_num,
        .duty = 1,
        .hpoint = 0,
    };
    ret = ledc_channel_config(&channel);
    if (ret == ESP_OK) {
        ESP_LOGI(TAG, "Generated IMX219 XCLK on GPIO%d at %d Hz", gpio_num, IMX219_XCLK_FREQ_HZ);
    } else {
        ESP_LOGW(TAG, "Could not route XCLK to GPIO%d: %s", gpio_num, esp_err_to_name(ret));
    }
    return ret;
}

static void configure_csi_lines_as_outputs(void)
{
    gpio_config_t io_conf = {
        .pin_bit_mask = (1ULL << CAM_CSI_IO0) | (1ULL << CAM_CSI_IO1),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&io_conf));
    ESP_LOGI(TAG, "Configured CSI_IO0=GPIO%d and CSI_IO1=GPIO%d as control outputs", CAM_CSI_IO0, CAM_CSI_IO1);
}

static bool probe_camera(i2c_master_bus_handle_t bus, i2c_master_dev_handle_t imx219)
{
    ESP_LOGI(TAG, "Scanning I2C/SCCB bus...");
    int found = 0;
    for (uint8_t addr = 0x08; addr < 0x78; addr++) {
        esp_err_t ret = i2c_master_probe(bus, addr, pdMS_TO_TICKS(50));
        if (ret == ESP_OK) {
            ESP_LOGI(TAG, "Found device at 0x%02x", addr);
            found++;
        }
    }
    ESP_LOGI(TAG, "Scan complete, devices found: %d", found);

    uint8_t id_high = 0;
    uint8_t id_low = 0;
    esp_err_t hi_ret = read_reg16(imx219, 0x0000, &id_high);
    esp_err_t lo_ret = read_reg16(imx219, 0x0001, &id_low);
    if (hi_ret == ESP_OK && lo_ret == ESP_OK) {
        ESP_LOGI(TAG, "IMX219 chip ID registers: 0x%02x 0x%02x", id_high, id_low);
        if (id_high == 0x02 && id_low == 0x19) {
            ESP_LOGI(TAG, "IMX219 detected");
            return true;
        } else {
            ESP_LOGW(TAG, "Device at 0x10 responded, but ID is not the expected 0x02 0x19");
        }
    } else {
        ESP_LOGW(TAG, "Could not read IMX219 ID at 0x10: high=%s low=%s",
                 esp_err_to_name(hi_ret), esp_err_to_name(lo_ret));
        ESP_LOGW(TAG, "If no devices are found, check flex orientation, camera power rails, pull-ups, and whether the sensor needs XCLK/reset before SCCB responds");
    }
    return found > 0;
}

static bool test_power_sequence(i2c_master_bus_handle_t bus, i2c_master_dev_handle_t imx219, int xclk_gpio, int reset_gpio, int reset_level)
{
    ESP_LOGI(TAG, "Testing XCLK=GPIO%d reset=GPIO%d reset_level=%d", xclk_gpio, reset_gpio, reset_level);
    enable_mipi_phy_ldo();
    ESP_ERROR_CHECK(gpio_set_level(reset_gpio, 0));
    vTaskDelay(pdMS_TO_TICKS(2));
    if (start_xclk_on_gpio(xclk_gpio) != ESP_OK) {
        return false;
    }
    ESP_ERROR_CHECK(gpio_set_level(reset_gpio, reset_level));
    vTaskDelay(pdMS_TO_TICKS(IMX219_XCLR_MIN_DELAY_MS));
    return probe_camera(bus, imx219);
}

void app_main(void)
{
    esp_log_level_set("i2c.master", ESP_LOG_NONE);
    ESP_LOGI(TAG, "ESP32-P4 MIPI camera SCCB/I2C probe");
    ESP_LOGI(TAG, "Expected Raspberry Pi Camera Module 2 sensor: Sony IMX219");
    ESP_LOGI(TAG, "Using SDA=%d SCL=%d, address candidate 0x%02x", CAM_SDA_IO, CAM_SCL_IO, IMX219_ADDR);
    enable_mipi_phy_ldo();
    vTaskDelay(pdMS_TO_TICKS(10));
    configure_csi_lines_as_outputs();

    i2c_master_bus_config_t bus_config = {
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .i2c_port = CAM_I2C_PORT,
        .scl_io_num = CAM_SCL_IO,
        .sda_io_num = CAM_SDA_IO,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };

    i2c_master_bus_handle_t bus = NULL;
    ESP_ERROR_CHECK(i2c_new_master_bus(&bus_config, &bus));

    i2c_device_config_t dev_config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = IMX219_ADDR,
        .scl_speed_hz = CAM_I2C_FREQ_HZ,
    };
    i2c_master_dev_handle_t imx219 = NULL;
    ESP_ERROR_CHECK(i2c_master_bus_add_device(bus, &dev_config, &imx219));

    while (1) {
        const int scenarios[][3] = {
            {CAM_CSI_IO0, CAM_CSI_IO1, 1},
            {CAM_CSI_IO0, CAM_CSI_IO1, 0},
            {CAM_CSI_IO1, CAM_CSI_IO0, 1},
            {CAM_CSI_IO1, CAM_CSI_IO0, 0},
        };
        for (int i = 0; i < 4; i++) {
            if (test_power_sequence(bus, imx219, scenarios[i][0], scenarios[i][1], scenarios[i][2])) {
                break;
            }
        }
        vTaskDelay(pdMS_TO_TICKS(5000));
    }
}
