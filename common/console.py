"""Operator-facing terminal presentation; never changes control or wire data."""
from collections import deque
import json
import math
import os
import shutil
import sys
import time
import unicodedata


REASONS = {
    'running': ('运行', '正在按机械状态计算阻力', '可在 Unity 点击 paused 暂停比赛。'),
    'match_waiting': ('等待', '等待开始比赛', '在 Unity 点击 running 开始。'),
    'match_paused': ('暂停', '比赛已暂停，阻力输出停止', '在 Unity 点击 running 继续。'),
    'match_finished': ('结束', '本场比赛已结束，阻力输出停止', '可在 Unity 选择 waiting 或开始下一场。'),
    'vr_timeout': ('中断', 'Unity 心跳超时，阻力输出停止', '检查 Unity 是否仍在 Play、脚本是否在发送心跳。'),
    'vr_missing': ('等待', '尚未收到 Unity 比赛状态', '打开 Unity 测试场景并点击 Play。'),
    'telemetry_missing': ('等待', '尚未收到有效机械数据，阻力输出停止', '检查模拟 STM32 是否启动、端口是否匹配。'),
    'telemetry_timeout': ('中断', '机械数据超时，阻力输出停止', '检查下位机程序、串口连接与会话握手。'),
    'device_fault': ('异常', '设备故障或尚未获得本地使能许可', '检查下位机状态；模拟器需显式指定 --demo-arm。'),
    'command_timeout': ('中断', 'PC 指令超时，模拟制动已撤销', '检查 PC 程序；模拟器重建会话后再测试。'),
    'stop': ('停止', 'PC 已要求停止制动', '比赛状态和停止原因请查看 PC 上位机窗口。'),
    'disconnected': ('等待', '尚未收到 PC 有效指令', '启动 PC 上位机并检查连接。'),
}
MATCHES = {'waiting': '等待开始', 'running': '比赛进行中', 'paused': '比赛暂停', 'finished': '比赛结束'}
EVENTS = {'listening': '通信入口已启动', 'connected': '通信对端已连接',
          'disconnected': '通信连接断开', 'timeout': '心跳超时', 'rejected': '忽略无效消息',
          'closed': '程序已停止', 'serial_open': 'PC 串口已打开，等待 V1 握手',
          'serial_unavailable': '串口暂不可用，正在重试', 'serial_error': '串口异常，正在重连',
          'serial_timeout': '串口数据超时，重新建立会话',
          'serial_session_ready': 'V1 会话握手已确认',
          'virtual_serial_ready': '模拟串口已打开，等待 PC',
          'vr_connected': 'Unity UDP 会话已连接', 'invalid_vr_packet': '忽略无效 Unity 消息',
          'session_open': 'V1 会话握手已响应', 'command_timeout': '指令看门狗已停止模拟制动'}


def safe(value):
    return ''.join(c if not unicodedata.category(c).startswith('C') else ' ' for c in str(value))


def number(value, digits=2):
    if type(value) not in (int, float) or not math.isfinite(value):
        return '--'
    return f'{value:.{digits}f}'


def freshness(value, age):
    if value is None or (not value and age is None):
        return '尚未收到'
    return ('正常' if value else '无效/超时') + (f'，距更新 {number(age, 0)} ms' if age is not None else '')


