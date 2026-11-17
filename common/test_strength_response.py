"""V0.2 rising-only response and protection resets with an injected clock."""
import math
import unittest

from common.characters import CHARACTERS
from common.state import BridgeState, vr_payload
from common.strength_response import StrengthResponse


def device(seq, status='ready', angle=-.4, speed=-.2):
    return dict(v=1, type='telemetry', seq=seq, device_status=status,
                angle_rad=angle, velocity_rad_s=speed)


def update(seq, character='person_02', state='running'):
    return dict(v=1, type='vr_update', seq=seq, character_id=character, match_state=state)


class StrengthResponseTests(unittest.TestCase):
    def test_zero_tau_is_exact_and_demo_is_unchanged(self):
        response = StrengthResponse()
        for now, target in ((0., .3), (.02, .8), (.02, .1), (100., .6), (99., .3)):
            self.assertEqual(response.update(target, 0., now), target)
        self.assertEqual(CHARACTERS['demo'].rise_time_constant_s, 0.)
        self.assertEqual([CHARACTERS[key].rise_time_constant_s for key in
                         ('person_01', 'person_02', 'person_03')], [.15, .4, .8])

    def test_actual_dt_repeat_time_and_partition_independence(self):
        for intervals in ((.02,)*50, (.05,)*20, (.1,)*10, (.01, .09)*10):
            response = StrengthResponse()
            self.assertEqual(response.update(.3, .4, 0.), 0.)
            now = 0.
            for dt in intervals:
                now += dt
                value = response.update(.3, .4, now)
                self.assertEqual(response.update(.3, .4, now), value)
            self.assertAlmostEqual(value, .3*(1-math.exp(-1/.4)), places=12)

    def test_falling_target_and_free_space_are_immediate_even_at_same_time(self):
        response = StrengthResponse()
        response.update(.6, .15, 0.)
        value = response.update(.6, .15, .2)
        self.assertGreater(value, .2)
        self.assertEqual(response.update(.1, .15, .2), .1)
        self.assertEqual(response.update(0., .15, .2), 0.)

    def test_clock_discontinuities_reset_instead_of_catching_up(self):
        for abnormal in (5., -.1, float('nan'), float('inf')):
            with self.subTest(now=abnormal):
                response = StrengthResponse()
                response.update(.3, .4, 0.)
                self.assertGreater(response.update(.3, .4, .1), 0)
                self.assertEqual(response.update(.3, .4, abnormal), 0.)
                self.assertEqual(response.update(.3, .4, 10.), 0.)
                self.assertAlmostEqual(response.update(.3, .4, 10.02), .3*(1-math.exp(-.02/.4)))

    def test_every_stop_discards_a_previously_established_nonzero_output(self):
        for failure in ('waiting','paused','finished','fault','telemetry_timeout','vr_timeout','vr_missing','telemetry_missing'):
            with self.subTest(failure=failure):
                state = BridgeState()
                for i in range(11):
                    now = i*.05
                    state.receive('stm32', device(i), now)
                    state.receive('vr', update(i), now)
                    command, _ = state.outputs(now)
                self.assertGreater(command['target_torque_nm'], .1)
                now = .55
                if failure in ('waiting','paused','finished'):
                    state.receive('vr', update(11, state=failure), now)
                elif failure == 'fault':
                    state.receive('stm32', device(11, status='fault'), now)
                elif failure == 'telemetry_timeout':
                    now = .8
                elif failure == 'vr_timeout':
                    now = 1.6
                    state.receive('stm32', device(11), now)
                else:
                    state.clear('vr' if failure == 'vr_missing' else 'stm32')
                command, _ = state.outputs(now)
                self.assertEqual((command['mode'],command['target_torque_nm'],command['brake_torque_nm']), ('stop',0.,0.))
                now += .01
                state.receive('stm32', device(20), now)
                state.receive('vr', update(20), now)
                self.assertEqual(state.outputs(now)[0]['target_torque_nm'], 0.)
                state.receive('stm32', device(21), now+.02)
                state.receive('vr', update(21), now+.02)
                command, status = state.outputs(now+.02)
                self.assertAlmostEqual(status['base_target_torque_nm'], .225)
                self.assertAlmostEqual(command['target_torque_nm'],
                    status['eligible_target_torque_nm']*(1-math.exp(-.02/.4)))

    def test_transient_pause_or_fault_between_output_ticks_also_resets(self):
        for transient in ('paused','fault'):
            state = BridgeState()
            state.receive('stm32', device(0), 0.)
            state.receive('vr', update(0), 0.)
            state.outputs(0.)
            self.assertGreater(state.outputs(.1)[0]['target_torque_nm'], 0.)
            if transient == 'paused':
                state.receive('vr', update(1, state='paused'), .11)
                state.receive('vr', update(2), .12)
            else:
                state.receive('stm32', device(1, status='fault'), .11)
                state.receive('stm32', device(2), .12)
            self.assertEqual(state.outputs(.12)[0]['target_torque_nm'], 0.)

    def test_expired_input_refreshed_before_output_cannot_keep_accumulated_response(self):
        state = BridgeState(telemetry_timeout=.12)
        state.receive('stm32', device(0), 0.)
        state.receive('vr', update(0), 0.)
        state.outputs(0.)
        self.assertGreater(state.outputs(.1)[0]['target_torque_nm'], 0.)
        state.receive('stm32', device(1), .2)
        state.receive('vr', update(1), .2)
        self.assertEqual(state.outputs(.2)[0]['target_torque_nm'], 0.)

    def test_role_lock_does_not_reset_growth_and_confirmed_switch_starts_fresh(self):
        state = BridgeState()
        state.receive('stm32', device(0), 0.)
        state.receive('vr', update(0), 0.)
        state.outputs(0.)
        old = state.outputs(.1)[0]['target_torque_nm']
        state.receive('vr', update(1, 'person_03'), .11)
        command, status = state.outputs(.12)
        self.assertGreater(command['target_torque_nm'], old)
        self.assertEqual(status['active_character_id'], 'person_02')
        state.receive('vr', update(2, 'person_03', 'paused'), .13)
        state.receive('vr', update(3, 'person_03'), .14)
        command, status = state.outputs(.14)
        self.assertEqual(status['active_character_id'], 'person_03')
        self.assertEqual(command['target_torque_nm'], 0.)

    def test_passive_bounded_output_and_original_udp_fields(self):
        for character in CHARACTERS:
            state = BridgeState()
            state.receive('vr', update(0, character), 0.)
            for i, speed in enumerate((-.2, .2, -.01, .01, 0., -.2)):
                now = i*.02
                state.receive('stm32', device(i, speed=speed), now)
                command, status = state.outputs(now)
                self.assertTrue(0 <= command['target_torque_nm'] <= status['base_target_torque_nm'] <= .8)
                self.assertLessEqual(command['brake_torque_nm'] * speed, 0.)
                self.assertEqual(set(vr_payload(status)), {'v','type','seq','angle_rad','mode','reason'})


if __name__ == '__main__':
    unittest.main()
