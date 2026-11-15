"""V1 binary STM32 simulator: Windows --port COMx, or Linux PTY by default."""
import argparse
import os
import time

from common.bridge import log, positive
from common.console import add_console_arguments, reporter_from_args
from sim.serial_device import SerialDevice


class PtyPort:
    """POSIX imports are lazy so this module also loads on Windows."""
    def __init__(self):
        import pty
        import tty
        self.master, self.slave = pty.openpty()
        tty.setraw(self.slave)
        os.set_blocking(self.master, False)
        self.name = os.ttyname(self.slave)

    @property
    def in_waiting(self):
        import select
        return 4096 if select.select([self.master], [], [], 0)[0] else 0

    def read(self, count):
        try:
            return os.read(self.master, count)
        except BlockingIOError:
            return b''

    def write(self, data):
        try:
            return os.write(self.master, data)
        except BlockingIOError:
            return 0

    def close(self):
        os.close(self.master)
        os.close(self.slave)


def serve(duration=None, demo_arm=False, port=None, baudrate=115200, reporter=None):
    emit = reporter or log
    if port:
        import serial
        connection = serial.Serial(port, baudrate=baudrate, timeout=0, write_timeout=0)
    else:
        if os.name != 'posix':
            raise OSError('Windows requires --port COMx (the simulator side of a virtual COM pair).')
        connection = PtyPort()
    core = SerialDevice(demo_arm, emit)
    pending = pending_ack = b''
    received_bytes = transmitted_bytes = 0
    start = next_tick = next_log = time.monotonic()
    emit(dict(event='virtual_serial_ready', port=port or connection.name,
              baudrate=baudrate, transport='serial-v1', demo_arm=demo_arm))
    try:
        while duration is None or time.monotonic() - start < duration:
            now = time.monotonic()
            available = connection.in_waiting
            if available:
                chunk = connection.read(min(available, 4096))
                received_bytes += len(chunk)
                pending_ack = core.feed(chunk, now) or pending_ack
            if now >= next_tick:
                elapsed = now - start
                phase = elapsed % 8
                user = -.15 if phase < 3 else (.15 if phase < 6 else 0.)
                core.step(.01, user, now)
                telemetry = core.telemetry(now)
                if not pending and not pending_ack:
                    pending = telemetry
                if now >= next_log:
                    device = core.device
                    emit(dict(type='stm32_serial_snapshot', elapsed_s=round(elapsed, 3),
                        angle_rad=device.angle, velocity_rad_s=device.velocity,
                        user_torque_nm=user, applied_torque_nm=device.brake,
                        reason=device.reason, armed=core.arm_ready,
                        session_ready=core.session_id is not None,
                        last_command_seq=core.last_command_seq,
                        accepted_commands=core.accepted_commands,
                        received_bytes=received_bytes, transmitted_bytes=transmitted_bytes))
                    next_log = now + .5
                next_tick = now + .01
            if not pending and pending_ack:
                pending, pending_ack = pending_ack, b''
            if pending:
                sent = connection.write(pending)
                pending = pending[sent:]
                transmitted_bytes += sent
            time.sleep(.001)
    except KeyboardInterrupt:
        pass
    finally:
        core.device.stop()
        connection.close()
        emit(dict(event='closed'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', help='simulator COM port, e.g. COM2 paired with PC COM1; omitted = Linux PTY')
    parser.add_argument('--baudrate', type=int, default=115200)
    parser.add_argument('--duration', type=positive)
    parser.add_argument('--demo-arm', action='store_true',
                        help='authorize simulated brake after a new session (simulation only)')
    add_console_arguments(parser)
    args = parser.parse_args()
    with reporter_from_args('模拟 STM32（V1 串口 / 软件机械）', args) as reporter:
        try:
            serve(args.duration, args.demo_arm, args.port, args.baudrate, reporter)
        except ImportError:
            parser.exit(2, 'COM simulation needs pyserial: python -m pip install pyserial\n')
        except OSError as error:
            parser.exit(2, f'Serial simulator error: {error}\n')


if __name__ == '__main__':
    main()
