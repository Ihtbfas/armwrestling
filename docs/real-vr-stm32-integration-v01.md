# 真实 VR / STM32 联调：交接与最小流程

2026-11-17：用户确认实体 STM32 和正式 VR 工程尚未到位。本阶段完成接口交接、启动命令和验收准备；真实场景 UDP、实体板串口、制动器台架均为**未执行**。V0.3 人物模型、UDP V0.1 和串口 V1 字节格式保持不变。

两条线可以分别推进：

| 路线 | 使用什么 | 这一阶段验收什么 |
|---|---|---|
| A：正式 VR | 正式场景 + PC `common.bridge` + `sim.mock_stm32` | 正式客户端的 UDP、角度映射和比赛状态 |
| B：实体 STM32 | 实体板 + PC `real.run`，先不启动 VR | COM、OPEN/ACK、有效遥测与零目标 STOP |
| C：合并 | 正式场景 + PC `real.run` + 实体板 | 前两条完成后检查完整状态链路 |

只做 A 不需要串口或实体板；只做 B 不需要 Unity。制动器接入和台架标定另行验收，不因 A/B 通信通过而自动获准开展。

## 联调前双方各需提供什么

| 对接方 | 最少信息 |
|---|---|
| VR | 工程路径/提交、Unity 或团结引擎版本；Editor/Windows/Android/PICO 及构建后端；运行设备和 PC 的 IP；本地 UDP 端口如何分配；手臂转轴、机械零位到场景零姿态的映射 |
| STM32 | 板卡和固件提交；USB CDC 或 USART+转串口、实际 COM、串口参数；OPEN/ACK、遥测、STOP、序号和看门狗的实现情况；READY/ARM_READY/故障位来源；编码器零位、方向及主轴单位换算 |
| 下位机与电气组，接制动器前 | 硬件限幅、急停、机械限位、独立保护及人工重新使能的确认人和记录；断联/断电动作；经确认的台架工况和测量方法 |

PC 交给对端的是 [UDP V0.1](vr-udp-v01.md) 和 [串口 V1.1](stm32-serial-v11.md)。[下位机首次交接包](../handoff/stm32_v11/START_HERE.md)包含 C 参考和黄金帧。完整固件由下位机组接入，实际传感器和硬件授权待双方确认。

## A：正式场景先与模拟机械联通

在仓库根目录打开两个 PowerShell：

```powershell
# 窗口一：PC，默认 TCP 9101 + UDP 9102，保存 PC 本地状态
.\.venv\Scripts\python.exe -m common.bridge --log-file "$env:TEMP\arm-vr-pc.jsonl"
```

```powershell
# 窗口二：持续提供机械角度，接收 PC 模拟制动指令
.\.venv\Scripts\python.exe -m sim.mock_stm32
```

同机正式 VR 客户端连接 `127.0.0.1:9102`。双方使用 UTF-8、无 BOM、一包一个 JSON；VR 用同一个 socket 收发，每约 200 ms 发完整状态，PC 约 50 Hz 回传。不要同时启动测试 Unity 或 `sim.mock_vr`，PC 同时只接受一个 VR 源端点。

最小请求示例，后续心跳必须继续增加 seq：

```json
{"v":1,"type":"vr_update","seq":0,"character_id":"demo","match_state":"waiting"}
```

回包只有 `v/type/seq/angle_rad/mode/reason`，没有角速度、疲劳或力矩。客户端必须区分 `angle_rad=0` 与 `angle_rad=null`；后者保留最后有效姿态并提示无效。`mode=stop` 时角度仍可能有效，暂停不要求机械画面冻结。`mode=enabled` 只说明 PC 在正常计算，不代表设备已获得制动许可。

建议按表顺序操作，每项在 [记录模板](real-integration-record-template.md) 中保存结果和日志时段：

