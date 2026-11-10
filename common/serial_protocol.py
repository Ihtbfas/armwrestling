"""STM32 <-> PC serial wire protocol V1; Python standard library only.

These bytes can travel over USART+USB-serial or USB CDC. This module
only handles framing/payloads; it does not open hardware ports.
"""
import binascii
from dataclasses import dataclass
import math
import struct
import time

SYNC = b'\xA5\x5A'
VERSION = 1
MAX_PAYLOAD = 32
TYPE_TELEMETRY = 1
TYPE_BRAKE_COMMAND = 2
TYPE_SESSION_OPEN = 3
TYPE_SESSION_ACK = 4
MODE_STOP = 0
MODE_ENABLE = 1
FLAG_READY = 1 << 0
FLAG_FAULT = 1 << 1
FLAG_ESTOP = 1 << 2
FLAG_LIMIT = 1 << 3
FLAG_ACK_VALID = 1 << 4
FLAG_FORCE_VALID = 1 << 5
FLAG_ARM_READY = 1 << 6  # Local operator re-arm completed; not a PC command.
_HEADER = struct.Struct('<BBHB')  # version, kind, sequence, payload length
_TELEMETRY = struct.Struct('<iiiHHH')  # angle, speed, measured force, status, ack, setpoint
_COMMAND = struct.Struct('<QBHH')  # session, mode, magnitude, lifetime
_SESSION = struct.Struct('<Q')  # uint64 session id, chosen fresh by the PC
_PAYLOAD_SIZES = {
    TYPE_TELEMETRY: _TELEMETRY.size,
    TYPE_BRAKE_COMMAND: _COMMAND.size,
    TYPE_SESSION_OPEN: _SESSION.size,
    TYPE_SESSION_ACK: _SESSION.size,
}

@dataclass(frozen=True)
class Frame:
    kind: int
    sequence: int
    payload: bytes

@dataclass(frozen=True)
class Telemetry:
    angle_mrad: int
    velocity_mrad_s: int
    sensor_force_mn: int
    status_flags: int
    last_command_seq: int
    applied_target_mnm: int

    @property
    def angle_rad(self):
        return self.angle_mrad / 1000.0

    @property
    def velocity_rad_s(self):
        return self.velocity_mrad_s / 1000.0

    @property
    def force_newtons(self):
        return self.sensor_force_mn / 1000.0 if self.status_flags & FLAG_FORCE_VALID else None

    @property
    def can_enable(self):
        # READY alone is not an operator's permission to re-arm a device.
        return (self.status_flags & (FLAG_READY | FLAG_ARM_READY)
                == FLAG_READY | FLAG_ARM_READY
                and not self.status_flags & (FLAG_FAULT | FLAG_ESTOP | FLAG_LIMIT | ~0x7F))

    @property
    def device_status(self):
        if self.status_flags & (FLAG_FAULT | FLAG_ESTOP | FLAG_LIMIT | ~0x7F):
            return 'fault'
        return 'ready' if self.status_flags & FLAG_READY else 'fault'

@dataclass(frozen=True)
class BrakeCommand:
    session_id: int
    mode: int
    target_torque_mnm: int
    valid_for_ms: int

    @property
    def target_torque_nm(self):
        return self.target_torque_mnm / 1000.0

@dataclass(frozen=True)
class SessionMessage:
    session_id: int


def _uint(value, maximum, name):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f'{name} must be an integer in [0, {maximum}]')
    return value

def _int32(value, name):
    if type(value) is not int or not -(2**31) <= value < 2**31:
        raise ValueError(f'{name} must be a signed 32-bit integer')
    return value

