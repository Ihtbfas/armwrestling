# STM32 V1 虚拟串口联调（Ubuntu）

这里使用 Linux 自带的 **PTY 伪终端** 模拟 USB 串口，不使用 TCP 模拟 STM32 数据。通过 PTY 传输的字节就是 [STM32 串口 V1 协议](stm32-serial-v1.md) 定义的完整二进制帧（帧头、定长字段、小端整数、序号、CRC16、会话握手）。VR 仍使用既定的 UDP JSON 9102 协议。

## 三个终端启动

只需要额外安装与真实 STM32 USB 串口相同的 Python 轻量依赖：

```bash
python3 -m pip install pyserial
```

在项目根目录打开三个终端：

**终端 1：虚拟 STM32，先启动**

```bash
python3 -m sim.mock_stm32_serial --demo-arm
```

程序显示类似 `{"event":"virtual_serial_ready","port":"/dev/pts/3",...}`，记下实际路径。`--demo-arm` 是**显式允许模拟器自动进入可运行状态**的演示参数；不代表真实硬件可以省略本地人工使能、安全检查。

**终端 2：PC 上位机，使用终端 1 输出的设备路径**

```bash
python3 -m real.run --port /dev/pts/3
```

**终端 3：模拟 VR，沿用已冻结的 UDP V0.1**

```bash
python3 -m sim.mock_vr --demo-states
```

三个进程可分别 Ctrl+C 退出。首次运行先观察 `session_open`、`serial_open`、PC 日志中 `running` 和 `match_paused`，以及 STM32 终端里 `applied_torque_nm` 出现非零；非零制动仅表示软件模型输出。

## 当前逻辑与未来实物

- 虚拟 STM32 以约 100 Hz 发出 V1 `telemetry`，包括主轴角度、角速度、测力无效标志、状态及最近接受的命令序号。
- PC 使用同一个 `real/stm32_link.py` 读写 V1 字节流，完成 `SESSION_OPEN/SESSION_ACK`，解析带 CRC16 的数据，再通过现有 `common/state.py` 和 `common/control.py`（50 Hz）计算阻力幅值，向下位机下发 V1 `brake_command`。
- 模拟 STM32 收到有效会话内的 ENABLE 后，把制动幅值应用于本地**简化单自由度动力学**；只接受最新序号的有效命令，指令超时后停止模拟制动并清除 ARM_READY。
- VR ↔ PC 的 UDP 端口 9102 不变，VR 只传角色编号/比赛状态，PC 只返回角度/运行状态/原因。
- 实际部署在 Windows 时，PC 用 `py -3 -m real.run --port COM5` 等命令连接 **真实 USB CDC 或 USART+USB 转串口**；只改变设备端口名，仍用相同的 V1 应用层字节协议。**本 PTY 模拟器只支持 Linux/Unix**，Windows 要做虚拟串口对接需另外的虚拟 COM 驱动。
- 旧的 `python3 -m common.bridge` + `python3 -m sim.mock_stm32` 仍保留作 TCP/UDP 历史演示，但它**不验证真实串口 V1**，不要将其与本方案混同。

## 验证

```bash
# 不需 pyserial：纯协议和串口适配器假设备测试
python3 -m unittest common.test_serial_protocol real.test_stm32_link -v

# Ubuntu 原生 PTY：不需 pyserial
python3 -m unittest sim.test_virtual_serial -v

# 三进程手动验证：PC 上位机此时需 pyserial
python3 -m sim.mock_stm32_serial --demo-arm
python3 -m real.run --port /dev/pts/N
python3 -m sim.mock_vr --demo-states
```

三个终端命令分别执行，`N` 是第一终端实际输出的数字，不是固定值。

**范围与限制：** 当前仅模拟标量摆臂动力学，不是 MuJoCo/真实制动器；真正 STM32 固件的会话处理、人体附近的故障处置、驱动标定、Windows COM 和 Unity 实测仍需联调。真实硬件故障与重新使能必须由 STM32/电气组实施独立保护，不应依据模拟器 `--demo-arm` 的行为推导安全性。
