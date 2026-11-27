"""Serial V1.1 bridge tests with actual loopback UDP and binary in-memory serial."""
from contextlib import contextmanager
from queue import Queue
import socket
import subprocess
import sys
from threading import Event, Thread
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common.protocol import encode_datagram
from common.serial_protocol import (
    FLAG_ACK_VALID, FLAG_ANGLE_VALID, FLAG_ARM_READY, FLAG_FAULT, FLAG_FORCE_VALID, FLAG_READY,
    MODE_ENABLE, MODE_STOP, TYPE_BRAKE_COMMAND, TYPE_SESSION_OPEN, FrameParser,
    decode_payload, session_ack_frame, telemetry_frame,
)
from real.run import serve
from real.stm32_link import Stm32Link
from real.test_stm32_link import ENABLE, MemorySerial

READY = FLAG_READY | FLAG_ANGLE_VALID | FLAG_ARM_READY


class Stm32Peer(MemorySerial):
    def __init__(self):
        super().__init__()
        self.parser = FrameParser()
        self.sequence = 40000
        self.session_id = 0
        self.flags = READY
        self.next_telemetry = 0.
        self.commands = []
        self.ack = None
        self.freeze_ack = False
        self.freeze_sample = False
        self.sample = 0xFFFFFF00

    @property
    def in_waiting(self):
        now = time.monotonic()
        if now >= self.next_telemetry:
            flags = self.flags | FLAG_ACK_VALID if self.ack is not None else self.flags
            self.incoming.extend(telemetry_frame(
                self.sequence, -400, -200, 0, flags, self.ack or 0,
                mcu_sample_ms=self.sample, session_id=self.session_id,
            ))
            self.sequence = (self.sequence + 1) & 0xFFFF
            if not self.freeze_sample:
                self.sample = (self.sample + 20) & 0xFFFFFFFF
            self.next_telemetry = now + .02
        return super().in_waiting

    def reset(self):
        self.ack = None
        self.session_id, self.sequence, self.sample = 0, 0, 0

    def write(self, data):
        for frame in self.parser.feed(data):
            payload = decode_payload(frame)
            if frame.kind == TYPE_SESSION_OPEN:
                if self.session_id != payload.session_id:
                    self.ack = None
                self.session_id = payload.session_id
                self.incoming.extend(session_ack_frame(0, payload.session_id))
            elif frame.kind == TYPE_BRAKE_COMMAND and payload.session_id == self.session_id:
                self.commands.append(payload)
                if not self.freeze_ack:
                    self.ack = frame.sequence
        return len(data)


@contextmanager
def backend(peer, **options):
    stop, messages = Event(), Queue()
    link = Stm32Link(peer, session_id=1)

    def run():
        try:
            serve('loopback', vr_port=0, reporter=messages.put, stop_event=stop,
                  diagnostic_interval=.025, **options)
        except Exception as error:
            messages.put(error)

    worker = Thread(target=run, daemon=True)
    with patch('real.run.Stm32Link.open', return_value=link):
        worker.start()
        try:
            yield messages
        finally:
            stop.set()
            worker.join(timeout=2)
            if worker.is_alive():
                raise RuntimeError('debug backend did not shut down')


def message_until(messages, predicate, timeout=2.):
    end = time.monotonic() + timeout
    while True:
        message = messages.get(timeout=max(0., end - time.monotonic()))
        if isinstance(message, Exception):
            raise message
        if predicate(message):
            return message
        if time.monotonic() >= end:
            raise AssertionError('expected backend diagnostic did not arrive')


def send_state(sender, port, seq, match_state):
    message = dict(v=1, type='vr_update', seq=seq, character_id='person_02',
                   match_state=match_state)
    sender.sendto(encode_datagram(message), ('127.0.0.1', port))


def listening(messages):
    return message_until(messages, lambda m: m.get('event') == 'listening')['vr_port']


