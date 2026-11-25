"""Standard-library scalar dynamics and TCP device simulator; not firmware."""
import argparse
import math
from pathlib import Path
import select
import socket
import sys
import time

from common.bridge import positive
from common.console import add_console_arguments, reporter_from_args
from common.control import passive_torque
from common.protocol import JsonSocket, validate_command


STEP_SECONDS = .02
INPUT_KEYS = {'1': 'down', '2': 'back', '0': 'release', 'r': 'reset'}
INPUT_SIGN = {'down': -1, 'back': 1, 'release': 0, 'reset': 0}


def effort_amplitude(value):
    value = float(value)
    if not 0 <= value <= .8:
        raise argparse.ArgumentTypeError('simulation torque must be in [0, 0.8] N*m')
    return value


def parse_sequence(value):
    """Convert action:seconds pairs into a fixed number of 20 ms ticks."""
    segments = []
    for item in value.split(','):
        action, _, seconds = item.partition(':')
        action = action.strip().lower()
        try:
            count = float(seconds) / STEP_SECONDS
            ticks = round(count)
        except (ValueError, OverflowError):
            ticks = 0
            count = 0
        if action not in INPUT_SIGN or ticks <= 0 or not math.isclose(count, ticks, rel_tol=0, abs_tol=1e-8):
            raise argparse.ArgumentTypeError(
                'use action:seconds pairs with positive multiples of 0.02 s, e.g. down:2,release:1')
        segments.append((action, ticks))
    return segments


def file_action(path):
    try:
        with path.open(encoding='utf-8-sig') as stream:
            token = stream.read(32).strip().lower()
    except (OSError, UnicodeError):
        return 'release'
    action = INPUT_KEYS.get(token, token)
    return action if action in INPUT_SIGN else 'release'


