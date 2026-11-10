# PC 上位机通信桥接 V0.1

本版用三个真实进程验证本机 TCP 闭环。通信部分只依赖 Python 3.10+ 标准库，不要求 MuJoCo、Unity 或串口。A/C/D 机械布局和原控制参数没有修改。模拟 STM32 使用简单标量动力学，不是把 MuJoCo 查看器接到网络，也不是实机固件。

## 目录与启动

```text
common/
  control.py       唯一控制实现；原公式、参数和低速过渡
  protocol.py      JSON 行拆包、数据校验、非阻塞收发
  state.py         最新有效数据、比赛门控和超时状态
  bridge.py        PC 入口，50 Hz 控制与两个 TCP 监听端口
  test_bridge.py   标准库测试
sim/
  control.py       兼容导入 common.control，旧仿真入口不变
  mock_stm32.py    模拟遥测、指令接收、本地看门狗和简单动力学
  mock_vr.py       模拟角色参数/比赛心跳，接收真实角度与状态
  test_bridge_clients.py / test_bridge_integration.py
  ...             原 MuJoCo 仿真及 A/C 模型
real/             本次未修改；真实适配和启动程序待接口确认
```

Ubuntu 三个终端都先执行 `cd /home/lyf/armwrestling`，再分别运行：

```bash
# 终端 1：PC
python3 -m common.bridge
# 终端 2：模拟 STM32
python3 -m sim.mock_stm32
# 终端 3：模拟 VR
python3 -m sim.mock_vr --demo-states
```

Windows PowerShell 三个终端都进入仓库根目录，例如 `cd "$HOME\armwrestling"`，再分别运行：

```powershell
# 终端 1
py -3 -m common.bridge
# 终端 2
py -3 -m sim.mock_stm32
# 终端 3
py -3 -m sim.mock_vr --demo-states
```

通信演示不需要 `pip install`。也可统一使用已存在的虚拟环境 Python（Ubuntu `.venv/bin/python`，Windows `.\.venv\Scripts\python.exe`）。Windows 代码使用标准 socket/selectors 和 `time.monotonic`，没有终端按键、POSIX 信号等平台专用实现；**本次只在 Ubuntu 运行，未声称经过 Windows 实机验证**。三个程序在各自终端用 Ctrl+C 结束；客户端会每约 0.5 s 尝试重连，因此启动顺序不严格。

默认监听 `127.0.0.1:9101`（STM32）和 `127.0.0.1:9102`（VR），每个角色只允许一个连接。PC 端可设 `--stm-port`、`--vr-port`，客户端对应 `--port`；三者都有 `--host`、`--duration 秒数`。端口 0 仅供测试自动分配，用启动 JSON 中的端口连接。

模拟 STM32 约 50 Hz 上报遥测，循环施加用户力矩：下压 -0.15 N·m 3 s，返回 +0.15 N·m 3 s，释放 2 s。使用惯量约 0.054695 kg·m²、阻尼 0.04 N·m·s/rad、2 ms 子步积分和简单角度夹限 [-0.55,0.60] rad；制动指令真实参与计算。这个数值演示没有重力、接触或人体模型，夹限不是机械保护设计。

模拟 VR 每 0.2 s 发送完整心跳。默认一直 `running`；`--demo-states` 则每 4 s 循环运行 3 s、暂停 1 s，可观察 PC 的 `enabled/stop` 切换。也可用：

```bash
python3 -m sim.mock_vr --stiffness 1.5 --max-torque 0.6 --match-state running
```

日志每约 0.5 s 输出一条 JSON：PC/VR 显示角度、q、目标/采样制动力矩、数据年龄、角色和比赛状态；STM32 显示本地实际施加力矩、制动功率和最近指令序号。看到 q>0.25 时目标阻力变大，暂停时为零，便说明链路在工作。只启动 PC 会看到 `telemetry_missing`，不是程序卡住。

## 消息协议

每条消息为一个 UTF-8 JSON 对象，以 LF（`\n`）结尾，发送端不要带 BOM。接收器支持 TCP 拆包、粘包和 CRLF，**一次 recv 并不等于一条消息**。单行不含 LF 最多 8192 字节。所有角度为 rad，速度 rad/s，力矩 N·m，刚度 N·m/rad，时间字段名标注 ms。禁止 NaN/Infinity、数值字符串和把布尔值当数值。

