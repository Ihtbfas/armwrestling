"""Operator language and machine-log compatibility, without a real terminal."""
import io
import json
import unittest

from common.console import ConsoleReporter, operator_lines


class ConsoleTests(unittest.TestCase):
    def test_waiting_and_timeout_have_different_explanations_and_actions(self):
        waiting = '\n'.join(operator_lines('PC 上位机', dict(type='status', mode='stop',
            reason='match_waiting', angle_rad=-.4, telemetry_fresh=True, vr_fresh=True)))
        self.assertIn('等待开始', waiting)
        self.assertIn('running', waiting)
        self.assertIn('-22.92', waiting)
        timeout = '\n'.join(operator_lines('PC 上位机', dict(type='status', mode='stop',
            reason='vr_timeout', angle_rad=-.4, telemetry_fresh=True, vr_fresh=False)))
        self.assertIn('Unity', timeout)
        self.assertIn('心跳超时', timeout)
        self.assertNotIn('等待开始', timeout)
        missing = '\n'.join(operator_lines('PC 上位机', dict(type='status', mode='stop',
            reason='vr_missing', vr_fresh=False, vr_age_ms=None)))
        self.assertIn('Unity 心跳：尚未收到', missing)

    def test_null_and_stale_angles_are_not_shown_as_zero_or_live_measurements(self):
        lines = '\n'.join(operator_lines('PC 上位机', dict(type='status',
            reason='telemetry_timeout', mode='stop', angle_rad=None, telemetry_fresh=False)))
        self.assertIn('无有效数据', lines)
        self.assertNotIn('0.00°', lines)
        self.assertIn('停止', lines)

    def test_redirected_auto_output_remains_json_and_can_record_jsonl(self):
        stream, recording = io.StringIO(), io.StringIO()
        reporter = ConsoleReporter('PC 上位机', stream=stream, recording=recording)
        message = dict(event='connected', character_id='对手')
        reporter(message)
        self.assertEqual(json.loads(stream.getvalue()), message)
        self.assertEqual(json.loads(recording.getvalue()), message)
        reporter.close()

    def test_untrusted_text_cannot_inject_terminal_controls(self):
        lines = '\n'.join(operator_lines('PC', dict(type='status', mode='enabled',
            reason='running', character_id='bad\x1b[2J\nname', angle_rad=0, telemetry_fresh=True)))
        self.assertNotIn('\x1b', lines)
        self.assertIn('bad', lines)

    def test_simulator_explains_handshake_and_watchdog_before_generic_authorization(self):
        waiting = '\n'.join(operator_lines('模拟 STM32', dict(type='stm32_serial_snapshot',
            session_ready=False, armed=False, reason='disconnected', angle_rad=0)))
        self.assertIn('等待 PC 握手', waiting)
        watchdog = '\n'.join(operator_lines('模拟 STM32', dict(type='stm32_serial_snapshot',
            session_ready=True, armed=False, reason='command_timeout', angle_rad=-.4)))
        self.assertIn('PC 指令超时', watchdog)


if __name__ == '__main__':
    unittest.main()
