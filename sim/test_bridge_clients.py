"""Local watchdog and passive direction checks without MuJoCo."""
import unittest

from sim.mock_stm32 import SimulatedDevice


def command(seq=0, **changes):
    result = dict(v=1, type='brake_command', seq=seq, mode='enabled', reason='running',
                  target_torque_nm=0.3, brake_torque_nm=0.3,
                  speed_transition_rad_s=0.05, valid_for_ms=200, telemetry_seq=1)
    result.update(changes)
    return result


class DeviceTests(unittest.TestCase):
    def test_velocity_reversal_cannot_turn_delayed_torque_into_assistance(self):
        device = SimulatedDevice()
        device.receive(command(), 10.0)  # PC saw negative velocity.
        device.velocity = 0.2           # Device has already reversed.
        device.step(0.002, 0.0, 10.01)
        self.assertLess(device.brake, 0)
        self.assertLessEqual(device.power, 0)

    def test_expired_disconnected_and_stop_commands_have_zero_output(self):
        device = SimulatedDevice()
        device.receive(command(), 10.0)
        device.velocity = -0.2
        device.step(0.002, 0, 10.21)
        self.assertEqual(device.brake, 0)
        self.assertEqual(device.reason, 'command_timeout')
        device.receive(command(seq=1), 10.22)
        device.stop()
        device.step(0.002, 0, 10.23)
        self.assertEqual(device.brake, 0)
        device.receive(command(mode='stop', target_torque_nm=0, brake_torque_nm=0), 10.24)
        device.step(0.002, 0, 10.25)
        self.assertEqual(device.brake, 0)

    def test_bad_or_repeated_command_cannot_extend_lifetime(self):
        device = SimulatedDevice()
        device.receive(command(), 10.0)
        for value in (command(), command(seq=1, valid_for_ms=5000),
                      command(seq=1, target_torque_nm=float('nan'))):
            with self.assertRaises(ValueError):
                device.receive(value, 10.19)
        device.step(0.002, 0, 10.21)
        self.assertEqual(device.brake, 0)


if __name__ == '__main__':
    unittest.main()
