# Windows Unity ↔ Python UDP V0.1 联调

2026-11-15，基于 `main` 的 `5f299d90bdd7813c532c3c79164c8f294acb2faa` 实施。协议仍以 [vr-udp-v01.md](vr-udp-v01.md) 为准。

## 已运行的环境和文件

- Windows 11 x64；Python 3.11.5：`D:\anaconda3\python.exe`（本机 `python` 可用）。无需 pyserial、MuJoCo、虚拟 COM 或 PICO。
- Unity/团结编辑器：`2022.3.62f3c1 (1623fc0bbb97)`，路径 `D:\Unity\Hub\Editor\2022.3.62f3c1\Editor\Unity.exe`。
- Git 仓库：`E:\桌面\研究生\实验室\人机交互课程\实物大作业\code`。
- 独立测试工程：`E:\桌面\研究生\实验室\人机交互课程\实物大作业\UnityUdpDemo`。
- 场景：工程内 `Assets/Scenes/UdpDemo.unity`；脚本副本：`Assets/ArmWrestling/`。
- 原始运行证据：工程内 `TestResults/report.json`、`samples.jsonl`、`unity-editor.log`、`integration-console.log`、`bridge.jsonl`、`bridge-restarted.jsonl`、`stm32.jsonl`、`stm32-reconnected.jsonl`。缓存和完整 Unity 工程不提交到 Git。
- 仓库内可查看的证据摘要：[test-results/2026-11-15-unity-udp.json](test-results/2026-11-15-unity-udp.json)。

## 立即运行本机演示

打开两个 PowerShell 终端，分别执行：

```powershell
# 终端 A
cd 'E:\桌面\研究生\实验室\人机交互课程\实物大作业\code'
python -m common.bridge
```

```powershell
# 终端 B
cd 'E:\桌面\研究生\实验室\人机交互课程\实物大作业\code'
python -m sim.mock_stm32
```

在 Unity Hub 中 Add/Open 上面的 `UnityUdpDemo` 工程，打开 `Assets/Scenes/UdpDemo.unity`，点击 **Play**，切到 **Game** 视图，再点击画面左上方的 **running**。

画面显示收发计数、本地端口、角度、mode、reason。橙色长方体绕蓝色关节摆动；默认绕局部 Z 轴，`负 rad × Rad2Deg` 为负角度。`paused`、`waiting`、`finished` 都使 PC 输出 stop，但只要机械遥测仍有效，摆臂继续显示实际位置。暂停比赛并不等于冻结机械状态。

不要同时启动 `sim.mock_vr`；PC 一次只接受一个有效 VR 源端口。退出两个 Python 终端用 Ctrl+C；停止 Unity 使用顶部 Play 按钮。若停止后立刻重进 Play，等待旧源端口约 1 秒超时。

如端口被其他进程占用，先用 `Get-NetUDPEndpoint -LocalPort 9102` / `Get-NetTCPConnection -LocalPort 9101` 确认进程及旧终端，不要改协议端口或批量结束无关 Python 进程。自动测试启动的进程会在结束时清理；若当前已有手动演示进程，先关闭再跑自动测试。

## 测试代码与正式系统的边界

仓库 `tests/unity/` 中的 C#、编辑器场景生成工具和 Python 自动化脚本均是**测试资产**，不是 PC 正式运行时的依赖。PC 端对外仍只通过 UDP V0.1 协议与 Unity/VR 交互，具体协议见 [vr-udp-v01.md](vr-udp-v01.md)。VR 同学可以参考这里的 C# UDP 实现，也可以独立编写自己的客户端，并不需要复制演示场景或运行测试脚本。

独立的 `UnityUdpDemo` 测试工程在仓库外，与 `code` 同级；这个整理不会自动改动该工程 `Assets/ArmWrestling` 中已有的脚本副本。

## 从仓库在新工程中配置

