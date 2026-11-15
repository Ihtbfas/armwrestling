"""V1 device behavior independent of Windows COM or Linux PTY transport."""
import unittest

from common.serial_protocol import (FLAG_ARM_READY, MODE_ENABLE, command_frame,
    decode_frame, decode_payload, session_open_frame)
from sim.serial_device import SerialDevice


class SerialDeviceTests(unittest.TestCase):
    def test_crc_session_duplicates_and_watchdog(self):
        device = SerialDevice(demo_arm=True)
        ack = device.feed(session_open_frame(0, 123), 0.)
        self.assertEqual(decode_payload(decode_frame(ack)).session_id, 123)
        bad = bytearray(command_frame(1, 123, MODE_ENABLE, 300, 200))
        bad[-1] ^= 1
        device.feed(bytes(bad), .01)
        self.assertIsNone(device.device.command)
        device.feed(command_frame(1, 999, MODE_ENABLE, 300, 200), .02)
        self.assertIsNone(device.device.command)
        device.feed(command_frame(1, 123, MODE_ENABLE, 300, 200), .03)
        self.assertEqual(device.device.command['target_torque_nm'], .3)
        device.feed(command_frame(1, 123, MODE_ENABLE, 300, 200), .2)
        device.step(.01, -.15, .24)
        value = decode_payload(decode_frame(device.telemetry(.24)))
        self.assertFalse(value.status_flags & FLAG_ARM_READY)
        self.assertEqual(value.applied_target_mnm, 0)

    def test_wire_sequence_wrap_does_not_break_internal_monotonic_device_sequence(self):
        device = SerialDevice(demo_arm=True)
        device.feed(session_open_frame(0, 123), 0.)
        device.feed(command_frame(65535, 123, MODE_ENABLE, 300, 200), .01)
        device.feed(command_frame(0, 123, MODE_ENABLE, 400, 200), .02)
        self.assertEqual(device.last_command_seq, 0)
        self.assertEqual(device.device.command['target_torque_nm'], .4)

    def test_no_demo_authorization_or_new_session_cannot_inherit_old_command(self):
        device = SerialDevice()
        device.feed(session_open_frame(0, 123), 0.)
        device.feed(command_frame(1, 123, MODE_ENABLE, 300, 200), .01)
        self.assertIsNone(device.device.command)
        device = SerialDevice(demo_arm=True)
        device.feed(session_open_frame(0, 123), 0.)
        device.feed(command_frame(1, 123, MODE_ENABLE, 300, 200), .01)
        device.feed(session_open_frame(0, 456), .02)
        self.assertIsNone(device.device.command)

    def test_late_enable_cannot_refresh_expired_authorization_before_next_step(self):
        device = SerialDevice(demo_arm=True)
        device.feed(session_open_frame(0, 123), 0.)
        device.feed(command_frame(1, 123, MODE_ENABLE, 300, 200), .01)
        device.feed(command_frame(2, 123, MODE_ENABLE, 400, 200), .22)
        self.assertFalse(device.arm_ready)
        self.assertIsNone(device.device.command)


if __name__ == '__main__':
    unittest.main()
