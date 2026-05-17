function dev = enkiConnect(port, baudrate)
%ENKICONNECT Open a serialport connection to an Enki ESP32 device.
if nargin < 2
    baudrate = 115200;
end
dev = serialport(port, baudrate);
configureTerminator(dev, "LF");
flush(dev);
end
