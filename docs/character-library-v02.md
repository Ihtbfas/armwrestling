# 角色库 V0.2：人物选择与阻力建立

> 本文记录 V0.2 提交 `10a368f` 的行为和历史数据。当前版本已加入 [V0.3 疲劳与恢复](character-library-v03.md)，保留相同建立模块，运行目标还会受到疲劳能力约束。

这版完成两件事：用现有 Windows Unity 工程实际验证选角，并在 PC 共用状态层加入简单的阻力建立过程。人物资料和 V0.1 的静态 K、阻力上限保持原样。

年龄、性别和训练经历仍是虚构人物背景，没有参与参数推算。下面的 tau 是手工指定的等效建立时间常数，不是实测神经反应时间，也没有独立反应延迟、疲劳或恢复模型。

## 参数与输出

| 人物 | tau，秒 | −0.4 rad 时静态目标，N·m |
|---|---:|---:|
| `demo` | 0 | 0.300 |
| `person_01` | 0.15 | 0.150 |
| `person_02` | 0.40 | 0.225 |
| `person_03` | 0.80 | 0.270 |

`common/characters.py` 的 `rise_time_constant_s` 保存这些值。`demo` 为 0，逐次输出与原纯控制器相同，不经过缓升。

其余人物在正常控制中先用原 `brake_state()` 算出 `M_base`，再建立当前幅值 M：

```text
alpha = 1 - exp(-dt / tau)
M_next = M_prev + alpha * (M_base - M_prev)
```

这条公式只用于当前输出低于目标时。新目标低于已经建立的幅值时，立即降到新目标；进入自由区立即为 0，不拖着旧阻力缓慢衰减。目标虽有降低、但仍高于当前输出时，可以继续在新上限以下缓升。

`common/strength_response.py` 只保存当前幅值和上次更新时间。`BridgeState` 使用实际传入的 `now`，不假定循环总是 20 ms。非 demo 第一个正常 tick 从 0 开始；相同时刻重复调用不会增加幅值。连续控制间隔超过 0.25 秒、时间倒退或响应时间非有限时，从 0 重新建立，不补算未观测时段。

暂停、等待、结束、设备未就绪/故障、机械遥测或 VR 超时都立即输出原 STOP/0，并清除建立状态。断开清理也复位；恢复后非 demo 从 0 开始。有效的暂停或故障消息即使在两个输出 tick 之间被后续消息替换，也会清除积累。输入曾过期但在下一次输出前刚好刷新，同样不能沿用旧积累。demo 恢复时仍保持原来的立即输出行为。

输出始终约束在 `0 <= M <= M_base`。有符号力矩继续使用原 `passive_torque()`，方向由当前角速度决定；静止时无静态保持力矩，制动功率不为正。`common/control.py` 未修改。

## 数据对照

下表使用同样的角度 `−0.4 rad`、速度 `−0.2 rad/s`，运行开始时刻为 0；数字是 PC 当前下发的幅值，单位 N·m：

| 时间，秒 | demo | person_01 | person_02 | person_03 |
|---:|---:|---:|---:|---:|
| 0 | 0.300000 | 0 | 0 | 0 |
| 0.10 | 0.300000 | 0.072987 | 0.049770 | 0.031726 |
| 0.20 | 0.300000 | 0.110460 | 0.088531 | 0.059724 |
| 0.50 | 0.300000 | 0.144649 | 0.160536 | 0.125479 |
| 1.00 | 0.300000 | 0.149809 | 0.206531 | 0.192644 |
| 2.00 | 0.300000 | 0.150000 | 0.223484 | 0.247837 |

完整 [CSV](test-results/2026-11-16-strength-response.csv) 共 404 行，包含时间、人物、tau、静态目标、建立后幅值、达到目标的比例、有符号力矩及功率。它通过实际 `BridgeState` 计算，机械遥测间隔 20 ms，VR 心跳间隔 200 ms；是确定输入下的软件模型对照，不是人体或制动器测量。

以下是 V0.2 时使用的生成脚本。当前分支运行它会计算当前共用模型，输出另存，避免覆盖历史 CSV；精确复现本文数据需使用 V0.2 提交：

```powershell
.\.venv\Scripts\python.exe tests/characters/response_table.py --output current-strength-response.csv
```

## 人物选择与通信

沿用 V0.1：PC 首条有效 VR 消息确定初始人物；此后 running 中锁定，waiting/paused/finished 才确认新人物。切换后不继承前一个人物的建立状态。未知 ID 解析到 demo，也不能绕过 running 锁定。

