"""Protocol and freshness contracts; runnable without MuJoCo."""
import socket
import contextlib
import io
import json
import unittest

from common.control import BrakeParameters, brake_state, passive_torque
from common.protocol import JsonSocket, LineBuffer, encode, validate_incoming
from common.state import BridgeState


def telemetry(seq=0, angle=-0.4, speed=-0.2, status='ready'):
    return dict(v=1, type='telemetry', seq=seq, angle_rad=angle,
                velocity_rad_s=speed, device_status=status)


def vr(seq=0, match='running'):
    return dict(v=1, type='vr_update', seq=seq, character_id='demo',
                match_state=match, stiffness_nm_per_rad=2.0, max_torque_nm=0.8)


class SharedControlTests(unittest.TestCase):
    def test_old_import_is_the_shared_implementation(self):
        from sim.control import brake_state as old_function
        self.assertIs(old_function, brake_state)
        state = brake_state(-0.4, -0.2, BrakeParameters())
        self.assertAlmostEqual(state.target_torque, 0.3)
        self.assertAlmostEqual(state.applied_torque, 0.3)
        self.assertAlmostEqual(passive_torque(0.3, 0.01, 0.05), -0.06)


class ProtocolTests(unittest.TestCase):
    def test_invalid_unicode_identifier_cannot_crash_pc_output(self):
        payload = b'{"v":1,"type":"vr_update","seq":0,"character_id":"\\ud800","match_state":"running","stiffness_nm_per_rad":2,"max_torque_nm":0.8}\n'
        message = LineBuffer().feed(payload)[0]
        with self.assertRaises(ValueError):
            validate_incoming(message, 'vr')

    def test_console_log_supports_non_ascii_names_on_legacy_encoding(self):
        from common.bridge import log
        buffer = io.BytesIO()
        with io.TextIOWrapper(buffer, encoding='ascii') as console:
            with contextlib.redirect_stdout(console):
                log({'character_id':'对手'})
            console.flush()
            self.assertEqual(json.loads(buffer.getvalue()), {'character_id':'对手'})

    def test_pending_output_skips_old_snapshots_instead_of_growing_a_queue(self):
        sender, receiver = socket.socketpair()
        with sender, receiver:
            peer = JsonSocket(sender)
            self.assertTrue(peer.queue({'seq':0}))
            self.assertFalse(peer.queue({'seq':1}))
            peer.flush()
            self.assertEqual(LineBuffer().feed(receiver.recv(8192)), [{'seq':0}])
            self.assertTrue(peer.queue({'seq':2}))
            peer.flush()
            self.assertEqual(LineBuffer().feed(receiver.recv(8192)), [{'seq':2}])

    def test_partial_and_combined_lines_are_reassembled(self):
        parser = LineBuffer()
        payload = encode(telemetry())
        self.assertEqual(parser.feed(payload[:9]), [])
        self.assertEqual(parser.feed(payload[9:]+encode(vr())), [telemetry(), vr()])

    def test_bad_json_and_oversized_unfinished_line_are_rejected(self):
        for payload in (b'{oops}\n', b'[]\n', b'{"x":NaN}\n', b'\xff\n', b'x'*8193):
            with self.subTest(payload=payload[:25]), self.assertRaises(ValueError):
                LineBuffer().feed(payload)

    def test_wrong_role_missing_fields_and_nonfinite_values_are_rejected(self):
        cases = [vr(), dict(telemetry(), angle_rad=float('nan')),
                 dict(telemetry(), velocity_rad_s=float('inf')),
                 dict(telemetry(), angle_rad=True), dict(telemetry(), seq=True),
                 dict(telemetry(), v=True), dict(telemetry(), angle_rad=10**1000),
                 dict(telemetry(), device_status='unknown'),
                 dict(telemetry(), velocity_rad_s=1001), {'v':1, 'type':'telemetry', 'seq':0}]
        for message in cases:
            with self.subTest(message=message), self.assertRaises(ValueError):
                validate_incoming(message, 'stm32')
        for field, value in [('stiffness_nm_per_rad', -1), ('max_torque_nm', 3),
                             ('match_state', 'invalid'), ('character_id', '')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_incoming(dict(vr(), **{field:value}), 'vr')


class StateTests(unittest.TestCase):
    def ready(self):
        state = BridgeState()
        state.receive('stm32', telemetry(), 10.0)
        state.receive('vr', vr(), 10.0)
        return state

    def test_real_formula_and_vr_state_reach_both_outputs(self):
        command, status = self.ready().outputs(10.02)
        self.assertEqual(command['mode'], 'enabled')
        self.assertAlmostEqual(command['target_torque_nm'], 0.3)
        self.assertAlmostEqual(command['brake_torque_nm'], 0.3)
        self.assertEqual(status['angle_rad'], -0.4)
        self.assertEqual(status['character_id'], 'demo')
        self.assertEqual(status['match_state'], 'running')

    def test_missing_stale_fault_and_paused_inputs_stop_output(self):
        cases = [(BridgeState(), 10.0, 'telemetry_missing')]
        cases.append((self.ready(), 10.3, 'telemetry_timeout'))
        stale_vr = self.ready()
        stale_vr.receive('stm32', telemetry(seq=1), 11.1)
        cases.append((stale_vr, 11.1, 'vr_timeout'))
        fault = self.ready()
        fault.receive('stm32', telemetry(seq=1, status='fault'), 10.01)
        cases.append((fault, 10.02, 'device_fault'))
        paused = self.ready()
        paused.receive('vr', vr(seq=1, match='paused'), 10.01)
        cases.append((paused, 10.02, 'match_paused'))
        for state, now, reason in cases:
            with self.subTest(reason=reason):
                command, status = state.outputs(now)
                self.assertEqual(command['mode'], 'stop')
                self.assertEqual(command['reason'], reason)
                self.assertEqual(command['target_torque_nm'], 0)
                self.assertEqual(command['brake_torque_nm'], 0)
                if reason == 'telemetry_timeout':
                    self.assertIsNone(status['angle_rad'])

    def test_duplicate_or_bad_messages_do_not_refresh_freshness(self):
        state = self.ready()
        for message in (telemetry(), dict(telemetry(seq=1), angle_rad='bad')):
            with self.assertRaises(ValueError):
                state.receive('stm32', message, 10.24)
        self.assertEqual(state.outputs(10.26)[0]['reason'], 'telemetry_timeout')

    def test_reconnect_clears_old_state_and_accepts_new_sequence(self):
        state = self.ready()
        state.clear('stm32')
        self.assertEqual(state.outputs(10.02)[0]['reason'], 'telemetry_missing')
        state.receive('stm32', telemetry(), 10.03)
        self.assertEqual(state.outputs(10.04)[0]['mode'], 'enabled')

    def test_free_space_return_and_character_parameter_update(self):
        state = self.ready()
        state.receive('stm32', telemetry(seq=1, angle=-0.2), 10.01)
        self.assertEqual(state.outputs(10.02)[0]['target_torque_nm'], 0)
        state.receive('vr', dict(vr(seq=1), stiffness_nm_per_rad=1.0), 10.03)
        state.receive('stm32', telemetry(seq=2, angle=-0.4, speed=0.2), 10.03)
        command, _ = state.outputs(10.04)
        self.assertAlmostEqual(command['target_torque_nm'], 0.15)
        self.assertAlmostEqual(command['brake_torque_nm'], -0.15)


if __name__ == '__main__':
    unittest.main()
