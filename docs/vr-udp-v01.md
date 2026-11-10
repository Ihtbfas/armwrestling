# VR ↔ PC UDP 通信 V0.1（基础联调版）

> 当前接口以本文件为准。VR 只负责角色与比赛流程，不需要向 PC 发送阻力参数；PC 只向 VR 返回机械角度和运行状态，**不传角速度**。STM32 模拟 TCP 9101、真实 STM32 串口协议及内部阻力控制算法不在此接口范围内。

## 连接与时序

- 同一台 Windows PC 上使用 UDP，PC 监听 `127.0.0.1:9102`；团结引擎 C# 可使用 `UdpClient`。VR 使用**同一个 UDP socket/本地端口**收发。
- 每个 UDP 数据报是一条完整的 UTF-8 JSON 对象；不需要换行符（LF），不要 UTF-8 BOM。单包最多 8192 字节。
- VR 每约 **200 ms** 发送一条完整 `vr_update` 状态心跳，即使角色和比赛状态没变也要继续发送；PC 按 **50 Hz**（约每 20 ms）发送 `status`。
- 两方向各自的 `seq` 从 0 起递增，重开会话后可重置；重复和乱序包直接忽略。PC 超过 **1 秒**未收到有效 VR 状态会停止正常比赛控制。UDP 不保证送达或顺序，因此重复发送的是**当前状态**，不是一次性脉冲事件。

## VR → PC：vr_update

| 字段 | 类型 | 含义 |
|---|---|---|
| `v` | int | 固定 1 |
| `type` | string | 固定 "vr_update" |
| `seq` | int | VR 消息递增序号 |
| `character_id` | string | 角色编号，1–64 个字符 |
| `match_state` | string | waiting / running / paused / finished |

```json
{"v":1,"type":"vr_update","seq":1,"character_id":"opponent_01","match_state":"running"}
```

上位机使用已有固定阻力控制参数。即使 VR 消息附带旧版 `stiffness_nm_per_rad` 和 `max_torque_nm`，也**不会**改变 PC 控制计算；不必发送它们。现阶段 `character_id` 用于记录角色选择，不自动切换控制策略。

## PC → VR：status

| 字段 | 类型 | 含义 |
|---|---|---|
| `v` | int | 固定 1 |
| `type` | string | 固定 "status" |
| `seq` | int | PC 消息递增序号 |
| `angle_rad` | number / null | 实际摆臂角度，单位 rad；无效/过期为 null |
| `mode` | string | enabled / stop |
| `reason` | string | 状态原因：如 running、match_paused、telemetry_timeout、device_fault、vr_timeout |

```json
{"v":1,"type":"status","seq":20,"angle_rad":-0.4,"mode":"enabled","reason":"running"}
```

机械零位角度为 0 rad，用户向获胜侧下压时角度变负。VR 根据角度更新虚拟手臂；当 `angle_rad=null` 或 `mode=stop` 时，VR 应显示对应的暂停/设备异常状态，不将过期角度当作正常交互数据。

`mode=enabled` 只说明上位机正在进行正常策略计算，不代表实物已解除急停或完成人工重新使能。实际硬件保护由 STM32/电气系统独立执行。

## 分工及下一步

VR：人物与场景、角色选择、开始/暂停/结束、角度动作映射。PC：接收 STM32 机械状态、计算阻力、回传有效角度和运行原因。先实现基本动作同步和比赛切换；重置、胜负事件确认和不同对手的详细策略等留给后续版本。

可用 `python3 -m common.bridge`、`python3 -m sim.mock_stm32`、`python3 -m sim.mock_vr --demo-states` 做三终端演示；Windows 以 `py -3` 替代 `python3`。真实 Windows/团结引擎与 STM32 串口联调尚未完成。
