"""Software input tests using the TCP simulator entry point."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from common.protocol import encode
from sim.test_bridge_clients import command


ROOT = Path(__file__).resolve().parents[1]


class UserInputTests(unittest.TestCase):
    def test_timed_sequence_waits_for_enable_and_uses_fixed_steps(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            listener.settimeout(3)
            process = subprocess.Popen(
                [sys.executable, '-m', 'sim.mock_stm32', '--port', str(listener.getsockname()[1]),
                 '--sequence', 'down:0.08,back:0.12', '--duration', '1.2', '--display', 'json'],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                peer, _ = listener.accept()
                with peer:
                    # A STOP must leave the input at rest rather than start the replay.
                    peer.sendall(encode(command(0, mode='stop', target_torque_nm=0, brake_torque_nm=0)))
                    time.sleep(.10)
                    peer.sendall(encode(command(1, target_torque_nm=0, brake_torque_nm=0)))
                    output, error = process.communicate(timeout=3)
                self.assertEqual(process.returncode, 0, error)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.communicate(timeout=3)

        rows = [json.loads(line) for line in output.splitlines()]
        actions = []
        for row in rows:
            if row.get('type') == 'stm32_snapshot':
                action = row['input_action']
                if not actions or actions[-1][0] != action:
                    actions.append((action, row['simulation_tick'], row['command_seq']))
        self.assertEqual([action for action, _, _ in actions], ['release', 'down', 'back', 'release'])
        self.assertEqual(actions[1][2], 1)
        self.assertEqual(actions[2][1] - actions[1][1], 4)
        self.assertEqual(actions[3][1] - actions[2][1], 6)

    def test_file_commands_move_release_and_hold_software_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'effort.txt'
            log = Path(temp) / 'sim.jsonl'
            path.write_text('reset\n', encoding='utf-8-sig')
            with log.open('w') as stream:
                process = subprocess.Popen(
                    [sys.executable, '-m', 'sim.mock_stm32', '--input-file', str(path),
                     '--torque', '0.35', '--port', '0', '--duration', '10', '--display', 'json'],
                    cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
                try:
                    def wait_for(predicate, after=-1):
                        deadline = time.monotonic() + 3
                        while time.monotonic() < deadline and process.poll() is None:
                            for line in log.read_text(encoding='utf-8').splitlines(keepends=True):
                                if not line.startswith('{') or not line.endswith('\n'):
                                    continue
                                row = json.loads(line)
                                if (row.get('type') == 'stm32_snapshot'
                                        and row['elapsed_s'] > after and predicate(row)):
                                    return row['elapsed_s']
                            time.sleep(.02)
                        self.fail('expected simulator state did not appear:\n' + log.read_text())

                    tick = wait_for(lambda row: row['angle_rad'] == row['velocity_rad_s'] == 0)
                    path.write_text('down', encoding='utf-8')
                    tick = wait_for(lambda row: row['angle_rad'] < -.1
                                    and row['user_torque_nm'] == -.35, tick)
                    path.write_text('back', encoding='utf-8')
                    tick = wait_for(lambda row: row['velocity_rad_s'] > .1
                                    and row['user_torque_nm'] == .35, tick)
                    path.write_text('release', encoding='utf-8')
                    tick = wait_for(lambda row: row['user_torque_nm'] == 0
                                    and row['angle_rad'] != 0, tick)
                    path.write_text('reset', encoding='utf-8')
                    wait_for(lambda row: row['angle_rad'] == row['velocity_rad_s'] == 0, tick)
                finally:
                    if process.poll() is None:
                        process.terminate()
                    process.wait(timeout=3)


if __name__ == '__main__':
    unittest.main()
