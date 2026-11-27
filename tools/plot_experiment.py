"""Offline export of V1.1 experiment JSONL. No serial or plotting dependency."""
import argparse
from collections import Counter, OrderedDict
from contextlib import contextmanager
import csv
from dataclasses import dataclass, field
import io
import json
import math
from pathlib import Path
from zipfile import BadZipFile, ZipFile

TRACE_COLUMNS = (
    'type', 'pc_monotonic_s', 'pc_elapsed_s', 'session_id', 'reported_session_id',
    'seq', 'disposition', 'reason', 'mode', 'valid_for_ms', 'status_flags',
    'mcu_sample_ms', 'mcu_since_first_ms', 'sample_state', 'mcu_sample_interval_ms',
    'pc_rx_interval_ms', 'pc_tx_interval_ms', 'seq_gap', 'angle_valid',
    'velocity_valid', 'force_valid', 'angle_rad', 'velocity_rad_s', 'force_n',
    'command_written_nm', 'mcu_target_reported_nm', 'last_command_seq',
    'ack_valid', 'verified_ack_seq', 'ack_written_nm', 'ack_target_diff_nm',
    'stop_ack_nonzero',
)
SNAPSHOT_COLUMNS = (
    'elapsed_s', 'serial_session_id', 'serial_peer_session_id', 'match_state', 'mode',
    'reason', 'serial_status_reason', 'mcu_sample_ms', 'angle_rad', 'velocity_rad_s',
    'force_n', 'pc_calculated_target_nm', 'mcu_target_reported_nm',
)
METRICS = {
    'angle_rad': '有效角度 (rad)',
    'velocity_rad_s': '有效角速度 (rad/s)',
    'command_written_nm': '完整写出指令目标 (N·m)',
    'mcu_target_reported_nm': 'MCU 回报目标 (N·m)',
    'force_n': '有效测力 (N)',
    'mcu_sample_interval_ms': 'MCU 采样更新时间间隔 (ms)',
    'pc_rx_interval_ms': 'PC 遥测到达间隔 (ms)',
    'pc_tx_interval_ms': 'PC 指令写出间隔 (ms)',
}


def number(value):
    if type(value) is float and math.isfinite(value) or type(value) is int and value.bit_length() <= 63:
        return value
    return None


def uint(value, bits):
    return value if type(value) is int and 0 <= value < 2**bits else None


def scaled(record, key):
    value = number(record.get(key))
    return value / 1000 if value is not None else None


def delta_ms(previous, now):
    if previous is not None and now is not None and now >= previous:
        return round((now - previous) * 1000, 6)
    return None


def records(stream):
    for line in stream:
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        yield row if isinstance(row, dict) else None


def zip_member(archive, name, *, required=True):
    members = [entry for entry in archive.infolist() if entry.filename == name]
    if not members and not required:
        return None
    if len(members) != 1:
        raise ValueError(f"ZIP 应包含一个根目录下的 {name}；如缺少实验日志，请先勾选逐帧记录。")
    return io.TextIOWrapper(archive.open(members[0]), encoding='utf-8-sig', errors='replace')


@contextmanager
def input_files(path):
    if path.suffix.lower() == '.zip':
        with ZipFile(path) as archive, zip_member(archive, 'experiment.jsonl') as trace:
            snapshots = zip_member(archive, 'pc.jsonl', required=False)
            try:
                yield trace, snapshots
            finally:
                if snapshots is not None:
                    snapshots.close()
    elif path.suffix.lower() == '.jsonl':
        with path.open(encoding='utf-8-sig', errors='replace') as trace:
            yield trace, None
    else:
        raise ValueError('请选择面板导出的 ZIP 或 experiment.jsonl。')


@dataclass
class Metric:
    count: int = 0
    total: float = 0
    minimum: float = math.inf
    maximum: float = -math.inf

    def add(self, value):
        if value is not None:
            self.count += 1
            self.total += value
            self.minimum = min(self.minimum, value)
            self.maximum = max(self.maximum, value)

    def describe(self):
        if not self.count:
            return '无有效记录'
        return (f'{self.count} 条，最小 {self.minimum:.4g}，'
                f'均值 {self.total / self.count:.4g}，最大 {self.maximum:.4g}')


