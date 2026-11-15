# Windows V1 串口联调与中文操作员终端

## 这次改变了什么

终端默认在交互窗口显示中文状态面板，在原位置约每 0.5 秒刷新，不再滚动输出整屏 JSON。显示只读取状态，不修改控制计算或通信字段。

两个窗口各有职责：**PC 上位机**接收比赛和机械状态、计算目标阻力；**模拟 STM32**模拟机械运动、接收 V1 指令并执行模拟制动。比赛按钮仍在 Unity 测试界面中。Unity 资产继续位于 `tests/unity/`，不是 PC 正式依赖。

面板从上到下排列：程序身份、刷新时间、总体状态、下一步操作、连接状态、带单位的数据、最近三条事件。具体设计取舍是：先让操作者回答“现在能不能运行、为什么、该做什么”，再查看数值。

## 面板如何读

PC 窗口示例（示意，数值会变化）：

```text
扳手腕系统 | PC 上位机（V1 串口 + Unity UDP）
当前状态：[运行] 正在按机械状态计算阻力
操作提示：可在 Unity 点击 paused 暂停比赛。

下位机链路：串口 V1 / COM1 / 115200 / 8N1
Unity 链路：UDP / 127.0.0.1:9102
下位机数据：正常，距更新 16 ms
Unity 心跳：正常，距更新 94 ms
比赛状态：比赛进行中 | 角色：demo
阻力控制：计算运行中
V1 会话：已确认 | 本地使能许可：已获得
机械角度：-23.55° / -0.411 rad（负方向为获胜侧）
运动速度：-97.17 °/s
计算目标阻力：0.322 N·m（计算值）
下位机回报目标：0.288 N·m（非实测力矩）
```

| 状态提示 | 含义与操作 |
|---|---|
| 等待开始比赛 | 两端有有效数据，Unity 仍是 waiting；点击 running |
| 比赛已暂停 | Unity 主动 paused；阻力停止，机械位置仍可更新 |
| 本场比赛已结束 | Unity finished；阻力停止 |
| Unity 心跳超时 | PC 没有及时收到新的有效 VR 状态；检查 Play、心跳脚本，不是机械串口必然断开 |
| 尚未收到/机械数据超时 | 机械状态不可用于控制；检查模拟器、COM 端口或会话握手 |
| 设备故障或未获本地使能许可 | 不允许正常输出；检查下位机状态，不能只看“串口已打开” |
| 模拟器等待 PC 握手 | 串口可能已打开，但 V1 会话尚未建立 |
| 模拟器 PC 指令超时 | 200 ms 本地看门狗已撤销模拟许可和目标，迟到 ENABLE 不能直接恢复 |

角度同时显示度和 rad；速度显示 °/s。机械数据过期时显示“无有效数据”，不会假装为零。PC 的目标阻力是计算值；下位机回报的是其接受的**目标幅值**，并非力矩传感器实测值，因采样/回传先后可能略有差异。模拟器的“模拟制动力矩”是软件动力学中的值。终端出现“运行”或“已获得许可”不代表真实硬件已解除急停。

`--display auto` 是默认值：真实交互终端使用中文面板，输出被重定向时保留逐行 JSON，兼容原自动测试。`--display human` 可强制中文，`--display json` 可强制原日志。`--log-file <路径>` 可在显示中文的同时保存完整 JSONL；日志文件的父目录须存在。终端过窄时会截断长行，可放大窗口。支持 VT 的窗口原位刷新；不支持时退为中文文本输出。Ctrl+C 退出并恢复光标和原屏幕。

同一显示方式也适用于旧 TCP 演示的 `common.bridge` 和 `sim.mock_stm32`。

## 本机真实使用的链路

```text
模拟 STM32：COM2
       ↕ 已安装的 ELTIMA 虚拟串口对 COM2 ↔ COM1
PC：real.run --port COM1
       ↕ UDP 127.0.0.1:9102
Unity：独立 UnityUdpDemo 工程的实际 C# 客户端
```

调查时设备管理器显示 `ELTIMA Virtual Serial Port (COM1->COM2)` 和 `(COM2->COM1)`，状态均 OK，驱动运行中。COM1/COM2 已做真实读写验证，所以没有安装驱动、修改驱动签名设置或使用 Linux `/dev/pts/N`。未来换电脑须先提供一对实际可用的 Windows 虚拟 COM 口；端口号应按本机配置调整。

通过 COM 收发的是原 [STM32 串口 V1](stm32-serial-v1.md)：A5 5A 帧头、版本、类型、16 位序号、长度、小端载荷和 CRC16；使用 64 位 SESSION_OPEN/ACK 会话，遥测、制动命令和 200 ms 本地上限都保留。串口设置 115200、8N1、无流控。**这条链路没有用 TCP 隧道代替 COM。**

PC 使用与未来实机相同的 `real/stm32_link.py` 和 `real.run`。模拟串口对端静默时，PC 会停止输出并重新打开端口、创建新会话，避免模拟器重启后双方仍困在旧会话。模拟器在协议层支持 65535→0 回绕，不把线上的 16 位序号误用为动力学对象的无限递增序号。

## 启动

