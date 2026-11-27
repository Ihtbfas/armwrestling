"""PC serial V1.1 adapter. Transport, sample freshness and command ACKs."""
from collections import deque
import secrets
import time

from common.serial_protocol import (
    FLAG_ACK_VALID, FLAG_ANGLE_VALID, FLAG_ARM_READY, FLAG_CMD_TIMEOUT,
    FLAG_ESTOP, FLAG_FAULT, FLAG_FORCE_VALID, FLAG_LIMIT, FLAG_READY, FLAG_VEL_VALID, KNOWN_FLAGS,
    TYPE_SESSION_ACK, TYPE_TELEMETRY, FrameParser, MODE_ENABLE, MODE_STOP,
    command_frame, decode_payload, is_new_sequence, sample_delta_ms, session_open_frame,
)

ACK_TIMEOUT_S = .2


def new_session_id():
    return secrets.randbelow(2**64 - 1) + 1


class Stm32Link:
    def __init__(self, port, now=None, session_id=None, *, trace=None, telemetry_timeout=.25):
        self.port = port
        self.parser = FrameParser()
        self.trace = trace
        self.telemetry_timeout = telemetry_timeout
        self.monitor_only = False
        self._require_complete_write = False
        self.retired = deque(maxlen=16)
        self.telemetry_count = self.sequence_drops = self.session_drops = 0
        self.payload_errors = self.ack_rejected = 0
        self.latest = self.last_wire_at = self.last_wire_seq = None
        self.interruption = None
        self.start_session(0. if now is None else now, session_id=session_id)

    @classmethod
    def open(cls, port_name, baudrate=115200, *, write_timeout=.02, **options):
        try:
            import serial
        except ImportError as error:
            raise RuntimeError('serial mode needs pyserial: python -m pip install pyserial') from error
        port = serial.Serial(port_name, baudrate=baudrate, timeout=0, write_timeout=write_timeout)
        link = cls(port, **options)
        link._require_complete_write = write_timeout != 0
        return link

    def close(self):
        self.port.close()

    def start_session(self, now, reason=None, *, session_id=None):
        if reason is not None:
            self.retired.append(self.session_id)
            self.interruption = reason
            self._trace('session_restart', now, reason=reason)
        self.session_id = new_session_id() if session_id is None else session_id
        self.session_ready = self.session_confirmed = self.matched_telemetry = False
        self.next_open = now
        self.last_device_seq = self.last_sample_ms = self.sample_at = None
        self.control_angle = None
        self.sample_state = 'unavailable'
        self.tx_seq = self.open_seq = 0
        self.pending = b''
        self.blocked_since = self._pending_command = None
        self.last_sent_command = self.last_sent_at = None
        self.outstanding = {}
        self.last_ack_seq = self.last_ack_at = None

    def _trace(self, kind, now, **fields):
        if self.trace is not None:
            self.trace(dict(type=kind, pc_monotonic_s=now, session_id=f'0x{self.session_id:016X}', **fields))

    def _write(self, packet, now, command=None):
        self.pending, self.blocked_since, self._pending_command = packet, now, command
        self._flush(now)

    def _flush(self, now):
        if not self.pending:
            return
        started = time.monotonic() if self._require_complete_write else None
        sent = self.port.write(self.pending) or 0
        self.pending = self.pending[sent:]
        if self.pending:
            if self._require_complete_write or now - self.blocked_since >= ACK_TIMEOUT_S:
                raise TimeoutError('serial write did not complete')
            return
        self.blocked_since = None
        if started is not None:
            now += time.monotonic() - started
        if self._pending_command is not None:
            command = self._pending_command
            self.last_sent_command, self.last_sent_at = command, now
            self.outstanding[command['seq']] = (now, command['mode'])
            self._trace('command_written', now, **command)
            self._pending_command = None

    def wire_fresh(self, now):
        return self.last_wire_at is not None and now - self.last_wire_at < self.telemetry_timeout

    def sample_fresh(self, now):
        return (self.wire_fresh(now) and self.sample_at is not None
                and now - self.sample_at < self.telemetry_timeout)

    def ack_pending_age(self, now):
        if not self.outstanding:
            return None
        since = next(iter(self.outstanding.values()))[0]
        if self.last_ack_at is not None:
            since = max(since, self.last_ack_at)
        return max(0., now - since)

    def reason(self, now):
        value = self.latest if self.wire_fresh(now) else None
        flags = value.status_flags if value is not None else 0
        if flags & FLAG_CMD_TIMEOUT:
            return 'command_timeout'
        if not self.session_ready or not self.matched_telemetry:
            return 'waiting_handshake'
        if value is None:
            return 'telemetry_timeout'
        if flags & ~KNOWN_FLAGS:
            return 'unknown_flags'
        if flags & FLAG_FAULT:
            return 'device_fault'
        if flags & FLAG_ESTOP:
            return 'estop'
        if flags & FLAG_LIMIT:
            return 'limit'
        if not flags & FLAG_READY:
            return 'not_ready'
        if not flags & FLAG_ANGLE_VALID or abs(value.angle_rad) > 100:
            return 'angle_invalid'
        if self.sample_state == 'backwards':
            return 'sample_backwards'
        if not self.sample_fresh(now):
            return 'sample_stale'
        if not self.session_confirmed:
            return 'waiting_stop_ack'
        if not flags & FLAG_ARM_READY:
            return 'unarmed'
        return 'ready'

    def control_ready(self, now):
        return self.reason(now) == 'ready'

    def _accept_ack(self, value, now):
        if not value.status_flags & FLAG_ACK_VALID:
            return
        seq = value.last_command_seq
        if seq == self.last_ack_seq:
            return
        if seq not in self.outstanding or not is_new_sequence(seq, self.last_ack_seq):
            self.ack_rejected += 1
            return
        mode = self.outstanding[seq][1]
        for written in list(self.outstanding):
            del self.outstanding[written]
            if written == seq:
                break
        self.last_ack_seq, self.last_ack_at = seq, now
        if mode == 'STOP':
            self.session_confirmed = True

    def _observation(self, frame, value, now):
        if value.session_id in self.retired:
            self.session_drops += 1
            return 'retired_session'
        if self.matched_telemetry and value.session_id != self.session_id:
            self.start_session(now, 'command_timeout' if value.status_flags & FLAG_CMD_TIMEOUT
                               else 'session_mismatch')
        matching = self.session_ready and value.session_id == self.session_id
        if matching and not is_new_sequence(frame.sequence, self.last_device_seq):
            self.sequence_drops += 1
            return 'old_sequence'
        prior_ready = self.control_ready(now)
        self.latest, self.last_wire_at, self.last_wire_seq = value, now, frame.sequence
        self.telemetry_count += 1
        if not matching:
            return 'unpaired' if value.session_id == 0 else 'waiting_handshake'
        self.matched_telemetry = True
        self.last_device_seq = frame.sequence
        self._accept_ack(value, now)
        sample = value.mcu_sample_ms
        delta = 1 if self.last_sample_ms is None else sample_delta_ms(sample, self.last_sample_ms)
        angle_valid = value.status_flags & FLAG_ANGLE_VALID and abs(value.angle_rad) <= 100
        if delta < 0:
            self.sample_state, self.sample_at = 'backwards', None
        elif delta > 0:
            self.last_sample_ms = sample
            self.sample_state = 'advancing'
            self.sample_at = now if angle_valid else None
            self.control_angle = value.angle_rad if angle_valid else None
        else:
            if self.sample_state != 'backwards':
                self.sample_state = 'repeated'
            if not angle_valid:
                self.sample_at = None
        reason = self.reason(now)
        if prior_ready and reason != 'ready':
            self.interruption = reason
        return reason

    def _trace_telemetry(self, frame, value, now, disposition):
        self._trace('telemetry', now, seq=frame.sequence, disposition=disposition,
                    reported_session_id=f'0x{value.session_id:016X}', mcu_sample_ms=value.mcu_sample_ms,
                    angle_mrad=value.angle_mrad, velocity_mrad_s=value.velocity_mrad_s,
                    sensor_force_mn=value.sensor_force_mn, status_flags=value.status_flags,
                    angle_valid=bool(value.status_flags & FLAG_ANGLE_VALID),
                    velocity_valid=bool(value.status_flags & FLAG_VEL_VALID),
                    force_valid=bool(value.status_flags & FLAG_FORCE_VALID),
                    last_command_seq=value.last_command_seq, ack_valid=bool(value.status_flags & FLAG_ACK_VALID),
                    verified_ack_seq=self.last_ack_seq, applied_target_mnm=value.applied_target_mnm)

    def poll(self, now=None):
        now = time.monotonic() if now is None else now
        available = self.port.in_waiting
        for frame in self.parser.feed(self.port.read(min(max(available, 1), 4096)), now):
            try:
                value = decode_payload(frame)
            except ValueError:
                self.payload_errors += 1
                continue
            if frame.kind == TYPE_SESSION_ACK:
                if value.session_id == self.session_id and not self.session_ready:
                    self.session_ready = True
                    self._trace('session_ack', now, seq=frame.sequence)
            elif frame.kind == TYPE_TELEMETRY:
                disposition = self._observation(frame, value, now)
                if self.trace is not None:
                    self._trace_telemetry(frame, value, now, disposition)
        if not self.matched_telemetry and now >= self.next_open and not self.pending:
            self._write(session_open_frame(self.open_seq, self.session_id), now)
            self._trace('session_open_written', now, seq=self.open_seq)
            self.open_seq = (self.open_seq + 1) & 0xFFFF
            self.next_open = now + .2
        else:
            self._flush(now)

    def control_input(self, now):
        if not self.matched_telemetry or not self.sample_fresh(now):
            return None
        # The symmetric baseline magnitude depends on angle. Zero is only an
        # internal placeholder if the board has no valid speed measurement.
        speed = self.measured_velocity
        return dict(v=1, type='telemetry', seq=self.telemetry_count,
                    angle_rad=self.control_angle, velocity_rad_s=speed if speed is not None else 0.,
                    device_status='ready' if self.control_ready(now) else 'fault')

    @property
    def measured_velocity(self):
        value = self.latest
        if value is not None and value.status_flags & FLAG_VEL_VALID and abs(value.velocity_rad_s) <= 1000:
            return value.velocity_rad_s
        return None

    def send_command(self, command, now=None):
        now = time.monotonic() if now is None else now
        if not self.session_ready or not self.matched_telemetry or self.pending:
            return False
        enabled = not self.monitor_only and command['mode'] == 'enabled' and self.control_ready(now)
        torque = int(round(command['target_torque_nm'] * 1000)) if enabled else 0
        mode = MODE_ENABLE if enabled else MODE_STOP
        seq = self.tx_seq
        packet = command_frame(seq, self.session_id, mode, torque, command['valid_for_ms'])
        self.tx_seq = (seq + 1) & 0xFFFF
        self._write(packet, now, dict(seq=seq, mode='ENABLE' if enabled else 'STOP',
                                      target_mnm=torque, valid_for_ms=command['valid_for_ms']))
        return True
