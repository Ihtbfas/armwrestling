"""Exercise strategy selection and phase through the shared controller."""
import math
import unittest

from common.state import BridgeState, vr_payload
from common.test_strength_response import device, update


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self.seq = 0

    def tick(self, state, now, angle=-.4, match='running', role='person_02', readiness='ready'):
        self.seq += 1
        state.receive('stm32', device(self.seq, readiness, angle, -.2), now)
        state.receive('vr', update(self.seq, role, match), now)
        return state.outputs(now)

    def test_strategy_validation_and_defensive_angle_response(self):
        with self.assertRaises(ValueError):
            BridgeState(strategy='other')
        state = BridgeState(strategy='defensive')
        samples = [self.tick(state, i*.02, angle=a)[1]
                   for i, a in enumerate((.3, -.25, -.27, -.40, -math.radians(25), -.55))]
        self.assertEqual(samples[0]['requested_target_torque_nm'], 0)
        self.assertEqual(samples[1]['strategy_scale'], .6)
        self.assertTrue(.6 < samples[2]['strategy_scale'] < samples[3]['strategy_scale'] < 1.)
        self.assertEqual(samples[4]['strategy_scale'], 1.)
        self.assertEqual(samples[5]['strategy_scale'], 1.)

    def test_rhythmic_has_deterministic_period_and_passive_bounded_output(self):
        state = BridgeState(strategy='rhythmic')
        for i in range(201):
            command, status = self.tick(state, i*.02)
            self.assertTrue(0 <= command['target_torque_nm'] <= status['eligible_target_torque_nm']
                            <= status['requested_target_torque_nm'] <= status['base_target_torque_nm'])
            self.assertLessEqual(command['brake_torque_nm'] * -.2, 0)
            self.assertTrue(.65 <= status['strategy_scale'] <= 1)
            self.assertEqual(set(vr_payload(status)), {'v', 'type', 'seq', 'angle_rad', 'mode', 'reason'})
            if i in (0, 100, 200):
                self.assertAlmostEqual(status['strategy_scale'], .65 if i == 100 else 1.)
                self.assertAlmostEqual(status['active_match_time_s'], i*.02)

    def test_phase_freezes_on_pause_fault_timeout_and_clear(self):
        state = BridgeState(strategy='rhythmic', telemetry_timeout=.09)
        for i in range(11):
            _, status = self.tick(state, i*.05)
        elapsed, fatigue = status['active_match_time_s'], status['fatigue_level']
        command, paused = self.tick(state, .55, match='paused')
        self.assertEqual(command['target_torque_nm'], 0.)
        self.assertEqual(paused['active_match_time_s'], elapsed)
        _, recovered = self.tick(state, .6, match='paused')
        self.assertLess(recovered['fatigue_level'], fatigue)
        command, resumed = self.tick(state, .65)
        self.assertEqual(command['target_torque_nm'], 0.)
        self.assertEqual(resumed['active_match_time_s'], elapsed)

        # Fault received and replaced before outputs() still interrupts the clock.
        self.seq += 1
        state.receive('stm32', device(self.seq, 'fault', -.4, -.2), .67)
        _, resumed = self.tick(state, .69)
        self.assertEqual(resumed['active_match_time_s'], elapsed)
        # The expired packet is replaced without an intervening output.
        command, resumed = self.tick(state, .8)
        self.assertEqual(command['target_torque_nm'], 0.)
        self.assertEqual(resumed['active_match_time_s'], elapsed)
        _, running = self.tick(state, .84)
        self.assertAlmostEqual(running['active_match_time_s'], elapsed+.04)
        state.clear('vr')
        command, stopped = state.outputs(.86)
        self.assertEqual(command['mode'], 'stop')
        self.assertEqual(stopped['active_match_time_s'], running['active_match_time_s'])
        _, finished = self.tick(state, .88, match='finished')
        self.assertEqual(finished['active_match_time_s'], running['active_match_time_s'])
        _, waiting = self.tick(state, .90, match='waiting')
        self.assertEqual(waiting['active_match_time_s'], 0.)

    def test_repeat_backward_and_long_control_gap_do_not_add_time(self):
        state = BridgeState(telemetry_timeout=2., strategy='rhythmic')
        self.tick(state, 0.)
        self.assertAlmostEqual(self.tick(state, .08)[1]['active_match_time_s'], .08)
        for now in (.08, .02, .42):
            self.assertAlmostEqual(self.tick(state, now)[1]['active_match_time_s'], .08)
        self.assertAlmostEqual(self.tick(state, .48)[1]['active_match_time_s'], .14)

    def test_character_lock_and_reset_are_independent_of_configured_strategy(self):
        state = BridgeState(strategy='rhythmic')
        self.tick(state, 0.)
        _, locked = self.tick(state, .1, role='person_01')
        self.assertEqual((locked['active_character_id'], locked['strategy']), ('person_02', 'rhythmic'))
        self.assertGreater(locked['active_match_time_s'], 0.)
        _, selected = self.tick(state, .2, role='person_01', match='paused')
        self.assertEqual((selected['active_character_id'], selected['strategy']), ('person_01', 'rhythmic'))
        self.assertEqual((selected['active_match_time_s'], selected['fatigue_level']), (0., 0.))

    def test_demo_command_is_identical_under_each_strategy(self):
        states = [BridgeState(strategy=s) for s in ('balanced', 'defensive', 'rhythmic')]
        for i in range(100):
            match = ('waiting', 'running', 'paused', 'running', 'finished')[i // 20]
            observed = [self.tick(state, i*.02, angle=math.sin(i/5)*.55, match=match, role='demo')
                        for state in states]
            # Telemetry sequence comes from the per-tick test counter.
            outputs = [{k:v for k,v in command.items() if k != 'telemetry_seq'} for command, _ in observed]
            self.assertEqual(outputs, [outputs[0]] * len(states))
            for _, status in observed:
                self.assertEqual(status['strategy'], 'demo')
                self.assertEqual(status['fatigue_level'], 0.)


if __name__ == '__main__':
    unittest.main()
