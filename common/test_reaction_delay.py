"""Test demand-triggered waiting through the actual PC state."""
import unittest

from common.state import BridgeState, vr_payload
from common.test_strength_response import device, update


class ReactionDelayTests(unittest.TestCase):
    def tick(self, state, seq, now, q=.25, match='running', role='person_02', readiness='ready'):
        state.receive('vr', update(seq, role, match), now)
        state.receive('stm32', device(seq, readiness, -q, -.2), now)
        return state.outputs(now)

    def test_free_motion_does_not_spend_wait_and_rise_starts_from_zero(self):
        state = BridgeState(resistance_profile='narrow', reaction_delay_ms=100)
        for seq in range(51):
            _, status = self.tick(state, seq, seq*.02, q=.04)
            self.assertEqual(status['reaction_elapsed_ms'], 0.)
        for seq, now in enumerate((1.02, 1.07, 1.12, 1.14), start=51):
            command, status = self.tick(state, seq, now)
            self.assertGreater(status['requested_target_torque_nm'], 0.)
            if seq < 53:
                self.assertTrue(status['reaction_waiting'])
                self.assertEqual(status['effective_requested_target_torque_nm'], 0.)
                self.assertEqual(status['fatigue_level'], 0.)
            else:
                self.assertFalse(status['reaction_waiting'])
                self.assertEqual(status['effective_requested_target_torque_nm'],
                                 status['requested_target_torque_nm'])
            if seq <= 53:
                self.assertEqual(command['target_torque_nm'], 0.)
                self.assertEqual(status['fatigue_level'], 0.)
            else:
                self.assertGreater(command['target_torque_nm'], 0.)
            self.assertLessEqual(command['brake_torque_nm'] * -.2, 0.)
            self.assertEqual(set(vr_payload(status)), {'v','type','seq','angle_rad','mode','reason'})

    def test_cancellation_and_reentry_do_not_reuse_the_old_timer(self):
        state = BridgeState(resistance_profile='narrow', reaction_delay_ms=200)
        self.tick(state, 0, 0.)
        self.tick(state, 1, .12)
        command, status = self.tick(state, 2, .14, q=.02)
        self.assertFalse(status['reaction_waiting'])
        self.assertEqual(command['target_torque_nm'], 0.)
        _, status = self.tick(state, 3, .16)
        self.assertTrue(status['reaction_waiting'])
        self.assertEqual(status['reaction_elapsed_ms'], 0.)
        self.assertTrue(self.tick(state, 4, .32)[1]['reaction_waiting'])
        _, status = self.tick(state, 5, .36, q=.10)
        self.assertFalse(status['reaction_waiting'])
        self.assertEqual(status['effective_requested_target_torque_nm'], status['base_target_torque_nm'])

    def test_drops_and_stop_are_immediate_after_gate_open(self):
        state = BridgeState(resistance_profile='narrow', reaction_delay_ms=100)
        for seq in range(51):
            self.tick(state, seq, seq*.02, q=.4)
        command, status = self.tick(state, 51, 1.02, q=.06)
        self.assertFalse(status['reaction_waiting'])
        self.assertEqual(command['target_torque_nm'], status['eligible_target_torque_nm'])
        command, status = self.tick(state, 52, 1.04, q=.04)
        self.assertEqual(command['target_torque_nm'], 0.)
        self.assertFalse(status['reaction_waiting'])
        self.assertTrue(self.tick(state, 53, 1.06)[1]['reaction_waiting'])
        command, status = self.tick(state, 54, 1.08, match='paused')
        self.assertEqual((command['mode'], command['target_torque_nm']), ('stop', 0.))
        self.assertEqual(status['reaction_elapsed_ms'], 0.)

    def test_transient_pause_fault_and_expiry_clear_wait_between_outputs(self):
        for intermission in ('pause', 'fault', 'expired', 'clear'):
            with self.subTest(intermission=intermission):
                state = BridgeState(telemetry_timeout=.11, resistance_profile='narrow', reaction_delay_ms=200)
                self.tick(state, 0, 0.)
                self.tick(state, 1, .08)
                if intermission == 'pause':
                    state.receive('vr', update(2, 'person_02', 'paused'), .09)
                elif intermission == 'fault':
                    state.receive('stm32', device(2, 'fault', -.25, -.2), .09)
                elif intermission == 'clear':
                    state.clear('vr')
                command, status = self.tick(state, 3, .20 if intermission == 'expired' else .10)
                self.assertEqual(command['target_torque_nm'], 0.)
                self.assertTrue(status['reaction_waiting'])
                self.assertEqual(status['reaction_elapsed_ms'], 0.)

    def test_repeated_backward_and_long_tick_times_do_not_advance_wait(self):
        state = BridgeState(telemetry_timeout=2., reaction_delay_ms=100)
        for i, (now, elapsed) in enumerate(((10., 0.), (10.05, 50.), (10.05, 50.),
                                            (10.04, 0.), (10.50, 0.), (10.54, 40.))):
            _, status = self.tick(state, i, now, q=.4)
            self.assertTrue(status['reaction_waiting'])
            self.assertAlmostEqual(status['reaction_elapsed_ms'], elapsed)

    def test_demo_bypasses_and_invalid_delay_is_rejected(self):
        for value in (-1, 201, float('inf'), float('nan'), True, '100'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                BridgeState(reaction_delay_ms=value)
        zero, delayed = BridgeState(), BridgeState(reaction_delay_ms=200)
        for seq in range(10):
            self.assertEqual(self.tick(zero, seq, seq*.02, q=.4, role='demo'),
                             self.tick(delayed, seq, seq*.02, q=.4, role='demo'))


if __name__ == '__main__':
    unittest.main()