def operator_lines(role, message, info=None, events=()):
    info = info or {}
    device = message.get('type') in ('stm32_snapshot', 'stm32_serial_snapshot') or '模拟 STM32' in role
    reason = message.get('reason', 'disconnected' if device else 'telemetry_missing')
    badge, summary, action = REASONS.get(reason, ('提示', safe(reason), '查看最近事件与另一端程序。'))
    if device and reason == 'running':
        badge, summary, action = ('模拟运行', '正在执行 PC 制动指令，模拟机械运动', '比赛控制请在 Unity 操作；阻力策略请看 PC 窗口。')
    if device and message.get('type') == 'stm32_serial_snapshot' and reason != 'command_timeout':
        if not message.get('session_ready'):
            badge, summary, action = ('等待', '等待 PC 握手，尚无有效制动指令', '启动 PC 串口程序，并确认两端使用成对 COM 口。')
        elif message.get('armed') is False:
            badge, summary, action = ('未授权', '模拟制动尚未获得运行许可', '仅模拟测试可使用 --demo-arm；比赛状态请看 PC 窗口。')
    lines = [f'扳手腕系统 | {safe(role)}',
             f"刷新时间：{time.strftime('%H:%M:%S')} | 已运行：{number(message.get('elapsed_s'), 1)} s", '=' * 48,
             f'当前状态：[{badge}] {summary}', f'操作提示：{action}', '']
    if info.get('port'):
        lines.append(f"下位机链路：串口 V1 / {safe(info['port'])} / {info.get('baudrate', 115200)} / 8N1")
    elif message.get('type') == 'stm32_serial_snapshot':
        lines.append('下位机链路：串口 V1（端口待确认）')
    elif info.get('stm_port') or info.get('stm_transport') == 'tcp' or device:
        lines.append(f"下位机链路：TCP 模拟 / 127.0.0.1:{info.get('stm_port', 9101)}")
    if device:
        input_mode = info.get('input_mode', 'cycle')
        lines.append({
            'manual': '按 1 下压、2 回推、0 松手、R 软件回零并保持。',
            'file': '本窗口按本地输入文件模拟施力。',
            'sequence': '本窗口在收到首条有效 ENABLE 后播放施力序列。',
        }.get(input_mode, '本窗口是软件模拟设备。模拟人周期性下压、回推、松手。'))
        if 'session_ready' in message:
            lines.append('V1 会话：' + ('已建立' if message['session_ready'] else '等待握手')
                         + ' | 模拟许可：' + ('已授权' if message.get('armed') else '未授权'))
            lines.append(f"有效指令：{message.get('accepted_commands', 0)} 条 | 最近指令序号：{message.get('last_command_seq', '--')}")
    else:
        lines.extend([
            f"Unity 链路：UDP / {safe(info.get('host', '127.0.0.1'))}:{info.get('vr_port', 9102)}",
            '下位机数据：' + freshness(message.get('telemetry_fresh'), message.get('telemetry_age_ms')),
            'Unity 心跳：' + freshness(message.get('vr_fresh'), message.get('vr_age_ms')),
            '比赛状态：' + MATCHES.get(message.get('match_state'), '尚无有效比赛状态')
                + ' | 角色：' + safe(message.get('character_id') or '--'),
            '阻力控制：' + ('计算运行中' if message.get('mode') == 'enabled' else '已停止'),
        ])
        if 'active_character_id' in message:
            lines.append('生效人物：' + safe(message['active_character_id']) + ' / '
                         + safe(message.get('active_character_name', '')))
            if message.get('character_fallback'):
                lines.append('人物提示：未知编号按 demo 处理' +
                    ('，本回合保持原人物' if message.get('character_selection_deferred') else '，已使用 demo 基线'))
            elif message.get('character_selection_deferred'):
                lines.append('人物提示：本回合保持原人物；请在 waiting/paused/finished 确认新人物')
        if 'serial_session_ready' in message:
            lines.append('V1 会话：' + ('已确认' if message['serial_session_ready'] else '等待确认')
                         + ' | 本地使能许可：' + ('已获得' if message.get('serial_arm_ready') else '未获得'))
    angle = message.get('angle_rad')
    if angle is None or message.get('telemetry_fresh') is False:
        lines.append('机械角度：无有效数据（不会当作 0°）')
    else:
        lines.append(f'机械角度：{number(math.degrees(angle))}° / {number(angle, 3)} rad（负方向为获胜侧）')
    speed = message.get('velocity_rad_s')
    lines.append('运动速度：' + (f'{number(math.degrees(speed))} °/s' if speed is not None and message.get('telemetry_fresh') is not False else '无有效数据'))
    if device:
        lines.extend([f"模拟人施力：{number(message.get('user_torque_nm'), 3)} N·m",
                      f"模拟制动力矩：{number(message.get('applied_torque_nm'), 3)} N·m"])
    else:
        if 'base_target_torque_nm' in message:
            lines.append(f"当前下发阻力：{number(message.get('target_torque_nm'), 3)} N·m（建立后计算值）")
            lines.append(f"静态目标：{number(message['base_target_torque_nm'], 3)} N·m | 建立常数：{number(message.get('character_rise_time_constant_s'))} s")
            if 'strategy' in message:
                strategy = {'demo': 'Demo 基线', 'balanced': '均衡', 'defensive': '防守',
                            'rhythmic': '节奏'}[message['strategy']]
                scale = number(message['strategy_scale']) if message.get('mode') == 'enabled' else '—'
                lines.append(f"策略：{strategy} | 占比：{scale} | 请求：{number(message['requested_target_torque_nm'], 3)} N·m")
            if 'fatigue_level' in message:
                lines.append(f"等效疲劳：{number(message['fatigue_level'], 3)} | 剩余能力系数：{number(message.get('fatigue_capacity'), 3)}（模型演示值）")
                lines.append(f"疲劳后目标：{number(message.get('eligible_target_torque_nm'), 3)} N·m（建立前上限）")
        else:
            lines.append(f"计算目标阻力：{number(message.get('target_torque_nm'), 3)} N·m（计算值）")
        if 'serial_applied_target_nm' in message:
            lines.append(f"下位机回报目标：{number(message['serial_applied_target_nm'], 3)} N·m（非实测力矩）")
    lines.extend(['', '最近事件：'] + list(events)[-3:])
    lines.append('Ctrl+C 退出 | JSON 记录可用 --log-file 保存 | 不代表实物已解除急停')
    return [safe(line) for line in lines]


