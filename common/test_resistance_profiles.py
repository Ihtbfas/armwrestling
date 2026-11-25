"""Check experimental resistance regions and the original control gates."""
import math
import unittest

from common.characters import CHARACTERS
from common.control import brake_state
from common.state import BridgeState, vr_payload
from common.test_strength_response import device, update


PROFILES = ('legacy', 'narrow', 'start-resistance')
WIN_RAD = math.radians(25)
PARAMETERS = CHARACTERS['person_02'].brake_parameters


class ResistanceProfileTests(unittest.TestCase):
    def test_curves_are_bounded_monotone_on_advance_and_join_legacy(self):
        from common.resistance_profiles import profile_target
        for profile in PROFILES:
            def target(q):
                return profile_target(profile, brake_state(-q, 0., PARAMETERS), PARAMETERS)

            values = [target(i/1000) for i in range(-600, 551)]
            self.assertTrue(all(math.isfinite(v) and 0 <= v <= PARAMETERS.max_torque for v in values))
            self.assertEqual(values, sorted(values))
            for q in (WIN_RAD, .45, .55, 1.):
                self.assertEqual(target(q), brake_state(-q, 0., PARAMETERS).target_torque)
            for q in (-.05, 0., .05, .25, WIN_RAD):
                self.assertAlmostEqual(target(q-1e-8), target(q+1e-8), places=6)

    def test_zero_and_small_angle_definitions(self):
        from common.resistance_profiles import profile_target

        def target(profile, q):
            return profile_target(profile, brake_state(-q, 0., PARAMETERS), PARAMETERS)

        anchor = target('legacy', WIN_RAD)
        self.assertEqual(target('legacy', .25), 0.)
        self.assertEqual(target('narrow', .05), 0.)
        self.assertGreater(target('narrow', .15), 0.)
        self.assertEqual(target('start-resistance', 0.), .15*anchor)
        self.assertAlmostEqual(target('start-resistance', -.025), .075*anchor)
        self.assertEqual(target('start-resistance', -.05), 0.)
        with self.assertRaises(ValueError):
            BridgeState(resistance_profile='unknown')

    def test_demo_bypasses_candidates_with_identical_outputs(self):
        states = [BridgeState(resistance_profile=profile) for profile in PROFILES]
        for i in range(50):
            angle = .5*math.sin(i*.2)
            now = i*.02
            compared = []
            for state in states:
                state.receive('vr', update(i, 'demo'), now)
                state.receive('stm32', device(i, 'ready', angle, -.2), now)
                command, status = state.outputs(now)
                self.assertEqual(status['resistance_profile'], 'legacy')
                compared.append((command, status))
            self.assertEqual(compared, [compared[0]]*len(states))

    def test_start_profile_gates_on_fresh_running_and_preserves_wire_shape(self):
        state = BridgeState(resistance_profile='start-resistance')
        for i, speed in enumerate((0., 0., -.2, .2)):
            now = i*.1
            state.receive('vr', update(i, 'person_02'), now)
            state.receive('stm32', device(i, 'ready', 0., speed), now)
            command, status = state.outputs(now)
            self.assertTrue(0 <= command['target_torque_nm'] <= status['eligible_target_torque_nm']
                            <= status['requested_target_torque_nm'] <= status['base_target_torque_nm'])
            self.assertEqual(set(vr_payload(status)), {'v','type','seq','angle_rad','mode','reason'})
            self.assertLessEqual(command['brake_torque_nm']*speed, 0.)
            if i == 1:
                self.assertGreater(command['target_torque_nm'], 0.)
                self.assertEqual(command['brake_torque_nm'], 0.)
            elif i >= 2:
                self.assertLess(command['brake_torque_nm']*speed, 0.)
        command, status = state.outputs(.6)
        self.assertEqual((command['mode'], command['target_torque_nm'], command['brake_torque_nm']), ('stop', 0., 0.))
        self.assertEqual(status['reason'], 'telemetry_timeout')
        for i, match in enumerate(('paused', 'finished', 'waiting'), start=4):
            now = .6+i*.02
            state.receive('vr', update(i, 'person_02', match), now)
            state.receive('stm32', device(i, 'ready', 0., -.2), now)
            command, status = state.outputs(now)
            self.assertEqual((command['mode'], command['target_torque_nm'], command['brake_torque_nm']), ('stop', 0., 0.))
            self.assertEqual(status['base_target_torque_nm'], 0.)
        self.assertEqual((status['fatigue_level'], status['active_match_time_s']), (0., 0.))

    def test_experimental_profiles_stop_on_fault_and_vr_timeout(self):
        for profile in PROFILES[1:]:
            for failure in ('device_fault', 'vr_timeout'):
                with self.subTest(profile=profile, failure=failure):
                    state = BridgeState(vr_timeout=.12, resistance_profile=profile)
                    for seq, now in enumerate((0., .05, .1)):
                        state.receive('vr', update(seq, 'person_02'), now)
                        state.receive('stm32', device(seq, 'ready', -.18, -.2), now)
                        command, _ = state.outputs(now)
                    self.assertGreater(command['target_torque_nm'], 0.)
                    now = .11 if failure == 'device_fault' else .25
                    state.receive('stm32', device(3, 'fault' if failure == 'device_fault' else 'ready',
                                                  -.18, -.2), now)
                    command, status = state.outputs(now)
                    self.assertEqual((command['mode'], command['target_torque_nm'], command['brake_torque_nm']),
                                     ('stop', 0., 0.))
                    self.assertEqual(status['reason'], failure)


if __name__ == '__main__':
    unittest.main()
