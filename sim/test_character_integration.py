"""Real Windows-compatible PC subprocess + UDP VR and fixed TCP telemetry."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from common.protocol import LineBuffer, decode_datagram, encode, encode_datagram

ROOT = Path(__file__).resolve().parents[1]


class CharacterProcessTests(unittest.TestCase):
    def test_role_selection_freeze_fallback_and_wire_compatibility(self):
        output_log = tempfile.TemporaryFile(mode='w+t', encoding='utf-8')
        pc = subprocess.Popen([sys.executable, '-u', '-m', 'common.bridge',
            '--stm-port', '0', '--vr-port', '0', '--duration', '40'], cwd=ROOT,
            stdout=output_log, stderr=subprocess.PIPE, text=True)
        remaining = ''
        try:
            deadline = time.monotonic()+5
            first_line = ''
            while not first_line.endswith('\n') and time.monotonic() < deadline:
                output_log.seek(0)
                first_line = output_log.readline()
                if not first_line.endswith('\n'):
                    time.sleep(.02)
            ready = json.loads(first_line)
            with socket.create_connection(('127.0.0.1', ready['stm_port']), timeout=1) as stm, \
                 socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as vr:
                stm.settimeout(.025)
                vr.connect(('127.0.0.1', ready['vr_port']))
                vr.setblocking(False)
                lines = LineBuffer()
                telemetry_seq = update_seq = 0
                last_status_seq = -1

                def phase(character, match, target, reason, device_status='ready', hold=.09):
                    nonlocal telemetry_seq, update_seq, last_status_seq
                    start = next_update = time.monotonic()
                    saw_command = saw_status = False
                    while time.monotonic() - start < 8.:
                        now = time.monotonic()
                        stm.sendall(encode(dict(v=1, type='telemetry', seq=telemetry_seq,
                            angle_rad=-.4, velocity_rad_s=-.2, device_status=device_status)))
                        telemetry_seq += 1
                        if now >= next_update:
                            update = dict(v=1, type='vr_update', seq=update_seq,
                                          character_id=character, match_state=match)
                            self.assertEqual(set(update), {'v', 'type', 'seq', 'character_id', 'match_state'})
                            vr.send(encode_datagram(update))
                            update_seq += 1
                            next_update = now + .2
                        try:
                            for command in lines.feed(stm.recv(4096)):
                                # Static expected values are retained; allow .001 N*m
                                # settling error for finite-time V0.2 exponential rise.
                                if command['reason'] == reason and abs(command['target_torque_nm'] - target) < .001:
                                    self.assertAlmostEqual(command['brake_torque_nm'], command['target_torque_nm'])
                                    self.assertEqual(command['mode'], 'enabled' if reason == 'running' else 'stop')
                                    saw_command = True
                        except socket.timeout:
                            pass
                        while True:
                            try:
                                status = decode_datagram(vr.recv(8193))
                            except BlockingIOError:
                                break
                            self.assertEqual(set(status), {'v', 'type', 'seq', 'angle_rad', 'mode', 'reason'})
                            self.assertGreater(status['seq'], last_status_seq)
                            last_status_seq = status['seq']
                            if status['reason'] == reason:
                                self.assertEqual(status['angle_rad'], -.4)
                                saw_status = True
                        if saw_command and saw_status and time.monotonic() - start >= hold:
                            return
                    self.fail(f'role phase failed: {character}/{match}/{reason}/{target}')

                for character, target in (('demo', .3), ('person_01', .15), ('person_02', .225), ('person_03', .27)):
                    phase(character, 'waiting', 0., 'match_waiting')
                    phase(character, 'running', target, 'running')
                phase('person_01', 'paused', 0., 'match_paused')
                phase('person_01', 'running', .15, 'running')
                phase('person_03', 'running', .15, 'running') # Locked to person_01.
                phase('person_03', 'running', 0., 'device_fault', device_status='fault')
                phase('person_03', 'running', .15, 'running') # Recovery is not role confirmation.
                phase('person_03', 'finished', 0., 'match_finished')
                phase('person_03', 'running', .27, 'running')
                phase('unknown_person', 'waiting', 0., 'match_waiting', hold=.65)
                phase('unknown_person', 'running', .3, 'running')
        finally:
            pc.terminate()
            _, errors = pc.communicate(timeout=3)
            output_log.seek(0)
            remaining = output_log.read()
            output_log.close()
        self.assertEqual(errors, '')
        logs = [json.loads(line) for line in remaining.splitlines()]
        self.assertTrue(any(row.get('character_fallback') and row.get('active_character_id') == 'demo'
                            and row.get('character_id') == 'unknown_person' for row in logs))


if __name__ == '__main__':
    unittest.main()
