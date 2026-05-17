% Demo: read lines from an Enki ESP32 device.
port = "/dev/cu.usbserial-110";
baudrate = 115200;
dev = enkiConnect(port, baudrate);

disp("Sending ping...");
disp(enkiPing(dev));

disp("Reading lines. Press Ctrl+C to stop.");
while true
    disp(enkiReadLine(dev));
end
