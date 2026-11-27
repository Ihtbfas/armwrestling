"""Byte-level V1.1 checks using the same fixture as the portable C test."""
from pathlib import Path
import unittest

from common.serial_protocol import (
    FLAG_ACK_VALID, FLAG_ANGLE_VALID, FLAG_ARM_READY, FLAG_CMD_TIMEOUT, FLAG_READY,
    FrameParser, MODE_ENABLE, MODE_STOP, TYPE_BRAKE_COMMAND, TYPE_SESSION_ACK,
    TYPE_SESSION_OPEN, command_frame, crc16, decode_frame, decode_payload, encode_frame,
    is_new_sequence, sample_delta_ms, session_ack_frame, session_open_frame, telemetry_frame,
)

SESSION = 0x0102030405060708
VECTORS = Path(__file__).resolve().parents[1] / 'firmware_reference/stm32_v11/golden_vectors.txt'


class SerialProtocolTests(unittest.TestCase):
    def test_standard_crc_check_value(self):
        self.assertEqual(crc16(b'123456789'), 0x29B1)

    def test_golden_vectors_for_firmware_interop(self):
        fixture = {}
        for line in VECTORS.read_text(encoding='ascii').splitlines():
            if line and not line.startswith('#'):
                key, data = line.split(maxsplit=1)
                fixture[key] = bytes.fromhex(data)
        actual = dict(
            OPEN=session_open_frame(0, SESSION), ACK=session_ack_frame(0, SESSION),
            STOP=command_frame(41, SESSION, MODE_STOP),
            ENABLE=command_frame(42, SESSION, MODE_ENABLE, 600),
            TELEMETRY=telemetry_frame(34, -400, -200, 12500, 0x01F1, 42, 550,
                                      mcu_sample_ms=123456, session_id=SESSION),
            UNPAIRED=telemetry_frame(0, -400, 0, 0, 0x0081, mcu_sample_ms=12345, session_id=0),
        )
        self.assertEqual(actual, fixture)
        measured = decode_payload(decode_frame(fixture['TELEMETRY']))
        self.assertEqual((measured.angle_rad, measured.velocity_rad_s, measured.force_newtons),
                         (-.4, -.2, 12.5))
        self.assertEqual((measured.session_id, measured.mcu_sample_ms, measured.last_command_seq,
                          measured.applied_target_mnm), (SESSION, 123456, 42, 550))
        self.assertIsNone(decode_payload(decode_frame(fixture['UNPAIRED'])).force_newtons)

    def test_stop_rules_and_zero_session(self):
        for build in (lambda: session_open_frame(0, 0), lambda: session_ack_frame(0, 0),
                      lambda: command_frame(1, 0, MODE_STOP),
                      lambda: command_frame(1, SESSION, MODE_STOP, 1),
                      lambda: command_frame(1, SESSION, MODE_ENABLE, 2, 19)):
            with self.assertRaises(ValueError):
                build()
        for kind, payload in ((TYPE_SESSION_OPEN, bytes(8)), (TYPE_SESSION_ACK, bytes(8)),
                              (TYPE_BRAKE_COMMAND, bytes(11) + b'\xc8\x00')):
            with self.assertRaises(ValueError):
                decode_payload(decode_frame(encode_frame(kind, 0, payload)))

    def test_stream_fragments_noise_crc_header_and_stalled_assembly(self):
        good = session_open_frame(1, 0x5AA5015AA5)
        stale = session_ack_frame(2, SESSION)[:8]
        legacy = bytes.fromhex('a5 5a 01 03 00 00 08 08 07 06 05 04 03 02 01 c5 0d')
        malformed = bytes.fromhex('a5 5a 02 02 00 00 20 a5 5a 02 77 00 00 08')
        damaged = good[:-1] + bytes([good[-1] ^ 1])
        parser = FrameParser()
        self.assertEqual(parser.feed(stale, now=1.), [])
        self.assertTrue(parser.expire(1.101))
        self.assertEqual(parser.feed(b'noise' + legacy + malformed + damaged + good[:6], now=2.), [])
        result = parser.feed(good[6:] + good, now=2.02)
        self.assertEqual(result, [decode_frame(good), decode_frame(good)])
        self.assertGreaterEqual(parser.header_errors, 3)
        self.assertEqual(parser.crc_errors, 1)
        self.assertEqual(parser.timeouts, 1)
        for broken in (legacy, good[:-1], malformed):
            with self.assertRaises(ValueError):
                decode_frame(broken)
        with self.assertRaises(ValueError):
            encode_frame(TYPE_BRAKE_COMMAND, 0, bytes(64))

    def test_sync_search_counts_only_discarded_bytes_and_keeps_tail(self):
        parser = FrameParser()
        frame = session_open_frame(1, SESSION)
        self.assertEqual(parser.feed(b''), [])
        self.assertEqual(parser.sync_discards, 0)
        self.assertEqual(parser.feed(b'boot text\r\n' + frame[:1], now=1.), [])
        self.assertEqual(parser.sync_discards, len(b'boot text\r\n'))
        self.assertEqual(parser.feed(frame[1:] + frame, now=1.01),
                         [decode_frame(frame)] * 2)
        self.assertEqual(parser.sync_discards, len(b'boot text\r\n'))
        self.assertEqual(parser.feed(frame[:5], now=2.), [])
        self.assertTrue(parser.expire(2.11))
        self.assertEqual(parser.timeouts, 1)
        self.assertEqual(parser.sync_discards, len(b'boot text\r\n'))

    def test_arm_requires_angle_and_known_safe_status(self):
        enabled = FLAG_READY | FLAG_ANGLE_VALID | FLAG_ARM_READY | FLAG_ACK_VALID
        for flags, expected in ((enabled, True), (enabled ^ FLAG_ANGLE_VALID, False),
                                (enabled ^ FLAG_ARM_READY, False), (enabled | FLAG_CMD_TIMEOUT, False),
                                (enabled | 0x8000, False)):
            payload = telemetry_frame(1, 0, 0, 0, flags, mcu_sample_ms=0, session_id=SESSION)
            self.assertIs(decode_payload(decode_frame(payload)).can_enable, expected)

    def test_sequence_and_sample_wrap(self):
        self.assertTrue(is_new_sequence(0, None))
        self.assertTrue(is_new_sequence(0, 65535))
        self.assertFalse(is_new_sequence(7, 7))
        self.assertFalse(is_new_sequence(6, 7))
        self.assertFalse(is_new_sequence(40000, 0))
        self.assertEqual(sample_delta_ms(4, 0xFFFFFFFE), 6)
        self.assertEqual(sample_delta_ms(0xFFFFFFFE, 4), -6)
        self.assertEqual(sample_delta_ms(42, 42), 0)


if __name__ == '__main__':
    unittest.main()
