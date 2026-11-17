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
        log_path = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'pc.jsonl'
        output_log = self.enterContext(log_path.open('w', encoding='utf-8'))

        def records():
            # Independent reader: never seek the child's shared stdout handle.
            return [json.loads(line) for line in log_path.read_text(encoding='utf-8').splitlines(keepends=True)
                    if line.endswith('\n')]
        pc = subprocess.Popen([sys.executable, '-u', '-m', 'common.bridge',
            '--stm-port', '0', '--vr-port', '0', '--duration', '40'], cwd=ROOT,
            stdout=output_log, stderr=subprocess.PIPE, text=True)
        remaining = ''
        try:
            deadline = time.monotonic()+5
            startup = records()
            while not startup and time.monotonic() < deadline:
                time.sleep(.02)
                startup = records()
            self.assertTrue(startup, 'PC did not report its bound ports')
            ready = startup[0]
            with socket.create_connection(('127.0.0.1', ready['stm_port']), timeout=1) as stm, \
                 socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as vr:
                stm.settimeout(.025)
                vr.connect(('127.0.0.1', ready['vr_port']))
                vr.setblocking(False)
                lines = LineBuffer()
                telemetry_seq = update_seq = 0
                last_status_seq = -1

                def phase(character, match, target, reason, device_status='ready', hold=.09, active=None):
                    nonlocal telemetry_seq, update_seq, last_status_seq
                    start = next_update = time.monotonic()
                    saw_command = saw_status = saw_local = False
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
                                # V0.3 output need not reach the static target;
                                # fatigue limits it. The base is checked locally.
                                magnitude = command['target_torque_nm']
                                bounded = 0 < magnitude <= target+1e-12 if reason == 'running' else magnitude == 0.
                                if command['reason'] == reason and bounded:
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
                        statuses = [row for row in records() if row.get('type') == 'status']
                        if statuses:
                            local = statuses[-1]
                            if local['reason'] == reason and local['active_character_id'] == (active or character):
                                self.assertAlmostEqual(local['base_target_torque_nm'], target)
                                self.assertTrue(0 <= local['target_torque_nm'] <= local['eligible_target_torque_nm'] <= target+1e-12)
                                saw_local = True
                        if saw_command and saw_status and saw_local and time.monotonic() - start >= hold:
                            return
                    self.fail(f'role phase failed: {character}/{match}/{reason}/{target}')

                for character, target in (('demo', .3), ('person_01', .15), ('person_02', .225), ('person_03', .27)):
                    phase(character, 'waiting', 0., 'match_waiting')
                    phase(character, 'running', target, 'running')
                phase('person_01', 'paused', 0., 'match_paused')
                phase('person_01', 'running', .15, 'running')
                phase('person_03', 'running', .15, 'running', active='person_01') # Locked.
                phase('person_03', 'running', 0., 'device_fault', device_status='fault', active='person_01')
                phase('person_03', 'running', .15, 'running', active='person_01') # Recovery is not confirmation.
                phase('person_03', 'finished', 0., 'match_finished')
                phase('person_03', 'running', .27, 'running')
                phase('unknown_person', 'waiting', 0., 'match_waiting', hold=.65, active='demo')
                phase('unknown_person', 'running', .3, 'running', active='demo')
        finally:
            pc.terminate()
            _, errors = pc.communicate(timeout=3)
            remaining = log_path.read_text(encoding='utf-8')
        self.assertEqual(errors, '')
        logs = [json.loads(line) for line in remaining.splitlines()]
        self.assertTrue(any(row.get('character_fallback') and row.get('active_character_id') == 'demo'
                            and row.get('character_id') == 'unknown_person' for row in logs))


if __name__ == '__main__':
    unittest.main()
