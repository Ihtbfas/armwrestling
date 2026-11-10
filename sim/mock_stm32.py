"""Standard-library scalar dynamics and TCP device simulator; not firmware."""
import argparse
import math
import select
import socket
import time

from common.bridge import log, positive
from common.control import passive_torque
from common.protocol import JsonSocket, validate_command


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

    def step(self, dt, user_torque, now):
        enabled = self.command is not None and now < self.deadline and self.command['mode']=='enabled'
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
    args = parser.parse_args()
    device = SimulatedDevice()
    peer = None
    start = next_tick = next_log = next_connect = time.monotonic()
    seq = 0
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
                # Repeated user effort: down 3 s, return 3 s, release 2 s.
                phase = elapsed % 8
                user = -.15 if phase < 3 else (.15 if phase < 6 else 0.)
                device.step(.02, user, now)
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
                if now >= next_log:
                    log(dict(type='stm32_snapshot', elapsed_s=round(elapsed, 3), angle_rad=device.angle,
                             velocity_rad_s=device.velocity, user_torque_nm=user,
                             applied_torque_nm=device.brake, brake_power_w=device.power,
                             maximum_brake_power_w=device.maximum_power, reason=device.reason,
                             command_seq=device.last_seq))
                    next_log = now+.5
                next_tick = now+.02
            time.sleep(.002)
    except KeyboardInterrupt:
        pass
    finally:
        if peer is not None:
            peer.socket.close()
        device.stop()


if __name__ == '__main__':
    main()