class SimulatedDevice:
    def __init__(self):
        self.angle = self.velocity = 0.0
        self.brake = self.power = self.maximum_power = 0.0
        self.command = None
        self.last_seq = -1
        self.deadline = 0.0
        self.reason = 'disconnected'

    def stop(self):
        self.command = None
        self.last_seq = -1
        self.brake = self.power = 0.0
        self.reason = 'disconnected'

    def receive(self, message, now):
        validate_command(message)
        if message['seq'] <= self.last_seq:
            raise ValueError('command seq must increase within a connection')
        self.command = dict(message)
        self.last_seq = message['seq']
        self.deadline = now+message['valid_for_ms']/1000

    def command_enabled(self, now):
        return self.command is not None and now < self.deadline and self.command['mode'] == 'enabled'

    def step(self, dt, user_torque, now):
        enabled = self.command_enabled(now)
        if self.command is None:
            self.reason = 'disconnected'
        elif now >= self.deadline:
            self.reason = 'command_timeout'
        else:
            self.reason = self.command.get('reason', self.command['mode'])
        steps = max(1, math.ceil(dt/.002))
        substep = dt/steps
        for _ in range(steps):
            # Never hold an old signed torque through a local velocity reversal.
            self.brake = passive_torque(self.command['target_torque_nm'], self.velocity,
                                       self.command['speed_transition_rad_s']) if enabled else 0.
            self.power = self.brake*self.velocity
            self.maximum_power = max(self.maximum_power, self.power)
            self.velocity += substep*(user_torque+self.brake-.04*self.velocity)/.05469492132716054
            self.angle += substep*self.velocity
            # Simple demo stops, not a physical contact/safety model.
            if self.angle < -.55 or self.angle > .60:
                self.angle = max(-.55, min(.60, self.angle))
                self.velocity = 0.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=9101)
    parser.add_argument('--duration', type=positive)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument('--manual', action='store_true', help='Windows console keys: 1 down, 2 back, 0 release, R reset')
    inputs.add_argument('--input-file', type=Path, help='local file containing down, back, release or reset')
    inputs.add_argument('--sequence', type=parse_sequence, help='script from first ENABLE, e.g. down:2,back:1,release:0.5')
    parser.add_argument('--torque', type=effort_amplitude, default=.15,
                        help='absolute simulated user effort (N*m), default 0.15, maximum 0.8')
    add_console_arguments(parser)
    args = parser.parse_args()
    if args.manual:
        if sys.platform != 'win32' or not sys.stdin.isatty():
            parser.error('--manual needs a Windows console; use --input-file or --sequence otherwise')
        import msvcrt
    input_mode = ('manual' if args.manual else 'file' if args.input_file else
                  'sequence' if args.sequence else 'cycle')
    sequence = (action for action, ticks in args.sequence or [] for _ in range(ticks))
    sequence_started = False
    action = previous_action = 'release'
    reporter = reporter_from_args('模拟 STM32（TCP / 软件机械）', args)
    log = reporter
    log(dict(event='listening', stm_port=args.port, stm_transport='tcp', input_mode=input_mode))
    device = SimulatedDevice()
    peer = None
    start = next_tick = next_log = next_connect = time.monotonic()
    seq = simulation_tick = 0
    try:
        while args.duration is None or time.monotonic()-start < args.duration:
            now = time.monotonic()
            if peer is None and now >= next_connect:
                try:
                    peer = JsonSocket(socket.create_connection((args.host, args.port), timeout=.2))
                    device.stop()
                    seq = 0
                    log(dict(event='connected', role='stm32'))
                except OSError:
                    next_connect = now+.5
            if peer is not None:
                try:
                    if select.select([peer.socket], [], [], 0)[0]:
                        for message in peer.receive():
                            device.receive(message, time.monotonic())
                except (OSError, EOFError, ValueError, RecursionError) as error:
                    peer.socket.close()
                    peer = None
                    device.stop()
                    next_connect = time.monotonic()+.5
                    log(dict(event='disconnected', role='stm32', reason=str(error)))
            now = time.monotonic()
            if now >= next_tick:
                elapsed = now-start
                enabled = device.command_enabled(now)
                if args.manual:
                    while msvcrt.kbhit():
                        key = msvcrt.getwch().lower()
                        if key in ('\x00', '\xe0'):
                            msvcrt.getwch()  # Skip the scan code of an extended key.
                            continue
                        action = INPUT_KEYS.get(key, action)
                elif args.input_file:
                    action = file_action(args.input_file)
                elif args.sequence:
                    sequence_started |= enabled
                    action = next(sequence, 'release') if sequence_started else 'release'
                else:
                    # Default cycle retains the original timing.
                    phase = elapsed % 8
                    action = 'down' if phase < 3 else ('back' if phase < 6 else 'release')
                if action == 'reset':
                    device.angle = device.velocity = 0.
                user = INPUT_SIGN[action] * args.torque
                command_target = device.command['target_torque_nm'] if enabled else 0.
                command_seq = device.last_seq
                device.step(STEP_SECONDS, user, now)
                if peer is not None:
                    try:
                        peer.queue(dict(v=1, type='telemetry', seq=seq, angle_rad=device.angle,
                                        velocity_rad_s=device.velocity, device_status='ready'))
                        peer.flush()
                        seq += 1
                    except (OSError, EOFError) as error:
                        peer.socket.close()
                        peer = None
                        device.stop()
                        next_connect = now+.5
                        log(dict(event='disconnected', role='stm32', reason=str(error)))
                if now >= next_log or (action != previous_action and input_mode != 'cycle'):
                    log(dict(type='stm32_snapshot', elapsed_s=round(elapsed, 3), angle_rad=device.angle,
                             velocity_rad_s=device.velocity, user_torque_nm=user,
                             applied_torque_nm=device.brake, brake_power_w=device.power,
                             maximum_brake_power_w=device.maximum_power, reason=device.reason,
                             command_seq=command_seq, command_target_torque_nm=command_target,
                             input_mode=input_mode, input_action=action, simulation_tick=simulation_tick))
                    next_log = now+.5
                previous_action = action
                simulation_tick += 1
                next_tick = now+STEP_SECONDS
            time.sleep(.002)
    except KeyboardInterrupt:
        pass
    finally:
        if peer is not None:
            peer.socket.close()
        device.stop()
        log(dict(event='closed'))
        reporter.close()


if __name__ == '__main__':
    main()
