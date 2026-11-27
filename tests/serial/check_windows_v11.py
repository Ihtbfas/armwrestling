"""Wire checks for an isolated virtual COM pair. Both endpoints must be unused.

python tests/serial/check_windows_v11.py --pc-port COM1 --stm-port COM2 --results-dir <folder>
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
    if args.pc_port.casefold() == args.stm_port.casefold():
        parser.error('specify the two distinct endpoints of the isolated virtual COM pair')
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    import serial
    from common.serial_protocol import (
        FLAG_ACK_VALID, FLAG_ANGLE_VALID, FLAG_ARM_READY, FLAG_CMD_TIMEOUT,
        FrameParser, MODE_ENABLE, MODE_STOP, TYPE_SESSION_ACK, TYPE_TELEMETRY,
        command_frame, decode_payload, sample_delta_ms, session_open_frame,
    )
    results = args.results_dir.resolve()
    results.mkdir(parents=True, exist_ok=True)
    checks = []
    child = None

    def passed(name, **evidence):
        checks.append(dict(check=name, result='PASS', **evidence))
        print('PASS:', name, flush=True)

    def test_wire(port):
        frames = FrameParser()
        first_session, second_session, third_session = 123, 456, 789

        def receive(kind, predicate, seconds=1.5):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                chunk = port.read(min(max(port.in_waiting, 1), 4096))
                for frame in frames.feed(chunk):
                    if frame.kind == kind:
                        value = decode_payload(frame)
                        if predicate(frame, value):
                            return frame.sequence, value
                if child.poll() is not None:
                    raise AssertionError('COM simulator exited early')
                time.sleep(.002)
            raise AssertionError('Timed out receiving expected V1.1 frame')

        def observation(condition, seconds=1.5):
            return receive(TYPE_TELEMETRY, condition, seconds)

        def open_session(session):
            port.write(session_open_frame(0, session))
            receive(TYPE_SESSION_ACK, lambda _, value: value.session_id == session)

        def expected(session, seq, target, mode=MODE_ENABLE):
            port.write(command_frame(seq, session, mode, target))
            return observation(lambda _, value: value.session_id == session
                               and value.status_flags & FLAG_ACK_VALID
                               and value.last_command_seq == seq and value.applied_target_mnm == target)

        _, initial = observation(lambda _, value: value.session_id == 0 and value.status_flags & FLAG_ANGLE_VALID)
        assert not initial.status_flags & (FLAG_ACK_VALID | FLAG_ARM_READY)
        assert initial.applied_target_mnm == 0
        _, later = observation(lambda _, value: value.session_id == 0
                               and sample_delta_ms(value.mcu_sample_ms, initial.mcu_sample_ms) > 0)
        passed('unpaired V1.1 diagnostics and advancing MCU sample time',
               initial_sample_ms=initial.mcu_sample_ms, later_sample_ms=later.mcu_sample_ms)

        open_session(first_session)
        old_seq, unconfirmed = observation(lambda _, value: value.session_id == first_session)
        assert old_seq >= 40000
        assert not unconfirmed.status_flags & (FLAG_ACK_VALID | FLAG_ARM_READY)
        _, stop = expected(first_session, 65534, 0, MODE_STOP)
        assert stop.status_flags & FLAG_ARM_READY
        expected(first_session, 65535, 300)
        expected(first_session, 0, 400)
        passed('matching OPEN/ACK and accepted STOP; command sequence wraps 65535 -> 0')

        corrupt = bytearray(command_frame(1, first_session, MODE_ENABLE, 500))
        corrupt[-1] ^= 1
        port.write(bytes(corrupt))
        port.write(command_frame(1, 999, MODE_ENABLE, 500))
        port.write(command_frame(0, first_session, MODE_ENABLE, 600))
        time.sleep(.035)
        port.reset_input_buffer()
        _, preserved = observation(lambda _, value: value.session_id == first_session)
        assert (preserved.last_command_seq, preserved.applied_target_mnm) == (0, 400)
        passed('bad CRC, wrong session and duplicate command leave the current target unchanged')

        seq = 1
        reset_deadline = time.monotonic() + 4
        while time.monotonic() < reset_deadline:
            port.write(command_frame(seq, first_session, MODE_STOP))
            seq += 1
            wire_seq, value = observation(lambda _, data: data.session_id in (0, first_session), .12)
            if value.session_id == 0:
                assert not value.status_flags & (FLAG_CMD_TIMEOUT | FLAG_ARM_READY | FLAG_ACK_VALID)
                assert value.applied_target_mnm == 0 and wire_seq < 100
                break
            old_seq = wire_seq
            time.sleep(.035)
        else:
            raise AssertionError('simulated MCU did not reset on the same COM handle')
        assert 32769 <= old_seq <= 65535
        port.write(command_frame(seq, first_session, MODE_ENABLE, 400))
        _, stale = observation(lambda _, data: data.session_id == 0)
        assert not stale.status_flags & (FLAG_ARM_READY | FLAG_ACK_VALID)
        assert stale.applied_target_mnm == 0
        open_session(second_session)
        expected(second_session, 0, 0, MODE_STOP)
        passed('same COM handle: MCU reboots with low seq and rejects previous-session command',
               old_sequence=old_seq, reset_sequence=wire_seq)

        expected(second_session, 1, 300)
        _, timeout = observation(lambda _, value: value.session_id == 0 and value.status_flags & FLAG_CMD_TIMEOUT)
        assert timeout.applied_target_mnm == 0
        assert not timeout.status_flags & (FLAG_ARM_READY | FLAG_ACK_VALID)
        port.write(session_open_frame(1, second_session))
        port.write(command_frame(2, second_session, MODE_ENABLE, 400))
        _, timed_out = observation(lambda _, value: value.status_flags & FLAG_CMD_TIMEOUT)
        assert timed_out.session_id == 0 and timed_out.applied_target_mnm == 0
        passed('command watchdog clears session, ACK and target; old OPEN and ENABLE cannot rearm')

        open_session(third_session)
        _, pending = observation(lambda _, value: value.session_id == third_session)
        assert pending.status_flags & FLAG_CMD_TIMEOUT
        assert not pending.status_flags & (FLAG_ARM_READY | FLAG_ACK_VALID)
        _, recovered = expected(third_session, 0, 0, MODE_STOP)
        assert not recovered.status_flags & FLAG_CMD_TIMEOUT
        assert recovered.applied_target_mnm == 0
        passed('new session plus accepted STOP clears CMD_TIMEOUT without inheriting an old target')

    code = 1
    with (results / 'com-wire-simulator.jsonl').open('w', encoding='utf-8') as output:
        try:
            child = subprocess.Popen(
                [sys.executable, '-u', '-m', 'sim.mock_stm32_serial', '--port', args.stm_port,
                 '--demo-arm', '--reset-after', '2.2', '--duration', '12', '--display', 'json'],
                cwd=root, stdout=output, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            with serial.Serial(args.pc_port, 115200, timeout=0, write_timeout=.2) as port:
                test_wire(port)
            code = 0
        except (AssertionError, OSError, ValueError) as error:
            checks.append(dict(check='COM wire integration', result='FAIL', error=str(error)))
            print('FAIL:', error, flush=True)
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
    (results / 'com-wire-report.json').write_text(json.dumps(dict(
        protocol='V1.1', result='PASS' if code == 0 else 'FAIL', pc_port=args.pc_port,
        stm_port=args.stm_port, checks=checks), indent=2) + '\n', encoding='utf-8')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
