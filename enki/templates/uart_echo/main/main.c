#include <stdio.h>
#include <string.h>

#include "driver/uart.h"
#include "enki.h"

#define ENKI_UART_NUM UART_NUM_0
#define ENKI_BUF_SIZE 256

static void write_json_line(const char *line)
{
    uart_write_bytes(ENKI_UART_NUM, line, strlen(line));
    uart_write_bytes(ENKI_UART_NUM, "\n", 1);
}

void app_main(void)
{
    const uart_config_t uart_config = {
        .baud_rate = 115200,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };

    uart_driver_install(ENKI_UART_NUM, ENKI_BUF_SIZE * 2, 0, 0, NULL, 0);
    uart_param_config(ENKI_UART_NUM, &uart_config);
    write_json_line("{\"ok\":true,\"event\":\"boot\",\"template\":\"uart_echo\"}");

    uint8_t data[ENKI_BUF_SIZE];
    while (1) {
        int len = uart_read_bytes(ENKI_UART_NUM, data, ENKI_BUF_SIZE - 1, 100 / portTICK_PERIOD_MS);
        if (len > 0) {
            data[len] = '\0';
            if (strstr((const char *)data, "\"cmd\":\"ping\"") != NULL) {
                write_json_line("{\"ok\":true,\"cmd\":\"ping\"}");
            } else {
                uart_write_bytes(ENKI_UART_NUM, (const char *)data, len);
            }
        }
    }
}
