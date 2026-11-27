"""Transport tests for session ordering, real ACK progress and sample freshness."""
import unittest

from common.serial_protocol import (
    FLAG_ACK_VALID, FLAG_ANGLE_VALID, FLAG_ARM_READY, FLAG_READY, FLAG_VEL_VALID,
    MODE_ENABLE, MODE_STOP, TYPE_SESSION_OPEN, decode_frame, decode_payload, session_ack_frame,
    telemetry_frame,
)
from common.state import BridgeState
from real.stm32_link import Stm32Link

FLAGS = FLAG_READY | FLAG_ANGLE_VALID | FLAG_ARM_READY | FLAG_VEL_VALID
ENABLE = dict(mode='enabled', target_torque_nm=.3, valid_for_ms=200)


class MemorySerial:
    def __init__(self):
        self.incoming = bytearray()
        self.outgoing = bytearray()
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.incoming)

    def read(self, count):
        result = bytes(self.incoming[:count])
        del self.incoming[:count]
        return result

    def write(self, data):
        self.outgoing.extend(data)
        return len(data)

    def close(self):
        self.closed = True

    def sent_frame(self):
        data = bytes(self.outgoing)
        self.outgoing.clear()
        return decode_frame(data)


class SerialLinkTests(unittest.TestCase):
    def setUp(self):
        self.wire = MemorySerial()
        self.link = Stm32Link(self.wire, now=0., session_id=123)

    def rx(self, seq, sample, now, *, session=123, ack=None, flags=FLAGS, angle=-400):
        self.wire.incoming.extend(telemetry_frame(
            seq, angle, -200, 0, flags | (FLAG_ACK_VALID if ack is not None else 0), ack or 0,
            mcu_sample_ms=sample, session_id=session,
        ))
        self.link.poll(now)

    def command(self, now, request=ENABLE):
        self.assertTrue(self.link.send_command(request, now))
        frame = self.wire.sent_frame()
        return frame.sequence, decode_payload(frame)

    def handshake(self, seq=0, sample=10):
        self.link.poll(0.)
        request = self.wire.sent_frame()
        self.assertEqual(request.kind, TYPE_SESSION_OPEN)
        self.wire.incoming.extend(session_ack_frame(0, 123))
        self.rx(seq, sample, .01)
        self.assertTrue(self.link.session_ready)
        first, command = self.command(.011)
        self.assertEqual((command.mode, command.target_torque_mnm), (MODE_STOP, 0))
        return first

    def test_first_stop_ack_then_character_target_and_pause(self):
        stop_seq = self.handshake()
        self.rx(1, 20, .02, ack=stop_seq)
        self.assertTrue(self.link.session_confirmed)
        self.assertTrue(self.link.control_ready(.02))
        state = BridgeState()
        update = dict(v=1, type='vr_update', seq=0, character_id='person_02', match_state='waiting')
        state.receive('vr', update, .02)
        for i in range(2, 72):
            now = .01 * (i+1)
            self.rx(i, round(now*1000), now, ack=stop_seq)
            state.receive('stm32', self.link.control_input(now), now)
            state.receive('vr', dict(update, seq=i, match_state='running'), now)
            output, status = state.outputs(now)
        self.assertAlmostEqual(status['base_target_torque_nm'], .225)
        _, command = self.command(now, output)
        self.assertEqual((command.mode, command.target_torque_mnm),
                         (MODE_ENABLE, round(output['target_torque_nm']*1000)))
        state.receive('vr', dict(update, seq=72, match_state='paused'), now+.01)
        _, command = self.command(now+.02, state.outputs(now+.02)[0])
        self.assertEqual((command.mode, command.target_torque_mnm), (MODE_STOP, 0))

    def test_ack_only_advances_for_complete_recent_writes(self):
        stop_seq = self.handshake()
        self.rx(1, 20, .02, ack=555)
        self.assertFalse(self.link.session_confirmed)
        self.assertEqual(self.link.ack_rejected, 1)
        self.rx(2, 30, .03, ack=stop_seq)
        self.link.tx_seq = 1
        seq, command = self.command(.04)
        self.assertEqual(command.mode, MODE_ENABLE)
        self.rx(3, 40, .15, ack=stop_seq)
        self.assertEqual(self.link.last_ack_at, .03)
        self.assertAlmostEqual(self.link.ack_pending_age(.241), .201)
        self.rx(4, 50, .16, ack=seq)
        self.assertEqual(self.link.last_ack_at, .16)
        self.assertIsNone(self.link.ack_pending_age(.25))

    def test_no_session_telemetry_wins_over_old_sequence_on_same_port(self):
        self.handshake(seq=40000, sample=2000)
        self.rx(40001, 2010, .02, ack=0)
        self.rx(0, 0, .03, session=0, flags=FLAG_ANGLE_VALID | FLAG_READY)
        new = self.link.session_id
        self.assertNotEqual(new, 123)
        self.assertFalse(self.wire.closed)
        self.assertEqual(self.link.interruption, 'session_mismatch')
        self.assertEqual(decode_payload(self.wire.sent_frame()).session_id, new)
        self.assertIsNone(self.link.control_input(.03))
        self.wire.incoming.extend(session_ack_frame(0, new))
        self.rx(1, 5, .04, session=new)
        first, command = self.command(.041)
        self.assertEqual(command.mode, MODE_STOP)
        self.rx(40002, 2020, .05, session=123, ack=1)
        self.assertEqual(self.link.session_id, new)
        self.assertEqual(self.link.session_drops, 1)
        self.rx(2, 15, .06, session=new, ack=first)
        self.assertTrue(self.link.control_ready(.06))

    def test_repeated_backwards_and_invalid_samples_never_refresh_control(self):
        self.handshake(sample=0xFFFFFFF0)
        self.rx(1, 3, .02, ack=0)
        self.assertEqual(self.link.sample_state, 'advancing')
        self.rx(2, 3, .2, ack=0, angle=-500)
        self.assertEqual(self.link.control_input(.2)['angle_rad'], -.4)
        self.assertEqual(self.link.sample_state, 'repeated')
        self.rx(3, 3, .28, ack=0)
        self.assertTrue(self.link.wire_fresh(.28))
        self.assertIsNone(self.link.control_input(.28))
        self.assertEqual(self.link.reason(.28), 'sample_stale')
        self.rx(4, 2, .29, ack=0)
        self.assertEqual(self.link.reason(.29), 'sample_backwards')
        self.rx(5, 4, .30, ack=0, flags=FLAGS ^ FLAG_ANGLE_VALID)
        self.assertEqual(self.link.reason(.30), 'angle_invalid')
        self.rx(6, 4, .31, ack=0)
        self.assertIsNone(self.link.control_input(.31))
        self.rx(7, 5, .32, ack=0, flags=FLAGS ^ FLAG_VEL_VALID)
        self.assertTrue(self.link.control_ready(.32))
        self.assertIsNone(self.link.measured_velocity)
        self.assertEqual(self.link.control_input(.32)['velocity_rad_s'], 0.)

    def test_wire_rejects_outdated_sequence_in_matched_session(self):
        self.handshake(seq=65534)
        self.rx(65535, 15, .02, ack=0)
        self.rx(0, 20, .03, ack=0)
        self.rx(65534, 25, .04)
        self.assertEqual(self.link.sequence_drops, 1)
        self.assertEqual(self.link.last_device_seq, 0)
        self.assertEqual(self.link.last_wire_at, .03)
        self.link.close()
        self.assertTrue(self.wire.closed)


if __name__ == '__main__':
    unittest.main()
