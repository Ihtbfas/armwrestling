# STM32 V1.1 C 参考

复制 `aw_protocol.h`、`aw_protocol.c` 到固件工程即可使用编解码和流式解析。需要会话和命令看门狗参考时，再复制 `aw_session.h`、`aw_session.c`。这四个文件使用 C99 标准库，可独立编译。所有线字段逐字节编解码，C 结构体只在程序内部使用。

## 接入顺序

1. 上电时调用 `aw_parser_init` 和 `aw_session_init`。执行输出先由板端独立保护禁止。使用现有 USB CDC，或 USART 115200、8N1。接收中断或 DMA 把字节交给通信任务，采集任务和执行周期独立运行。
2. 通信任务逐字节调用 `aw_parser_feed`。每次返回 `true` 先处理得到的 `AwFrame`，再循环调用 `aw_parser_next` 直到没有更多帧。线路空闲时也周期调用 `aw_parser_next`，以处理 100 ms 组帧超时。两个函数使用同一 MCU 单调毫秒计时，同一解析器只由一个任务访问。
3. 将有效帧交给 `aw_session_handle`。返回 `AW_SEND_ACK` 时，用 `aw_encode` 打包回复；`ack_seq` 由调用方独立计数。每个控制周期都调用 `aw_session_tick`，即使串口没有收到字节。读取 `applied_target_mnm` 前根据最新本地保护条件更新会话。STOP 为请求零阻力。机械、电气如何响应失效，由台架和独立保护方案确认。
4. `aw_session_authorize` 只在本地新授权时调用。已接受本会话第一条 STOP、角度有效且当前保护条件满足后才可授权。通信恢复、重复 OPEN 和 STOP 不会授予本地许可。急停、限位、故障、角度失效或命令超时均撤销本地许可，机械组/电气组负责授权信号和解除条件。板级应用收到本地撤销信号时调用 `aw_session_revoke`。
5. 约 100 Hz 发送遥测。采集任务填主轴角度、速度和测力的原始值、各有效位与采样时刻。`mcu_sample_ms` 取采集该角度时的计时值，角度过期则清除 `AW_ANGLE_VALID`。未知的速度或测力填零并清对应有效位。`aw_session_set_telemetry` 填当前会话和 ACK、授权、超时诊断位以及目标回报。使用独立遥测序号，包括未建会话的诊断帧。新会话的序号从头计数或继续递增均可，PC 会建立新的接收基准。

解析器在写入前检查缓存容量。收到异常的满缓存状态时会丢弃该缓存，当前新字节仍可作为下一帧的开头。读取入口也检查超范围的长度。每次容量重置将 `buffer_overruns` 加一，既有 CRC、帧头、载荷和组帧超时计数保留。遇到容量重置时检查调用方式和缓冲区使用，继续在每次返回有效帧后排空剩余帧。

`device_flags` 由板级状态提供，通信模块管理 `ACK_VALID`、`ARM_READY` 和 `CMD_TIMEOUT`。应用层传入 `local_cap_mnm`，台架未标定时保留为 0，并保持禁止输出。DMA/USB 异步发送时，提交的字节缓冲在发送完成前必须保持有效；繁忙时留最新遥测即可。命令状态和会话只能由同一任务更新，跨任务读写时由应用提供合适的同步。

初次实物验证先物理隔离励磁功率、设备旁无人接触，在 PC 上选“仅 STOP”。检查无会话诊断、匹配 ACK、遥测采样时间持续推进、STOP 被确认和目标回报为零，再进行断开、复位和重握手。电气使能方式、实际限幅、传感器、零位和执行映射由下位机与机械/电气组确认。

## 在电脑上校验

`golden_vectors.txt` 为 Python 和 C 共用的六条黄金帧。用 GCC 或 Clang 在本目录运行以下命令。Windows 也可将 `cc` 换成 `zig cc -target x86_64-windows-gnu`，输出文件改为 `test_aw_protocol.exe`，再执行 `.\test_aw_protocol.exe golden_vectors.txt`。

```sh
cc -std=c99 -Wall -Wextra -pedantic -Werror aw_protocol.c aw_session.c test_aw_protocol.c -o test_aw_protocol
./test_aw_protocol golden_vectors.txt
```

测试覆盖黄金帧双向编解码、旧版本和坏帧拒绝、分包/粘包恢复、序号和采样时间回绕、STOP/ACK、局部限幅、超时和复位。补充检查了长噪声、连续无效帧、迟到字节、跳过排空仍能恢复，以及故意构造的满缓存和超范围长度。后两项用于覆盖防御性分支。样例中的 ENABLE 只用于离线字节检查。主机测试可以检查协议和纯逻辑，STM32 板级驱动、传感器、电气保护和执行输出仍需实物验证。

[串口 V1.1 协议](../../docs/stm32-serial-v11.md)
