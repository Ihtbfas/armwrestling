# PC 串口入口

`real.run` 使用 [STM32 串口 V1.1](../docs/stm32-serial-v11.md) 和 VR UDP V0.1。帧解析、会话、角度采样有效性与 ACK 进度由 `stm32_link.py` 管理，现有阻力控制算法仍由 `common.state` 和 `common.control` 执行。

首次接板前物理隔离制动器励磁功率，设备旁无人接触。可使用 [STM32 联调面板](../docs/stm32-debug-panel-v02.md)，或在隔离的 UDP 端口启动仅 STOP 检查。COM9 需换成实际端口。

```powershell
.\.venv\Scripts\python.exe -m pip install pyserial
.\.venv\Scripts\python.exe -m real.run --port COM9 --vr-port 19102 --monitor-only --log-file pc.jsonl --experiment-log experiment.jsonl
```

握手在 COM 保持打开时重试。板端遥测确认当前会话，匹配本会话已完整写出的 STOP，并回报零目标。随后核对角度采样和本地授权。实验日志可选，记录已解析的逐条遥测及完整写出的指令。PC 单调时间和 MCU 采样毫秒分列。

正式 VR 使用默认 UDP `127.0.0.1:9102`，PC 与 `common.bridge` 只能选一个占用该端口。启动或通信、采样、设备许可中断之后，由 VR 重新发送 `waiting` 或 `paused`，再由操作员明确开始或继续。固件按本地规则独立保证授权和保护。

[板端 C 参考](../firmware_reference/stm32_v11/README.md) · [首次接板流程](../docs/real-vr-stm32-integration-v01.md) · [Windows 虚拟 COM 验收](../docs/stm32-serial-v11-windows.md)