| 项目 | 操作 | 验收 |
|---|---|---|
| A1 双向收发 | demo / waiting 持续心跳 | PC 收到正确编号和状态；VR 收到 stop / match_waiting，双方序号增长 |
| A2 角度 | 观察模拟机械角度变化 | rad 转度正确，0 为约定零姿态，负角为获胜侧；转轴正确。世界坐标对齐另行记录 |
| A3 状态 | waiting → running → paused → running → finished | 正常时 enabled/running；暂停、结束为 STOP/0；有效角度可以继续变化 |
| A4 选角 | waiting 选 person_02；running 请求 person_03；paused 确认；waiting 新回合 | PC 本地日志先保持 person_02，再确认 person_03。VR 回包没有生效人物字段，需对照 PC 日志；未知 ID 按 demo 回退 |
| A5 无有效角度 | 停止模拟 STM32，VR 继续心跳 | PC 停止计算，回 angle_rad=null；reason 可能为 telemetry_missing 或 telemetry_timeout，不能把空值当零位 |
| A6 心跳中断 | 停止 Play/发送超过 1 秒，再启动；同时试 PC 重启 | PC 本地记录 vr_timeout/STOP；VR 自行检测接收超时；重开后接受新会话序号并先发送 waiting，再恢复有效状态 |

VR 超时后 PC 会释放旧源端点，**不保证继续向旧客户端回传 vr_timeout**。新客户端重开 socket 或重置 seq 时，应让旧心跳停止超过 1 秒，避免仍被旧会话占用；不要只发一次 running 就停止心跳。PC 重启会重置其 status 序号，VR 应在接收超时后重建接收会话，不能永远用旧最大序号拒绝新 PC。

V0.3 的非 demo 阻力先缓升、再受疲劳能力约束，不要求持续达到静态目标。paused 立即停止并逐渐恢复疲劳，waiting 清疲劳准备新回合，finished 保留；故障/断联冻结疲劳时钟。具体数值只查 PC 本地日志，规则见 [V0.3](character-library-v03.md)。

### 跨电脑或头显时的地址

先完成同机，再改成可信局域网。`127.0.0.1` 只指向各自设备。以下 IP 只是示例，必须换成 PC 当前可用网卡地址：

```powershell
# 窗口一；--host 同时绑定 TCP 和 UDP
$pcLanIp = '192.168.1.10'
.\.venv\Scripts\python.exe -m common.bridge --host $pcLanIp --log-file "$env:TEMP\arm-vr-lan-pc.jsonl"
```

```powershell
# 窗口二；使用与 PC 绑定地址相同的实际 IP
$pcLanIp = '192.168.1.10'
.\.venv\Scripts\python.exe -m sim.mock_stm32 --host $pcLanIp
```

VR 目标为 PC 的该 IP 和 UDP 9102，本地 socket 需绑定可路由的本地网卡或任意本地地址。参考 `tests/unity/ArmWrestlingUdpClient.cs` 当前绑定 `IPAddress.Loopback`，只改 host 不足以用于跨设备；这里不修改参考脚本，也不把 Editor 测试当作 Android/IL2CPP 验证。

核对两端 IP、VR 实际源端口、私有网络防火墙是否允许约定对端的 UDP 9102；回复发往 VR 的源端口，不是固定发给 VR 的 9102。无需对外开放 TCP 9101 给 VR，更不需公网映射。具体防火墙规则在设备/IP 到位后设置；本次未更改系统规则。

## B：实体 STM32 先串口与遥测，不接制动功率

下位机组先确认励磁功率未连接、无人手接触设备，提供固件版本及端口。不要把本机虚拟 COM1/COM2 或蓝牙 COM 当作 STM32；连接实板后根据设备信息和插拔前后枚举确认：

```powershell
.\.venv\Scripts\python.exe -m pip install pyserial==3.5
.\.venv\Scripts\python.exe -m serial.tools.list_ports -v
```

关闭占用该 COM 的串口助手。首次只启动 PC，不运行任何 VR 客户端，使用单独测试端口与原演示隔离。COM9 为示例，必须替换：

```powershell
.\.venv\Scripts\python.exe -m real.run --port COM9 --baudrate 115200 --host 127.0.0.1 --vr-port 19102 --monitor-only --duration 60 --log-file "$env:TEMP\arm-stm32-first-link.jsonl"
```

19102 只用于这次无 VR 检查，不改变正式 UDP 9102 约定。也可启动 Tk 面板，保持默认的仅 STOP。串口端主动发送 OPEN，握手后仅写出 STOP/0，UDP 请求不会使能制动。物理隔离由下位机/电气组落实，命令参数不能代替隔离或独立保护。

