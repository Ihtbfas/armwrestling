"""Exercise VR UDP framing, stale/duplicate packets and a real PC UDP socket."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest

from common.protocol import decode_datagram, encode, encode_datagram

ROOT = Path(__file__).resolve().parents[1]


class UdpTests(unittest.TestCase):
    def test_one_datagram_is_one_json_message(self):
        packet = encode_datagram({'v': 1, 'type': 'vr_update', 'character_id': '对手'})
        self.assertFalse(packet.endswith(b'\n'))
        self.assertEqual(decode_datagram(packet)['character_id'], '对手')
        for bad in (b'', b'{oops}', b'[]', b'{"x":NaN}', b'{}{}', b'x' * 8193):
            with self.subTest(bad=bad[:20]), self.assertRaises(ValueError):
                decode_datagram(bad)
        self.assertTrue(encode({'x': 1}).endswith(b'\n'))  # TCP framing unchanged.

    def test_three_way_udp_roundtrip_timeout_and_new_sender(self):
        pc = subprocess.Popen([sys.executable, '-u', '-m', 'common.bridge',
                               '--stm-port', '0', '--vr-port', '0',
                               '--telemetry-timeout', '.30', '--vr-timeout', '.35',
                               '--duration', '3.4'], cwd=ROOT,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ready = json.loads(pc.stdout.readline())
            self.assertEqual((ready['stm_transport'], ready['vr_transport']), ('tcp', 'udp'))
            vr_address = ('127.0.0.1', ready['vr_port'])
            with socket.create_connection(('127.0.0.1', ready['stm_port']), timeout=1) as stm, \
                 socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as vr, \
                 socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as other:
                stm.settimeout(.2)
                vr.settimeout(.3)
                vr.connect(vr_address)
                other.connect(vr_address)
                seq = 0
                def telemetry():
                    nonlocal seq
                    stm.sendall(encode(dict(v=1, type='telemetry', seq=seq,
                                           angle_rad=-.4, velocity_rad_s=-.2,
                                           device_status='ready')))
                    seq += 1
                def update(vr_sock, no, state='running'):
                    vr_sock.send(encode_datagram(dict(v=1, type='vr_update', seq=no,
                                          character_id='demo', match_state=state,
                                          stiffness_nm_per_rad=2., max_torque_nm=.8)))
                def wait_status(predicate, period=.8):
                    end = time.monotonic()+period
                    while time.monotonic() < end:
                        telemetry()
                        try:
                            packet = vr.recv(8193)
                        except socket.timeout:
                            continue
                        message = decode_datagram(packet)
                        if predicate(message):
                            return message
                    self.fail('expected UDP status missing')
                vr.send(b'{bad json}')
                telemetry()
                update(vr, 0)
                found = wait_status(lambda m: m.get('mode') == 'enabled')
                self.assertAlmostEqual(found['target_torque_nm'], .3)
                # Stale/reordered packet does not override the newest pause state.
                update(vr, 1, 'paused')
                found = wait_status(lambda m: m['reason'] == 'match_paused')
                self.assertEqual(found['target_torque_nm'], 0)
                update(vr, 0, 'running')
                update(other, 10, 'running')
                found = wait_status(lambda m: m['reason'] == 'match_paused')
                self.assertEqual(found['match_state'], 'paused')
                # Wait for first sender to expire, then allow another sender with seq=0.
                until = time.monotonic()+.5
                while time.monotonic()<until:
                    telemetry()
                    time.sleep(.035)
                update(other, 0, 'running')
                other.settimeout(.5)
                resumed = False
                end = time.monotonic()+.75
                while time.monotonic()<end:
                    telemetry()
                    try:
                        status = decode_datagram(other.recv(8193))
                    except socket.timeout:
                        continue
                    if status.get('reason')=='running':
                        resumed = True
                        break
                self.assertTrue(resumed)
        finally:
            pc.terminate()
            pc.communicate(timeout=3)


if __name__ == '__main__':
    unittest.main()