@dataclass
class TelemetryClock:
    seq: int | None = None
    sample: int | None = None
    since_first_ms: int = 0
    pc_time: float | None = None

    def add(self, seq, sample, pc_time):
        """Unwrap within one session. Bad samples never replace the baseline."""
        if seq is None:
            return dict(sample_state='unavailable'), False
        if self.seq is not None:
            diff = (seq - self.seq) & 0xFFFF
            if not 0 < diff < 0x8000:
                return dict(sample_state='old_sequence'), False
            gap = diff - 1
        else:
            gap = None
        self.seq = seq
        result = dict(seq_gap=gap, pc_rx_interval_ms=delta_ms(self.pc_time, pc_time))
        self.pc_time = pc_time
        if sample is None:
            return dict(result, sample_state='unavailable'), True
        if self.sample is None:
            self.sample = sample
            return dict(result, sample_state='first', mcu_since_first_ms=0), True
        advance = (sample - self.sample) & 0xFFFFFFFF
        if advance == 0:
            return dict(result, sample_state='repeated', mcu_since_first_ms=self.since_first_ms), True
        if advance >= 0x80000000:
            return dict(result, sample_state='backwards'), True
        state = 'wrap' if sample < self.sample else 'advancing'
        self.sample = sample
        self.since_first_ms += advance
        return dict(result, sample_state=state, mcu_since_first_ms=self.since_first_ms,
                    mcu_sample_interval_ms=advance), True


@dataclass
class Analysis:
    counts: Counter = field(default_factory=Counter)
    sessions: Counter = field(default_factory=Counter)
    metrics: dict = field(default_factory=lambda: {key: Metric() for key in METRICS})
    clock: TelemetryClock = field(default_factory=TelemetryClock)
    telemetry_pair: tuple | None = None
    tx_session: str | None = None
    tx_time: float | None = None
    pc_start: float | None = None
    recent_commands: OrderedDict = field(default_factory=OrderedDict)
    dropped: int | None = None

    def add(self, record):
        if record is None:
            self.counts['bad_lines'] += 1
            return None
        kind = record.get('type')
        if not isinstance(kind, str):
            self.counts['bad_lines'] += 1
            return None
        self.counts[kind] += 1
        pc_time = number(record.get('pc_monotonic_s'))
        session = record.get('session_id')
        if not isinstance(session, str):
            session = None
        if self.pc_start is None:
            self.pc_start = pc_time
        row = dict(record, type=kind, session_id=session, pc_monotonic_s=pc_time,
                   pc_elapsed_s=round(pc_time - self.pc_start, 6) if pc_time is not None else None)
        if kind == 'trace_summary':
            self.dropped = uint(record.get('dropped'), 64)
        elif kind == 'command_written':
            self.command(record, row, session, pc_time)
        elif kind == 'telemetry':
            self.telemetry(record, row, session, pc_time)
        if session is not None and kind in ('command_written', 'telemetry'):
            self.sessions[session] += 1
        for key, metric in self.metrics.items():
            metric.add(number(row.get(key)))
        return row

    def command(self, record, row, session, pc_time):
        target = scaled(record, 'target_mnm')
        row['command_written_nm'] = target
        if self.tx_session == session:
            row['pc_tx_interval_ms'] = delta_ms(self.tx_time, pc_time)
        self.tx_session, self.tx_time = session, pc_time
        sequence = uint(record.get('seq'), 16)
        if session and sequence is not None and target is not None:
            key = (session, sequence)
            self.recent_commands[key] = (record.get('mode'), target)
            self.recent_commands.move_to_end(key)
            if len(self.recent_commands) > 128:
                self.recent_commands.popitem(last=False)

    def telemetry(self, record, row, session, pc_time):
        peer = record.get('reported_session_id')
        if not isinstance(peer, str):
            peer = None
        pair = (session, peer)
        disposition = record.get('disposition')
        if disposition in ('old_sequence', 'retired_session'):
            self.counts[str(disposition)] += 1
            return
        if pair != self.telemetry_pair:
            self.telemetry_pair = pair
            self.clock = TelemetryClock()
        timing, accepted = self.clock.add(uint(record.get('seq'), 16),
                                          uint(record.get('mcu_sample_ms'), 32), pc_time)
        row.update(timing)
        sample_state = timing['sample_state']
        self.counts[sample_state] += 1
        if not accepted:
            return
        self.counts['seq_gap'] += timing.get('seq_gap') or 0
        if (record.get('angle_valid') is True and sample_state not in ('backwards', 'unavailable')
                and disposition not in ('angle_invalid', 'sample_stale', 'sample_backwards')):
            row['angle_rad'] = scaled(record, 'angle_mrad')
        if record.get('velocity_valid') is True:
            row['velocity_rad_s'] = scaled(record, 'velocity_mrad_s')
        if record.get('force_valid') is True:
            row['force_n'] = scaled(record, 'sensor_force_mn')
        row['mcu_target_reported_nm'] = scaled(record, 'applied_target_mnm')
        self.compare_ack(record, row, pair)

    def compare_ack(self, record, row, pair):
        session, peer = pair
        verified = uint(record.get('verified_ack_seq'), 16)
        if (not session or session != peer or record.get('ack_valid') is not True
                or verified is None or verified != record.get('last_command_seq')):
            return
        written = self.recent_commands.get((session, verified))
        applied = row.get('mcu_target_reported_nm')
        if written is None or applied is None:
            return
        mode, target = written
        row['ack_written_nm'] = target
        row['ack_target_diff_nm'] = round(applied - target, 6)
        row['stop_ack_nonzero'] = mode == 'STOP' and applied != 0
        self.counts['stop_ack_nonzero'] += row['stop_ack_nonzero']

    def summary(self):
        counts = self.counts
        dropped = self.dropped if self.dropped is not None else '未知（缺少最终 trace_summary）'
        lines = [
            'STM32 V1.1 实验日志分析',
            f'遥测记录 {counts["telemetry"]} 条 · 指令完整写出 {counts["command_written"]} 条 · 会话重建 {counts["session_restart"]} 次',
            f'记录器报告丢记录 {dropped} · 无法解析 {counts["bad_lines"]} 行',
            f'相邻遥测序号跳过 {counts["seq_gap"]} · 重复旧序号 {counts["old_sequence"]} · 已撤销会话遥测 {counts["retired_session"]}',
            f'MCU 采样时间回绕 {counts["wrap"]} · 重复 {counts["repeated"]} · 倒退 {counts["backwards"]}',
            f'已校验 STOP ACK 对应非零目标回报 {counts["stop_ack_nonzero"]} 条',
            '',
        ]
        lines.extend(f'{label}  {self.metrics[key].describe()}' for key, label in METRICS.items())
        for key, label in (('pc_rx_interval_ms', '遥测到达'), ('pc_tx_interval_ms', '指令写出')):
            intervals = self.metrics[key]
            if intervals.total > 0:
                lines.append(f'按记录间隔折算的{label}频率  {1000 * intervals.count / intervals.total:.2f} Hz')
        if self.sessions:
            lines.extend(('', '各 PC 会话的遥测及指令记录数'))
            lines.extend(f'{session}  {count}' for session, count in self.sessions.items())
        lines.extend((
            '',
            'PC 到达/写出时间来自 PC 单调时钟，MCU 采样时间来自板端时钟。两者不能相减得到单程延迟。',
            '序号间隔只描述这个文件的可见记录，日志节选或漏记同样会表现为跳号。PC 到达间隔可能为零，例如串口读取时聚合多帧。',
            'ACK 差值仅比较日志中同会话、经核验且能找到写出记录的同序号。MCU 目标回报与有效测力分列。',
        ))
        return '\n'.join(lines) + '\n'


