"""Opt-in real Unity Editor Play integration; no Python VR client is started.

python tests/unity/run_windows_integration.py --unity <Unity.exe> --project <UnityUdpDemo>
The Unity test project must contain copies of the scripts from tests/unity/ and tests/unity/Editor/.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unity', required=True, type=Path)
    parser.add_argument('--project', required=True, type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    results = args.project.resolve() / 'TestResults'
    results.mkdir(exist_ok=True)
    samples_file, command_file = results / 'samples.jsonl', results / 'command.json'
    for path in (samples_file, command_file):
        path.unlink(missing_ok=True)
    processes, handles, samples, checks = {}, [], [], []
    command_id = 0
    sample_offset = 0

    def start(name, module):
        handle = (results / (name + '.jsonl')).open('w', encoding='utf-8')
        handles.append(handle)
        processes[name] = subprocess.Popen([sys.executable, '-u', '-m', module],
                                           cwd=repo, stdout=handle, stderr=subprocess.STDOUT)

    def stop(name):
        proc = processes.pop(name, None)
        if proc and proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)

    def read_samples():
        nonlocal sample_offset
        if samples_file.exists():
            with samples_file.open(encoding='utf-8') as stream:
                stream.seek(sample_offset)
                while True:
                    position = stream.tell()
                    line = stream.readline()
                    if not line or not line.endswith('\n'):
                        stream.seek(position)
                        break
                    samples.append(json.loads(line))
                sample_offset = stream.tell()

    def wait_for(predicate, label, timeout=15):
        begin, index = time.monotonic(), len(samples)
        while time.monotonic() - begin < timeout:
            read_samples()
            for sample in samples[index:]:
                if predicate(sample):
                    checks.append(dict(check=label, result='PASS', sample=sample))
                    print('PASS:', label, flush=True)
                    return sample
            index = len(samples)
            if processes['unity'].poll() is not None:
                raise RuntimeError('Unity exited early; inspect unity-editor.log')
            time.sleep(.05)
        raise AssertionError('Timed out: ' + label)

    def command(action, state=''):
        nonlocal command_id
        command_id += 1
        pending = command_file.with_suffix('.tmp')
        pending.write_text(json.dumps(dict(id=command_id, action=action, state=state)), encoding='utf-8')
        os.replace(pending, command_file)

    def state(name, reason):
        command('state', name)
        return wait_for(lambda s: s['playing'] and s['fresh'] and s['match'] == name
                        and s['reason'] == reason and s['mode'] == ('enabled' if name == 'running' else 'stop'), name)

    exit_code = 1
    try:
        start('bridge', 'common.bridge')
        start('stm32', 'sim.mock_stm32')
        processes['unity'] = subprocess.Popen([
            str(args.unity), '-batchmode', '-projectPath', str(args.project.resolve()),
            '-executeMethod', 'ArmWrestlingDemoTools.RunIntegration',
            '-logFile', str(results / 'unity-editor.log')])
        first = wait_for(lambda s: s['playing'] and s['fresh'] and s['reason'] == 'match_waiting',
                         'Editor Play + waiting', timeout=180)
        running = state('running', 'running')
        # The unchanged passive brake can limit a cycle to a few degrees.
        # A real >0.01 rad change plus exact pose mapping verifies motion;
        # a large swing is not part of the control/dynamics contract.
        wait_for(lambda s: s['fresh'] and s['validAngle'] and abs(s['angle'] - running['angle']) > .01,
                 'continuous mechanical angle changes', timeout=15)
        state('paused', 'match_paused')
        state('running', 'running')
        state('finished', 'match_finished')
        state('waiting', 'match_waiting')
        before_reload = state('running', 'running')
        command('reload')
        wait_for(lambda s: s['playing'] and s['fresh'] and s['port'] != before_reload['port']
                 and s['poseErrorDegrees'] < .01, 'script/domain reload while playing', timeout=25)
        stop('stm32')
        missing = wait_for(lambda s: s['fresh'] and not s['validAngle'] and s['reason'] == 'telemetry_missing',
                           'STM32 disconnect -> null angle')
        held = wait_for(lambda s: s['fresh'] and s['seq'] > missing['seq'] + 15
                        and not s['validAngle'], 'null angle persists')
        assert abs(held['poseDegrees'] - missing['poseDegrees']) < .001, 'null moved the arm'
        checks.append(dict(check='null holds last pose', result='PASS'))
        start('stm32-reconnected', 'sim.mock_stm32')
        state('running', 'running')
        command('stop')
        wait_for(lambda s: not s['playing'], 'Editor Stop Play closes client')
        # The server no longer replies to an expired endpoint; inspect its own control output.
        time.sleep(1.6)
        bridge_rows = [json.loads(line) for line in (results / 'bridge.jsonl').read_text().splitlines()]
        assert any(row.get('reason') == 'vr_timeout' and row.get('mode') == 'stop'
                   for row in bridge_rows), 'bridge did not stop after lost Unity heartbeat'
        checks.append(dict(check='PC vr_timeout after Editor Stop', result='PASS'))
        command('play')
        restarted = wait_for(lambda s: s['playing'] and s['fresh'] and s['port'] != first['port'],
                             'Editor re-enter Play with new socket/source port')
        state('running', 'running')
        stop('bridge')
        timed_out = wait_for(lambda s: s['playing'] and not s['fresh'] and s['received'] > 0,
                             'Unity status timeout after PC shutdown')
        held = wait_for(lambda s: s['playing'] and not s['fresh'] and s['time'] > timed_out['time'] + .4,
                        'timeout continues to hold pose')
        assert abs(held['poseDegrees'] - timed_out['poseDegrees']) < .001
        start('bridge-restarted', 'common.bridge')
        wait_for(lambda s: s['playing'] and s['fresh'] and s['reason'] == 'running'
                 and s['seq'] < restarted['seq'] + 100, 'PC restart/seq reset + STM32 auto reconnect')
        # Every recorded wire snapshot has only the frozen V0.1 fields.
        live = [s for s in samples if s['playing'] and s['fresh'] and s['validAngle']]
        assert len(live) > 10
        for s in live:
            assert s['poseErrorDegrees'] < .01, 'pivot differs from rad->degree mapping'
            assert set(json.loads(s['statusJson'])) == {'v', 'type', 'seq', 'angle_rad', 'mode', 'reason'}
            assert set(json.loads(s['updateJson'])) == {'v', 'type', 'seq', 'character_id', 'match_state'}
        assert any(abs(s['angle']) > .1 for s in live)
        checks.append(dict(check='wire field sets + actual pivot quaternion', result='PASS', samples=len(live)))
        command('quit')
        processes['unity'].wait(timeout=30)
        assert processes['unity'].returncode == 0
        log = (results / 'unity-editor.log').read_text(encoding='utf-8', errors='replace')
        assert 'UDP_PROTOCOL_CHECKS_PASS' in log
        assert 'error CS' not in log and 'Exception:' not in log
        checks.append(dict(check='Unity protocol checks + clean Editor exit', result='PASS'))
        exit_code = 0
    except Exception as error:
        checks.append(dict(check='integration', result='FAIL', error=str(error)))
        print('FAIL:', error, flush=True)
    finally:
        for name in list(processes):
            stop(name)
        for handle in handles:
            handle.close()
        read_samples()
        report = dict(result='PASS' if exit_code == 0 else 'FAIL', checks=checks,
                      unity=str(args.unity), python=sys.version, sample_count=len(samples))
        (results / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
