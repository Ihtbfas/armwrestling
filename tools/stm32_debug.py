"""Windows Tk panel for the STM32 V1.1 PC backend."""
import json
import math
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from zipfile import ZIP_DEFLATED, ZipFile

from common.characters import CHARACTERS
from common.protocol import MAX_LINE, decode_datagram, encode_datagram
from common.serial_protocol import (
    FLAG_ACK_VALID, FLAG_ANGLE_VALID, FLAG_ARM_READY, FLAG_CMD_TIMEOUT, FLAG_ESTOP,
    FLAG_FAULT, FLAG_FORCE_VALID, FLAG_LIMIT, FLAG_READY, FLAG_VEL_VALID,
)
from common.strategies import STRATEGY_NAMES

ROOT = Path(__file__).resolve().parents[1]
HEARTBEAT_S = .2
STATUS_TIMEOUT_S = .65
MATCH_LABELS = dict(waiting='准备', running='进行中', paused='暂停', finished='已结束')
STRATEGY_LABELS = dict(balanced='均衡', defensive='防守', rhythmic='节奏')
STOP_REASONS = dict(
    running='正常计算', match_waiting='等待开始', match_paused='比赛已暂停',
    match_finished='比赛已结束', vr_timeout='模拟 VR 心跳超时',
    vr_missing='等待模拟 VR', telemetry_missing='等待遥测', telemetry_timeout='遥测超时',
    device_fault='设备异常', session_not_ready='等待会话握手',
    not_armed='等待下位机本地使能', monitor_only='通信检查：仅 STOP',
    debug_resume_required='等待准备或暂停状态确认',
)
FLAGS = (
    (FLAG_READY, 'READY'), (FLAG_ARM_READY, 'ARM_READY'), (FLAG_FAULT, 'FAULT'),
    (FLAG_ESTOP, 'ESTOP'), (FLAG_LIMIT, 'LIMIT'), (FLAG_CMD_TIMEOUT, 'CMD_TIMEOUT'),
    (FLAG_ANGLE_VALID, 'ANGLE_VALID'), (FLAG_VEL_VALID, 'VEL_VALID'),
    (FLAG_FORCE_VALID, 'FORCE_VALID'), (FLAG_ACK_VALID, 'ACK_VALID'),
)
KNOWN_FLAGS = sum(bit for bit, _ in FLAGS)
SAMPLE_STATES = dict(advancing='在更新', repeated='时间戳重复', backwards='时间戳倒退',
                     unavailable='待采样')
SERIAL_REASONS = dict(
    waiting_handshake='等待会话握手', waiting_stop_ack='等待 STOP 确认',
    telemetry_timeout='遥测超时', angle_invalid='角度无效（检查采集和零位）',
    sample_stale='角度采样停滞', sample_backwards='MCU 采样时间戳倒退',
    unarmed='等待下位机本地许可', device_fault='下位机报告故障',
    estop='下位机报告急停', limit='下位机报告行程越限', not_ready='设备尚未就绪',
    command_timeout='下位机命令看门狗超时', unknown_flags='下位机状态含未知位',
    session_mismatch='遥测会话已变化（板端复位或失去会话）',
    ack_timeout='命令 ACK 进度超时', serial_error='串口发送或接收失败',
)
EVENTS = dict(
    listening='PC 后端已启动', serial_open='串口已打开',
    serial_unavailable='无法打开串口（检查占用、端口和驱动）', serial_error='串口收发失败',
    serial_timeout='遥测超时，PC 重新等待串口会话',
    serial_session_ready='SESSION_ACK 匹配', serial_session_ack='SESSION_ACK 匹配',
    serial_stop_confirmed='下位机已确认本会话的 STOP',
    serial_session_restart='串口会话失效，重新握手',
    experiment_log_incomplete='实验日志不完整，查看丢记录计数',
    vr_connected='模拟 VR 已连接', closed='PC 后端已停止',
    debug_interlock='设备或通信中断，需手动继续',
    backend_output='PC 后端输出',
)


def format_number(value, unit, digits=3):
    return '—' if value is None else f'{value:.{digits}f} {unit}'


def format_flags(flags):
    if flags is None:
        return '—'
    bits = ' · '.join(f'{label}={int(bool(flags & bit))}' for bit, label in FLAGS)
    unknown = flags & ~KNOWN_FLAGS
    return f'0x{flags:04X}  {bits}' + (f' · 未知 0x{unknown:04X}' if unknown else '')


