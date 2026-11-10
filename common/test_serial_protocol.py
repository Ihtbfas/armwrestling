"""Byte-level contract tests for the STM32 <-> PC serial protocol V1."""
import unittest

from common.serial_protocol import (
    FLAG_ACK_VALID, FLAG_READY, FLAG_FORCE_VALID, FrameParser, MODE_ENABLE, MODE_STOP,
    command_frame, crc16, decode_frame, decode_payload, encode_frame,
    is_new_sequence, telemetry_frame,
)

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
            command_frame(42, MODE_ENABLE, 600, 200).hex(),
            "a55a01022a0005015802c8003a4a")
        self.assertEqual(
            command_frame(43, MODE_STOP).hex(),
            "a55a01022b0005000000c8002039")

    def test_command_roundtrip_and_stop_rules(self):
        packet = command_frame(42, MODE_ENABLE, 600, 200)
        command = decode_payload(decode_frame(packet))
        self.assertEqual((command.mode, command.target_torque_nm, command.valid_for_ms),
                         (MODE_ENABLE, .6, 200))
        stop = decode_payload(decode_frame(command_frame(43, MODE_STOP)))
        self.assertEqual(stop.target_torque_mnm, 0)
        with self.assertRaises(ValueError):
            command_frame(44, MODE_STOP, 1)
        with self.assertRaises(ValueError):
            command_frame(44, MODE_ENABLE, 2, 1000)

    def test_stream_fragments_noise_and_bad_crc(self):
        first = telemetry_frame(1, -200, 100, 0, FLAG_READY)
        second = command_frame(2, MODE_ENABLE, 250)
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
        message = encode_frame(0x7E, 65535, b'\xA5\x5A\x00\xA5\x5A')
        chunks = [message[:3], message[3:9], message[9:]]
        parser = FrameParser()
        result = []
        for chunk in chunks:
            result.extend(parser.feed(chunk))
        self.assertEqual(result[0].payload, b'\xA5\x5A\x00\xA5\x5A')

    def test_invalid_version_length_and_payload_are_rejected(self):
        packet = command_frame(1, MODE_ENABLE, 30)
        for broken in (packet[:-1], packet[:2] + b'\x02' + packet[3:]):
            with self.assertRaises(ValueError):
                decode_frame(broken)
        with self.assertRaises(ValueError):
            encode_frame(2, 0, b'x' * 33)
        with self.assertRaises(ValueError):
            decode_payload(decode_frame(encode_frame(1, 0, b'abc')))

    def test_sequence_wrap_and_duplicates(self):
        self.assertTrue(is_new_sequence(0, None))
        self.assertTrue(is_new_sequence(0, 65535))
        self.assertTrue(is_new_sequence(7, 6))
        self.assertFalse(is_new_sequence(7, 7))
        self.assertFalse(is_new_sequence(6, 7))
        self.assertFalse(is_new_sequence(40000, 0))

if __name__ == '__main__':
    unittest.main()
