function response = enkiPing(dev)
%ENKIPING Send a JSON Lines ping command and read one response.
enkiWriteJson(dev, struct("cmd", "ping"));
response = enkiReadLine(dev);
end
