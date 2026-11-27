"""V1.1 simulated device behavior independent of COM and PTY transports."""
import unittest

from common.serial_protocol import (
    FLAG_ACK_VALID, FLAG_ANGLE_VALID, FLAG_ARM_READY, FLAG_CMD_TIMEOUT, FLAG_VEL_VALID,
    MODE_ENABLE, MODE_STOP, command_frame, decode_frame, decode_payload, session_open_frame,
)
from sim.serial_device import SerialDevice


def telemetry(device, now):
    return decode_payload(decode_frame(device.telemetry(now)))


def open_session(device, session=123, now=0.):
    ack = device.feed(session_open_frame(0, session), now)
    return decode_payload(decode_frame(ack)).session_id


class SerialDeviceTests(unittest.TestCase):
    def test_crc_session_duplicates_and_watchdog(self):
        device = SerialDevice(demo_arm=True)
        self.assertEqual(open_session(device), 123)
        device.step(.01, -.15, 0.)
        device.feed(command_frame(0, 123, MODE_STOP), 0.)
        bad = bytearray(command_frame(1, 123, MODE_ENABLE, 300, 200))
        bad[-1] ^= 1
        device.feed(bytes(bad), .01)
        self.assertEqual(device.last_command_seq, 0)
        device.feed(command_frame(1, 999, MODE_ENABLE, 300, 200), .02)
        self.assertEqual(device.last_command_seq, 0)
        device.feed(command_frame(1, 123, MODE_ENABLE, 300, 500), .03)
        self.assertEqual(device.device.command['target_torque_nm'], .3)
        device.feed(command_frame(1, 123, MODE_ENABLE, 300, 200), .19)
        self.assertEqual(open_session(device, now=.21), 123)
        device.step(.01, -.15, .24)
        value = telemetry(device, .24)
        self.assertFalse(value.status_flags & (FLAG_ARM_READY | FLAG_ACK_VALID))
        self.assertTrue(value.status_flags & FLAG_CMD_TIMEOUT)
        self.assertEqual((value.session_id, value.applied_target_mnm), (0, 0))

    def test_wire_sequence_wrap_preserves_internal_monotonic_sequence(self):
        device = SerialDevice(demo_arm=True)
        open_session(device)
        device.step(.01, -.15, 0.)
        device.feed(command_frame(65534, 123, MODE_STOP), 0.)
        device.feed(command_frame(65535, 123, MODE_ENABLE, 300, 200), .01)
        device.feed(command_frame(0, 123, MODE_ENABLE, 400, 200), .02)
        self.assertEqual(device.last_command_seq, 0)
        self.assertEqual(device.device.command['target_torque_nm'], .4)

    def test_stop_is_acknowledged_without_authorization_or_valid_angle(self):
        device = SerialDevice()
        open_session(device)
        device.feed(command_frame(0, 123, MODE_STOP), .01)
        value = telemetry(device, .02)
        self.assertTrue(value.status_flags & FLAG_ACK_VALID)
        self.assertFalse(value.status_flags & (FLAG_ARM_READY | FLAG_ANGLE_VALID))
        self.assertEqual((value.session_id, value.last_command_seq, value.applied_target_mnm), (123, 0, 0))
        device.step(.01, -.15, .03)
        device.feed(command_frame(1, 123, MODE_ENABLE, 300, 200), .04)
        self.assertEqual(device.last_command_seq, 0)

    def test_enable_requires_first_stop_and_new_session_clears_old_state(self):
        device = SerialDevice(demo_arm=True)
        open_session(device)
        device.step(.01, -.15, 0.)
        device.feed(command_frame(0, 123, MODE_ENABLE, 300, 200), .01)
        self.assertIsNone(device.device.command)
        device.feed(command_frame(0, 123, MODE_STOP), .02)
        device.feed(command_frame(1, 123, MODE_ENABLE, 300, 200), .03)
        self.assertTrue(telemetry(device, .03).status_flags & FLAG_ARM_READY)
        open_session(device, session=456, now=.04)
        value = telemetry(device, .04)
        self.assertEqual((value.session_id, value.applied_target_mnm), (456, 0))
        self.assertFalse(value.status_flags & (FLAG_ARM_READY | FLAG_ACK_VALID))

    def test_watchdog_requires_new_session_and_stop_to_clear_flag(self):
        device = SerialDevice(demo_arm=True)
        open_session(device)
        device.step(.01, -.15, 0.)
        device.feed(command_frame(0, 123, MODE_STOP), .01)
        device.feed(command_frame(1, 123, MODE_ENABLE, 400, 200), .22)
        self.assertFalse(device.arm_ready)
        self.assertIsNone(device.device.command)
        self.assertFalse(device.feed(session_open_frame(0, 123), .23))
        open_session(device, session=456, now=.24)
        self.assertTrue(telemetry(device, .24).status_flags & FLAG_CMD_TIMEOUT)
        device.feed(command_frame(0, 456, MODE_STOP), .25)
        value = telemetry(device, .25)
        self.assertTrue(value.status_flags & FLAG_ACK_VALID)
        self.assertFalse(value.status_flags & FLAG_CMD_TIMEOUT)

    def test_reboot_discards_old_session_at_high_sequence(self):
        device = SerialDevice(demo_arm=True)
        open_session(device)
        device.step(.01, -.15, 1.0)
        device.feed(command_frame(0, 123, MODE_STOP), 1.01)
        device.feed(command_frame(1, 123, MODE_ENABLE, 400), 1.02)
        device.telemetry_seq = 42000
        before = decode_frame(device.telemetry(1.02))
        self.assertEqual(before.sequence, 42000)
        device.reset(1.03)
        after = decode_frame(device.telemetry(1.03))
        self.assertEqual(after.sequence, 0)
        value = decode_payload(after)
        self.assertEqual((value.session_id, value.mcu_sample_ms, value.applied_target_mnm), (0, 0, 0))
        self.assertFalse(value.status_flags & (FLAG_ARM_READY | FLAG_ACK_VALID | FLAG_ANGLE_VALID))
        device.step(.01, -.15, 1.05)
        device.feed(command_frame(2, 123, MODE_ENABLE, 400), 1.06)
        self.assertEqual(telemetry(device, 1.06).session_id, 0)

    def test_sample_time_is_captured_in_step_and_wraps_as_uint32(self):
        device = SerialDevice()
        device.reset(0.)
        device.step(.01, -.15, 4_294_967.29)
        first = telemetry(device, 4_294_967.3)
        self.assertGreater(first.mcu_sample_ms, 0xFFFFFF00)
        self.assertEqual(first.mcu_sample_ms, telemetry(device, 4_294_967.4).mcu_sample_ms)
        stale = telemetry(device, 4_294_967.56)
        self.assertFalse(stale.status_flags & (FLAG_ANGLE_VALID | FLAG_VEL_VALID))
        self.assertEqual(stale.mcu_sample_ms, first.mcu_sample_ms)
        device.step(.01, -.15, 4_294_967.6)
        fresh = telemetry(device, 4_294_967.62)
        self.assertTrue(fresh.status_flags & FLAG_ANGLE_VALID)
        self.assertLess(fresh.mcu_sample_ms, 1000)


if __name__ == '__main__':
    unittest.main()