class DebugBackendTests(unittest.TestCase):
    def test_monitor_ignores_running_and_reports_zero_force_only_when_valid(self):
        peer = Stm32Peer()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender, backend(
                peer, monitor_only=True) as messages:
            port = listening(messages)
            send_state(sender, port, 1, 'running')
            snapshot = message_until(messages, lambda m: m.get('serial_session_confirmed'))
            self.assertEqual(snapshot['serial_session_id'], '0x0000000000000001')
            self.assertEqual(snapshot['serial_status_flags'], READY | FLAG_ACK_VALID)
            self.assertEqual(snapshot['serial_last_sent']['mode'], 'STOP')
            self.assertEqual(snapshot['serial_applied_target_nm'], 0.)
            self.assertIsNone(snapshot['serial_force_n'])
            self.assertIsNone(snapshot['velocity_rad_s'])
            self.assertFalse(snapshot['vr_fresh'])
            peer.flags |= FLAG_FORCE_VALID
            fresh = message_until(messages, lambda m: m.get('serial_force_n') == 0.)
            self.assertGreater(fresh['serial_telemetry_rate_hz'], 0.)
        self.assertTrue(peer.commands)
        self.assertTrue(all(cmd.mode == MODE_STOP and cmd.target_torque_mnm == 0
                            for cmd in peer.commands))

    def test_debug_source_must_explicitly_resume_after_fault_and_vr_timeout(self):
        peer = Stm32Peer()
        with (socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender,
              socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as other):
            sender.bind(('127.0.0.1', 0))
            with backend(peer, vr_source_port=sender.getsockname()[1], vr_timeout=.25) as messages:
                port = listening(messages)
                message_until(messages, lambda m: m.get('serial_enable_ready'))
                send_state(other, port, 1, 'waiting')
                send_state(sender, port, 1, 'running')
                message_until(messages, lambda m: m.get('debug_resume_required') and m.get('serial_enable_ready'))
                self.assertTrue(all(cmd.mode == MODE_STOP for cmd in peer.commands))
                send_state(sender, port, 2, 'waiting')
                message_until(messages, lambda m: m.get('reason') == 'match_waiting')
                send_state(sender, port, 3, 'running')
                message_until(messages, lambda m: m.get('mode') == 'enabled')
                self.assertEqual(peer.commands[-1].mode, MODE_ENABLE)

                peer.flags |= FLAG_FAULT
                message_until(messages, lambda m: m.get('serial_status_reason') == 'device_fault')
                self.assertEqual(peer.commands[-1].mode, MODE_STOP)
                peer.flags &= ~FLAG_FAULT
                send_state(sender, port, 4, 'running')
                message_until(messages, lambda m: m.get('serial_enable_ready') and m.get('debug_resume_required'))
                self.assertEqual(peer.commands[-1].mode, MODE_STOP)
                send_state(sender, port, 5, 'paused')
                message_until(messages, lambda m: m.get('reason') == 'match_paused')
                send_state(sender, port, 6, 'running')
                message_until(messages, lambda m: m.get('mode') == 'enabled')
                message_until(messages, lambda m: m.get('v') == 1 and m.get('reason') == 'vr_timeout')
                send_state(sender, port, 7, 'running')
                message_until(messages, lambda m: m.get('type') == 'status' and m.get('debug_resume_required')
                              and m.get('reason') != 'running')
                self.assertEqual(peer.commands[-1].mode, MODE_STOP)

    def test_formal_udp_waits_for_manual_resume_after_reset_and_ack_timeout(self):
        peer = Stm32Peer()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender, backend(peer) as messages:
            port = listening(messages)
            initial = message_until(messages, lambda m: m.get('serial_enable_ready'))
            send_state(sender, port, 1, 'waiting')
            message_until(messages, lambda m: m.get('reason') == 'match_waiting')
            send_state(sender, port, 2, 'running')
            message_until(messages, lambda m: m.get('mode') == 'enabled')
            peer.reset()
            restart = message_until(messages, lambda m: m.get('event') == 'serial_session_restart')
            self.assertEqual(restart['reason'], 'session_mismatch')
            send_state(sender, port, 3, 'running')
            recovered = message_until(messages, lambda m: m.get('serial_enable_ready'))
            self.assertNotEqual(initial['serial_session_id'], recovered['serial_session_id'])
            self.assertTrue(recovered['debug_resume_required'])
            self.assertEqual(recovered['mode'], 'stop')
            send_state(sender, port, 4, 'paused')
            message_until(messages, lambda m: m.get('reason') == 'match_paused')
            send_state(sender, port, 5, 'running')
            message_until(messages, lambda m: m.get('mode') == 'enabled')
            peer.freeze_ack = True
            stalled = message_until(messages, lambda m: m.get('event') == 'serial_session_restart')
            self.assertEqual(stalled['reason'], 'ack_timeout')
            peer.freeze_ack = False
            send_state(sender, port, 6, 'running')
            recovered = message_until(messages, lambda m: m.get('serial_enable_ready'))
            self.assertTrue(recovered['debug_resume_required'])
            self.assertEqual(recovered['mode'], 'stop')

    def test_valid_wire_and_ack_do_not_keep_stalled_angle_fresh(self):
        peer = Stm32Peer()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender, backend(peer) as messages:
            port = listening(messages)
            message_until(messages, lambda m: m.get('serial_enable_ready'))
            send_state(sender, port, 1, 'waiting')
            message_until(messages, lambda m: m.get('reason') == 'match_waiting')
            send_state(sender, port, 2, 'running')
            message_until(messages, lambda m: m.get('mode') == 'enabled')
            peer.freeze_sample = True
            stale = message_until(messages, lambda m: m.get('serial_status_reason') == 'sample_stale')
            self.assertTrue(stale['serial_wire_fresh'])
            self.assertIsNone(stale['angle_rad'])
            self.assertEqual(stale['mode'], 'stop')
            self.assertEqual(peer.commands[-1].mode, MODE_STOP)

    def test_supervised_write_cannot_finish_a_cancelled_frame_on_a_later_tick(self):
        fake = MemorySerial()
        with patch.dict(sys.modules, serial=SimpleNamespace(Serial=lambda *args, **kwargs: fake)):
            link = Stm32Link.open('loopback')
        link.poll()
        fake.sent_frame()
        fake.incoming.extend(session_ack_frame(0, link.session_id))
        fake.incoming.extend(telemetry_frame(0, 0, 0, 0, READY,
                                            mcu_sample_ms=0, session_id=link.session_id))
        link.poll()
        with patch.object(fake, 'write', side_effect=lambda data: len(data) - 1):
            with self.assertRaises(TimeoutError):
                link.send_command(ENABLE)
        self.assertIsNone(link.last_sent_command)
        self.assertFalse(link.outstanding)

    def test_owner_pipe_eof_signals_stop(self):
        command = [sys.executable, '-c',
                   'from real.run import _watch_stdin; raise SystemExit(not _watch_stdin().wait(2))']
        result = subprocess.run(command, input=b'', capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
