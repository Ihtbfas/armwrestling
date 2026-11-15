# PC 串口入口

`run.py` 是 PC 侧的 STM32 V1 串口＋VR UDP 程序。`stm32_link.py` 负责字节收发、会话握手和遥测解析，控制算法仍在 `common/control.py`。

在仓库根目录安装 `pyserial`，然后指定 PC 要打开的端口：

```powershell
.\.venv\Scripts\python.exe -m pip install pyserial==3.5
.\.venv\Scripts\python.exe -m real.run --port COM1
```

Windows 软件联调已使用 COM1 ↔ COM2 虚拟串口对，另一端运行 `sim.mock_stm32_serial --port COM2 --demo-arm`。VR 仍通过 UDP `127.0.0.1:9102` 与 PC 通信。`common.bridge` 是另一种 PC 入口，两者不能同时占用 UDP 9102。

接实体设备时换成实际端口，并与下位机组核对 V1 固件状态机、本地使能、故障保护及标定。仓库没有 STM32 固件，实体设备尚未联调。

完整启动和验收步骤见 [项目 README](../README.md)、[Windows 串口说明](../docs/windows-serial-operator-guide.md)；线协议见 [STM32 V1](../docs/stm32-serial-v1.md)。
