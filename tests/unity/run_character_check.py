"""Actual Unity Editor character selection, existing scene, no Python VR client.

Starts the existing moving mock STM32, then replaces only its TCP connection
with fixed test telemetry so different people can be compared at identical input.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unity', required=True, type=Path)
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--results-dir', required=True, type=Path)
    parser.add_argument('--label', default='character-check')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    from common.protocol import LineBuffer, encode
    results = args.results_dir.resolve()
    results.mkdir(parents=True, exist_ok=True)
    sample_path, command_path = results/'samples.jsonl', results/'command.json'
    for path in (sample_path, command_path):
        path.unlink(missing_ok=True)
    # Do not close or take over a colleague's running PC/demo.
    with socket.socket() as tcp, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        tcp.bind(('127.0.0.1', 9101))
        udp.bind(('127.0.0.1', 9102))
    children, handles, checks, commands = {}, [], [], []
    fixed = None
    next_telemetry = telemetry_seq = command_id = 0
    framing = LineBuffer()

    def records(path):
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines(keepends=True)
                if line.endswith('\n')]

    def start(name, module):
        stream = (results/(name+'.jsonl')).open('w', encoding='utf-8')
        handles.append(stream)
        children[name] = subprocess.Popen([sys.executable, '-u', '-m', module], cwd=root,
                                         stdout=stream, stderr=subprocess.STDOUT)

    def stop(name):
        child = children.pop(name, None)
        if child is not None and child.poll() is None:
            child.terminate()
            child.wait(timeout=8)

    def pump():
        nonlocal next_telemetry, telemetry_seq
        if fixed is None:
            return
        now = time.monotonic()
        if now >= next_telemetry:
            fixed.sendall(encode(dict(v=1, type='telemetry', seq=telemetry_seq,
                angle_rad=-.4, velocity_rad_s=-.2, device_status='ready')))
            telemetry_seq += 1
            next_telemetry = now+.02
        while True:
            try:
                data = fixed.recv(4096)
            except BlockingIOError:
                break
            if not data:
                raise RuntimeError('PC closed the fixed test telemetry source')
            commands.extend(framing.feed(data))

    def wait_for(predicate, label, timeout=12):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            pump()
            statuses = [row for row in records(results/'pc.jsonl') if row.get('type') == 'status']
            snapshots = records(sample_path)
            pc, sample = (statuses[-1] if statuses else {}), (snapshots[-1] if snapshots else {})
            if predicate(pc, sample):
                checks.append(dict(check=label, result='PASS', pc=pc, unity=sample))
                print('PASS:', label, flush=True)
                return pc, sample
            if children.get('unity') and children['unity'].poll() is not None:
                raise RuntimeError('Unity exited early; inspect unity-editor.log')
            time.sleep(.02)
        raise AssertionError('Timed out: '+label)

    def command(action, state='', character=''):
        nonlocal command_id
        command_id += 1
        pending = command_path.with_suffix('.tmp')
        pending.write_text(json.dumps(dict(id=command_id, action=action, state=state,
                                          character_id=character)), encoding='utf-8')
        os.replace(pending, command_path)

    def alive(pc, sample):
        return (sample.get('fresh') and sample.get('commandId', -1) >= command_id
                and pc.get('vr_fresh') and pc.get('telemetry_fresh'))

    def select(character, active=None):
        command('character', character=character)
        return wait_for(lambda pc,s: alive(pc,s) and pc.get('character_id') == character
            and pc.get('active_character_id') == (active or character)
            and json.loads(s.get('updateJson') or '{}').get('character_id') == character,
            f'Unity requests {character}; PC uses {active or character}')

    def state(value):
        command('state', state=value)
        return wait_for(lambda pc,s: alive(pc,s) and pc.get('match_state') == value
            and s.get('match') == value and pc.get('reason') == ('running' if value == 'running' else 'match_'+value),
            'Unity state '+value)

    code = 1
    try:
        start('pc', 'common.bridge')
        start('stm32', 'sim.mock_stm32')
        children['unity'] = subprocess.Popen([str(args.unity), '-batchmode',
            '-projectPath', str(args.project.resolve()), '-executeMethod',
            'ArmWrestlingDemoTools.RunCharacterCheck', '-armUdpResults', str(results),
            '-logFile', str(results/'unity-editor.log')])
        wait_for(lambda pc,s: alive(pc,s) and pc.get('reason') == 'match_waiting',
                 'real Editor Play + moving mock STM32', timeout=150)
        select('person_02')
        state('running')
        select('person_03', active='person_02')
        wait_for(lambda pc,s: alive(pc,s) and pc.get('character_selection_deferred'), 'running request is deferred')
        state('paused')
        wait_for(lambda pc,s: alive(pc,s) and pc.get('active_character_id') == 'person_03'
                 and pc.get('target_torque_nm') == 0, 'pause confirms person_03 with zero output')
        state('running')
        state('waiting')
        select('demo')
        state('running')
        stop('stm32')
        wait_for(lambda pc,s: pc.get('angle_rad') is None and pc.get('target_torque_nm') == 0
                 and s.get('fresh') and not s.get('validAngle'), 'moving device stopped -> null/STOP')
        fixed = socket.create_connection(('127.0.0.1', 9101), timeout=1)
        fixed.setblocking(False)
        wait_for(lambda pc,s: alive(pc,s) and pc.get('angle_rad') == -.4, 'fixed test telemetry: -0.4 rad / -0.2 rad/s')
        for character, target in (('person_01', .15), ('person_02', .225), ('person_03', .27), ('demo', .3)):
            state('waiting')
            select(character)
            state('running')
            pc, sample = wait_for(lambda pc,s: alive(pc,s) and pc.get('active_character_id') == character
                and pc.get('angle_rad') == -.4 and pc.get('velocity_rad_s') == -.2
                and abs(pc.get('target_torque_nm', -1)-target) < .001,
                f'fixed-state {character} target approaches {target:.3f} N*m')
            assert sample['poseErrorDegrees'] < .01
        state('paused')
        wait_for(lambda pc,s: alive(pc,s) and pc.get('target_torque_nm') == 0, 'Unity pause stops output')
        for sample in records(sample_path):
            if sample.get('fresh'):
                assert set(json.loads(sample['statusJson'])) == {'v','type','seq','angle_rad','mode','reason'}
                assert set(json.loads(sample['updateJson'])) == {'v','type','seq','character_id','match_state'}
        checks.append(dict(check='actual Unity wire fields unchanged', result='PASS'))
        command('quit')
        children['unity'].wait(timeout=25)
        assert children['unity'].returncode == 0
        editor_log = (results/'unity-editor.log').read_text(encoding='utf-8', errors='replace')
        assert 'UDP_PROTOCOL_CHECKS_PASS' in editor_log
        assert 'error CS' not in editor_log and 'Exception:' not in editor_log
        checks.append(dict(check='clean Editor compile, protocol checks and exit', result='PASS'))
        code = 0
    except Exception as error:
        checks.append(dict(check='character integration', result='FAIL', error=str(error)))
        print('FAIL:', error, flush=True)
    finally:
        if fixed is not None:
            fixed.close()
        for name in list(children):
            stop(name)
        for stream in handles:
            stream.close()
        report = dict(label=args.label, result='PASS' if code == 0 else 'FAIL',
            checks=checks, editor_snapshots=len(records(sample_path)), fixed_commands=len(commands),
            scope='Actual Unity C# UDP; moving sim.mock_stm32 then fixed TCP test telemetry; existing scene unmodified.')
        (results/'character-report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