1. 用本机编辑器新建普通 3D 工程，使用内置渲染管线，不安装 XR 或额外网络/JSON 包。
2. 把 `tests/unity/ArmWrestlingUdpClient.cs`、`tests/unity/ArmWrestlingUdpDemo.cs` 复制到 `Assets/ArmWrestling/`。
3. 把 `tests/unity/Editor/ArmWrestlingDemoTools.cs` 复制到 `Assets/ArmWrestling/Editor/`。保持 **Editor** 子目录，避免编辑器工具进入玩家程序。
4. 等编译完成，执行菜单 **Arm Wrestling → Create UDP Demo Scene**。它创建 Pivot、长方体、关节、桌面、相机、光源和挂好脚本的 UDP Demo，并保存场景。它会提示保存当前已修改场景；建议只在独立测试工程中执行。
5. 点 Play，默认发送角色 `demo`、状态 `waiting`。点击四个按钮进行切换。

也可以手动配置：创建空物体 `UDP Demo`，挂两个运行时脚本；另建空物体 `Pivot`，其子 Cube 的局部 Position 设 `(0,0.9,0)`、Scale 设 `(0.22,1.8,0.22)`，这样转轴位于臂根。把 Pivot 拖入 Demo 脚本的 `pivot`，`localAxis=(0,0,1)`、`angleSign=1`，添加朝向模型的相机与光源。轴向和正负映射由 Inspector 调整，不表示真实设备已完成空间标定。

## 通信与移植

`tests/unity/ArmWrestlingUdpClient.cs` 是经过测试的 Unity 端通信参考实现，可供 VR 同学选用，但不是必须使用的正式代码。绑定 IPv4 loopback 的临时端口，使用同一 `UdpClient` 发往和接收自 `127.0.0.1:9102`。每约 200 ms 发完整状态；按钮切换立即额外发送。UTF-8 无 BOM、无换行，VR 包只有 `v/type/seq/character_id/match_state`。

主线程每帧最多轮询 64 个包，socket 设为非阻塞，不等待网络，也没有后台线程。界面和 Transform 都在主线程更新。`Status` 是最新有效状态；`HasFreshStatus` 和 `HasValidAngle` 是读取状态/角度时应检查的有效性标志；`LastAngleRad` 留存最后有效角度。使用 `SetMatchState("running")` 等方法切换比赛状态。

接收使用 .NET 标准库 `DataContractJsonSerializer`：必填的 `double? angle_rad` 可可靠区分显式 null、数值 0 和缺失字段；缺失字段拒收。没有用 `JsonUtility` 的浮点默认值猜测 null。`JsonUtility` 只用于构造非空的发送包和可选编辑器检查数据。

旧/重复 PC seq 在当前 socket 会话中忽略，不刷新接收时间。超过 1 秒没有新的有效 status 时关闭旧 socket，显示超时并保持姿态，等待 1.1 秒让 PC 的旧 VR 端口锁定过期，再绑定新端口、重置序号重连。PC 不在线时持续等待，不反复打印错误；Windows UDP 的 ConnectionReset 不会导致每帧报异常。

`ArmWrestlingUdpDemo.cs` 仅是可替换的 OnGUI 和摆臂映射：`LateUpdate` 使用有效 `angle_rad * Mathf.Rad2Deg` 更新 Pivot；无效或通信过期时保持姿态。OnDisable、OnDestroy、退出程序均关闭 socket；实际 Stop/Play 和脚本域重载均已测试。暂停/stop 本身不会清除仍然有效的机械角度。

如果 VR 同学选择复用该示例，可把网络脚本挂在随会话启停的对象上，用 VR UI 调用 `SetMatchState`，以有效角度驱动自己的骨骼或转轴，替换 Demo 即可。`characterId` 应保持 1–64 字符；角色编号目前只记录，不切换 PC 控制策略。不发送角速度、刚度、最大力矩等字段。`enabled` 只表示 PC 正在正常计算策略。