所有消息含 `v:1`、`type`、`seq`。seq 是 0..2^53-1 内的整数，同一连接单调递增，可跳号；重连后允许从零开始。PC 的指令与状态共用 PC 周期序号，`telemetry_seq` 指明计算依据的遥测。未知额外字段忽略，已定义字段必须有效；不存在只更新一部分 VR 参数的消息。

**STM32 → PC，telemetry：**

```json
{"v":1,"type":"telemetry","seq":12,"angle_rad":-0.4,"velocity_rad_s":-0.2,"device_status":"ready"}
```

`device_status` 仅 `ready` / `fault`。角度校验范围 [-100,100]、速度 [-1000,1000]，用于拒绝异常数据，**不是设备允许行程或人体安全范围**。

**VR → PC，vr_update：**

```json
{"v":1,"type":"vr_update","seq":3,"character_id":"demo","match_state":"running","stiffness_nm_per_rad":2.0,"max_torque_nm":0.8}
```

`character_id` 1..64 个有效 Unicode 字符（拒绝孤立代理码）；`match_state` 为 `waiting/running/paused/finished`。本版仅将刚度和上限映射到原控制参数；刚度校验 [0,20]、力矩上限 [0,2]，这些也是演示协议范围，不是实机标定或安全限值。零位、获胜方向、自由边界和低速过渡仍用原默认值，不由 VR 修改。网络仍用 UTF-8，终端日志则对非 ASCII 字符使用 JSON 转义，避免 Windows 旧控制台编码导致输出失败。

**PC → STM32，brake_command：**

```json
{"v":1,"type":"brake_command","seq":45,"mode":"enabled","reason":"running","target_torque_nm":0.3,"brake_torque_nm":0.3,"speed_transition_rad_s":0.05,"valid_for_ms":200,"telemetry_seq":12}
```

`target_torque_nm` 是位置决定的阻力幅值 M；`brake_torque_nm` 是 PC 用采样速度计算的有符号力矩。模式为 `stop` 时两者均为 0。指令不表示 PWM 或电流。

**重要的方向约定：** 模拟 STM32 不直接持续施加可能已过时的有符号力矩，而是用最新有效 M 和自身当前速度调用同一个 `passive_torque` 函数。这样即使网络传输期间已经换向，实际输出仍阻碍当前运动。`brake_torque_nm` 用于核对 PC 计算；它可能与设备此刻的本地实际力矩不同。没有加入新的静态、返回或角色策略。

**PC → VR，status：**

```json
{"v":1,"type":"status","seq":45,"mode":"enabled","reason":"running","angle_rad":-0.4,"velocity_rad_s":-0.2,"q_rad":0.4,"target_torque_nm":0.3,"brake_torque_nm":0.3,"telemetry_seq":12,"telemetry_fresh":true,"vr_fresh":true,"telemetry_age_ms":10.0,"vr_age_ms":80.0,"match_state":"running","character_id":"demo"}
```

过期遥测的角度、速度、q 和 telemetry_seq 返回 `null`；没有收到过数据时 age 也为 `null`。VR 过期时其 match_state/character_id 返回 `null`。消费端必须处理 nullable 数值。终端日志会附加 `elapsed_s`，它不是必需的网络字段。桥接状态没有自动判胜 AI；比赛状态以 VR 为准。

## 控制与超时

```text
q = -(theta - 0)
M = min(Mmax, K * max(0, q - 0.25))
tau_b = -M * clamp(omega / 0.05, -1, +1)
```

只有两个角色的数据新鲜、设备 `ready`、比赛 `running` 时计算制动。静止无保持力矩，越界区返回仍受制动；原有这些行为没有改变。

| 条件 | PC 输出 | reason |
| --- | --- | --- |
| 尚无遥测 / 设备断开 | stop，M=tau=0 | telemetry_missing |
| 遥测年龄 ≥0.25 s | stop | telemetry_timeout |
| 设备上报故障 | stop | device_fault |
| 尚无 VR / VR 断开 | stop | vr_missing |
| VR 年龄 ≥1.0 s | stop | vr_timeout |
| 比赛等待 / 暂停 / 结束 | stop | match_waiting / match_paused / match_finished |
| 两端有效并运行 | enabled，按原公式 | running |

