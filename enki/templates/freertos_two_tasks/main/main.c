#include "enki.h"

static void blink_task(void *arg)
{
    (void)arg;
    enki_gpio_output(2);

    while (1) {
        enki_gpio_toggle(2);
        enki_delay_ms(500);
    }
}

static void log_task(void *arg)
{
    (void)arg;

    while (1) {
        enki_log_info("enki", "FreeRTOS task heartbeat");
        enki_delay_ms(1000);
    }
}

void app_main(void)
{
    enki_task_create(blink_task, "blink", 2048, NULL, 5);
    enki_task_create(log_task, "log", 2048, NULL, 5);
}