本次只验证上面的 Windows 编辑器版本。其他 Unity/团结版本、PICO/Android、IL2CPP/AOT 或程序集裁剪尚未验证；使用反射的 .NET JSON 数据契约在新构建目标上应重新做运行验证，不能据此宣称头显构建已兼容。将来改用真实 STM32 的 `real.run --port COMx` 时，VR UDP 接口保持不变；本次未进行串口 V1 或实物验证。

## 实际结果

实际启动 `common.bridge`、`sim.mock_stm32`，由 **Unity Editor 的真实 Play 模式**发送 UDP；全程没有启动 Python VR 模拟器。检查工具通过同一个 `SetMatchState` 方法自动触发按钮对应状态，实际读取 C# 接收快照和 Pivot quaternion；自动测试不等同于逐个鼠标点击按钮或头显测试。

| 检查 | 实际结果 |
|---|---|
| Windows Python 基线 | 19 项全部通过 |
| Unity JSON 检查 | 8 项通过：null、0、负数指数、缺失角度、坏 JSON、超长包、错误版本、负 seq |
| Editor Play waiting → running | 双向通信，`match_waiting/stop` → `running/enabled` |
| paused / running / finished / waiting | 对应 `match_paused`、`running`、`match_finished`、`match_waiting`，模式正确 |
| 连续机械角度和真实转轴 | 全次采样范围 `-0.55～0.60 rad`；有效采样的 quaternion 映射误差 `0°` |
| Play 中真实脚本/域重载 | 新端口重新建立 UDP 会话，角度映射继续正确，无异常 |
| 关闭模拟 STM32 | Unity 收到 `angle_rad:null`、`telemetry_missing/stop`；连续空值期间姿态不回零 |
| 重启模拟 STM32 | 自动重连 TCP，Unity 恢复有效机械角度和 running |
| Editor Stop Play | PC 日志实际记录 `vr_timeout/stop`，正常策略停止 |
| 再次 Play | 新临时源端口成功建立会话，无遗留 socket |
| 关闭 PC / 重启 PC | Unity 超时并保留姿态；新 socket 接受重启 PC 的低 seq，模拟 STM32 自动重连 |
| UDP 字段与退出 | 两方向字段集合精确匹配 V0.1；编辑器无 C# 编译错误/运行异常，退出码 0 |

最终自动联调 **22 项检查通过，记录 95 个编辑器快照**。这些不是 95 个 UDP 包的全量抓包；C# 接收计数还包括快照之间收到的包。PC 名义发送周期 20 ms，受本机 Windows 调度影响，初始稳定 2.812 s 窗口实收约 **31.65 Hz**；心跳约 5 Hz，状态切换会额外发送。本次未修改 PC 时序或控制策略来改变这一结果，也不是实时性/延迟基准测试。

首轮检查错误地假设 running 下 15 s 内一定摆动超过 0.12 rad，实测该轮只有约 0.056 rad：固定被动制动对运动限幅，通信和姿态映射正常。因此检查改为实际变化 >0.01 rad 并逐个快照核对角度映射，没有改控制算法、模拟动力学或任何原有测试。首轮原始日志保留在本机 `TestResults/first-attempt/`；最终结果另行记录。

## 复现自动联调

关闭该工程的其他 Unity 编辑器实例以及手动启动的两个 Python 进程。工程包含上述三个 C# 文件后，在仓库根目录运行：

```powershell
python -m unittest common.test_bridge sim.test_bridge_clients sim.test_bridge_integration sim.test_vr_udp -v
python tests/unity/run_windows_integration.py --unity 'D:\Unity\Hub\Editor\2022.3.62f3c1\Editor\Unity.exe' --project '..\UnityUdpDemo'
```

可选自动工具会启动真正的 Unity 编辑器（batchmode，实际进入/退出 Play、域重载）、PC 和模拟 STM32，约几十秒后写出 `TestResults/report.json` 并退出。它只在显式运行时启动，不是普通 Python 测试发现的一部分；本机端口必须空闲。自动测试中的 `sim.mock_vr` 仅可能由原有 Python 回归测试内部启动，那些结果不计入 Unity 联调证据。