def write_snapshots(stream, output):
    writer = csv.DictWriter(output, fieldnames=SNAPSHOT_COLUMNS, extrasaction='ignore')
    writer.writeheader()
    for row in records(stream):
        if row is not None and row.get('type') == 'status':
            writer.writerow(dict(row, mcu_sample_ms=row.get('serial_mcu_sample_ms'),
                                 force_n=row.get('serial_force_n'),
                                 pc_calculated_target_nm=row.get('target_torque_nm'),
                                 mcu_target_reported_nm=row.get('serial_applied_target_nm')))


def analyze(source, out_dir):
    analysis = Analysis()
    with input_files(source) as (trace, snapshots):
        out_dir.mkdir(parents=True, exist_ok=True)
        with (out_dir / 'experiment.csv').open('w', encoding='utf-8-sig', newline='') as output:
            writer = csv.DictWriter(output, fieldnames=TRACE_COLUMNS, extrasaction='ignore')
            writer.writeheader()
            for record in records(trace):
                row = analysis.add(record)
                if row is not None:
                    writer.writerow(row)
        if snapshots is not None:
            with (out_dir / 'pc_snapshots.csv').open('w', encoding='utf-8-sig', newline='') as output:
                write_snapshots(snapshots, output)
    report = analysis.summary()
    if snapshots is not None:
        report += 'pc_snapshots.csv 的 PC 计算目标来自面板使用的低频快照，计算目标与逐条串口写出分别记录。\n'
    (out_dir / 'summary.txt').write_text(report, encoding='utf-8')
    return analysis


def main():
    parser = argparse.ArgumentParser(description='离线分析 V1.1 实验日志，生成 CSV 和统计。')
    parser.add_argument('input', type=Path, help='联调面板 ZIP 或 experiment.jsonl')
    parser.add_argument('--out-dir', type=Path, help='输出目录，默认使用同目录下的“原文件名-analysis”')
    args = parser.parse_args()
    out_dir = args.out_dir or args.input.with_name(args.input.stem + '-analysis')
    try:
        result = analyze(args.input, out_dir)
    except (OSError, ValueError, BadZipFile, RuntimeError) as error:
        parser.exit(1, f'无法分析日志：{error}\n')
    print(result.summary(), end='')
    print(f'输出目录  {out_dir.resolve()}')


if __name__ == '__main__':
    main()