| 项目 | 检查 | 验收 |
|---|---|---|
| B1 打开与握手 | PC serial_open、serial_session_ready；固件收到 OPEN 并回相同 session_id | 不能只凭“COM 已打开”通过；CRC、类型和会话编号匹配 |
| B2 遥测 | 先输出已知软件角度，再检查真实编码器（若可安全转动） | telemetry_fresh=true；−400 mrad 对应 −0.400 rad，−200 mrad/s 对应 −0.200 rad/s；零位、符号、主轴换算一致 |
| B3 未使能状态 | 固件保留 ARM_READY=0，不为通过画面而授权 | 会话和角度仍可正常，serial_arm_ready=false、mode=stop、目标 0；`serial_status_reason=unarmed` 表示本地未授权 |
| B4 STOP 与回报 | 固件记录实际收到的命令、模式和序号 | 匹配会话的 STOP 携带 0；applied_target_mnm 为本地目标回报，不能称为实测力矩 |
| B5 串口中断 | 在无制动功率条件下停止遥测/拔插、重启 PC 或板卡 | PC 清除有效机械数据并重新握手；固件丢弃旧命令/会话。观察 `serial_session_restart` 及重连后的 STOP 确认 |
| B6 固件本地保护 | 对照原协议确认 CRC/旧序号/旧会话/看门狗处理；记录固件侧证据 | 无效帧不续命；命令失效清目标和 ARM_READY；握手、故障消除和 VR running 均不代替人工重新使能 |

通信验收先看会话和本会话 STOP 确认，再看板端的角度采样是否推进、`ANGLE_VALID` 是否有效。保持 COM 打开重启板卡，确认上位机生成新会话、再次收到 STOP 回报，比赛停在等待或暂停。

面板可导出 PC 诊断和逐帧实验日志。FORCE_VALID=0 时传感器力无效；STM32 回报目标与实测力矩分别记录。第 4 节的六条黄金帧供离线核对。

纯 PC 协议/状态回归可独立运行，不打开真实 COM：

```powershell
.\.venv\Scripts\python.exe -m unittest common.test_serial_protocol real.test_stm32_link common.test_fatigue common.test_characters sim.test_vr_udp -q
```

`tests/serial/check_windows_v11.py` 会发送非零 ENABLE 并使用模拟授权，仅用于软件虚拟 COM 对，**不要将它的端口改为实板来做首次测试**。`--demo-arm` 只属于模拟设备，实机不能据此获得许可。

## 接制动器与合并联调

串口/遥测通过后，由下位机与电气组确认硬件限幅、物理急停、机械限位、独立保护和人工重新使能，才进入无人接触的台架。先验 STOP、急停、故障、PC 被杀、USB 拔插和命令超时等停止路径，再按双方确认的低风险工况标定电流、制动器轴侧与主轴侧力矩，记录 4:1 传动、损耗、有效力臂和测量不确定性。硬件确认和标定完成之前不做人体扳手腕实验。

合并时关闭 `common.bridge` 和所有模拟器，PC 改用 `real.run --port 实际COM --vr-port 9102`；同机 VR 用回环地址，跨设备时另加 `--host PC局域网IP`。先 demo / waiting，再逐步检查 running、paused、finished 和其他人物。V0.3 不是硬件接入前提，不等待继续扩展人物模型。

日志中区分：静态 M_base → 疲劳后 M_eligible → PC 下发 M_output → MCU 回报目标 → 台架实测力矩。前四项不能当作第五项。每完成 A 或 B 的真实验证，就保存记录、故障和双方版本，独立提交，不等待另一条线完成。

## 当前准备结果与遗留

本阶段只修改说明，不添加控制模式、协议字段、正式 VR 功能或占位驱动。命令与当前 CLI 对照过；测试结果见 [准备记录](test-results/2026-11-17-real-integration-preparation.md)。实体 STM32 固件与端口、正式 VR 工程和跨设备网络配置待对端提供。

MuJoCo 的 Windows WinError 1114 沿用 [V0.3 测试记录](character-library-v03.md)，单独待办，不阻塞本流程。设备到位后复制 [结果模板](real-integration-record-template.md)，未执行项保持未执行，不把软件模拟结果填成真实通过。
