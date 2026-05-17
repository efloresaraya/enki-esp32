function data = enkiReadLine(dev)
%ENKIREADLINE Read one JSON line from the device.
line = readline(dev);
try
    data = jsondecode(char(line));
catch
    data = struct("raw", char(line));
end
end
