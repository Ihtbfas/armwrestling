"""Byte-level contract tests for the STM32 <-> PC serial protocol V1."""
import unittest

from common.serial_protocol import (
    FLAG_ACK_VALID, FLAG_READY, FLAG_FORCE_VALID, FLAG_ARM_READY, FrameParser, MODE_ENABLE, MODE_STOP,
    TYPE_BRAKE_COMMAND, TYPE_SESSION_OPEN, TYPE_SESSION_ACK,
    command_frame, crc16, decode_frame, decode_payload, encode_frame,
    is_new_sequence, session_open_frame, session_ack_frame, telemetry_frame,
)

SESSION_ID = 0x0102030405060708


class SerialProtocolTests(unittest.TestCase):
    def test_standard_crc_check_value(self):
        self.assertEqual(crc16(b'123456789'), 0x29B1)

    def test_telemetry_roundtrip_and_units(self):
        packet = telemetry_frame(34, -400, -200, 12500, FLAG_READY | FLAG_ACK_VALID | FLAG_FORCE_VALID,
                                 last_command_seq=33, applied_target_mnm=300)
        frame = decode_frame(packet)
        telemetry = decode_payload(frame)
        self.assertEqual((frame.kind, frame.sequence, len(packet)), (1, 34, 27))
        self.assertEqual(telemetry.force_newtons, 12.5)
        self.assertEqual(telemetry.angle_rad, -0.4)
        self.assertEqual(telemetry.velocity_rad_s, -0.2)
        self.assertEqual(telemetry.device_status, 'ready')
        self.assertEqual(telemetry.last_command_seq, 33)
        self.assertEqual(telemetry.applied_target_mnm, 300)

    def test_golden_vectors_for_firmware_interop(self):
        self.assertEqual(
            telemetry_frame(34, -400, -200, 12500, FLAG_READY | FLAG_ACK_VALID | FLAG_FORCE_VALID, 33, 300).hex(),
            "a55a010122001270feffff38ffffffd4300000310021002c01f03f")
        self.assertEqual(
            command_frame(42, SESSION_ID, MODE_ENABLE, 600, 200).hex(),
            "a55a01022a000d0807060504030201015802c8007104")
        self.assertEqual(
            command_frame(43, SESSION_ID, MODE_STOP).hex(),
            "a55a01022b000d0807060504030201000000c8009c98")

    def test_command_roundtrip_and_stop_rules(self):
        packet = command_frame(42, SESSION_ID, MODE_ENABLE, 600, 200)
        command = decode_payload(decode_frame(packet))
        self.assertEqual((command.mode, command.target_torque_nm, command.valid_for_ms),
                         (MODE_ENABLE, .6, 200))
        stop = decode_payload(decode_frame(command_frame(43, SESSION_ID, MODE_STOP)))
        self.assertEqual(stop.target_torque_mnm, 0)
        with self.assertRaises(ValueError):
            command_frame(44, SESSION_ID, MODE_STOP, 1)
        with self.assertRaises(ValueError):
            command_frame(44, SESSION_ID, MODE_ENABLE, 2, 1000)

    def test_stream_fragments_noise_and_bad_crc(self):
        first = telemetry_frame(1, -200, 100, 0, FLAG_READY)
        second = command_frame(2, SESSION_ID, MODE_ENABLE, 250)
        damaged = bytearray(first)
        damaged[-1] ^= 1
        parser = FrameParser()
        self.assertEqual(parser.feed(b'noise\xA5'), [])
        self.assertEqual(parser.feed(b'\x5A\xff'), [])
        self.assertEqual(parser.feed(bytes(damaged) + first[:4]), [])
        frames = parser.feed(first[4:] + second + b'bad')
        self.assertEqual([(frame.kind, frame.sequence) for frame in frames],
                         [(1, 1), (2, 2)])

    def test_payload_can_include_sync_bytes(self):
        payload = b'\xA5\x5A\x00\xA5\x5A' + bytes(8)
        message = encode_frame(TYPE_BRAKE_COMMAND, 65535, payload)
        chunks = [message[:3], message[3:9], message[9:]]
        parser = FrameParser()
        result = []
        for chunk in chunks:
            result.extend(parser.feed(chunk))
        self.assertEqual(result[0].payload, payload)

    def test_invalid_version_length_and_payload_are_rejected(self):
        packet = command_frame(1, SESSION_ID, MODE_ENABLE, 30)
        for broken in (packet[:-1], packet[:2] + b'\x02' + packet[3:]):
            with self.assertRaises(ValueError):
                decode_frame(broken)
        with self.assertRaises(ValueError):
            encode_frame(2, 0, b'x' * 33)
        with self.assertRaises(ValueError):
            decode_payload(decode_frame(encode_frame(1, 0, b'abc')))

    def test_session_messages_and_command_scoping(self):
        hello = session_open_frame(0, SESSION_ID)
        ack = session_ack_frame(0, SESSION_ID)
        self.assertEqual(hello.hex(), 'a55a01030000080807060504030201c50d')
        self.assertEqual(ack.hex(), 'a55a010400000808070605040302018e05')
        self.assertEqual(decode_frame(hello).kind, TYPE_SESSION_OPEN)
        self.assertEqual(decode_frame(ack).kind, TYPE_SESSION_ACK)
        self.assertEqual(decode_payload(decode_frame(ack)).session_id, SESSION_ID)
        command = decode_payload(decode_frame(command_frame(42, SESSION_ID, MODE_ENABLE, 600)))
        self.assertEqual(command.session_id, SESSION_ID)
        self.assertNotEqual(command.session_id, 99)
        # A new session must not accept the previous session's command.
        self.assertNotEqual(command.session_id, decode_payload(
            decode_frame(session_open_frame(0, 99))).session_id)

    def test_bad_header_does_not_block_following_complete_stop(self):
        bad = bytes.fromhex('a5 5a 01 02 00 00 20')
        stop = command_frame(43, SESSION_ID, MODE_STOP)
        parser = FrameParser()
        self.assertEqual([frame.sequence for frame in parser.feed(bad + stop)], [43])
        self.assertEqual([frame.sequence for frame in parser.feed(bad)], [])
        self.assertEqual([frame.sequence for frame in parser.feed(stop)], [43])

    def test_unknown_type_and_valid_wrong_length_recover_immediately(self):
        unknown = bytes.fromhex('a5 5a 01 77 00 00 08')
        wrong_size = bytes.fromhex('a5 5a 01 02 00 00 05')
        good = command_frame(55, SESSION_ID, MODE_STOP)
        parser = FrameParser()
        self.assertEqual([frame.sequence for frame in parser.feed(unknown + wrong_size + good)], [55])

    def test_partial_valid_frame_assembly_timeout(self):
        parser = FrameParser(assembly_timeout_s=0.1)
        fragment = command_frame(44, SESSION_ID, MODE_ENABLE, 300)[:8]
        self.assertEqual(parser.feed(fragment, now=10.0), [])
        self.assertFalse(parser.expire(now=10.09))
        self.assertTrue(parser.expire(now=10.11))
        stop = command_frame(45, SESSION_ID, MODE_STOP)
        self.assertEqual([frame.sequence for frame in parser.feed(stop, now=10.12)], [45])
        self.assertEqual(parser.feed(fragment, now=20.0), [])
        self.assertEqual([frame.sequence for frame in parser.feed(stop, now=20.15)], [45])

    def test_telemetry_can_enable_requires_explicit_arm_ready(self):
        ordinary = decode_payload(decode_frame(telemetry_frame(
            1, 0, 0, 0, FLAG_READY)))
        self.assertFalse(ordinary.can_enable)
        armed = decode_payload(decode_frame(telemetry_frame(
            2, 0, 0, 0, FLAG_READY | FLAG_ARM_READY)))
        self.assertTrue(armed.can_enable)

    def test_sequence_wrap_and_duplicates(self):
        self.assertTrue(is_new_sequence(0, None))
        self.assertTrue(is_new_sequence(0, 65535))
        self.assertTrue(is_new_sequence(7, 6))
        self.assertFalse(is_new_sequence(7, 7))
        self.assertFalse(is_new_sequence(6, 7))
        self.assertFalse(is_new_sequence(40000, 0))

if __name__ == '__main__':
    unittest.main()
