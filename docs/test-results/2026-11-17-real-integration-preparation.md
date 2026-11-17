# 真实联调准备阶段记录：2026-11-17

本记录的结论是**接口交接和测试准备已完成，真实联调尚未执行**。用户已明确实体 STM32 与正式 VR 工程未到位。

## 版本与范围

- 准备基线：main `3cce2c98b1bf85e30b09aecbbf6ec87dcd85e035`，人物模型仍为 V0.3 `d4c6513`。
- 本机：Windows，Python 3.11.5，已有 pyserial 3.5。已枚举 COM：COM1/COM2 为 ELTIMA 虚拟串口对，COM3–COM6 为蓝牙端口；没有据此认定某个 COM 是 STM32，也没有打开这些端口。
- 本阶段只修改 Markdown 说明、交接流程和记录模板。未修改人物模型、Python/C# 程序、UDP 字段、串口帧格式、系统防火墙或设备权限。

## 本次实际检查

| 检查 | 结果与覆盖范围 |
|---|---|
| `common.bridge`、`real.run`、`sim.mock_vr` 的 `--help` | 退出码 0；文档所用参数与当前入口一致。`--help` 不打开 COM |
| Python 相关回归 | 39 项通过，0 失败，0 跳过；包括 V1 黄金字节/CRC、串口内存流、疲劳状态、人物选择以及本机 Python UDP 子进程测试 |
| PC 无输入启动 | 回环地址、动态 TCP/UDP 端口，运行 0.1 秒后正常退出；观察到 listening → `stop / telemetry_missing`、`angle_rad=null`、目标 0 → closed |
| 交接文档核对 | 对照真实代码核对源端点占用/超时、回环绑定、串口握手及 ARM_READY 映射；修正串口协议文档过时的“尚无 COM 接入”描述 |

本次相关回归命令（仓库根目录）：

```powershell
.\.venv\Scripts\python.exe -m unittest common.test_serial_protocol real.test_stm32_link common.test_fatigue common.test_characters sim.test_vr_udp -q
```

无输入启动检查使用临时端口，未占用正式演示的 9101/9102：

```powershell
.\.venv\Scripts\python.exe -m common.bridge --host 127.0.0.1 --stm-port 0 --vr-port 0 --duration 0.1 --display json
```

Python UDP 子进程测试只是已有 PC 传输层回归，**不是正式 Unity/VR 测试**；串口内存流不是真实 COM 或实体板。本次未重跑 Unity 演示、虚拟 COM 全链路或全量 MuJoCo 测试。此前 V0.3 的软件结果及 MuJoCo WinError 1114 独立保留，见 [V0.3 说明](../character-library-v03.md)。

## 交给各组的材料

- VR：按 [最小联调流程 A](../real-vr-stm32-integration-v01.md) 对接正式场景，使用 [UDP V0.1](../vr-udp-v01.md)。参考 C# 的 loopback 绑定不能直接当跨设备实现，PICO/Android/IL2CPP 另测。
- STM32：按同一流程 B，在励磁功率及人手隔离条件下执行 OPEN/ACK、遥测和 STOP。使用 [串口 V1 修订草案](../stm32-serial-v1.md) 确认固件实现；未使能引起的 device_fault 不作为通信失败判据。
- 每条线使用 [实际记录模板](../real-integration-record-template.md)，记录对端版本、地址/端口、报文、观察值和未执行项。A/B 任一条实际通过即可独立形成下一阶段提交。

## 尚未执行及后续入口

| 阶段 | 当前状态 | 下一步需要 |
|---|---|---|
| A 正式 VR UDP | 未执行 | 正式工程/提交、运行设备、双方网络信息 |
| B 实体 STM32 V1 | 未执行 | 实板、COM/固件提交及已实现功能，确认无励磁功率/人手 |
| 制动器保护及标定 | 未执行 | 下位机/电气组确认硬件限幅、急停、独立保护及人工使能；无人台架和测量方案 |
| C 实体 STM32 + 正式 VR | 未执行 | A/B 分别通过后组合验证 |
| 人体扳手腕实验 | 未执行 | 本阶段不开展 |

没有新增疲劳、策略或人物模型功能。MuJoCo Windows DLL 问题单独待办；通信联调不依赖它。
