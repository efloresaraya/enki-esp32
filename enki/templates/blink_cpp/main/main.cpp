#include "enki.h"

extern "C" void app_main(void)
{
    enki_gpio_output(2);

    while (true) {
        enki_gpio_toggle(2);
        enki_delay_ms(500);
    }
}