def _enable_vt(stream):
    if os.name != 'nt':
        return True
    try:
        import ctypes
        from ctypes import wintypes
        import msvcrt
        kernel = ctypes.windll.kernel32
        kernel.GetConsoleMode.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.SetConsoleMode.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        handle = msvcrt.get_osfhandle(stream.fileno())
        mode = wintypes.DWORD()
        return bool(kernel.GetConsoleMode(handle, ctypes.byref(mode)) and kernel.SetConsoleMode(handle, mode.value | 4))
    except (AttributeError, OSError, ValueError):
        return False


def _fit(line, width):
    result, cells = [], 0
    for char in line:
        cells += 2 if unicodedata.east_asian_width(char) in 'WF' else 1
        if cells > width:
            break
        result.append(char)
    return ''.join(result)


class ConsoleReporter:
    def __init__(self, role, mode='auto', stream=None, recording=None):
        self.role, self.stream, self.recording = role, stream or sys.stdout, recording
        tty = self.stream.isatty()
        self.human = mode == 'human' or (mode == 'auto' and tty)
        self.dynamic = self.human and tty and _enable_vt(self.stream)
        self.info, self.snapshot, self.events = {}, {}, deque(maxlen=3)
        self.started = False

    def __call__(self, message):
        serialized = json.dumps(message, ensure_ascii=True, allow_nan=False) + '\n'
        if self.recording is not None:
            self.recording.write(serialized)
            self.recording.flush()
        if not self.human:
            self.stream.write(serialized)
        else:
            self.info.update(message)
            if message.get('type') in ('status', 'stm32_snapshot', 'stm32_serial_snapshot'):
                self.snapshot = message
            if message.get('event'):
                text = EVENTS.get(message['event'], safe(message['event']))
                detail = message.get('error') or message.get('reason')
                self.events.append(time.strftime('%H:%M:%S') + ' ' + text + (': ' + safe(detail) if detail else ''))
            lines = operator_lines(self.role, self.snapshot, self.info, self.events)
            if self.dynamic:
                columns, rows = shutil.get_terminal_size((100, 30))
                if not self.started:
                    self.stream.write('\x1b[?1049h\x1b[?25l')
                    self.started = True
                self.stream.write('\x1b[H' + '\n'.join('\x1b[2K' + _fit(line, max(20, columns-1)) for line in lines[:max(5, rows-1)]) + '\x1b[J')
            else:
                self.stream.write('\n'.join(lines) + '\n\n')
        self.stream.flush()

    def close(self):
        if self.started:
            self.stream.write('\x1b[?25h\x1b[?1049l')
            self.stream.flush()
            self.started = False
        if self.recording is not None and not self.recording.closed:
            self.recording.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def add_console_arguments(parser):
    parser.add_argument('--display', choices=('auto', 'human', 'json'), default='auto',
                        help='auto: Chinese dashboard in a terminal, JSON when redirected')
    parser.add_argument('--log-file', help='also save full structured JSONL to this UTF-8 file')


def reporter_from_args(role, args):
    recording = open(args.log_file, 'a', encoding='utf-8') if args.log_file else None
    return ConsoleReporter(role, args.display, recording=recording)