两种 PC 入口 `common.bridge` 和 `real.run` 都使用 `BridgeState`，没有在某个网络入口单独实现模型。UDP 仍只收原五个字段、回原六个字段；串口 V1 仍传原来的幅值和模式，没有增加 tau 或角色字段。

PC 本地日志增加 `base_target_torque_nm` 和 `character_rise_time_constant_s`。原 `target_torque_nm` 是当前建立后实际交给下位机的计算幅值，停止时两者为 0。中文面板分别显示“静态目标”“当前下发阻力”和建立常数；下位机回报目标仍不等于传感器实测力矩。

## Windows Unity 实测

使用本机原有 `UnityUdpDemo/Assets/Scenes/UdpDemo.unity`，没有重建场景，没有修改网络客户端、摆臂组件或 VR 同学的项目。仅在 `tests/unity/Editor/ArmWrestlingDemoTools.cs` 增加测试入口和设置已有 `characterId` 字段的命令。

先在接入模型前验证 V0.1，再在接入后复测 V0.2，各有 33 项检查通过。编辑器实际进入 Play，由 C# 原客户端发送 UDP；测试程序不运行 `sim.mock_vr`。状态按钮通过相同的 `SetMatchState()` 自动调用，未逐个模拟鼠标点击。

前半段实际运行 `common.bridge`、`sim.mock_stm32` 和 Unity，验证 person_02 的 waiting→running、运行中请求 person_03 仍用 person_02、paused 确认 person_03、demo 可用。随后停止移动模拟器，确认空角度/STOP，再用测试程序接管 TCP 遥测来源，固定为 `−0.4 rad / −0.2 rad/s`，逐个人物验证。

V0.2 中三个示例逐步接近各自静态目标，验收误差为 0.001 N·m；demo 立即保持原目标。还检查实际 Unity 数据报字段集合、模型角度映射、暂停归零、JSON 检查及编辑器退出码。原始证据分别保存在本机工程的 `TestResultsCharactersV01/` 和 `TestResultsCharactersV02/`；仓库摘要见 [V0.2 验证记录](test-results/2026-11-16-characters-v02.json)。

复现前关闭该工程的其他编辑器实例及占用 TCP 9101、UDP 9102 的手动演示。在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe tests/unity/run_character_check.py --unity 'D:\Unity\Hub\Editor\2022.3.62f3c1\Editor\Unity.exe' --project '..\UnityUdpDemo' --results-dir '..\UnityUdpDemo\TestResultsCharactersV02' --label V0.2
```

工具自动启动并清理自己的 PC、移动模拟器和编辑器进程。它要求独立测试工程已有场景，并包含最新的 Editor 测试工具；不创建或改写场景。

手动观察只需启动原来的两个 Python 终端，在 Unity 的 `ArmWrestlingUdpClient` Inspector 中填人物 ID，以 waiting 开始，再点 running。看 PC 的请求/生效人物和静态/建立后幅值。running 中改 ID 不会立即切换，先点 paused 再 running。使用移动模拟器时目标也随角度变化，人物时间常数的定量比较请看固定输入 CSV。

## 回归检查与范围

```powershell
.\.venv\Scripts\python.exe -m unittest common.test_strength_response common.test_characters common.test_bridge common.test_console common.test_serial_protocol real.test_stm32_link sim.test_serial_device sim.test_bridge_clients sim.test_bridge_integration sim.test_vr_udp sim.test_character_integration sim.test_virtual_serial -v
```

本次 Windows Python 测试 67 项：66 通过，1 项 Linux PTY 专用测试跳过。新增检查覆盖 dt=0、不同更新时间分割、时钟异常、下降/自由区、已建立非零阻力后的所有停止原因、恢复、角色锁定/切换及被动功率。独立复核没有发现阻断问题。

V0.1 测试中非 demo 的瞬时输出断言已明确改为“原静态目标＋新的启动/增长断言”；原静态数值、人物锁定和停止断言保留。串口适配器仍在建立到稳态后验证精确的 225 mN·m，并保留暂停 STOP/0。真实 socket 测试仍检查原目标，采用有限时间建立误差 0.001 N·m，保留字段集合、模式和功率方向检查。

这次验证的是 PC 演示模型与真正的 Windows Unity 通信。没有进行新的实体 STM32、人体、磁粉制动器标定或头显测试；实机全局上限、本地保护及后续参数依据仍需与 VR、STM32 和机械组继续联调。
