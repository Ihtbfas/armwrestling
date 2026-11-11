"""PTY smoke test: actual OS byte stream, V1 handshake, command and watchdog."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
import unittest

from common.serial_protocol import (
    FLAG_ARM_READY, FLAG_ACK_VALID, FrameParser, MODE_ENABLE,
    TYPE_SESSION_ACK, TYPE_TELEMETRY, command_frame, decode_payload,
    session_open_frame,
)

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix', 'PTY simulation requires Linux/Unix')
class VirtualSerialTests(unittest.TestCase):
    def test_pty_v1_handshake_command_and_timeout(self):
        child = subprocess.Popen(
            [sys.executable, '-u', '-m', 'sim.mock_stm32_serial',
             '--demo-arm', '--duration', '2.4'],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        fd = None
        try:
            info = json.loads(child.stdout.readline())
            self.assertEqual(info['event'], 'virtual_serial_ready')
            fd = os.open(info['port'], os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            parser = FrameParser()
            serial_id = 0x0102030405060708
            os.write(fd, session_open_frame(0, serial_id))
            acked = False
            deadline = time.monotonic()+.6
            while time.monotonic()<deadline and not acked:
                if select.select([fd], [], [], .05)[0]:
                    for frame in parser.feed(os.read(fd, 4096)):
                        if (frame.kind == TYPE_SESSION_ACK
                                and decode_payload(frame).session_id == serial_id):
                            acked = True
            self.assertTrue(acked, 'STM32 simulator did not ACK session')
            # Send a live brake magnitude repeatedly, as a PC controller would.
            sent_seq = 1
            accepted = False
            for _ in range(8):
                os.write(fd, command_frame(sent_seq, serial_id,
                                           MODE_ENABLE, 300, 200))
                sent_seq += 1
                time.sleep(.03)
                if select.select([fd], [], [], 0)[0]:
                    for frame in parser.feed(os.read(fd, 4096)):
                        if frame.kind == TYPE_TELEMETRY:
                            telem = decode_payload(frame)
                            if telem.applied_target_mnm == 300:
                                accepted = True
            self.assertTrue(accepted, 'V1 command did not reach simulated brake')
            # No new command for >200 ms: the simulated board drops ARM_READY.
            disarmed = False
            deadline = time.monotonic()+.6
            while time.monotonic()<deadline and not disarmed:
                if select.select([fd], [], [], .05)[0]:
                    for frame in parser.feed(os.read(fd, 4096)):
                        if frame.kind == TYPE_TELEMETRY:
                            telem = decode_payload(frame)
                            if not (telem.status_flags & FLAG_ARM_READY):
                                disarmed = True
                                self.assertEqual(telem.applied_target_mnm, 0)
            self.assertTrue(disarmed, 'local command watchdog did not disarm')
        finally:
            if fd is not None:
                os.close(fd)
            if child.poll() is None:
                child.terminate()
            child.communicate(timeout=3)


if __name__ == '__main__':
    unittest.main()
