"""Bounded JSON messages: TCP newline framing and one-object UDP datagrams."""
import json
import math

MAX_LINE = 8192


def encode(message):
    return (json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(',', ':'))+'\n').encode('utf-8')


def _reject_constant(value):
    raise ValueError('nonfinite JSON number: '+value)


def encode_datagram(message):
    """One complete JSON object in one UDP datagram; no newline delimiter."""
    packet = encode(message)[:-1]
    if len(packet) > MAX_LINE:
        raise ValueError('UDP message exceeds 8192 bytes')
    return packet


def decode_datagram(packet):
    if not packet or len(packet) > MAX_LINE:
        raise ValueError('UDP message must contain 1..8192 bytes')
    message = json.loads(packet.decode('utf-8'), parse_constant=_reject_constant)
    if not isinstance(message, dict):
        raise ValueError('UDP message must be a JSON object')
    return message


class LineBuffer:
    def __init__(self):
        self.pending = b''

    def feed(self, chunk):
        self.pending += chunk
        messages = []
        while b'\n' in self.pending:
            line, self.pending = self.pending.split(b'\n', 1)
            if len(line) > MAX_LINE:
                raise ValueError('message exceeds 8192 bytes')
            message = json.loads(line.decode('utf-8'), parse_constant=_reject_constant)
            if not isinstance(message, dict):
                raise ValueError('message must be a JSON object')
            messages.append(message)
        if len(self.pending) > MAX_LINE:
            raise ValueError('unfinished message exceeds 8192 bytes')
        return messages


class JsonSocket:
    """One socket, one partial line, one pending output; never queue old snapshots."""
    def __init__(self, sock):
        self.socket = sock
        sock.setblocking(False)
        self.parser = LineBuffer()
        self.pending = b''

    def receive(self):
        chunk = self.socket.recv(4096)
        if not chunk:
            raise EOFError('peer disconnected')
        return self.parser.feed(chunk)

    def queue(self, message):
        if self.pending:
            return False
        payload = encode(message)
        if len(payload)-1 > MAX_LINE:
            raise ValueError('outgoing message exceeds 8192 bytes')
        self.pending = payload
        return True

    def flush(self):
        if self.pending:
            try:
                count = self.socket.send(self.pending)
                if count == 0:
                    raise EOFError('peer stopped receiving')
                self.pending = self.pending[count:]
            except BlockingIOError:
                pass


def number(message, name, low, high):
    value = message.get(name)
    if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
        raise ValueError(f'{name} must be finite and within [{low}, {high}]')
    return value


def envelope(message, expected_type):
    if type(message.get('v')) is not int or message['v'] != 1 or message.get('type') != expected_type:
        raise ValueError('wrong protocol version or message type')
    seq = message.get('seq')
    if type(seq) is not int or not 0 <= seq <= 2**53-1:
        raise ValueError('seq must be a nonnegative JSON-safe integer')


def validate_incoming(message, role):
    if role == 'stm32':
        envelope(message, 'telemetry')
        number(message, 'angle_rad', -100, 100)
        number(message, 'velocity_rad_s', -1000, 1000)
        if message.get('device_status') not in ('ready', 'fault'):
            raise ValueError('device_status must be ready or fault')
    elif role == 'vr':
        envelope(message, 'vr_update')
        name = message.get('character_id')
        if not isinstance(name, str) or not 1 <= len(name) <= 64:
            raise ValueError('character_id must contain 1..64 characters')
        try:
            name.encode('utf-8')
        except UnicodeEncodeError as error:
            raise ValueError('character_id must contain valid Unicode') from error
        if message.get('match_state') not in ('waiting', 'running', 'paused', 'finished'):
            raise ValueError('invalid match_state')
    else:
        raise ValueError('unknown role')
    return message


def validate_command(message):
    envelope(message, 'brake_command')
    if message.get('mode') not in ('enabled', 'stop'):
        raise ValueError('invalid command mode')
    target = number(message, 'target_torque_nm', 0, 2)
    actual = number(message, 'brake_torque_nm', -target, target)
    number(message, 'speed_transition_rad_s', 0.001, 1)
    lifetime = message.get('valid_for_ms')
    if type(lifetime) is not int or not 20 <= lifetime <= 500:
        raise ValueError('valid_for_ms must be an integer within [20, 500]')
    if message['mode'] == 'stop' and (target != 0 or actual != 0):
        raise ValueError('stop command must have zero output')
    return message