若多个条件同时发生，按上表顺序选一个 reason。PC 的 `--telemetry-timeout` 和 `--vr-timeout` 可配置，必须为有限正数。VR 不需要与每个 20 ms 控制周期同步；没有新心跳但未过期时使用最新有效值。

错误 JSON、错误类型/字段、非有限数或非递增序号会关闭该连接并立即清空对应状态，不影响另一端监听。重复或无效消息不能延长有效期。新连接和新有效数据满足条件后自动恢复，这是模拟阶段的行为，不是硬件故障复位策略。

模拟 STM32 独立检查指令寿命：最后一条有效指令收妥 200 ms 后没有更新，就停止本地模拟制动力；已知断开立即清空指令。允许的协议寿命范围为 20..500 ms，重复序号或错误指令不会续期。PC 被强制结束时也不依赖退出回调来释放旧指令。

网络使用非阻塞收发，每端应用层只保留一条待发消息，不累积旧快照；未发完的半行先完成，随后换最新快照，持续发送受阻超过 0.5 s 断开。这是普通操作系统上的尽力调度和基于**接收时刻**的超时，不能证明网络延迟上界或硬实时安全；内核缓冲及调度仍可能造成延迟。实机若需要严格指令时效，还需双方确认采样关联、时钟/往返延迟及本地下位机策略。

本版“停止输出”仅表示模拟力矩为零，装置可继续惯性运动；不能推导零电流、自由滑行或断电就是实机安全状态。真实磁粉制动器需要另行确认故障处理、电流标定和本地保护。没有引入主动电机或重力补偿。

## 交接给 VR / 下位机同学

团结引擎 C# 可以连接 VR 端口，用一个后台网络读循环持续接收 JSON 行，另按心跳周期发送完整 `vr_update`，然后把最新 `status` 交给主线程更新场景。不要在渲染主线程同步等待网络。发送 UTF-8 无 BOM，以 `\n` 分隔；反序列化时保留上述数值/布尔/null 类型。客户端只需处理本节协议，没有 Python 专用序列化。

当前同机使用 127.0.0.1，无需开放 LAN。若以后跨电脑连接，才调整 PC `--host` 和客户端目标地址并检查 Windows 防火墙；本版无认证、加密或多客户端仲裁，不建议直接暴露到公网。

接真实 STM32 时保留公共控制算法与状态协调，将 TCP 模拟遥测/指令边界替换为已商定的串口格式；届时再引入 pyserial。`real/` 中的真实启动和适配程序尚未实现。编码器方向、零位、单位、时间戳、更新率、电流—力矩标定和失联策略需双方确定。

## 已执行验证

```bash
# 只测标准库桥接，无需 MuJoCo：
python3 -m unittest common.test_bridge sim.test_bridge_clients sim.test_bridge_integration -v
# 在已有 MuJoCo 虚拟环境运行全部测试：
.venv/bin/python -m unittest discover -v
# 原查看器入口：
.venv/bin/python -m sim.run --duration 1
```

Ubuntu 实际三进程运行的 [原始日志与汇总](analysis/bridge-v01/summary.json)：PC 8.5 s、模拟 STM32 9.5 s、模拟 VR 6.5 s，使用 VR 暂停/恢复演示。观察到 `running → match_paused → running`，VR 退出后 `vr_missing`，PC 退出后设备停止输出。PC 日志目标力矩最高约 0.595 N·m；设备累计记录的最大制动功率为 0 W，包含零输出采样，未出现正值。这是标量模拟验证，不是实机被动性的测量。

全套 47 项测试通过（原有 30 项、新增 17 项）；17 项通信测试也在 Python 3.14.6 环境中单独通过。集成测试让两端保持连接但停止更新，分别确认 `vr_timeout` / `telemetry_timeout`；发送错误 JSON 后重新连接，监听器继续工作；检查三进程角度回传与实际制动。单元测试覆盖半包/粘包、超长消息、坏字段、NaN/Infinity/巨大整数、旧序号、暂停/故障、参数更新、指令看门狗和延迟换向。Windows 尚待 VR 同学实机确认。
