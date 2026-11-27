"""The GUI backend is exercised with real UDP and in-memory serial V1 bytes."""
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
    FLAG_ACK_VALID, FLAG_ARM_READY, FLAG_FAULT, FLAG_FORCE_VALID, FLAG_READY,
    MODE_ENABLE, MODE_STOP, TYPE_BRAKE_COMMAND, TYPE_SESSION_OPEN, FrameParser,
    decode_payload, session_ack_frame, telemetry_frame,
)
from real.run import serve
from real.stm32_link import Stm32Link
from real.test_stm32_link import MemorySerial


class Stm32Peer(MemorySerial):
    def __init__(self):
        super().__init__()
        self.parser = FrameParser()
        self.sequence = 0
        self.handshaken = False
        self.flags = FLAG_READY | FLAG_ARM_READY
        self.next_telemetry = 0.
        self.commands = []

    @property
    def in_waiting(self):
        now = time.monotonic()
        if self.handshaken and now >= self.next_telemetry:
            self.incoming.extend(telemetry_frame(self.sequence, -400, -200, 0, self.flags))
            self.sequence = (self.sequence + 1) & 0xFFFF
            self.next_telemetry = now + .02
        return super().in_waiting

    def write(self, data):
        for frame in self.parser.feed(data):
            payload = decode_payload(frame)
            if frame.kind == TYPE_SESSION_OPEN:
                self.incoming.extend(session_ack_frame(0, payload.session_id))
                self.handshaken = True
            elif frame.kind == TYPE_BRAKE_COMMAND:
                self.commands.append(payload)
        return len(data)


@contextmanager
def backend(peer, **options):
    stop, messages = Event(), Queue()
    link = Stm32Link(peer, session_id=1)

    def run():
        try:
            serve('loopback', vr_port=0, reporter=messages.put, stop_event=stop,
                  diagnostic_interval=.04, **options)
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


class DebugBackendTests(unittest.TestCase):
    def test_monitor_ignores_running_packets_and_reports_v1_stop(self):
        peer = Stm32Peer()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender, backend(
                peer, monitor_only=True) as messages:
            port = message_until(messages, lambda m: m.get('event') == 'listening')['vr_port']
            send_state(sender, port, 1, 'running')
            snapshot = message_until(
                messages, lambda m: m.get('serial_arm_ready') and m.get('serial_last_sent'))
            self.assertEqual(snapshot['serial_session_id'], '0x0000000000000001')
            self.assertEqual(snapshot['serial_status_flags'], FLAG_READY | FLAG_ARM_READY)
            self.assertEqual(snapshot['serial_last_sent']['mode'], 'STOP')
            self.assertEqual(snapshot['serial_last_sent']['target_mnm'], 0)
            self.assertEqual(snapshot['serial_applied_target_nm'], 0.)
            self.assertIsNone(snapshot['serial_force_n'])
            self.assertIsNone(snapshot['serial_last_ack_seq'])
            self.assertFalse(snapshot['vr_fresh'])
            peer.flags |= FLAG_FORCE_VALID | FLAG_ACK_VALID
            fresh = message_until(messages, lambda m: m.get('serial_last_ack_seq') == 0)
            self.assertEqual(fresh['serial_force_n'], 0.)
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
                port = message_until(messages, lambda m: m.get('event') == 'listening')['vr_port']
                message_until(messages, lambda m: m.get('serial_arm_ready'))
                send_state(other, port, 1, 'waiting')
                send_state(sender, port, 1, 'running')
                message_until(messages, lambda m: m.get('debug_resume_required'))
                self.assertTrue(all(cmd.mode == MODE_STOP for cmd in peer.commands))
                send_state(sender, port, 2, 'waiting')
                message_until(messages, lambda m: m.get('reason') == 'match_waiting')
                send_state(sender, port, 3, 'running')
                message_until(messages, lambda m: m.get('mode') == 'enabled')
                self.assertEqual(peer.commands[-1].mode, MODE_ENABLE)

                peer.flags |= FLAG_FAULT
                message_until(messages, lambda m: m.get('type') == 'status'
                              and m.get('reason') == 'device_fault')
                self.assertEqual(peer.commands[-1].mode, MODE_STOP)
                peer.flags &= ~FLAG_FAULT
                send_state(sender, port, 4, 'running')
                message_until(messages, lambda m: m.get('serial_arm_ready')
                              and m.get('debug_resume_required'))
                self.assertEqual(peer.commands[-1].mode, MODE_STOP)
                send_state(sender, port, 5, 'paused')
                message_until(messages, lambda m: m.get('reason') == 'match_paused')
                send_state(sender, port, 6, 'running')
                message_until(messages, lambda m: m.get('mode') == 'enabled')

                message_until(messages, lambda m: m.get('debug_resume_required'))
                send_state(sender, port, 7, 'running')
                message_until(messages, lambda m: m.get('reason') != 'running'
                              and m.get('debug_resume_required'))
                self.assertEqual(peer.commands[-1].mode, MODE_STOP)

    def test_supervised_write_cannot_finish_a_cancelled_frame_on_a_later_tick(self):
        fake = MemorySerial()
        with patch.dict(sys.modules, serial=SimpleNamespace(Serial=lambda *args, **kwargs: fake)):
            link = Stm32Link.open('loopback', write_timeout=.02)
        link.poll()
        fake.sent_frame()
        fake.incoming.extend(session_ack_frame(0, link.session_id))
        fake.incoming.extend(telemetry_frame(0, 0, 0, 0, FLAG_READY | FLAG_ARM_READY))
        link.poll()
        with patch.object(fake, 'write', side_effect=lambda packet: len(packet) - 1):
            with self.assertRaises(TimeoutError):
                link.send_command(dict(mode='enabled', target_torque_nm=.3, valid_for_ms=200))
        self.assertIsNone(link.last_sent_command)

    def test_owner_pipe_eof_signals_stop(self):
        command = [
            sys.executable, '-c',
            'from real.run import _watch_stdin; raise SystemExit(not _watch_stdin().wait(2))',
        ]
        result = subprocess.run(command, input=b'', capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_partial_command_does_not_advance_last_complete_write(self):
        class PartialSerial(MemorySerial):
            write_size = 100

            def write(self, data):
                return super().write(data[:self.write_size])

        fake = PartialSerial()
        link = Stm32Link(fake, now=0., session_id=1)
        link.poll(0.)
        fake.sent_frame()
        fake.incoming.extend(session_ack_frame(0, 1))
        fake.incoming.extend(telemetry_frame(0, 0, 0, 0, FLAG_READY | FLAG_ARM_READY))
        link.poll(.01)
        link.send_command(dict(mode='stop', target_torque_nm=0., valid_for_ms=200), .02)
        initial = fake.sent_frame()
        fake.write_size = 4
        link.send_command(dict(mode='enabled', target_torque_nm=.3, valid_for_ms=200), .03)
        self.assertEqual(link.last_sent_command, dict(seq=initial.sequence, mode='STOP',
                                                      target_mnm=0, valid_for_ms=200))
        self.assertEqual(link.last_sent_at, .02)
        fake.write_size = 100
        link.poll(.04)
        command = fake.sent_frame()
        self.assertEqual(link.last_sent_command, dict(seq=command.sequence, mode='ENABLE',
                                                     target_mnm=300, valid_for_ms=200))
        self.assertEqual(link.last_sent_at, .04)


if __name__ == '__main__':
    unittest.main()
