# V0.5-A 阻力生效区域比较

本轮用同一个 `person_02` 和均衡策略，比较阻力从哪个角度开始生效。三种方案只改变角度到基础阻力的映射。人物上限、发力建立、疲劳、运动方程和 VR 判定沿用 V0.4/P3.0。

| 方案 | 零位到获胜角线的定义 |
|---|---|
| `legacy` 原自由区 | 推进量 q≤0.25 rad 时为零，之后沿用原斜率和人物上限 |
| `narrow` 小自由区 | q≤0.05 rad 时为零，之后线性接到旧曲线的 25° 点 |
| `start-resistance` 零位起步 | 零位目标为旧曲线 25° 点的 15%，正向线性接到 25°，负向到 −0.05 rad 逐渐归零 |

推进量 q 为机械角的反号。25° 对应约 0.4363 rad，是当前 VR 演示角线。对于 person_02，三条曲线在那里同为 0.2795 N·m，此后的静态曲线完全相同。动态下发量还随此前的发力建立和疲劳变化。零位起步的静态目标约 0.0419 N·m，静止时模拟制动力矩仍为零。运动改变方向时，制动始终反向。

## 软件仿真启动

在 PC 仓库根目录运行。省略选项时仍用 `legacy`，`demo` 始终走原曲线。只有 TCP 软件模拟入口提供候选选项，串口入口 `real.run` 沿用原行为。

```powershell
.\.venv\Scripts\python.exe -m common.bridge --strategy balanced --resistance-profile narrow --log-file "$env:TEMP\arm-v05a-pc.jsonl"
```

在另一个 PowerShell 使用 V0.4 的同一施力序列。对比时换用 `legacy`、`narrow`、`start-resistance` 重启 PC 和模拟器，保持人物、策略、施力幅值与序列相同。

```powershell
.\.venv\Scripts\python.exe -m sim.mock_stm32 --torque 0.065 --sequence release:1.2,down:0.66,release:1.2,back:0.22,release:1.72
```

在 [正式 VR 工程](https://github.com/Ihtbfas/armwrestling-vr/commit/129c989035b7657af526f9eec9bfe6eebea50e56)打开 `Assets/Scenes/ArmWrestlingMain.unity`，选择网络数据和 **乙（person_02）**，再准备对战。原 UDP 和串口协议沿用。PC 日志的 `resistance_profile` 是生效方案，启动记录保留指定的方案名。防守策略继续参考原 0.25 rad 边界，本轮仅用均衡做候选比较。

## 验证

- [固定角度、同施力闭环、曲线与数据](test-results/2026-11-25-v05a-resistance-comparison.md)。
- Windows Python 相关回归 97 项，96 通过，Linux PTY 1 项跳过。另将默认方案和冻结 V0.4 逐步比较 12,000 步，覆盖四个人物、三个策略、时钟跳变、暂停及遥测和 VR 断开。制动命令、原状态字段和 UDP 回包逐步相等。TCP 候选选项成功启停，串口入口拒绝候选选项。
- Windows 正式 Unity 主场景用原 P3.0 Editor 验证流程运行三次，每次只从测试启动器给 PC 传不同的候选选项。三套方案均完成 5 局、35 个检查点，Unity 运行错误为 0。等待、倒计时、暂停和结束归零，模拟器与 PC 中断后能恢复，退出 Editor 后 PC 记录超时和 STOP/0。此处用 P3.0 的 0.8 N·m 快速完赛输入检查通信和比赛流程，三档较低施力的曲线来自独立固定步长对照。
- 实际 Editor 启动 person_02 后，零位起步在固定零位采到基础目标 0.0419 N·m，缓升中的下发 0.0289 N·m，模拟速度规则计算的制动力矩为 0。其余两种在该零位采样的基础目标和下发均为 0。[Unity 检查点和零位遥测](test-results/2026-11-25-v05a-editor.json)

## 下一候选

建议先验证小自由区。它在 0.05 rad 左右开始制动，仍保留零位附近的回推余量。以同一 0.065 N·m 施力为例，它的峰值推进为 0.3166 rad，处于原方案的 0.4327 rad 与零位起步的 0.0980 rad 之间。其阻力释放和退让仍需从相同起始角做闭环比较。同一短回推脉冲在本实验中的起点不同，位移大小不足以判断哪种配置更易回推。

小自由区在已测三档也没有跨过软件判定线。下一轮应优先检查不同施力和回推工况中的可推进性，再决定是否进一步缩小自由区。零位起步可作为低角度对照保留。结论来自软件仿真，人体手感、实体制动器和 PICO 尚未验证。
