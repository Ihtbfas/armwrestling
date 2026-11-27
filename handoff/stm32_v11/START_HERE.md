# STM32 V1.1 首次交接 · 面板 V0.2

这份交接包可在 Windows 上独立运行 PC 联调面板。板端先移植 C 编解码和会话接口，用黄金帧核对字节，再接串口。V1.1 线上版本是 `0x02`。

## PC 启动

在解压目录或仓库根目录打开 PowerShell。已有 `.venv` 时跳过第一行。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install pyserial
.\start-stm32-debug.cmd
```

只需带 Tkinter 的 Python 3.10 或更新版本及 pyserial。启动脚本会提示依赖问题。默认选 **串口检查 · 仅 STOP**。勾选 **记录逐帧实验日志** 后连接，可从面板保存 ZIP。

## 第一次接板

1. 先由下位机和电气同学确认制动器励磁功率已物理隔离，设备旁无人接触。选实际设备对应的 COM 口；USART 默认 115200、8N1。
2. 保持仅 STOP 模式。观察 COM 打开、RX/TX、合法帧、PC 与板端会话匹配，以及 **STOP 已确认、板端回报零目标**。未授权或角度还没标定时仍应能验证 STOP。若有 RX 却无合法帧，到串口详情核对计数和复制 Hex。
3. 确认 MCU 采样时间在走。角度有效后，核对机械零位和方向，向获胜侧压下是负角。速度和测力还没接好时，相应有效位清零。
4. 保持 COM 打开重启 MCU。应看到会话重新握手并再次确认 STOP。保存日志，记录固件版本、COM 口和复现步骤。

晚些时候如需模拟比赛，断开后显式选 **模拟 VR 比赛** 再连接。开始和继续取决于当前会话、角度及板端本地许可。真实制动器的台架测试按双方确认的保护和标定流程执行。

## 板端从这里接

- [串口协议与黄金报文](../../docs/stm32-serial-v11.md)
- [C 编解码、会话参考和主机测试](../../firmware_reference/stm32_v11/README.md)
- [接入时双方要确认的事项](HARDWARE.md)
- [面板操作说明](../../docs/stm32-debug-panel-v02.md) · [实测记录与截图](../../docs/stm32-debug-panel-v02-windows.md) · [本次 STOP 日志节选](example-v02-stop.jsonl)

当前验证覆盖 Windows 虚拟 COM 和 C 主机编译。待双方核对实际板端行为，再确认协议冻结和硬件验收。
