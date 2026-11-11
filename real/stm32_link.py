"""STM32 V1 serial adapter shared by virtual PTY and real USB COM ports.

The control law is in common/control.py; this module only handles bytes.
Requires pyserial only when a serial port is actually opened.
"""
import secrets
import time

from common.serial_protocol import (
    TYPE_SESSION_ACK, TYPE_TELEMETRY, FrameParser, MODE_ENABLE, MODE_STOP,
    command_frame, decode_payload, is_new_sequence, session_open_frame,
)


class Stm32Link:
    def __init__(self, port, now=None, session_id=None):
        self.port = port
        self.session_id = secrets.randbits(64) if session_id is None else session_id
        self.parser = FrameParser()
        self.session_ready = False
        self.arm_ready = False
        self.last_device_seq = None
        self.telemetry_count = 0
        self.tx_seq = 0
        self.next_open = 0.0
        self.pending = b''
        self.blocked_since = None
        self.latest = None
        if now is not None:
            self.next_open = now

    @classmethod
    def open(cls, port_name, baudrate=115200):
        try:
            import serial
        except ImportError as error:
            raise RuntimeError('serial mode needs pyserial: python -m pip install pyserial') from error
        port = serial.Serial(port_name, baudrate=baudrate, timeout=0, write_timeout=0)
        return cls(port)

    def close(self):
        self.port.close()

    def _send(self, packet, now):
        if self.pending:
            return False
        self.pending = packet
        self.blocked_since = now
        return True

    def _flush(self, now):
        if not self.pending:
            return
        sent = self.port.write(self.pending)
        if sent is None:
            sent = 0
        self.pending = self.pending[sent:]
        if not self.pending:
            self.blocked_since = None
        elif self.blocked_since is not None and now - self.blocked_since > 0.25:
            raise TimeoutError('serial output blocked >250 ms')

    def poll(self, now=None):
        """Return new telemetry in the same shape BridgeState already accepts."""
        now = time.monotonic() if now is None else now
        self.parser.expire(now)
        available = self.port.in_waiting
        chunk = self.port.read(min(max(available, 1), 4096))
        updates = []
        for frame in self.parser.feed(chunk, now):
            try:
                value = decode_payload(frame)
            except ValueError:
                continue
            if frame.kind == TYPE_SESSION_ACK:
                if value.session_id == self.session_id and not self.session_ready:
                    self.session_ready = True
                    self.last_device_seq = None
            elif frame.kind == TYPE_TELEMETRY and self.session_ready:
                if not is_new_sequence(frame.sequence, self.last_device_seq):
                    continue
                self.last_device_seq = frame.sequence
                self.arm_ready = bool(value.can_enable)
                update = dict(v=1, type='telemetry', seq=self.telemetry_count,
                              angle_rad=value.angle_rad,
                              velocity_rad_s=value.velocity_rad_s,
                              device_status='ready' if self.arm_ready else 'fault')
                self.telemetry_count += 1
                self.latest = value
                updates.append(update)
        if not self.session_ready and now >= self.next_open and not self.pending:
            self._send(session_open_frame(self.tx_seq, self.session_id), now)
            self.tx_seq = (self.tx_seq + 1) & 0xFFFF
            self.next_open = now + .2
        self._flush(now)
        return updates

    def send_command(self, command, now=None):
        """Never send ENABLE without a matching session and ARM_READY telemetry."""
        now = time.monotonic() if now is None else now
        if not self.session_ready or self.pending:
            return False
        enabled = command['mode'] == 'enabled' and self.arm_ready
        torque = int(round(command['target_torque_nm'] * 1000)) if enabled else 0
        mode = MODE_ENABLE if enabled else MODE_STOP
        packet = command_frame(self.tx_seq, self.session_id, mode, torque,
                               command['valid_for_ms'])
        self._send(packet, now)
        self.tx_seq = (self.tx_seq + 1) & 0xFFFF
        self._flush(now)
        return True