def format_peer_session(peer):
    if peer is None:
        return '—'
    if peer in (0, '0x0', '0x0000000000000000'):
        return '设备未建会话'
    return f'0x{peer:016X}' if isinstance(peer, int) else peer


class Backend:
    """Own one real.run subprocess and, in match mode, one UDP socket."""
    def __init__(self, port, baudrate, strategy, udp_port, match, experiment=False):
        self.records = queue.Queue(maxsize=128)
        self.log_dir = Path(tempfile.mkdtemp(prefix='armwrestling-stm32-'))
        self.address = ('127.0.0.1', udp_port)
        self.udp = None
        self.seq = 0
        self.last_reply = None
        self.closed_at = None
        self.started = time.monotonic()
        self.log('connect_requested', tool_version='0.1', port=port, baudrate=baudrate,
                 serial_format='8N1', serial_protocol='1.1', strategy=strategy, udp_port=udp_port,
                 match=match, experiment_log=experiment,
                 started_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'))
        args = [
            sys.executable, '-m', 'real.run', '--port', port, '--baudrate', str(baudrate),
            '--strategy', strategy, '--vr-port', str(udp_port),
            '--display', 'json', '--log-file', str(self.log_dir / 'pc.jsonl'),
            '--diagnostic-interval', '0.2', '--exit-on-stdin-eof',
        ]
        if experiment:
            args += ['--experiment-log', str(self.log_dir / 'experiment.jsonl')]
        try:
            if match:
                self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.udp.bind(('127.0.0.1', 0))
                self.udp.connect(self.address)
                self.udp.setblocking(False)
                args += ['--vr-source-port', str(self.udp.getsockname()[1])]
            else:
                args += ['--monitor-only']
            self.process = subprocess.Popen(
                args, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, encoding='utf-8', errors='replace',
                env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except OSError:
            self.close_udp()
            raise
        self.reader = threading.Thread(target=self.read_output, daemon=True)
        self.reader.start()

    def read_output(self):
        with self.process.stdout as output:
            for line in output:
                try:
                    record = json.loads(line)
                except ValueError:
                    record = dict(event='backend_output', detail=line.strip())
                item = time.monotonic(), record
                try:
                    self.records.put_nowait(item)
                except queue.Full:
                    try:
                        self.records.get_nowait()
                    except queue.Empty:
                        pass
                    self.records.put_nowait(item)

    def log(self, action, **fields):
        with (self.log_dir / 'gui.jsonl').open('a', encoding='utf-8') as output:
            output.write(json.dumps(dict(event=action, elapsed_s=round(time.monotonic() - self.started, 3),
                                         **fields),
                                    ensure_ascii=False) + '\n')

    def heartbeat(self, character, state, *, receive=False):
        if self.udp is None:
            return
        if receive:
            for _ in range(128):
                try:
                    packet = self.udp.recv(MAX_LINE + 1)
                except (BlockingIOError, ConnectionResetError):
                    break
                try:
                    message = decode_datagram(packet)
                except ValueError:
                    continue
                if message.get('v') == 1 and message.get('type') == 'status':
                    self.last_reply = time.monotonic()
        else:
            try:
                self.udp.send(encode_datagram(dict(
                    v=1, type='vr_update', seq=self.seq, character_id=character, match_state=state,
                )))
            except (BlockingIOError, ConnectionResetError):
                return
            self.seq += 1

    def close_udp(self):
        if self.udp is not None:
            self.udp.close()
            self.udp = None

    def close(self):
        self.close_udp()
        self.process.stdin.close()

    def export(self, path):
        with ZipFile(path, 'w', ZIP_DEFLATED) as archive:
            for logfile in sorted(self.log_dir.glob('*.jsonl')):
                archive.write(logfile, logfile.name)


class DebugPanel:
    def __init__(self, root, list_ports):
        self.root = root
        self.list_ports = list_ports
        self.backend = None
        self.last_backend = None
        self.ports = {}
        self.status = {}
        self.status_at = None
        self.session_id = None
        self.phase = 'waiting'
        self.last_tick = time.monotonic()
        self.next_send = 0.
        self.closing = False
        self.quitting = False
        self.opened = False
        self.failure = ''
        self.mode = tk.StringVar(value='monitor')
        self.port = tk.StringVar()
        self.baudrate = tk.StringVar(value='115200')
        self.udp_port = tk.StringVar(value='19102')
        self.role = tk.StringVar(value='person_02')
        self.strategy = tk.StringVar(value='balanced')
        self.experiment = tk.BooleanVar(value=False)
        self.profile = tk.StringVar()
        self.hint = tk.StringVar()
        self.values = {}
        self.buttons = {}
        self.build()
        self.scan()
        self.render(time.monotonic())
        root.protocol('WM_DELETE_WINDOW', lambda: self.disconnect(quit_window=True))
        root.bind('<FocusOut>', lambda event: root.after_idle(self.check_focus), add='+')
        root.bind('<Unmap>', lambda event: self.check_focus() if event.widget is root else None)
        root.after(50, self.tick)

    def build(self):
        root = self.root
        root.title('扳手腕 · STM32 联调工具（串口 V1.1）')
        root.geometry('1100x850')
        root.minsize(980, 700)
        font = ('Microsoft YaHei UI', -16)
        root.option_add('*Font', font)
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=font, background='#f0f3f8', foreground='#17273c')
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', -26, 'bold'))
        style.configure('TLabelframe.Label', font=('Microsoft YaHei UI', -16, 'bold'))
        style.configure('TButton', padding=(6, 4))
        style.configure('Primary.TButton', foreground='#113c6b')
        style.map('Primary.TButton', background=[('!disabled', '#dcecff')])
        style.configure('Hint.TLabel', foreground='#103f76', background='#edf5ff',
                        padding=(10, 8), font=('Microsoft YaHei UI', -17, 'bold'))
        style.configure('Value.TLabel', foreground='#13365d')
        body = ttk.Frame(root, padding=(14, 10))
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='STM32 上位机联调', style='Title.TLabel').pack(anchor='w')
        ttk.Label(body, textvariable=self.hint, style='Hint.TLabel', wraplength=900).pack(fill='x', pady=(5, 8))

        connection = ttk.LabelFrame(body, text='串口连接', padding=8)
        connection.pack(fill='x', pady=(0, 6))
        ttk.Label(connection, text='端口').grid(row=0, column=0, padx=(0, 5))
        self.port_box = ttk.Combobox(connection, textvariable=self.port, state='readonly', width=34)
        self.port_box.grid(row=0, column=1, padx=(0, 6), sticky='ew')
        self.refresh = ttk.Button(connection, text='刷新', width=6, command=self.scan)
        self.refresh.grid(row=0, column=2)
        ttk.Label(connection, text='波特率 / 8N1').grid(row=0, column=3, padx=(10, 5))
        self.baud_box = ttk.Entry(connection, textvariable=self.baudrate, width=8)
        self.baud_box.grid(row=0, column=4)
        self.connect_button = ttk.Button(connection, text='连接', width=8, command=self.connect)
        self.connect_button.grid(row=0, column=5, padx=(10, 4))
        self.disconnect_button = ttk.Button(connection, text='断开', width=8, command=self.disconnect)
        self.disconnect_button.grid(row=0, column=6)
        connection.columnconfigure(1, weight=1)
        modes = ttk.Frame(connection)
        modes.grid(row=1, column=0, columnspan=7, sticky='ew', pady=(7, 0))
        self.monitor_button = ttk.Radiobutton(modes, variable=self.mode, value='monitor',
                                              text='串口检查 · 仅 STOP', command=self.mode_changed)
        self.monitor_button.pack(side='left')
        self.match_button = ttk.Radiobutton(modes, variable=self.mode, value='match',
                                            text='模拟 VR 比赛', command=self.mode_changed)
        self.match_button.pack(side='left', padx=14)
        ttk.Label(modes, text='本地 UDP 127.0.0.1').pack(side='left', padx=(4, 4))
        self.udp_box = ttk.Entry(modes, textvariable=self.udp_port, width=7)
        self.udp_box.pack(side='left')
        self.experiment_box = ttk.Checkbutton(modes, text='记录逐帧实验日志', variable=self.experiment)
        self.experiment_box.pack(side='right')

        match = ttk.LabelFrame(body, text='模拟比赛', padding=8)
        match.pack(fill='x', pady=(0, 6))
        choices = ttk.Frame(match)
        choices.pack(fill='x')
        ttk.Label(choices, text='人物').pack(side='left', padx=(0, 4))
        self.role_box = ttk.Combobox(choices, textvariable=self.role, values=tuple(CHARACTERS), width=10)
        self.role_box.pack(side='left', padx=(0, 10))
        self.role_box.bind('<<ComboboxSelected>>', self.role_changed)
        ttk.Label(choices, text='策略').pack(side='left', padx=(0, 4))
        self.strategy_box = ttk.Combobox(choices, textvariable=self.strategy,
                                         values=STRATEGY_NAMES, width=11)
        self.strategy_box.pack(side='left', padx=(0, 10))
        tk.Label(choices, textvariable=self.profile, anchor='w', justify='left',
                 wraplength=590, font=font, bg=style.lookup('TFrame', 'background')).pack(side='left', fill='x')
        controls = ttk.Frame(match)
        controls.pack(fill='x', pady=(6, 0))
        for name, label, target in (
            ('start', '开始', 'running'), ('pause', '暂停', 'paused'),
            ('resume', '继续', 'running'), ('finish', '结束', 'finished'),
            ('reset', '再来一局', 'waiting'),
        ):
            button = ttk.Button(controls, text=label, width=9,
                                style='Primary.TButton' if name in ('start', 'resume') else 'TButton',
                                command=lambda phase=target: self.change_phase(phase))
            button.pack(side='left', padx=(0, 6))
            self.buttons[name] = button
        ttk.Label(controls, text='换策略需断开重连；demo 始终使用原控制').pack(side='left', padx=4)

        diagnostics = ttk.Frame(body)
        diagnostics.pack(fill='x', pady=(0, 6))
        diagnostics.columnconfigure((0, 1), weight=1, uniform='diagnostics')
        self.card(diagnostics, 0, '设备与串口 V1.1', (
            ('com', '串口'), ('handshake', '握手 / STOP 确认'), ('peer', '遥测会话'),
            ('telemetry', '串口遥测'), ('sample', 'MCU 角度采样'), ('arm', '本地授权'),
            ('flags', '状态位'), ('angle', '机械角度'), ('speed', '角速度'), ('force', '测力'),
        ))
        self.card(diagnostics, 1, '比赛与阻力指令', (
            ('phase', '面板请求 / PC 回报'), ('character', '请求 / 生效人物'),
            ('strategy', '启动 / 生效策略'), ('reason', '控制状态'), ('fatigue', '等效疲劳'),
            ('target', 'PC 计算目标'), ('sent', 'PC 最近完整写出'),
            ('ack', 'STM32 已接收序号'), ('applied', 'STM32 回报目标'),
            ('errors', '丢帧诊断'), ('log', '逐帧日志'),
        ))

        event_header = ttk.Frame(body)
        event_header.pack(fill='x', pady=(0, 4))
        ttk.Label(event_header, text='运行事件', style='TLabelframe.Label').pack(side='left')
        self.export_button = ttk.Button(event_header, text='保存本轮日志…', command=self.export)
        self.export_button.pack(side='right')
        events = ttk.Frame(body)
        events.pack(fill='both', expand=True)
        scroll = ttk.Scrollbar(events, orient='vertical')
        scroll.pack(side='right', fill='y')
        self.event_list = tk.Text(events, height=4, wrap='word', state='disabled',
                                  yscrollcommand=scroll.set, font=font, relief='flat', padx=6, pady=3)
        self.event_list.pack(fill='both', expand=True)
        scroll.configure(command=self.event_list.yview)

    def card(self, parent, column, title, fields):
        card = ttk.LabelFrame(parent, text=title, padding=7)
        card.grid(row=0, column=column, sticky='nsew', padx=(0, 4) if column == 0 else (4, 0))
        for row, (key, label) in enumerate(fields):
            variable = self.values[key] = tk.StringVar(value='—')
            ttk.Label(card, text=label).grid(row=row, column=0, sticky='nw', padx=(0, 8))
            ttk.Label(card, textvariable=variable, style='Value.TLabel', wraplength=325,
                      justify='left').grid(row=row, column=1, sticky='w')

    def event(self, message):
        output = self.event_list
        output.configure(state='normal')
        output.insert('end', f'{time.strftime("%H:%M:%S")}  {message}\n')
        if int(output.index('end-1c').split('.')[0]) > 100:
            output.delete('1.0', '2.0')
        output.see('end')
        output.configure(state='disabled')

    def scan(self):
        previous = self.port.get()
        try:
            self.ports = {
                f'{p.device} · {p.description}': p.device for p in sorted(self.list_ports())
            }
            self.failure = '' if self.ports else '未找到串口。接入设备或建立虚拟 COM 对后点击刷新。'
        except OSError as error:
            self.ports = {}
            self.failure = f'无法扫描串口：{error}'
        self.port_box.configure(values=tuple(self.ports))
        self.port.set(previous if previous in self.ports else next(iter(self.ports), ''))
        self.event(f'串口扫描完成，找到 {len(self.ports)} 个端口。')

    def mode_changed(self):
        self.failure = ''
        self.render(time.monotonic())

    def role_changed(self, _event=None):
        if self.backend is not None:
            self.backend.log('character_requested', character=self.role.get())
            self.send_state()
        self.render(time.monotonic())

    def connect(self):
        if self.backend is not None:
            return
        try:
            baud = int(self.baudrate.get())
            udp = int(self.udp_port.get())
            if baud <= 0 or not 1024 <= udp <= 65535 or udp == 9102:
                raise ValueError
        except ValueError:
            self.failure = '请输入有效波特率和 1024–65535 的调试 UDP 端口，9102 留给正式 VR。'
            return
        if self.port.get() not in self.ports:
            self.failure = '先刷新并选择一个串口。'
            return
        self.failure = ''
        self.phase = 'waiting'
        self.session_id = None
        self.status, self.status_at = {}, None
        self.opened = False
        try:
            self.backend = self.last_backend = Backend(
                self.ports[self.port.get()], baud, self.strategy.get(), udp, self.mode.get() == 'match',
                experiment=self.experiment.get(),
            )
        except OSError as error:
            self.failure = f'无法启动 PC 后端：{error}'
            self.event(self.failure)
            return
        self.event(f'连接 {self.ports[self.port.get()]} · {"模拟 VR 比赛" if self.mode.get() == "match" else "仅 STOP"}')
        self.next_send = 0.
        self.render(time.monotonic())

    def ready(self, now):
        backend = self.backend
        return (
            backend is not None and backend.udp is not None and not self.closing
            and self.status_at is not None and now - self.status_at < STATUS_TIMEOUT_S
            and backend.last_reply is not None and now - backend.last_reply < STATUS_TIMEOUT_S
            and self.status.get('serial_open') and self.status.get('serial_session_ready')
            and self.status.get('serial_session_confirmed')
            and self.status.get('telemetry_fresh') and self.status.get('serial_enable_ready')
            and self.status.get('vr_fresh')
        )

    def may_start(self, now):
        return (
            self.ready(now) and not self.status.get('debug_resume_required')
            and self.status.get('match_state') == self.phase
            and self.status.get('active_character_id') == self.role.get()
            and self.root.state() in ('normal', 'zoomed') and self.root.focus_displayof() is not None
        )

    def send_state(self):
        if self.backend is not None:
            try:
                self.backend.heartbeat(self.role.get(), self.phase)
            except OSError as error:
                self.failure = f'模拟 VR 发包失败：{error}'
            self.next_send = time.monotonic() + HEARTBEAT_S

    def change_phase(self, phase, reason='操作员'):
        if self.backend is None or self.backend.udp is None or self.closing or phase == self.phase:
            return
        if phase == 'running' and not self.may_start(time.monotonic()):
            self.event('尚未满足开始条件，请查看窗口顶部提示。')
            return
        self.phase = phase
        self.backend.log('match_requested', match_state=phase, character=self.role.get(), reason=reason)
        self.event(f'{reason} → {MATCH_LABELS[phase]}')
        self.send_state()
        self.render(time.monotonic())

    def check_focus(self):
        if self.phase == 'running' and (self.root.state() not in ('normal', 'zoomed') or
                                        self.root.focus_displayof() is None):
            self.change_phase('paused', '面板失去焦点')

    def disconnect(self, quit_window=False):
        self.quitting |= quit_window
        if self.backend is None:
            if self.quitting:
                self.root.destroy()
            return
        if self.closing:
            return
        self.change_phase('waiting', '断开前复位比赛')
        self.send_state()
        self.closing = True
        self.backend.closed_at = time.monotonic()
        self.backend.log('disconnect_requested')
        self.render(time.monotonic())

    def read_backend(self):
        backend = self.backend
        for _ in range(128):
            try:
                received, record = backend.records.get_nowait()
            except queue.Empty:
                break
            if record.get('type') == 'status':
                new_id = record.get('serial_session_id')
                if self.phase == 'running' and self.session_id is not None and new_id != self.session_id:
                    self.change_phase('paused', '串口会话已变化')
                self.session_id = new_id
                self.status, self.status_at = record, received
            elif 'event' in record:
                event = record['event']
                if event == 'serial_open':
                    self.opened = True
                    self.failure = ''
                if event == 'serial_timeout':
                    self.opened = False
                detail = record.get('error') or record.get('detail') or ''
                if not detail and event in ('debug_interlock', 'serial_session_restart'):
                    reason = record.get('reason', '')
                    detail = SERIAL_REASONS.get(reason, STOP_REASONS.get(reason, reason))
                message = EVENTS.get(event, event)
                message = f'{message} {detail}'.strip()
                if event in ('serial_unavailable', 'serial_error', 'backend_output'):
                    if message == self.failure:
                        continue
                    self.failure = message
                    if event == 'backend_output':
                        backend.log(event, detail=detail)
                self.event(message)

    def tick(self):
        now = time.monotonic()
        if self.backend is not None:
            backend = self.backend
            self.read_backend()
            if backend.process.poll() is not None:
                self.read_backend()
                backend.close()
                expected = self.closing
                backend.log('backend_exited', exit_code=backend.process.returncode, expected=expected)
                self.event('PC 后端已关闭。' if expected else 'PC 后端退出，比赛指令已撤销。')
                if not expected and not self.failure:
                    self.failure = 'PC 后端已退出。检查端口占用或运行事件，再点击连接。'
                self.backend, self.closing, self.phase = None, False, 'waiting'
                self.status, self.status_at, self.opened = {}, None, False
                if self.quitting:
                    self.root.destroy()
                    return
            elif self.closing:
                elapsed = now - backend.closed_at
                if elapsed > 1.5:
                    backend.process.terminate()
                elif elapsed > .15 and not backend.process.stdin.closed:
                    backend.close()
            elif backend.udp is not None:
                try:
                    backend.heartbeat(None, None, receive=True)
                except OSError as error:
                    self.failure = f'模拟 VR 收包失败：{error}'
                if self.phase == 'running':
                    if now - self.last_tick > STATUS_TIMEOUT_S:
                        self.change_phase('paused', '面板刷新超时')
                    elif not self.ready(now) or self.status.get('debug_resume_required'):
                        self.change_phase('paused', 'PC、串口或设备许可中断')
                    else:
                        self.check_focus()
                if now >= self.next_send:
                    self.send_state()
        self.last_tick = now
        self.render(now)
        self.root.after(50, self.tick)

    def render(self, now):
        connected = self.backend is not None
        match = self.mode.get() == 'match'
        self.port_box.configure(state='disabled' if connected else 'readonly')
        for control in (self.refresh, self.baud_box, self.udp_box, self.experiment_box, self.monitor_button,
                        self.match_button, self.connect_button):
            control.configure(state='disabled' if connected else 'normal')
        self.disconnect_button.configure(state='normal' if connected and not self.closing else 'disabled')
        self.role_box.configure(state='readonly' if match and self.phase != 'running' and
                                not self.closing else 'disabled')
        self.strategy_box.configure(state='readonly' if match and not connected else 'disabled')
        self.export_button.configure(state='normal' if self.last_backend else 'disabled')
        enabled = match and connected and not self.closing
        allowed = dict(start=self.phase == 'waiting' and self.may_start(now),
                       pause=self.phase == 'running',
                       resume=self.phase == 'paused' and self.may_start(now),
                       finish=self.phase in ('running', 'paused'),
                       reset=self.phase in ('finished', 'paused'))
        for key, button in self.buttons.items():
            button.configure(state='normal' if enabled and allowed[key] else 'disabled')

        person = CHARACTERS[self.role.get()]
        self.profile.set(
            '比赛操作已锁定' if not match else
            'demo 使用原控制，策略设置对其无效' if person.age_years is None else
            f'{person.name} · {person.age_years} 岁 · {person.sex} · {person.training_background}\n'
            f'演示参数：最大阻力 {person.brake_parameters.max_torque:.2f} N·m，发力建立 {person.rise_time_constant_s:g} s'
        )
        fresh_status = self.status_at is not None and now - self.status_at < STATUS_TIMEOUT_S
        status = self.status if fresh_status else {}
        serial_open = status.get('serial_open', self.opened and self.status_at is None)
        wire_fresh = status.get('serial_wire_fresh')
        control_fresh = status.get('telemetry_fresh')
        flags = status.get('serial_status_flags') if wire_fresh else None
        armed = bool(flags & FLAG_ARM_READY) if flags is not None else None
        handshake = status.get('serial_session_ready')
        confirmed = status.get('serial_session_confirmed')
        session = status.get('serial_session_id')
        peer = status.get('serial_peer_session_id') if wire_fresh else None
        matched = wire_fresh and handshake and session is not None and peer == session
        ack = status.get('serial_last_ack_seq') if matched and flags is not None and flags & FLAG_ACK_VALID else None
        ack_age = status.get('serial_ack_progress_age_ms')
        applied = status.get('serial_applied_target_nm') if wire_fresh else None
        sent = status.get('serial_last_sent')
        angle = status.get('angle_rad') if control_fresh else None
        raw_angle = status.get('serial_raw_angle_rad') if wire_fresh else None
        speed = status.get('velocity_rad_s') if control_fresh else None
        raw_speed = status.get('serial_raw_velocity_rad_s') if wire_fresh else None
        angle_valid = flags is not None and flags & FLAG_ANGLE_VALID
        speed_valid = flags is not None and flags & FLAG_VEL_VALID
        fatigue = status.get('fatigue_level')
        target = status.get('target_torque_nm')
        reason = status.get('reason')
        serial_reason = status.get('serial_status_reason')
        age = status.get('serial_wire_age_ms')
        rate = status.get('serial_telemetry_rate_hz')
        if wire_fresh:
            telemetry = f'有数据 · {format_number(rate, "Hz", 1)}'
            if age is not None:
                telemetry += f' · {age:.0f} ms 前'
        else:
            telemetry = f'已过期 · {age:.0f} ms 前' if age is not None else '等待读数'
        sample_ms = status.get('serial_mcu_sample_ms') if wire_fresh else None
        sample_state = SAMPLE_STATES.get(status.get('serial_sample_state'), '待采样')
        sample_age = status.get('serial_sample_age_ms')
        sample = '—' if sample_ms is None else f'{sample_ms} ms · {sample_state}'
        if sample_ms is not None and sample_age is not None:
            sample += f' · 距末次新采样 {sample_age:.0f} ms'
        angle_text = '—'
        if angle is not None:
            angle_text = f'{angle:.4f} rad / {math.degrees(angle):.2f}°（负角获胜）'
        elif raw_angle is not None:
            validity = '无效' if not angle_valid else '仅诊断'
            if serial_reason in ('sample_stale', 'sample_backwards'):
                validity = '采样无效'
            angle_text = f'原始 {raw_angle:.4f} rad（{validity}）'
        speed_text = '未提供' if wire_fresh else '—'
        if speed is not None and speed_valid:
            speed_text = f'{speed:.3f} rad/s / {math.degrees(speed):.1f}°/s'
        elif speed_valid and raw_speed is not None:
            speed_text = f'{raw_speed:.3f} rad/s（仅诊断）'
        ack_text = f'#{ack}' if ack is not None else ('等待有效 ACK' if matched else '—')
        if matched and status.get('serial_ack_pending') and ack_age is not None:
            ack_text += f' · 距确认进度 {ack_age:.0f} ms'
        errors = ' · '.join(f'{name} {value}' for name, value in (
            ('CRC', status.get('serial_crc_errors')),
            ('帧头', status.get('serial_header_errors')),
            ('序号', status.get('serial_sequence_drops')),
            ('组帧超时', status.get('serial_frame_timeouts')),
        ) if value is not None)
        dropped = status.get('serial_experiment_dropped')
        trace_error = status.get('serial_experiment_error')
        experiment_text = '关闭'
        if self.experiment.get():
            experiment_text = f'记录中 · 丢记录 {dropped}' if connected and dropped is not None else '已选择'
        local_reason = SERIAL_REASONS.get(serial_reason)
        control_reason = '—'
        if status.get('monitor_only'):
            control_reason = 'STOP · 通信检查'
        elif status.get('mode') == 'enabled':
            control_reason = 'ENABLE · 正常计算'
        elif reason:
            control_reason = f'STOP · {local_reason or STOP_REASONS.get(reason, reason)}'
        handshake_text = '等待匹配'
        if handshake:
            handshake_text = f'已匹配 · {"STOP 已确认" if confirmed else "等待 STOP 确认"}'
        if session is not None:
            handshake_text += f'\nPC {session}'
        rendered = dict(
            com='已打开' if serial_open else '未打开',
            handshake=handshake_text if serial_open else '—', peer=format_peer_session(peer),
            telemetry=telemetry, sample=sample,
            arm=('已获许可' if armed else '未获本地许可') if armed is not None else '—',
            flags=format_flags(flags), angle=angle_text, speed=speed_text,
            force=format_number(status.get('serial_force_n'), 'N') if flags is not None
                and flags & FLAG_FORCE_VALID else ('未提供' if wire_fresh else '—'),
            phase=f'{MATCH_LABELS[self.phase]} / {MATCH_LABELS.get(status.get("match_state"), "—")}' if
                match and connected else '通信检查 · 不发送比赛状态' if not match else '—',
            character=f'{self.role.get()} / {status.get("active_character_id") or "—"}' if match else '—',
            strategy=f'{STRATEGY_LABELS[self.strategy.get()]} / '
                     f'{STRATEGY_LABELS.get(status.get("strategy"), status.get("strategy") or "—")}'
                     if match else '—',
            reason=control_reason,
            fatigue='—' if fatigue is None else f'{fatigue:.1%}',
            target=format_number(target, 'N·m'),
            sent=f'{sent["mode"]} · {sent["target_mnm"]} mN·m · #{sent["seq"]} · 有效 {sent["valid_for_ms"]} ms'
                 if sent else '—',
            ack=ack_text,
            applied=f'{applied:.3f} N·m' + ('（仅诊断）' if not matched else '')
                    if applied is not None else '—',
            errors=errors or '—', log=experiment_text,
        )
        if not serial_open:
            rendered['sent'] = '—'
        if trace_error:
            rendered['log'] = '记录写入失败，查看导出日志'
        for key, value in rendered.items():
            self.values[key].set(value)

        if self.closing:
            hint = '正在断开，已撤销比赛请求。'
        elif self.failure:
            hint = self.failure
        elif not connected:
            hint = ('选定串口后点击连接。首次接板先隔离励磁功率，设备旁无人接触。' if not match
                    else '模拟比赛可能产生非零目标。首次接板先隔离励磁功率，设备旁无人接触。')
        elif not serial_open:
            hint = '等待打开串口。检查 COM 号、驱动和端口是否被占用。'
        elif serial_reason in ('ack_timeout', 'command_timeout', 'device_fault', 'estop', 'limit',
                               'unknown_flags', 'serial_error'):
            hint = f'{local_reason}。已停止正常阻力请求。'
        elif not handshake:
            hint = ('已收到诊断遥测，等待匹配的 SESSION_ACK。' if wire_fresh else
                    '等待匹配的 SESSION_ACK。检查端口、波特率和固件 V1.1。')
        elif not confirmed:
            hint = '会话已握手，等待下位机确认本会话的 STOP。'
        elif not wire_fresh:
            hint = '等待新鲜串口遥测。'
        elif serial_reason in ('angle_invalid', 'sample_stale', 'sample_backwards', 'not_ready'):
            hint = f'{local_reason}。已停止正常阻力请求。'
        elif not match:
            hint = ('串口、握手和遥测正常；当前仅发送 STOP/0。' if armed else
                    '串口、握手和遥测正常；设备未获本地使能，检查模式仍只发送 STOP/0。')
        elif not armed:
            hint = '串口和遥测正常；等待下位机本地许可。'
        elif self.phase == 'running':
            hint = '比赛进行中。点击暂停或结束可停止正常阻力。'
        elif self.phase == 'finished':
            hint = '比赛已结束。点击再来一局回到准备并清除等效疲劳。'
        elif self.may_start(now):
            hint = '准备就绪。点击继续恢复计算。' if self.phase == 'paused' else '准备就绪。点击开始进入比赛。'
        else:
            hint = ('点击面板获取操作焦点。' if self.ready(now) and self.root.focus_displayof() is None
                    else '等待 PC 确认模拟 VR 状态和人物。')
        if sent and ack == sent['seq'] and applied is not None and (
                abs(applied * 1000 - sent['target_mnm']) > .5):
            hint += ' 回报目标有差值，检查时序和设备本地限幅。'
        if dropped:
            hint += f' 实验日志有 {dropped} 条未记录。'
        self.hint.set(hint)

    def export(self):
        if self.last_backend is None:
            return
        path = filedialog.asksaveasfilename(
            parent=self.root, title='保存本轮诊断日志', defaultextension='.zip',
            initialfile='stm32-v11-' + time.strftime('%Y%m%d-%H%M%S') + '.zip',
            filetypes=[('ZIP 日志', '*.zip')],
        )
        if path:
            try:
                self.last_backend.export(path)
                self.event(f'日志已保存 · {Path(path).name}')
            except OSError as error:
                self.event(f'日志保存失败：{error}')


def main():
    try:
        from serial.tools.list_ports import comports
    except ImportError:
        sys.exit('需要 pyserial：python -m pip install pyserial')
    root = tk.Tk()
    DebugPanel(root, comports)
    root.mainloop()


if __name__ == '__main__':
    main()
