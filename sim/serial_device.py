"""Simulated V1 board state, shared by Windows COM and Linux PTY transports."""
from common.serial_protocol import (
    FLAG_ACK_VALID, FLAG_ARM_READY, FLAG_READY, FrameParser, MODE_ENABLE,
    TYPE_BRAKE_COMMAND, TYPE_SESSION_OPEN, decode_payload, is_new_sequence,
    session_ack_frame, telemetry_frame,
)
from sim.mock_stm32 import SimulatedDevice


class SerialDevice:
    def __init__(self, demo_arm=False, emit=None):
        self.demo_arm, self.emit = demo_arm, emit or (lambda message: None)
        self.device, self.parser = SimulatedDevice(), FrameParser()
        self.session_id = None
        self.arm_ready = False
        self.last_command_seq = None
        self.telemetry_seq = self.ack_seq = self.accepted_commands = 0

    def feed(self, data, now):
        self._expire_command(now)
        ack = b''
        for frame in self.parser.feed(data, now):
            try:
                message = decode_payload(frame)
            except ValueError:
                continue
            if frame.kind == TYPE_SESSION_OPEN:
                if self.session_id != message.session_id:
                    self.session_id = message.session_id
                    self.device.stop()
                    self.last_command_seq = None
                    self.arm_ready = bool(self.demo_arm) # Only this explicit simulation flag authorizes it.
                    self.emit(dict(event='session_open', demo_armed=self.arm_ready))
                ack = session_ack_frame(self.ack_seq, self.session_id)
                self.ack_seq = (self.ack_seq + 1) & 0xFFFF
            elif frame.kind == TYPE_BRAKE_COMMAND:
                if self.session_id is None or message.session_id != self.session_id:
                    continue
                if not is_new_sequence(frame.sequence, self.last_command_seq):
                    continue
                if message.mode == MODE_ENABLE and not self.arm_ready:
                    continue
                # The wire counter wraps at 65536; the reused dynamics has a
                # monotonic JSON-style counter, so do not pass the wire seq to it.
                command = dict(v=1, type='brake_command', seq=self.accepted_commands,
                    mode='enabled' if message.mode == MODE_ENABLE else 'stop',
                    reason='running' if message.mode == MODE_ENABLE else 'stop',
                    target_torque_nm=message.target_torque_mnm / 1000,
                    brake_torque_nm=0., speed_transition_rad_s=.05,
                    valid_for_ms=min(message.valid_for_ms, 200), telemetry_seq=0)
                try:
                    self.device.receive(command, now)
                except ValueError:
                    continue
                self.accepted_commands += 1
                self.last_command_seq = frame.sequence
        return ack

    def _expire_command(self, now):
        if self.device.command is not None and now >= self.device.deadline:
            self.arm_ready = False
            self.device.stop()
            self.device.reason = 'command_timeout'
            self.emit(dict(event='command_timeout', armed=False))

    def step(self, dt, user_torque, now):
        self.parser.expire(now)
        self._expire_command(now)
        # Preserve the watchdog reason after stop() removed the old command.
        watchdog = self.device.command is None and self.device.reason == 'command_timeout'
        self.device.step(dt, user_torque, now)
        if watchdog:
            self.device.reason = 'command_timeout'

    def telemetry(self, now):
        flags = FLAG_READY | (FLAG_ARM_READY if self.arm_ready else 0)
        if self.last_command_seq is not None:
            flags |= FLAG_ACK_VALID
        target = (round(self.device.command['target_torque_nm'] * 1000)
                  if self.device.command is not None and now < self.device.deadline else 0)
        packet = telemetry_frame(self.telemetry_seq, round(self.device.angle * 1000),
            round(self.device.velocity * 1000), 0, flags,
            self.last_command_seq if self.last_command_seq is not None else 0, target)
        self.telemetry_seq = (self.telemetry_seq + 1) & 0xFFFF
        return packet
