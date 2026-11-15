# 测试代码（不参与 PC 正式运行）

本目录用于放置**独立的联调演示与测试资产**。它不构成 VR 组的软件交付要求。

## Unity/UDP 测试

- `unity/ArmWrestlingUdpClient.cs`：Unity/C# UDP 发送与接收的参考实现；只是测试客户端，**不是 PC 上位机必需组件**。
- `unity/ArmWrestlingUdpDemo.cs`：测试用按钮、摆臂显示与角度映射。
- `unity/Editor/ArmWrestlingDemoTools.cs`：只在 Unity Editor 下使用的场景生成与测试工具。
- `unity/run_windows_integration.py`：Windows 自动化脚本，启动 Python PC、模拟 STM32 和 Unity Editor 做联调。

**正式对接时，VR 组只需遵循 [VR UDP V0.1 协议](../docs/vr-udp-v01.md)**，自行实现角色/比赛状态上报与角度/状态接收；可以参考测试客户端，但无需引入这里的 C# 或 Unity 场景。

运行与迁移方式见 [Windows Unity 联调说明](../docs/windows-unity-udp-test.md)。独立 `UnityUdpDemo` 测试工程不提交到仓库；脚本已复制到测试工程中，因此调整 GitHub 目录不会自动改变本机已打开的 Unity 场景。

PC 正式运行入口仍是 `common/bridge.py`（模拟 STM32 TCP + VR UDP）或 `real/run.py`（真实 STM32 串口 + VR UDP）。`sim/` 保留 MuJoCo、模拟设备和相关测试。
