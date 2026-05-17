# Enki MATLAB bridge

This folder is a v1 foundation for future MATLAB communication with ESP32 boards over serial JSON Lines.

Planned command examples:

```json
{"cmd":"ping"}
{"cmd":"read_adc","pin":34}
{"cmd":"set_gpio","pin":2,"value":1}
{"cmd":"set_pwm","pin":5,"value":128}
{"cmd":"stream_start","target":"adc","pin":34,"rate_hz":100}
{"cmd":"stream_stop"}
```

Planned response examples:

```json
{"ok":true,"cmd":"ping","time_ms":1234}
{"ok":true,"cmd":"read_adc","pin":34,"value":2870}
{"ok":true,"cmd":"set_gpio","pin":2,"value":1}
{"ok":false,"error":"invalid_pin"}
```
