"""V0.3 demonstration fatigue, with deterministic clocks and real PC state."""
import math
import unittest

from common.characters import CHARACTERS
from common.state import BridgeState, vr_payload
from common.test_strength_response import device, update


class BridgeFatigueTests(unittest.TestCase):
    def setUp(self):
        self.state = BridgeState()
        self.seq = 0

    def tick(self, now, match='running', character='person_02', angle=-.4,
             speed=-.2, status='ready'):
        self.state.receive('stm32', device(self.seq, status, angle, speed), now)
        self.state.receive('vr', update(self.seq, character, match), now)
        self.seq += 1
        return self.state.outputs(now)

    def loaded(self):
        for i in range(101):
            command, status = self.tick(i*.1)
        self.assertGreater(status['fatigue_level'], .1)
        return command, status

    def test_rise_then_sustained_fatigue_with_passive_bounds(self):
        samples = []
        for i in range(201):
            command, status = self.tick(i*.1)
            samples.append(command['target_torque_nm'])
            self.assertIn('fatigue_level', status)
            self.assertTrue(0 <= status['fatigue_level'] <= 1)
            self.assertTrue(0 <= command['target_torque_nm'] <=
                status['eligible_target_torque_nm'] <= status['base_target_torque_nm'])
            self.assertLessEqual(command['brake_torque_nm'] * -.2, 0)
            self.assertEqual(set(vr_payload(status)), {'v','type','seq','angle_rad','mode','reason'})
        self.assertEqual(samples[0], 0.)
        self.assertGreater(samples[10], samples[1])
        self.assertLess(samples[-1], samples[30])

    def test_low_load_and_pause_recover_then_resume_rises_from_zero(self):
        _, loaded = self.loaded()
        for i in range(101, 131):
            _, low = self.tick(i*.1, angle=-.26)
        self.assertLess(low['fatigue_level'], loaded['fatigue_level'])
        for i in range(131, 161):
            command, paused = self.tick(i*.1, match='paused')
            self.assertEqual((command['mode'], command['target_torque_nm'],
                              command['brake_torque_nm']), ('stop', 0., 0.))
        self.assertLess(paused['fatigue_level'], low['fatigue_level'])
        command, resumed = self.tick(16.1)
        self.assertEqual(command['target_torque_nm'], 0.)
        self.assertAlmostEqual(resumed['fatigue_level'], paused['fatigue_level'])
        command, _ = self.tick(16.2)
        self.assertGreater(command['target_torque_nm'], 0.)

    def test_finished_preserves_and_waiting_resets_even_with_repeated_heartbeats(self):
        _, loaded = self.loaded()
        for i in range(101, 121):
            command, finished = self.tick(i*.1, match='finished')
            self.assertEqual(command['target_torque_nm'], 0.)
            self.assertEqual(finished['fatigue_level'], loaded['fatigue_level'])
        for i in range(121, 125):
            _, waiting = self.tick(i*.1, match='waiting')
            self.assertEqual(waiting['fatigue_level'], 0.)
        self.assertEqual(self.tick(12.5)[0]['target_torque_nm'], 0.)

    def test_faults_timeouts_and_disconnects_freeze_without_catchup(self):
        for failure in ('fault', 'telemetry_timeout', 'vr_timeout', 'clear_stm32', 'clear_vr'):
            with self.subTest(failure=failure):
                self.setUp()
                _, loaded = self.loaded()
                now = 10.1
                if failure == 'fault':
                    self.tick(now, status='fault')
                elif failure.startswith('clear_'):
                    self.state.clear(failure[6:])
                else:
                    now = 11.1
                    if failure == 'vr_timeout':
                        self.state.receive('stm32', device(self.seq), now)
                        self.seq += 1
                for t in (now, now+1):
                    command, stopped = self.state.outputs(t)
                    self.assertEqual((command['mode'], command['target_torque_nm']), ('stop', 0.))
                    self.assertEqual(stopped['fatigue_level'], loaded['fatigue_level'])
                command, resumed = self.tick(now+2)
                self.assertEqual(command['target_torque_nm'], 0.)
                self.assertEqual(resumed['fatigue_level'], loaded['fatigue_level'])

    def test_running_id_is_locked_and_paused_switch_does_not_inherit_fatigue(self):
        _, loaded = self.loaded()
        _, locked = self.tick(10.1, character='person_03')
        self.assertEqual(locked['active_character_id'], 'person_02')
        self.assertGreater(locked['fatigue_level'], loaded['fatigue_level'])
        command, switched = self.tick(10.2, match='paused', character='person_03')
        self.assertEqual(switched['active_character_id'], 'person_03')
        self.assertEqual(switched['fatigue_level'], 0.)
        self.assertEqual(command['target_torque_nm'], 0.)
        self.assertEqual(self.tick(10.3, character='person_03')[0]['target_torque_nm'], 0.)

    def test_transient_pause_fault_and_expired_input_do_not_integrate_unknown_interval(self):
        for interruption in ('paused', 'fault', 'expired'):
            with self.subTest(interruption=interruption):
                self.setUp()
                _, loaded = self.loaded()
                if interruption == 'paused':
                    self.state.receive('vr', update(self.seq, state='paused'), 10.01)
                    self.seq += 1
                elif interruption == 'fault':
                    self.state.receive('stm32', device(self.seq, status='fault'), 10.01)
                    self.seq += 1
                now = 10.3 if interruption == 'expired' else 10.02
                command, resumed = self.tick(now)
                self.assertEqual(command['target_torque_nm'], 0.)
                self.assertEqual(resumed['fatigue_level'], loaded['fatigue_level'])

    def test_demo_exact_original_control_during_long_runs_and_states(self):
        from common.control import brake_state
        for i in range(401):
            speed = (-.2, .2, 0.)[i % 3]
            match = ('running', 'paused', 'waiting', 'finished')[i // 100 % 4]
            command, status = self.tick(i*.1, character='demo', speed=speed, match=match)
            expected = brake_state(-.4, speed, CHARACTERS['demo'].brake_parameters)
            self.assertEqual(command['target_torque_nm'], expected.target_torque if match == 'running' else 0.)
            self.assertEqual(command['brake_torque_nm'], expected.applied_torque if match == 'running' else 0.)
            self.assertEqual(status['fatigue_level'], 0.)
            self.assertEqual(status['fatigue_capacity'], 1.)


class FatigueModelTests(unittest.TestCase):
    def test_parameters_are_finite_and_bounded(self):
        from common.fatigue import FatigueParameters
        for field in ('fatigue_rate', 'recovery_rate', 'fatigue_loss_fraction'):
            for value in (-.1, 1.1, float('inf'), float('nan')):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    FatigueParameters(**{field: value})

    def test_exact_dt_independent_fatigue_and_recovery(self):
        from common.fatigue import FatigueParameters, FatigueState
        params = FatigueParameters(.1, .2, .5)
        for intervals in ((.02,)*50, (.1,)*10, (.01, .09)*10):
            state = FatigueState()
            state.update(1., params, 0.)
            now = 0.
            for dt in intervals:
                now += dt
                state.update(1., params, now)
            self.assertAlmostEqual(state.value, 1-math.exp(-.1), places=12)
            loaded = state.value
            for i in range(1, 11):
                state.update(0., params, now+i*.1)
            self.assertAlmostEqual(state.value, loaded*math.exp(-.2), places=12)

    def test_clock_repeat_backwards_gap_and_nonfinite_preserve_fatigue(self):
        from common.fatigue import FatigueParameters, FatigueState
        params = FatigueParameters(.1, .2, .5)
        for abnormal in (.1, -.1, 5., float('nan'), float('inf')):
            state = FatigueState()
            state.update(1., params, 0.)
            old = state.update(1., params, .1)
            self.assertEqual(state.update(1., params, abnormal), old)
            state.freeze()
            self.assertEqual(state.update(1., params, 100.), old)
            self.assertGreater(state.update(1., params, 100.1), old)
            state.reset()
            self.assertEqual(state.value, 0.)

    def test_load_clamping_and_long_run_bounds(self):
        from common.fatigue import FatigueParameters, FatigueState
        params = FatigueParameters(1., 1., 1.)
        state = FatigueState()
        for i in range(1001):
            state.update(5., params, i*.1)
            self.assertTrue(0 <= state.value <= 1)
        self.assertGreater(state.value, .99)
        for i in range(1001, 2001):
            state.update(-5., params, i*.1)
        self.assertLess(state.value, .01)


if __name__ == '__main__':
    unittest.main()
