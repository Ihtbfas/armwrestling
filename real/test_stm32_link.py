"""Serial V1 link tests with an in-memory byte stream (no pyserial needed)."""
import unittest

from common.serial_protocol import (
    FLAG_ARM_READY, FLAG_READY, MODE_ENABLE, MODE_STOP,
    command_frame, decode_frame, decode_payload, session_ack_frame,
    telemetry_frame, TYPE_SESSION_OPEN,
)
from real.stm32_link import Stm32Link


class MemorySerial:
    def __init__(self):
        self.incoming = bytearray()
        self.outgoing = bytearray()
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.incoming)

    def read(self, count):
        result = bytes(self.incoming[:count])
        del self.incoming[:count]
        return result

    def write(self, data):
        self.outgoing.extend(data)
        return len(data)

    def close(self):
        self.closed = True

    def sent_frame(self):
        data = bytes(self.outgoing)
        self.outgoing.clear()
        return decode_frame(data)


class SerialLinkTests(unittest.TestCase):
    def test_session_telemetry_and_real_v1_command(self):
        fake = MemorySerial()
        link = Stm32Link(fake, now=10., session_id=123)
        self.assertEqual(link.poll(10.), [])
        open_request = fake.sent_frame()
        self.assertEqual(open_request.kind, TYPE_SESSION_OPEN)
        self.assertEqual(decode_payload(open_request).session_id, 123)
        fake.incoming.extend(session_ack_frame(0, 123))
        fake.incoming.extend(telemetry_frame(0, -400, -200, 0,
                                           FLAG_READY | FLAG_ARM_READY))
        updates = link.poll(10.01)
        self.assertTrue(link.session_ready)
        self.assertTrue(link.arm_ready)
        self.assertEqual(updates[0]['angle_rad'], -.4)
        self.assertEqual(updates[0]['velocity_rad_s'], -.2)
        self.assertEqual(updates[0]['device_status'], 'ready')
        self.assertTrue(link.send_command(dict(mode='enabled', target_torque_nm=.3,
                                               valid_for_ms=200), 10.02))
        cmd = decode_payload(fake.sent_frame())
        self.assertEqual((cmd.session_id, cmd.mode, cmd.target_torque_mnm),
                         (123, MODE_ENABLE, 300))

    def test_not_armed_uses_stop_even_if_pc_asks_enable(self):
        fake = MemorySerial()
        link = Stm32Link(fake, now=0., session_id=11)
        link.poll(0)
        fake.sent_frame()
        fake.incoming.extend(session_ack_frame(0, 11))
        fake.incoming.extend(telemetry_frame(0, 0, 0, 0, FLAG_READY))
        updates = link.poll(.01)
        self.assertEqual(updates[0]['device_status'], 'fault')
        link.send_command(dict(mode='enabled', target_torque_nm=1.,
                               valid_for_ms=200), .02)
        command = decode_payload(fake.sent_frame())
        self.assertEqual((command.mode, command.target_torque_mnm), (MODE_STOP, 0))

    def test_wrong_ack_duplicate_telemetry_and_old_session_rejected(self):
        fake = MemorySerial()
        link = Stm32Link(fake, now=0, session_id=321)
        link.poll(0)
        fake.sent_frame()
        fake.incoming.extend(session_ack_frame(0, 999))
        self.assertEqual(link.poll(.01), [])
        self.assertFalse(link.session_ready)
        fake.incoming.extend(telemetry_frame(0, -100, 0, 0, FLAG_READY))
        self.assertEqual(link.poll(.02), [])  # No telemetry before valid ACK.
        fake.incoming.extend(session_ack_frame(1, 321))
        fake.incoming.extend(telemetry_frame(1, -200, 0, 0,
                                           FLAG_READY | FLAG_ARM_READY))
        self.assertEqual(len(link.poll(.03)), 1)
        fake.incoming.extend(telemetry_frame(1, -300, 0, 0, FLAG_READY))
        self.assertEqual(link.poll(.04), [])  # Duplicate seq ignored.
        self.assertTrue(link.arm_ready)

    def test_serial_port_close(self):
        fake = MemorySerial()
        link = Stm32Link(fake)
        link.close()
        self.assertTrue(fake.closed)


if __name__ == '__main__':
    unittest.main()
