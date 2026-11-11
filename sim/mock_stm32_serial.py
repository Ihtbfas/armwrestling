"""Linux PTY STM32 simulator using the real V1 serial frame protocol.

Run first; copy the printed /dev/pts/N into python -m real.run --port ...
This demo has no firmware, physical brake, measured force, or safety certification.
"""
import argparse
import os
import pty
import select
import time
import tty

from common.bridge import log, positive
from common.serial_protocol import (
    FLAG_ACK_VALID, FLAG_ARM_READY, FLAG_READY, FrameParser,
    MODE_ENABLE, TYPE_BRAKE_COMMAND, TYPE_SESSION_OPEN,
    decode_payload, is_new_sequence, session_ack_frame, telemetry_frame,
)
from sim.mock_stm32 import SimulatedDevice


def serve(duration=None, demo_arm=False):
    master, slave = pty.openpty()
    tty.setraw(slave)
    os.set_blocking(master, False)
    path = os.ttyname(slave)
    device = SimulatedDevice()
    parser = FrameParser()
    session_id = None
    arm_ready = False
    last_command_seq = None
    telemetry_seq = 0
    ack_seq = 0
    pending = b''
    start = next_tick = next_log = time.monotonic()
    log(dict(event='virtual_serial_ready', port=path, transport='serial-v1',
             demo_arm=demo_arm))
    try:
        while duration is None or time.monotonic()-start < duration:
            now = time.monotonic()
            parser.expire(now)
            if select.select([master], [], [], 0)[0]:
                try:
                    chunk = os.read(master, 4096)
                except BlockingIOError:
                    chunk = b''
                for frame in parser.feed(chunk, now):
                    try:
                        message = decode_payload(frame)
                    except ValueError:
                        continue
                    if frame.kind == TYPE_SESSION_OPEN:
                        if session_id != message.session_id:
                            # A newly accepted session can never inherit an old brake command.
                            session_id = message.session_id
                            device.stop()
                            last_command_seq = None
                            arm_ready = bool(demo_arm)  # Explicit opt-in, demo only.
                            log(dict(event='session_open', demo_armed=arm_ready))
                        ack = session_ack_frame(ack_seq, session_id)
                        ack_seq = (ack_seq + 1) & 0xFFFF
                        if not pending:
                            pending = ack
                    elif frame.kind == TYPE_BRAKE_COMMAND:
                        if session_id is None or message.session_id != session_id:
                            continue
                        if not is_new_sequence(frame.sequence, last_command_seq):
                            continue
                        if message.mode == MODE_ENABLE and not arm_ready:
                            continue
                        validated = dict(
                            v=1, type='brake_command', seq=frame.sequence,
                            mode='enabled' if message.mode == MODE_ENABLE else 'stop',
                            reason='running' if message.mode == MODE_ENABLE else 'stop',
                            target_torque_nm=message.target_torque_mnm/1000,
                            brake_torque_nm=0.0,
                            speed_transition_rad_s=.05,
                            valid_for_ms=min(message.valid_for_ms, 200),
                            telemetry_seq=0,
                        )
                        try:
                            device.receive(validated, now)
                        except ValueError:
                            continue
                        last_command_seq = frame.sequence
            now = time.monotonic()
            if device.command is not None and now >= device.deadline:
                # A timeout requires an explicit new arm; sessions do not re-arm hardware.
                arm_ready = False
                device.stop()
                log(dict(event='command_timeout', armed=False))
            if now >= next_tick:
                elapsed = now-start
                phase = elapsed % 8
                user = -.15 if phase < 3 else (.15 if phase < 6 else 0.)
                device.step(.01, user, now)
                flags = FLAG_READY | (FLAG_ARM_READY if arm_ready else 0)
                flags |= FLAG_ACK_VALID if last_command_seq is not None else 0
                torque_mnm = (round(device.command['target_torque_nm']*1000)
                              if device.command is not None and now < device.deadline else 0)
                telemetry = telemetry_frame(
                    telemetry_seq, round(device.angle*1000),
                    round(device.velocity*1000), 0, flags,
                    last_command_seq if last_command_seq is not None else 0,
                    torque_mnm)
                telemetry_seq = (telemetry_seq+1) & 0xFFFF
                if not pending:
                    pending = telemetry
                if now >= next_log:
                    log(dict(type='stm32_serial_snapshot', elapsed_s=round(elapsed, 3),
                             angle_rad=device.angle, velocity_rad_s=device.velocity,
                             applied_torque_nm=device.brake, armed=arm_ready,
                             session_ready=session_id is not None,
                             last_command_seq=last_command_seq))
                    next_log = now+.5
                next_tick = now+.01
            if pending:
                try:
                    sent = os.write(master, pending)
                    pending = pending[sent:]
                except BlockingIOError:
                    pass
            time.sleep(.001)
    except KeyboardInterrupt:
        pass
    finally:
        device.stop()
        os.close(master)
        os.close(slave)
        log(dict(event='closed'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration', type=positive)
    parser.add_argument('--demo-arm', action='store_true',
                        help='auto-authorize simulated brake after handshake (demo only)')
    args = parser.parse_args()
    serve(args.duration, args.demo_arm)


if __name__ == '__main__':
    main()
