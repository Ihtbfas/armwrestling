# V0.5-B 小自由区与回推减阻

本轮以 `person_02 + balanced` 为主。先扩展下压幅值和持续时间，再从相同角度与静止速度比较回推。V0.5-A 的三条角度曲线继续使用。默认是 `legacy + symmetric`，demo 保持原基线。

## 实验发现

小自由区在 0.12 N·m 下压 0.66 s，或 0.09 N·m 下压 1.32 s 的同输入闭环中，均跨过 25° 并连续保持超过 0.35 s，且未触模拟限位。阈值和保持结果按 20 ms 角度采样计算。正式比赛结算还取决于 VR 的实际状态和有效遥测。

从 q=0.25 rad、速度为零、相同 2 s 预处理开始，两种原方案在 0.065 N·m 回推中的差异明显。原自由区 0.64 s 到达 q=0.05 rad。小自由区在回推 2 s 结束时仍在 q≈0.199 rad，因此增加了一个软件减阻候选。

减阻让相同小自由区、相同起点的回推在 1.94 s 到达 q=0.05 rad。但释放输入后仍有惯性，松手 1 s 时已到 q≈−0.315 rad。两次小自由区试验起始下发和疲劳相同，后续能耗和疲劳会因轨迹不同而分开变化。q=0.40 rad 起步时，减阻在同一回推输入下的改善较小，两种模式都没有回到自由区。

[全部输入档位、同起点回推、曲线与 CSV](test-results/2026-11-25-v05b-direction-comparison.md)

## 方向系数

PC 按人物原推进符号计算 `qdot = forward_sign * omega`。正向和静止保持原请求。检测到朝 q 减小的速度后，使用比例按速度连续降低，到反向速度 0.05 rad/s 时为 0.5，之后维持 0.5。过渡速度沿用原速度过渡尺度。请求幅值依次乘策略系数和方向系数，再进入原疲劳及发力建立过程。命令仍按当前速度施加被动制动。减阻响应的是已测速度，无法判定用户是否已经松手。

日志增加 `return_mode`、`qdot_rad_s`、`direction_scale`。停机条件依原优先级执行 STOP/0。选项只出现在 TCP 仿真入口，`relief` 需要显式选择 `narrow`。`real.run` 沿用原串口行为。

## 复现

在 PC 仓库根目录分别开两个 PowerShell。先启动桥接，再启动模拟器。

```powershell
.\.venv\Scripts\python.exe -m common.bridge --strategy balanced --resistance-profile narrow --return-mode relief --log-file "$env:TEMP\arm-v05b-pc.jsonl"
.\.venv\Scripts\python.exe -m sim.mock_stm32 --manual --torque 0.12
```

打开正式场景 `Assets/Scenes/ArmWrestlingMain.unity`，选择网络数据、乙（person_02），再准备对战。模拟器窗口的 `1`、`2`、`0`、`R` 分别为下压、回推、松手、软件回零。改为 `--return-mode symmetric` 可比较原双向制动。固定起点由独立实验脚本初始化，手动操作适合观察软件交互。

## 验证和下一步

- Windows 相关 Python 回归 101 项，100 通过，Linux PTY 1 项跳过。冻结 V0.5-A 对照覆盖 3 条曲线、4 个人物和 3 种策略，共 36,000 步。使用默认双向制动时，原控制命令、旧诊断字段和 UDP 回包逐步相等。TCP 接受合法候选组合，串口入口拒绝实验参数。
- 正式场景复验配对 VR P3.0 的 `129c989`。实际 Windows Editor Play 完成 5 局、35 个检查点，运行错误为 0。原 P3 流程沿用，测试启动器为模拟器传入 0.18 N·m 幅值，并在 person_02 局执行下压、回推、再下压。日志采到回推 qdot≈−0.125 rad/s、方向系数 0.5，请求由基础目标 0.1261 降至 0.0630 N·m。重新推进的系数恢复为 1。退出 Editor 后 PC 记录超时和 STOP/0。[正式场景复验数据](test-results/2026-11-25-v05b-editor.json)
- 下一阶段建议保留小自由区作为验证主线，先用对称模式作为对照，减阻维持显式选项。优先测同起点回推中的到达时间、过零速度和松手后的额外位移，再判断减阻是否适合推进到台架候选。独立反应延迟单列到 V0.5-C。实体制动器、STM32 和 PICO 尚未验证。
