"""Three genuine TCP subprocesses plus malformed-client recovery; no MuJoCo."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from common.protocol import LineBuffer, encode

ROOT = Path(__file__).resolve().parents[1]


class ProcessTests(unittest.TestCase):
    def test_silent_inputs_timeout_without_blocking_the_control_loop(self):
        pc = subprocess.Popen([sys.executable, '-u', '-m', 'common.bridge', '--stm-port', '0',
                               '--vr-port', '0', '--telemetry-timeout', '.12', '--vr-timeout', '.20'],
                              cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ready = json.loads(pc.stdout.readline())
            with socket.create_connection(('127.0.0.1', ready['stm_port']), timeout=1) as stm, \
                 socket.create_connection(('127.0.0.1', ready['vr_port']), timeout=1) as vr_sock:
                parser = LineBuffer()
                def until(reason):
                    deadline = time.monotonic()+1
                    while time.monotonic()<deadline:
                        for message in parser.feed(stm.recv(4096)):
                            if message['reason']==reason:
                                return message
                    self.fail('did not receive '+reason)
                message = dict(v=1, type='vr_update', seq=0, character_id='demo', match_state='running',
                               stiffness_nm_per_rad=2., max_torque_nm=.8)
                payload = encode(message)
                vr_sock.sendall(payload[:8])
                vr_sock.sendall(payload[8:])
                device = dict(v=1, type='telemetry', seq=0, angle_rad=-.4,
                              velocity_rad_s=-.2, device_status='ready')
                stm.sendall(encode(device))
                self.assertAlmostEqual(until('running')['target_torque_nm'], .3)
                message.update(seq=1, match_state='paused')
                vr_sock.sendall(encode(message))
                self.assertEqual(until('match_paused')['mode'], 'stop')
                message.update(seq=2, match_state='running')
                vr_sock.sendall(encode(message))
                # Keep telemetry fresh while VR is connected but silent.
                for seq in range(1, 16):
                    device['seq'] = seq
                    stm.sendall(encode(device))
                    time.sleep(.02)
                self.assertEqual(until('vr_timeout')['target_torque_nm'], 0)
                message['seq'] = 3
                vr_sock.sendall(encode(message))
                self.assertEqual(until('telemetry_timeout')['mode'], 'stop')
        finally:
            pc.terminate()
            pc.communicate(timeout=3)

    def test_three_process_roundtrip_and_command_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            processes, handles = [], []
            def start(name, module, *args):
                path = Path(directory)/name
                handle = path.open('w')
                handles.append(handle)
                child = subprocess.Popen([sys.executable, '-u', '-m', module, *args],
                                         cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT)
                processes.append(child)
                return child, path
            try:
                pc, pc_path = start('pc.log', 'common.bridge', '--stm-port', '0', '--vr-port', '0',
                                    '--duration', '3.4')
                deadline = time.monotonic()+5
                while not pc_path.read_text().strip() and time.monotonic()<deadline and pc.poll() is None:
                    time.sleep(.02)
                self.assertTrue(pc_path.read_text().strip(), 'bridge did not announce ports')
                ready = json.loads(pc_path.read_text().splitlines()[0])
                # Bad framing must only drop this peer, leaving listeners alive.
                with socket.create_connection(('127.0.0.1', ready['vr_port']), timeout=1) as bad:
                    bad.sendall(b'{bad json}\n')
                    self.assertEqual(bad.recv(1), b'')
                stm, stm_path = start('stm.log', 'sim.mock_stm32', '--port', str(ready['stm_port']),
                                      '--duration', '4.4')
                vr, vr_path = start('vr.log', 'sim.mock_vr', '--port', str(ready['vr_port']),
                                    '--duration', '2.0')
                for child in (pc, stm, vr):
                    self.assertEqual(child.wait(timeout=8), 0)
                records = lambda path: [json.loads(line) for line in path.read_text().splitlines()]
                pc_rows, stm_rows, vr_rows = records(pc_path), records(stm_path), records(vr_path)
                self.assertTrue(any(r.get('event')=='rejected' for r in pc_rows))
                self.assertTrue(any(r.get('reason')=='running' and r.get('target_torque_nm', 0)>0
                                    for r in pc_rows))
                self.assertTrue(any(r.get('reason')=='vr_missing' for r in pc_rows))
                self.assertTrue(any(r.get('type')=='status' and r.get('angle_rad') is not None
                                    and r.get('character_id')=='demo' for r in vr_rows))
                snapshots = [r for r in stm_rows if r.get('type')=='stm32_snapshot']
                self.assertTrue(any(abs(r['applied_torque_nm'])>0 for r in snapshots))
                self.assertTrue(all(r['maximum_brake_power_w']<=0 for r in snapshots))
                self.assertTrue(any(r['elapsed_s']>3.6 and r['applied_torque_nm']==0 for r in snapshots))
            finally:
                for child in processes:
                    if child.poll() is None:
                        child.terminate()
                    child.wait(timeout=3)
                for handle in handles:
                    handle.close()


if __name__ == '__main__':
    unittest.main()
