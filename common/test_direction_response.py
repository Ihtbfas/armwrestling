"""Boundary and lifecycle tests for the opt-in software return relief."""
import unittest

from common.state import BridgeState, vr_payload
from common.test_strength_response import device, update


class DirectionResponseTests(unittest.TestCase):
    def tick(self, state, seq, now, speed, match='running', character='person_02', readiness='ready'):
        state.receive('vr', update(seq, character, match), now)
        state.receive('stm32', device(seq, readiness, -.25, speed), now)
        return state.outputs(now)

    def test_option_requires_narrow_and_demo_bypasses(self):
        for profile in ('legacy', 'start-resistance'):
            with self.assertRaises(ValueError):
                BridgeState(resistance_profile=profile, return_mode='relief')
        with self.assertRaises(ValueError):
            BridgeState(resistance_profile='narrow', return_mode='unknown')
        states = [BridgeState(), BridgeState(resistance_profile='narrow', return_mode='relief')]
        for i, speed in enumerate((-.2, 0., .001, .025, .05, .2)):
            results = [self.tick(state, i, i*.02, speed, character='demo') for state in states]
            self.assertEqual(*results)
            self.assertEqual(results[1][1]['return_mode'], 'symmetric')

    def test_continuous_velocity_factor_and_passivity(self):
        state = BridgeState(resistance_profile='narrow', return_mode='relief')
        probes = ((-.2, 1.), (-1e-8, 1.), (0., 1.), (1e-8, .9999999),
                  (.025, .75), (.05, .5), (.2, .5))
        for seq, (speed, expected) in enumerate(probes):
            command, status = self.tick(state, seq, seq*.02, speed)
            self.assertEqual(status['qdot_rad_s'], -speed)
            self.assertAlmostEqual(status['direction_scale'], expected)
            self.assertAlmostEqual(status['requested_target_torque_nm'],
                                   status['base_target_torque_nm']*expected)
            self.assertLessEqual(command['brake_torque_nm']*speed, 0.)
            self.assertLessEqual(command['target_torque_nm'], status['eligible_target_torque_nm'])
            self.assertEqual(set(vr_payload(status)), {'v','type','seq','angle_rad','mode','reason'})

    def test_push_reversal_drops_then_rebuilds_existing_rise(self):
        symmetric = BridgeState(resistance_profile='narrow')
        relief = BridgeState(resistance_profile='narrow', return_mode='relief')
        for seq in range(51):
            reference, a = self.tick(symmetric, seq, seq*.02, -.2)
            command, b = self.tick(relief, seq, seq*.02, -.2)
            self.assertEqual(command, reference)
            self.assertEqual(a['fatigue_level'], b['fatigue_level'])
        command, _ = self.tick(relief, 51, 1.02, .2)
        self.assertLess(command['target_torque_nm'], reference['target_torque_nm'])
        dropped = command['target_torque_nm']
        command, status = self.tick(relief, 52, 1.04, -.2)
        self.assertTrue(dropped < command['target_torque_nm'] < status['eligible_target_torque_nm'])

    def test_all_stop_gates_with_relief_and_reset(self):
        for failure in ('paused', 'waiting', 'finished', 'fault', 'telemetry_timeout', 'vr_timeout'):
            with self.subTest(failure=failure):
                state = BridgeState(vr_timeout=.2, resistance_profile='narrow', return_mode='relief')
                for seq in range(3):
                    command, _ = self.tick(state, seq, seq*.05, .1)
                self.assertGreater(command['target_torque_nm'], 0.)
                if failure == 'telemetry_timeout':
                    command, status = state.outputs(.4)
                elif failure == 'vr_timeout':
                    state.receive('stm32', device(3, 'ready', -.25, .1), .4)
                    command, status = state.outputs(.4)
                else:
                    command, status = self.tick(state, 3, .15, .1,
                        match='running' if failure == 'fault' else failure,
                        readiness='fault' if failure == 'fault' else 'ready')
                self.assertEqual((command['mode'], command['target_torque_nm'], command['brake_torque_nm']),
                                 ('stop', 0., 0.))
                command, status = self.tick(state, 4, .45, .1, match='waiting')
                self.assertEqual((status['fatigue_level'], status['active_match_time_s']), (0., 0.))


if __name__ == '__main__':
    unittest.main()