仓库：`E:\桌面\研究生\实验室\人机交互课程\实物大作业\code`。本次已在仓库 `.venv` 安装 pyserial 3.5，不修改系统 Anaconda 环境。首次在其他环境运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install pyserial==3.5
```

先关闭旧 TCP 演示和重复的 PC 进程，保持 UDP 9102 和 COM1/COM2 不被其他程序占用。两个 PowerShell 都切到仓库根目录，分别执行：

```powershell
# 窗口一：软件模拟下位机，打开 COM2
.\.venv\Scripts\python.exe -m sim.mock_stm32_serial --port COM2 --demo-arm --log-file "$env:TEMP\armwrestling-stm32-v1.jsonl"
```

```powershell
# 窗口二：PC 上位机，打开成对的 COM1
.\.venv\Scripts\python.exe -m real.run --port COM1 --log-file "$env:TEMP\armwrestling-pc-v1.jsonl"
```

打开仓库同级 `UnityUdpDemo` 工程，场景 `Assets/Scenes/UdpDemo.unity`，点击 Play → Game → running。不要启动 `sim.mock_vr`。`--demo-arm` 仅显式授权软件模拟器在新会话中允许制动，不是实机参数。

暂停、等待、结束和故障都可能导致零阻力，终端会分别说明原因；零阻力不会强制机械角度静止，模拟人仍周期性施力。两个终端 Ctrl+C 退出。

未来接真实 STM32，停止模拟器，在 PC 命令中把 COM1 换为设备实际端口；真实设备仍须实现并确认 V1 固件状态机及本地保护，不运行 `--demo-arm`。Unity UDP V0.1 和公共控制算法不用因此改变。本次没有接实体设备，也不是硬件安全或固件认证。

## 实际验证与复现

2026-11-15，Windows 11、Python 3.11.5、pyserial 3.5、Unity 2022.3.62f3c1：

- Python 测试 46 项：45 通过，1 项 Linux PTY 专用测试按平台跳过。
- 实际 COM 协议检查 6 项通过：握手与 CRC 解码；制动指令/遥测与序号回绕；坏 CRC/错误会话/重复包拒绝；200 ms 看门狗；超时后 ENABLE 不直接恢复；新会话不继承旧制动目标。
- 真正的 Unity Editor Play + COM1/COM2 + `real.run` 完成 23 项检查，覆盖四种比赛状态、角度/转轴映射、域重载、模拟器退出/重启、新会话恢复、Unity Stop/Play、VR 超时、PC 退出/重启，以及 V1 指令实际产生非零模拟制动。
- 独立代码复核未发现阻断问题。新增了迟到 ENABLE 先触发看门狗再决定是否接收的回归测试。

完整原始证据在独立工程 `TestResultsSerial/`，与早期 TCP 的 `TestResults/` 分开。仓库摘要见 [串口测试记录](test-results/2026-11-15-windows-serial.json)。没有用 Python 模拟 VR 的结果代替 Unity 测试。图形按钮由编辑器自动调用相同状态方法验证，未逐个模拟鼠标点击；Linux PTY 路线本次没有在 Windows 上实际运行。

复现前关闭该工程的编辑器实例以及手动演示的两个 Python 程序，避免抢占端口或工程锁：

```powershell
.\.venv\Scripts\python.exe -m unittest common.test_console sim.test_serial_device common.test_serial_protocol real.test_stm32_link common.test_bridge sim.test_bridge_clients sim.test_bridge_integration sim.test_vr_udp sim.test_virtual_serial -v
.\.venv\Scripts\python.exe tests/serial/check_windows_v1.py --pc-port COM1 --stm-port COM2 --results-dir '..\UnityUdpDemo\TestResultsSerial'
.\.venv\Scripts\python.exe tests/unity/run_windows_integration.py --unity 'D:\Unity\Hub\Editor\2022.3.62f3c1\Editor\Unity.exe' --project '..\UnityUdpDemo' --serial-pc-port COM1 --serial-stm-port COM2 --results-dir '..\UnityUdpDemo\TestResultsSerial'
```

## 调查依据

- [NN/g：Visibility of System Status](https://www.nngroup.com/articles/visibility-system-status/)：状态反馈应让使用者理解发生了什么并决定下一步。本项目据此把状态、原因和可执行提示放到原始数值之前。
- [NN/g：Usability Heuristics](https://media.nngroup.com/media/articles/attachments/Heuristic_Summary1_Letter-compressed.pdf)：采用使用者熟悉的语言，错误提示说明问题并给出操作方向。因此使用中文、单位和明确原因，而非只展示内部字段名。
- [Microsoft：Console Virtual Terminal Sequences](https://learn.microsoft.com/en-us/windows/console/console-virtual-terminal-sequences)：使用系统 VT 光标/清行和屏幕缓冲区功能实现固定刷新，不增加第三方终端库。
- [pySerial 官方 API](https://pyserial.readthedocs.io/en/latest/pyserial_api.html)：原生 COM、串口参数和非阻塞读取接口；本次用 `Serial(COMx)`，未用 `socket://` 或 RFC2217。
- [Electronic Team：创建虚拟串口对](https://www.virtual-serial-port.org/user-guides/create-com-port-pairs.html)：Windows 虚拟端口对连接两个串口程序。本机已装的旧版驱动以实际双向读写结果为依据，未据新版产品文档宣称它在所有 Windows 环境兼容。
