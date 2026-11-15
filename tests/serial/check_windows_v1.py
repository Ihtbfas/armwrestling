"""Opt-in actual COM pair checks. Both endpoints must be unused.

python tests/serial/check_windows_v1.py --pc-port COM1 --stm-port COM2 --results-dir <folder>
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pc-port', required=True)
    parser.add_argument('--stm-port', required=True)
    parser.add_argument('--results-dir', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    import serial
    from common.serial_protocol import (FLAG_ARM_READY, FrameParser, MODE_ENABLE,
        TYPE_SESSION_ACK, TYPE_TELEMETRY, command_frame, decode_payload, session_open_frame)
    results = args.results_dir.resolve()
    results.mkdir(parents=True, exist_ok=True)
    checks, child = [], None
    def passed(name):
        checks.append(dict(check=name, result='PASS'))
        print('PASS:', name, flush=True)
    code = 1
    with (results/'com-wire-simulator.jsonl').open('w', encoding='utf-8') as output:
        try:
            child = subprocess.Popen([sys.executable, '-u', '-m', 'sim.mock_stm32_serial',
                '--port', args.stm_port, '--demo-arm', '--duration', '15'], cwd=root,
                stdout=output, stderr=subprocess.STDOUT)
            with serial.Serial(args.pc_port, 115200, timeout=0, write_timeout=.2) as port:
                frames = FrameParser()
                def receive(predicate, seconds=1.5):
                    deadline = time.monotonic() + seconds
                    while time.monotonic() < deadline:
                        for frame in frames.feed(port.read(min(max(port.in_waiting, 1), 4096))):
                            value = decode_payload(frame)
                            if predicate(frame, value):
                                return value
                        if child.poll() is not None:
                            raise AssertionError('COM simulator exited early')
                        time.sleep(.002)
                    raise AssertionError('Timed out receiving expected V1 frame')
                # Send repeated opens until the independently started COM peer is ready.
                deadline, ack = time.monotonic()+3, None
                while ack is None and time.monotonic() < deadline:
                    port.write(session_open_frame(0, 123))
                    try:
                        ack = receive(lambda f,v: f.kind == TYPE_SESSION_ACK and v.session_id == 123, .25)
                    except AssertionError:
                        pass
                assert ack is not None
                passed('actual COM V1 session request/ACK + CRC-checked decode')
                port.write(command_frame(65535, 123, MODE_ENABLE, 300, 200))
                receive(lambda f,v: f.kind == TYPE_TELEMETRY and v.applied_target_mnm == 300)
                port.write(command_frame(0, 123, MODE_ENABLE, 400, 200))
                receive(lambda f,v: f.kind == TYPE_TELEMETRY and v.last_command_seq == 0 and v.applied_target_mnm == 400)
                passed('actual COM brake command/telemetry and sequence 65535 -> 0')
                corrupt = bytearray(command_frame(1, 123, MODE_ENABLE, 500, 200))
                corrupt[-1] ^= 1
                port.write(bytes(corrupt))
                port.write(command_frame(1, 999, MODE_ENABLE, 500, 200))
                port.write(command_frame(0, 123, MODE_ENABLE, 600, 200))
                time.sleep(.04)
                port.reset_input_buffer() # Inspect frames generated after the invalid inputs.
                value = receive(lambda f,v: f.kind == TYPE_TELEMETRY and v.last_command_seq == 0)
                assert value.applied_target_mnm == 400
                passed('bad CRC, wrong session and duplicate command do not replace target')
                value = receive(lambda f,v: f.kind == TYPE_TELEMETRY and not v.status_flags & FLAG_ARM_READY)
                assert value.applied_target_mnm == 0
                passed('200 ms command watchdog removes ARM_READY and clears target')
                port.write(command_frame(2, 123, MODE_ENABLE, 300, 200))
                value = receive(lambda f,v: f.kind == TYPE_TELEMETRY and not v.status_flags & FLAG_ARM_READY)
                assert value.applied_target_mnm == 0
                passed('ENABLE cannot restore authorization after watchdog')
                port.write(session_open_frame(0, 456))
                receive(lambda f,v: f.kind == TYPE_SESSION_ACK and v.session_id == 456)
                value = receive(lambda f,v: f.kind == TYPE_TELEMETRY and v.status_flags & FLAG_ARM_READY)
                assert value.applied_target_mnm == 0
                passed('new demo-authorized session does not inherit old brake target')
            code = 0
        except Exception as error:
            checks.append(dict(check='COM wire integration', result='FAIL', error=str(error)))
            print('FAIL:', error)
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
    (results/'com-wire-report.json').write_text(json.dumps(dict(
        result='PASS' if code == 0 else 'FAIL', pc_port=args.pc_port,
        stm_port=args.stm_port, checks=checks), indent=2), encoding='utf-8')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
