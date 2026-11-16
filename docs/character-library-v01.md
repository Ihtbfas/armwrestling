# 角色库 V0.1

本页保留人物资料、静态参数和选角规则。V0.2 已增加人物建立时间常数并完成真正的 Unity 选角测试，见 [V0.2 说明](character-library-v02.md)。下面表格是静态目标，非 demo 的当前输出需逐步建立，不能再当作 running 第一帧的输出。

角色库放在 `common/characters.py`。每个人物有 ID、调试名称、年龄、性别、训练背景，以及当前可执行的基础阻力参数。参数由 PC 保存，VR 继续只发送原有 `character_id`。

这里的三个人物是虚构示例。年龄、性别和训练经历用于描述人物，代码没有根据这些信息推算力量。刚度和上限是手工指定的演示值，不是人群平均值、人体测量结果或硬件标定后的安全限值。此版没有发力策略库、疲劳或肌肉模型。

## 示例人物和参数

| character_id | 调试名称 | 年龄 | 性别 | 训练背景（虚构） | K，N·m/rad | 目标阻力上限，N·m |
|---|---|---:|---|---|---:|---:|
| `demo` | 原控制基线 | 未设定 | 未设定 | 无人物设定 | 2.0 | 0.80 |
| `person_01` | 示例人物甲 | 24 | 女 | 休闲运动，未做专项训练 | 1.0 | 0.20 |
| `person_02` | 示例人物乙 | 38 | 男 | 规律力量训练 | 1.5 | 0.35 |
| `person_03` | 示例人物丙 | 52 | 女 | 有力量训练经历 | 1.8 | 0.50 |

人物资料与参数集中在只读字典中，人物记录和 `BrakeParameters` 都不可原地修改。替换参数时编辑这一处即可，后续有测量依据后再更新数值及来源。当前所有记录的 `parameter_source` 均为 `demonstration_only`。

`demo` 直接使用原来的 `BrakeParameters()`。其余人物只改 K 和最大目标阻力；零位、方向、自由边界 `0.25 rad`、速度过渡 `0.05 rad/s` 均保持原值。三个示例在同一机械状态下的目标阻力都不高于 demo；这只是相对基线的演示约束，不表示已验证实机或人员安全。

以角速度 `−0.2 rad/s` 比较目标阻力：

| 角度，rad | demo | person_01 | person_02 | person_03 |
|---:|---:|---:|---:|---:|
| −0.20 | 0 | 0 | 0 | 0 |
| −0.25 | 0 | 0 | 0 | 0 |
| −0.40 | 0.300 | 0.150 | 0.225 | 0.270 |
| −0.55 | 0.600 | 0.200 | 0.350 | 0.500 |

表内单位为 N·m。只显示目标幅值；有符号制动力矩仍由原控制算法按速度决定，静止时不提供静态保持力矩。可直接打印同样的对照表：

```powershell
.\.venv\Scripts\python.exe -m common.characters
```

## 什么时候确认人物

选角在 `BridgeState.receive()` 中完成。消息先通过原有字段和递增 seq 校验，然后才可能更新人物：

1. PC 启动后，首条有效 VR 消息确定初始人物，包含直接以 running 启动的情况。这样已有客户端无需增加初始化消息。
2. 人物确认后，running 消息只更新比赛心跳，人物参数保持不变。不同 ID 的请求会显示为“待确认”，不会自动排队生效。
3. 收到 waiting、paused 或 finished 时，按这条完整消息里的 `character_id` 确认人物。后续 running 应继续发送同一 ID。
4. 未知 ID 按 demo 解析；非 running 时可确认回退。running 中出现未知 ID 仍遵守锁定规则，不能突然把当前人物换成阻力更大的 demo。

例如：`person_01 / running` → `person_03 / running`，仍按 person_01 计算；再发 `person_03 / paused`，控制先停止并确认 person_03；随后发 `person_03 / running` 才按它计算。

VR 超时、换 UDP 源端口、机械数据断开或恢复，都不会代替一次非 running 的选角确认。PC 进程重新启动则开始新的初始选择。停止、数据过期、设备故障等判断仍先于阻力计算执行。

## 从 Windows 验证

在仓库根目录打开两个 PowerShell，分别运行原入口：

```powershell
# 终端一：PC
.\.venv\Scripts\python.exe -m common.bridge
```

```powershell
# 终端二：模拟机械数据源
.\.venv\Scripts\python.exe -m sim.mock_stm32
```

再开一个终端，使用原来的模拟 VR 客户端，只多指定本地命令行参数：

```powershell
.\.venv\Scripts\python.exe -m sim.mock_vr --character-id person_02 --demo-states
```

`--demo-states` 循环运行 3 秒、暂停 1 秒。新启动的 PC 可立即接受初始人物；若 PC 原来已锁定其他人物，第一次 paused 才会确认 person_02。目标阻力随角度变化，不能用两个不同时刻的角度直接推断人物强弱；固定角度的对照用上面的表或自动测试。

要试其他人物，停止这个 VR 测试进程，再用其他 `--character-id` 重启；等待原 UDP 来源超时，并观察下一次暂停后的生效人物。未知 ID 也可按同样方法验证。不要和 Unity 或第二个 VR 客户端同时运行。

PC 中文面板显示原请求编号及“生效人物”。运行中请求不同人物会提示“本回合保持原人物”；未知编号会明确提示按 demo 处理。完整 JSON 日志另有以下诊断字段：

- `active_character_id`、`active_character_name`：已确认的人物。
- `character_fallback`：当前有效请求的 ID 是否未知。
- `character_selection_deferred`：当前请求解析得到的人物是否与已确认人物不同。
- `character_stiffness_nm_per_rad`、`character_max_torque_nm`：当前已确认人物的基础参数。

这些字段只用于 PC 本地状态和日志，未加入 UDP 或 STM32 指令帧。VR 断开后原 `character_id` 仍按旧规则显示为空，已确认人物保留供恢复时继续使用。

如果用现有 Unity 自测工程，在 `ArmWrestlingUdpClient` 的 Inspector 中设置 `characterId`，以 waiting 开始，再点 running。运行中修改编号不会立即换参数，须发送一次非 running 状态。无需改 C# 脚本、VR 场景或 UDP 报文结构。本次人物选择验证使用 Windows Python 和真实 TCP/UDP socket，没有重新声称完成 Unity、实机或人员测试。

## 验证范围

```powershell
.\.venv\Scripts\python.exe -m unittest common.test_characters common.test_bridge common.test_console common.test_serial_protocol real.test_stm32_link sim.test_serial_device sim.test_bridge_clients sim.test_bridge_integration sim.test_vr_udp sim.test_character_integration sim.test_virtual_serial -v
```

新增检查包括 demo 的原输出、同角度人物差异、被动性与基线上限、首次选择及运行中锁定、未知 ID、重连/故障恢复、不合格消息不刷新状态，以及每个人物的暂停、结束、数据过期和故障停止。真实 PC 子进程使用固定机械遥测和现有 UDP 字段验证选择、回退与保护；串口适配器测试确认 person_02 的 `0.225 N·m` 编成原 V1 的 `225 mN·m`，暂停后发 STOP/0。

Windows 测试结果及最小三进程演示记录见 [角色库验证记录](test-results/2026-11-16-characters.json)。Linux PTY 专用测试在 Windows 上跳过；新的实体 STM32、Unity 选角、硬件全局上限与参数标定留待后续实际联调。
