function enkiWriteJson(dev, payload)
%ENKIWRITEJSON Write a MATLAB struct as one JSON line.
line = jsonencode(payload);
writeline(dev, line);
end
