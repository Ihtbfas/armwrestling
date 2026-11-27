"""Simulated V1.1 board state, shared by Windows COM and Linux PTY."""

from common.serial_protocol import (
    FLAG_ACK_VALID, FLAG_ANGLE_VALID, FLAG_ARM_READY, FLAG_CMD_TIMEOUT, FLAG_READY,
    FLAG_VEL_VALID, FrameParser, MODE_ENABLE, MODE_STOP, TYPE_BRAKE_COMMAND,
    TYPE_SESSION_OPEN, decode_payload, is_new_sequence, session_ack_frame, telemetry_frame,
)
from sim.mock_stm32 import SimulatedDevice

LOCAL_COMMAND_LIMIT_MS = 200
SAMPLE_TIMEOUT_S = .25


class SerialDevice:
    def __init__(self, demo_arm=False, emit=None):
        self.demo_arm = demo_arm
        self.emit = emit or (lambda message: None)
        self.device = SimulatedDevice()
        self.accepted_commands = 0
        self._boot()

    def _boot(self, now=None):
        self.parser = FrameParser()
        self._clear_session()
        self.expired_session_id = None
        self.command_timed_out = False
        self.boot_at = now
        self.sample_at = None
        self.sample_ms = 0
        self.telemetry_seq = self.ack_seq = 0

    def _clear_session(self):
        self.device.stop()
        self.session_id = self.last_command_seq = None
        self.arm_ready = False

    def reset(self, now):
        """Simulate an MCU reboot while the existing PC serial port stays open."""
        self._boot(now)
        self.emit(dict(event='simulated_mcu_reset'))

    def _sample_fresh(self, now):
        return self.sample_at is not None and now - self.sample_at < SAMPLE_TIMEOUT_S

    def feed(self, data, now):
        self._expire_command(now)
        ack = b''
        for frame in self.parser.feed(data, now):
            try:
                message = decode_payload(frame)
            except ValueError:
                continue
            if frame.kind == TYPE_SESSION_OPEN:
                if message.session_id == self.expired_session_id:
                    continue
                if self.session_id != message.session_id:
                    self._clear_session()
                    self.session_id = message.session_id
                    self.telemetry_seq = 0
                    self.emit(dict(event='session_open', session_id=f'0x{self.session_id:016X}',
                                   demo_armed=False))
                ack = session_ack_frame(self.ack_seq, self.session_id)
                self.ack_seq = (self.ack_seq + 1) & 0xFFFF
            elif frame.kind == TYPE_BRAKE_COMMAND:
                self._accept_command(frame.sequence, message, now)
        return ack

    def _accept_command(self, sequence, message, now):
        if message.session_id != self.session_id or not is_new_sequence(sequence, self.last_command_seq):
            return
        enabled = message.mode == MODE_ENABLE
        if enabled and (not self.arm_ready or not self._sample_fresh(now) or self.command_timed_out):
            return
        # Keep the existing simulator's monotonic command counter separate from
        # the uint16 wire sequence. The wire ACK echoes the latter.
        command = dict(v=1, type='brake_command', seq=self.accepted_commands,
                       mode='enabled' if enabled else 'stop',
                       reason='running' if enabled else 'stop',
                       target_torque_nm=message.target_torque_nm,
                       brake_torque_nm=0., speed_transition_rad_s=.05,
                       valid_for_ms=min(message.valid_for_ms, LOCAL_COMMAND_LIMIT_MS), telemetry_seq=0)
        try:
            self.device.receive(command, now)
        except ValueError:
            return
        if message.mode == MODE_STOP:
            self.command_timed_out = False
            if self.last_command_seq is None:
                # Explicit simulator setup only. STOP never grants real hardware authority.
                self.arm_ready = self.demo_arm
        self.accepted_commands += 1
        self.last_command_seq = sequence

    def _expire_command(self, now):
        if self.device.command is not None and now >= self.device.deadline:
            self.expired_session_id = self.session_id
            self._clear_session()
            self.telemetry_seq = 0
            self.command_timed_out = True
            self.emit(dict(event='command_timeout', armed=False))

    def step(self, dt, user_torque, now):
        self.parser.expire(now)
        self._expire_command(now)
        self.device.step(dt, user_torque, now)
        if self.command_timed_out:
            self.device.reason = 'command_timeout'
        if self.boot_at is None:
            self.boot_at = now
        self.sample_at = now
        self.sample_ms = int((now - self.boot_at) * 1000) & 0xFFFFFFFF

    def telemetry(self, now):
        self._expire_command(now)
        flags = FLAG_READY
        fresh = self._sample_fresh(now)
        if fresh:
            flags |= FLAG_ANGLE_VALID | FLAG_VEL_VALID
        if self.arm_ready:
            flags |= FLAG_ARM_READY
        if self.last_command_seq is not None:
            flags |= FLAG_ACK_VALID
        if self.command_timed_out:
            flags |= FLAG_CMD_TIMEOUT
        target = (round(self.device.command['target_torque_nm'] * 1000)
                  if self.device.command is not None else 0)
        packet = telemetry_frame(
            self.telemetry_seq, round(self.device.angle * 1000),
            round(self.device.velocity * 1000) if fresh else 0, 0, flags,
            self.last_command_seq if self.last_command_seq is not None else 0, target,
            mcu_sample_ms=self.sample_ms, session_id=self.session_id or 0,
        )
        self.telemetry_seq = (self.telemetry_seq + 1) & 0xFFFF
        return packet