def crc16(data):
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection/xorout)."""
    return binascii.crc_hqx(data, 0xFFFF)

def encode_frame(kind, sequence, payload):
    _uint(kind, 255, 'kind')
    _uint(sequence, 65535, 'sequence')
    if not isinstance(payload, bytes) or len(payload) > MAX_PAYLOAD:
        raise ValueError('payload must be bytes with length <= 32')
    content = _HEADER.pack(VERSION, kind, sequence, len(payload)) + payload
    return SYNC + content + struct.pack('<H', crc16(content))

def decode_frame(packet):
    if not isinstance(packet, bytes) or len(packet) < 9 or packet[:2] != SYNC:
        raise ValueError('invalid packet start')
    version, kind, sequence, size = _HEADER.unpack(packet[2:7])
    if version != VERSION or _PAYLOAD_SIZES.get(kind) != size or len(packet) != 9 + size:
        raise ValueError('invalid version, message type or frame size')
    received_crc = struct.unpack('<H', packet[-2:])[0]
    if crc16(packet[2:-2]) != received_crc:
        raise ValueError('CRC mismatch')
    return Frame(kind, sequence, packet[7:-2])

def telemetry_frame(sequence, angle_mrad, velocity_mrad_s, sensor_force_mn, status_flags,
                    last_command_seq=0, applied_target_mnm=0):
    payload = _TELEMETRY.pack(_int32(angle_mrad, 'angle_mrad'),
                              _int32(velocity_mrad_s, 'velocity_mrad_s'),
                              _int32(sensor_force_mn, 'sensor_force_mn'),
                              _uint(status_flags, 65535, 'status_flags'),
                              _uint(last_command_seq, 65535, 'last_command_seq'),
                              _uint(applied_target_mnm, 65535, 'applied_target_mnm'))
    return encode_frame(TYPE_TELEMETRY, sequence, payload)

def session_open_frame(sequence, session_id):
    return encode_frame(TYPE_SESSION_OPEN, sequence,
                        _SESSION.pack(_uint(session_id, 2**64-1, 'session_id')))


def session_ack_frame(sequence, session_id):
    return encode_frame(TYPE_SESSION_ACK, sequence,
                        _SESSION.pack(_uint(session_id, 2**64-1, 'session_id')))


def command_frame(sequence, session_id, mode, target_torque_mnm=0, valid_for_ms=200):
    _uint(session_id, 2**64-1, 'session_id')
    _uint(mode, 1, 'mode')
    _uint(target_torque_mnm, 65535, 'target_torque_mnm')
    if type(valid_for_ms) is not int or not 20 <= valid_for_ms <= 500:
        raise ValueError('valid_for_ms must be in [20, 500]')
    if mode == MODE_STOP and target_torque_mnm != 0:
        raise ValueError('STOP must have zero torque')
    return encode_frame(TYPE_BRAKE_COMMAND, sequence,
                        _COMMAND.pack(session_id, mode, target_torque_mnm, valid_for_ms))

def decode_payload(frame):
    if frame.kind == TYPE_TELEMETRY:
        if len(frame.payload) != _TELEMETRY.size:
            raise ValueError('telemetry payload must be 18 bytes')
        return Telemetry(*_TELEMETRY.unpack(frame.payload))
    if frame.kind in (TYPE_SESSION_OPEN, TYPE_SESSION_ACK):
        if len(frame.payload) != _SESSION.size:
            raise ValueError('session payload must be 8 bytes')
        return SessionMessage(*_SESSION.unpack(frame.payload))
    if frame.kind == TYPE_BRAKE_COMMAND:
        if len(frame.payload) != _COMMAND.size:
            raise ValueError('brake command payload must be 13 bytes')
        cmd = BrakeCommand(*_COMMAND.unpack(frame.payload))
        if cmd.mode not in (MODE_STOP, MODE_ENABLE) or not 20 <= cmd.valid_for_ms <= 500:
            raise ValueError('invalid brake command')
        if cmd.mode == MODE_STOP and cmd.target_torque_mnm != 0:
            raise ValueError('STOP must have zero torque')
        return cmd
    raise ValueError('unknown message type')

def is_new_sequence(value, previous):
    """Modulo-65536 comparison. Set previous=None on a new serial session."""
    _uint(value, 65535, 'sequence')
    if previous is None:
        return True
    _uint(previous, 65535, 'previous')
    difference = (value - previous) & 0xFFFF
    return 0 < difference < 0x8000

class FrameParser:
    """CRC-checked byte-stream parser with early header checks and frame timeout."""

    def __init__(self, assembly_timeout_s=0.1):
        if not math.isfinite(assembly_timeout_s) or assembly_timeout_s <= 0:
            raise ValueError('assembly_timeout_s must be positive')
        self.buffer = bytearray()
        self.assembly_timeout_s = assembly_timeout_s
        self.started_at = None

    def expire(self, now=None):
        """Call periodically even without incoming bytes; discard stalled fragments."""
        now = time.monotonic() if now is None else now
        if (self.buffer and self.started_at is not None
                and now - self.started_at >= self.assembly_timeout_s):
            self.buffer.clear()
            self.started_at = None
            return True
        return False

    def feed(self, data, now=None):
        if not isinstance(data, bytes):
            raise TypeError('data must be bytes')
        now = time.monotonic() if now is None else now
        self.expire(now)
        self.buffer.extend(data)
        frames = []
        while True:
            start = self.buffer.find(SYNC)
            if start < 0:
                self.buffer = bytearray(b'\xA5') if self.buffer.endswith(b'\xA5') else bytearray()
                self.started_at = now if self.buffer else None
                break
            if start:
                del self.buffer[:start]
                self.started_at = None
            if self.started_at is None:
                self.started_at = now
            if len(self.buffer) < 7:
                break
            version, kind, _sequence, length = _HEADER.unpack(self.buffer[2:7])
            # A corrupted length must not block a valid frame after it.
            if version != VERSION or _PAYLOAD_SIZES.get(kind) != length:
                del self.buffer[0]
                self.started_at = None
                continue
            packet_length = 9 + length
            if len(self.buffer) < packet_length:
                break
            try:
                frame = decode_frame(bytes(self.buffer[:packet_length]))
            except ValueError:
                del self.buffer[0]
                self.started_at = None
                continue
            frames.append(frame)
            del self.buffer[:packet_length]
            self.started_at = None
        return frames
