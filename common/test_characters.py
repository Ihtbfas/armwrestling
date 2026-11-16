"""Character metadata and round selection; no hardware, GUI, or extra packages."""
from dataclasses import FrozenInstanceError
import math
import unittest

from common.characters import CHARACTERS, resolve_character
from common.control import BrakeParameters, brake_state
from common.state import BridgeState, vr_payload


def telemetry(seq=0, angle=-.4, speed=-.2, status='ready'):
    return dict(v=1, type='telemetry', seq=seq, angle_rad=angle,
                velocity_rad_s=speed, device_status=status)


def vr(seq=0, character='demo', match='waiting'):
    return dict(v=1, type='vr_update', seq=seq, character_id=character, match_state=match)


class CharacterTests(unittest.TestCase):
    def test_catalogue_has_people_and_explicit_demo_values_with_readonly_profiles(self):
        self.assertEqual(len(CHARACTERS), 4)
        self.assertEqual(resolve_character('demo').brake_parameters, BrakeParameters())
        for key, person in CHARACTERS.items():
            self.assertEqual(person.character_id, key)
            self.assertTrue(person.name and person.sex and person.training_background)
            self.assertEqual(person.parameter_source, 'demonstration_only')
            if key != 'demo':
                self.assertIsInstance(person.age_years, int)
            parameters = person.brake_parameters
            self.assertTrue(math.isfinite(parameters.stiffness))
            self.assertTrue(0 <= parameters.stiffness <= 2.)
            self.assertTrue(0 <= parameters.max_torque <= .8)
            self.assertEqual((parameters.free_boundary, parameters.forward_sign, parameters.speed_transition), (.25, -1, .05))
        with self.assertRaises(FrozenInstanceError):
            resolve_character('person_01').age_years = 99
        with self.assertRaises(TypeError):
            CHARACTERS['new'] = resolve_character('demo')

    def test_demo_exact_baseline_and_other_profiles_are_bounded_and_passive(self):
        for angle in (0., .6, -.2, -.25, -.4, -.55, -1., -100.):
            for speed in (-.2, -.01, 0., .01, .2):
                expected = brake_state(angle, speed, BrakeParameters())
                state = BridgeState()
                state.receive('stm32', telemetry(angle=angle, speed=speed), 10.)
                state.receive('vr', vr(match='running'), 10.)
                command, _ = state.outputs(10.01)
                self.assertEqual(command['target_torque_nm'], expected.target_torque)
                self.assertEqual(command['brake_torque_nm'], expected.applied_torque)
                for person in CHARACTERS.values():
                    result = brake_state(angle, speed, person.brake_parameters)
                    self.assertLessEqual(result.target_torque, expected.target_torque)
                    self.assertLessEqual(result.applied_torque * speed, 0.)

    def test_same_telemetry_has_distinct_torques_and_exact_udp_field_set(self):
        targets = []
        for key in CHARACTERS:
            state = BridgeState()
            state.receive('stm32', telemetry(), 10.)
            state.receive('vr', vr(character=key), 10.)
            state.receive('vr', vr(1, key, 'running'), 10.01)
            command, status = state.outputs(10.02)
            # V0.1 static targets remain exact; V0.2 starts non-demo output at
            # zero and tests the actual time response in test_strength_response.
            targets.append(round(status['base_target_torque_nm'], 6))
            self.assertEqual(command['target_torque_nm'], status['base_target_torque_nm'] if key == 'demo' else 0.)
            self.assertEqual(status['active_character_id'], key)
            self.assertEqual(set(vr_payload(status)), {'v', 'type', 'seq', 'angle_rad', 'mode', 'reason'})
            self.assertEqual(status['angle_rad'], -.4)
        self.assertEqual(targets, [.3, .15, .225, .27])

    def test_first_running_can_choose_then_running_packets_cannot_change_role(self):
        state = BridgeState()
        state.receive('stm32', telemetry(), 10.)
        state.receive('vr', vr(0, 'person_01', 'running'), 10.)
        first, first_status = state.outputs(10.01)
        self.assertEqual(first['target_torque_nm'], 0.)
        self.assertAlmostEqual(first_status['base_target_torque_nm'], .15)
        state.receive('vr', vr(1, 'person_03', 'running'), 10.02)
        command, status = state.outputs(10.03)
        self.assertAlmostEqual(status['base_target_torque_nm'], .15)
        self.assertAlmostEqual(command['target_torque_nm'], .15*(1-math.exp(-.02/.15)))
        self.assertEqual(status['character_id'], 'person_03')
        self.assertEqual(status['active_character_id'], 'person_01')
        self.assertTrue(status['character_selection_deferred'])
        state.receive('vr', vr(2, 'person_03', 'paused'), 10.04)
        self.assertEqual(state.outputs(10.05)[0]['target_torque_nm'], 0.)
        state.receive('vr', vr(3, 'person_03', 'running'), 10.06)
        command, status = state.outputs(10.07)
        self.assertEqual(command['target_torque_nm'], 0.)
        self.assertAlmostEqual(status['base_target_torque_nm'], .27)

    def test_unknown_role_falls_back_visibly_but_cannot_change_locked_round(self):
        self.assertEqual(resolve_character('missing').character_id, 'demo')
        state = BridgeState()
        state.receive('stm32', telemetry(), 10.)
        state.receive('vr', vr(character='missing'), 10.)
        command, status = state.outputs(10.01)
        self.assertEqual(status['active_character_id'], 'demo')
        self.assertTrue(status['character_fallback'])
        state.receive('vr', vr(1, 'person_01', 'paused'), 10.02)
        state.receive('vr', vr(2, 'person_01', 'running'), 10.03)
        state.receive('vr', vr(3, 'missing', 'running'), 10.04)
        command, status = state.outputs(10.05)
        self.assertEqual(command['target_torque_nm'], 0.)
        self.assertAlmostEqual(status['base_target_torque_nm'], .15)
        self.assertTrue(status['character_fallback'])
        self.assertTrue(status['character_selection_deferred'])

    def test_protection_stops_for_every_profile_without_changing_selection(self):
        for key in CHARACTERS:
            for failure in ('waiting', 'paused', 'finished', 'fault', 'telemetry_timeout', 'vr_timeout', 'vr_missing', 'telemetry_missing'):
                with self.subTest(character=key, failure=failure):
                    state = BridgeState()
                    state.receive('stm32', telemetry(), 10.)
                    state.receive('vr', vr(0, key, 'running'), 10.)
                    now = 10.01
                    if failure in ('waiting', 'paused', 'finished'):
                        state.receive('vr', vr(1, key, failure), now)
                    elif failure == 'fault':
                        state.receive('stm32', telemetry(1, status='fault'), now)
                    elif failure == 'telemetry_timeout':
                        now = 10.3
                    elif failure == 'vr_timeout':
                        now = 11.1
                        state.receive('stm32', telemetry(1), now)
                    else:
                        state.clear('vr' if failure == 'vr_missing' else 'stm32')
                    command, status = state.outputs(now)
                    self.assertEqual((command['mode'], command['target_torque_nm'], command['brake_torque_nm']), ('stop', 0., 0.))
                    self.assertEqual(status['active_character_id'], key)
                    expected = ('match_' + failure if failure in ('waiting', 'paused', 'finished')
                                else 'device_fault' if failure == 'fault' else failure)
                    self.assertEqual(command['reason'], expected)
                    self.assertEqual(status['reason'], expected)
                    if failure in ('telemetry_timeout', 'telemetry_missing'):
                        self.assertIsNone(status['angle_rad'])

    def test_rejected_update_cannot_select_role_or_refresh_heartbeat(self):
        state = BridgeState()
        state.receive('stm32', telemetry(), 10.)
        state.receive('vr', vr(1, 'person_01', 'running'), 10.)
        for message in (vr(1, 'person_03', 'paused'), vr(2, 'person_03', 'bad-state')):
            with self.assertRaises(ValueError):
                state.receive('vr', message, 10.1)
        self.assertEqual(state.outputs(10.11)[1]['active_character_id'], 'person_01')
        self.assertEqual(state.received['vr'], 10.)

    def test_disconnect_timeout_and_device_recovery_do_not_confirm_running_request(self):
        for reset in ('vr', 'stm32', 'timeout'):
            with self.subTest(reset=reset):
                state = BridgeState()
                state.receive('stm32', telemetry(), 10.)
                state.receive('vr', vr(0, 'person_01', 'running'), 10.)
                state.receive('vr', vr(1, 'person_03', 'running'), 10.01)
                if reset == 'timeout':
                    state.outputs(11.1)
                else:
                    state.clear(reset)
                state.receive('stm32', telemetry(1), 11.2)
                state.receive('vr', vr(2, 'person_03', 'running'), 11.2)
                command, status = state.outputs(11.21)
                self.assertEqual(command['target_torque_nm'], 0.)
                self.assertAlmostEqual(status['base_target_torque_nm'], .15)
                self.assertEqual(status['active_character_id'], 'person_01')


if __name__ == '__main__':
    unittest.main()
