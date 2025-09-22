# create snapshot

Invoke-CimMethod -MethodName Create -ClassName Win32_ShadowCopy -Arguments @{Volume="C:\\"}
