# V0.4 独立对抗策略库

V0.4 将人物身体能力和比赛中的用力方式分开。人物库维持 V0.3 的静态阻力、发力建立、疲劳和恢复参数。策略决定每个控制周期使用多少静态目标。比赛开始前由 PC 进程选择策略，正式 VR 仍通过原有人物编号和比赛状态控制回合。

## 启动和选择

在 PC 仓库根目录运行。省略 `--strategy` 时选择均衡。

```powershell
.\.venv\Scripts\python.exe -m common.bridge --strategy rhythmic --log-file "$env:TEMP\arm-v04-pc.jsonl"
```

在另一个终端用同一人物重复闭环输入。序列从模拟 STM32 收到首条有效 ENABLE 开始，幅值仅设置模拟用户施力。

```powershell
.\.venv\Scripts\python.exe -m sim.mock_stm32 --torque 0.065 --sequence release:1.2,down:0.66,release:1.2,back:0.22,release:1.72
```

在 [正式 VR 工程](https://github.com/Ihtbfas/armwrestling-vr/commit/129c989035b7657af526f9eec9bfe6eebea50e56)打开 `Assets/Scenes/ArmWrestlingMain.unity`，选择网络模式和 **乙（person_02）**。换策略时重启 PC 桥接和序列模拟器，从软件零位开始下一次试验。手动操作可改用 `--manual`，按 `1` 下压、`2` 回推、`0` 松手、`R` 软件回零。

串口入口 `python -m real.run --port COMx --strategy defensive` 使用同一个控制层。该命令沿用 [首次接板和独立使能流程](real-vr-stm32-integration-v01.md)。

## 策略与计算

| 选项 | 使用比例 | 参数依据 |
|---|---|---|
| `balanced` 均衡 | 始终 1 | V0.3 精确对照 |
| `defensive` 防守 | 由 0.60 线性升到 1 | 从原自由区边界 0.25 rad 升到当前 VR 演示判定的机械推进约 0.4363 rad，过此点维持 1 |
| `rhythmic` 节奏 | 0.65–1 的余弦周期，4 s 一周期 | 首个有效时刻从 1 开始，随后平滑减弱再增强 |

这些比例和周期沿用任务书中的演示值。固定遥测能清楚分辨三种响应，因此只调模拟施力工况。防守阈值取自现有软件场景的机械角度映射，供演示使用。人物年龄、性别与训练经历不参与策略计算。选 demo 时仍得到原基线。

```text
策略请求 = 原静态目标 × 使用比例
疲劳负载 = clamp(策略请求 / 人物阻力上限, 0, 1)
疲劳后目标 = 策略请求 × 原剩余能力系数
实际下发 = 原发力建立模块(疲劳后目标)
```

阻力始终满足 `0 ≤ 下发 ≤ 疲劳后目标 ≤ 策略请求 ≤ 原静态目标`，自由区为零，原速度规则继续保证被动制动。疲劳负载是模型内部比例，数值与实测用户力或实际制动力矩分开解释。

有效比赛时间只累计无中断的 running 间隔。首次有效周期建立时间锚点。重复时间、时间回退和超过 0.25 秒的间隔不补算。暂停立即输出 STOP/0，保留节奏相位并按原规则恢复疲劳。结束、故障和通信中断保留相位。waiting 或确认另一人物时清零。PC 状态日志可查 `strategy`、`strategy_scale`、`requested_target_torque_nm` 和 `active_match_time_s`。停止周期没有策略请求，日志中的比例和请求量为 0。两套通信沿用现有字段。

## 实测结果

- 固定遥测使用同一人物和机械输入验证阻力、相位、疲劳、暂停和新回合。闭环对照使用幅值 0.065 N·m 的相同用户施力序列，三种策略都在原阻力区持续约 3.08 秒，均未触及机械限位。曲线、选参记录、完整表格和复现命令见 [策略对照](test-results/2026-11-25-v04-strategy-comparison.md)。
- Windows 相关 Python 测试运行 92 项，91 通过，Linux PTY 1 项按平台跳过。两种 CLI 均拒绝未知策略。用冻结的 P3.0 控制层做 6,000 步等值比较，覆盖均衡三个人物及三种启动选项下的 demo，原控制指令、V0.3 诊断和 UDP 回包逐步相等。原 P3.0 的 3,008 条采样 CSV 也复现相同散列。
- 正式场景复验使用 VR P3.0 的 `129c989` 和原有 P3Validation Editor 流程。测试启动器本地传入 `--strategy balanced` 或 `--strategy rhythmic` 给 PC。每组 5 局、35 个检查点，均为 0 个运行错误。demo 仍用基线，同一人物 person_02 在 PC 日志中分别显示两种生效策略。两组都完成暂停继续、故障恢复和退出后 STOP/0。这项复验使用 P3.0 的 0.8 N·m 快速完赛输入，策略差异以固定遥测和 0.065 N·m 对照为准。
- `real.run --strategy defensive` 在本机 ELTIMA COM1/COM2 与串口模拟器完成附加 V1 检查。观察到 10 条 enabled 和 2 条暂停归零记录，下位机最大目标回报 0.337 N·m。[Editor 检查点、PC 诊断样本和虚拟 COM 摘要](test-results/2026-11-25-v04-editor.json)

P3.0 的 PC 和正式 VR 提交已分别快进到各自 main。V0.4 的 PC 实现在独立工作分支，复验继续配对 VR main 的 P3.0 提交。跨设备、PICO、实体 STM32 和实际制动器仍待各自测试。
