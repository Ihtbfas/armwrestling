"""Run the same PC controller with STM32 V1 serial + VR UDP.

Use a Linux PTY created by sim.mock_stm32_serial, or a real COM port.
"""
import argparse
from contextlib import closing
import select
import socket
import time

from common.bridge import log, positive
from common.protocol import MAX_LINE, decode_datagram, encode_datagram, validate_incoming
from common.state import BridgeState, vr_payload
from common.console import add_console_arguments, reporter_from_args
from common.strategies import DEFAULT_STRATEGY, STRATEGY_NAMES
from real.stm32_link import Stm32Link


def serve(port, host='127.0.0.1', vr_port=9102, baudrate=115200,
          duration=None, telemetry_timeout=.25, vr_timeout=1., reporter=None, *,
          strategy=DEFAULT_STRATEGY):
    emit = reporter or log
    state = BridgeState(telemetry_timeout, vr_timeout, strategy=strategy)
    link = None
    vr_address = None
    start = time.monotonic()
    next_tick = next_log = next_open = start
    opened_at = last_telemetry_at = None
    with closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as vr_socket:
        vr_socket.bind((host, vr_port))
        vr_socket.setblocking(False)
        emit(dict(event='listening', stm_transport='serial-v1', port=port, baudrate=baudrate,
                 vr_transport='udp', vr_port=vr_socket.getsockname()[1], configured_strategy=strategy))
        try:
            while duration is None or time.monotonic() - start < duration:
                now = time.monotonic()
                if link is None and now >= next_open:
                    try:
                        link = Stm32Link.open(port, baudrate)
                        opened_at, last_telemetry_at = now, None
                        state.clear('stm32')
                        emit(dict(event='serial_open', port=port))
                    except OSError as error:
                        next_open = now + .5
                        emit(dict(event='serial_unavailable', error=str(error)))
                if link is not None:
                    try:
                        was_session_ready = link.session_ready
                        for message in link.poll(now):
                            state.receive('stm32', message, time.monotonic())
                            last_telemetry_at = time.monotonic()
                        if link.session_ready and not was_session_ready:
                            emit(dict(event='serial_session_ready', port=port))
                    except (OSError, TimeoutError, ValueError) as error:
                        link.close()
                        link = None
                        state.clear('stm32')
                        next_open = now + .5
                        emit(dict(event='serial_error', error=str(error)))
                if link is not None and (
                    (last_telemetry_at is not None and now - last_telemetry_at >= telemetry_timeout)
                    or (last_telemetry_at is None and now - opened_at >= max(1., telemetry_timeout))
                ):
                    # A virtual COM peer can disappear without closing our COM
                    # handle. A fresh session is required when it comes back.
                    link.close()
                    link = None
                    state.clear('stm32')
                    next_open = now + .5
                    emit(dict(event='serial_timeout', reason='telemetry_timeout'))

                # One latest UDP VR sender, exactly as in common.bridge.
                if select.select([vr_socket], [], [], 0)[0]:
                    try:
                        packet, address = vr_socket.recvfrom(MAX_LINE+1)
                        message = decode_datagram(packet)
                        validate_incoming(message, 'vr')
                        if vr_address is None or address == vr_address:
                            if vr_address is None:
                                state.clear('vr')
                                emit(dict(event='vr_connected', address=address[0]))
                            state.receive('vr', message, time.monotonic())
                            vr_address = address
                    except (OSError, ValueError, UnicodeError) as error:
                        emit(dict(event='invalid_vr_packet', error=str(error)))
                now = time.monotonic()
                received = state.received['vr']
                if vr_address is not None and received is not None and now-received >= vr_timeout:
                    vr_address = None
                if now >= next_tick:
                    command, status = state.outputs(now)
                    if link is not None:
                        try:
                            link.send_command(command, now)
                        except (OSError, TimeoutError, ValueError) as error:
                            link.close()
                            link = None
                            state.clear('stm32')
                            next_open = now + .5
                            emit(dict(event='serial_error', error=str(error)))
                    if vr_address is not None:
                        try:
                            vr_socket.sendto(encode_datagram(vr_payload(status)), vr_address)
                        except BlockingIOError:
                            pass
                    if now >= next_log:
                        extra = dict(serial_session_ready=link is not None and link.session_ready,
                            serial_arm_ready=link is not None and link.arm_ready,
                            serial_applied_target_nm=(link.latest.applied_target_mnm / 1000
                                if link is not None and link.latest is not None and status['telemetry_fresh'] else None))
                        emit(dict(status, **extra, elapsed_s=round(now-start, 3)))
                        next_log = now + .5
                    next_tick = now + .02
                time.sleep(.002)
        finally:
            if link is not None:
                # Close and rely on STM32's independent command watchdog.
                # Do not assume serial close immediately changes brake current.
                link.close()
            emit(dict(event='closed'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True, help='COM5, /dev/ttyUSB0, /dev/pts/3, etc')
    parser.add_argument('--baudrate', type=int, default=115200)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--vr-port', type=int, default=9102)
    parser.add_argument('--duration', type=positive)
    parser.add_argument('--strategy', choices=STRATEGY_NAMES, default=DEFAULT_STRATEGY,
                        help='PC-local opponent policy (default: balanced)')
    add_console_arguments(parser)
    args = parser.parse_args()
    with reporter_from_args('PC 上位机（V1 串口 + Unity UDP）', args) as reporter:
        try:
            serve(args.port, args.host, args.vr_port, args.baudrate, args.duration,
                  reporter=reporter, strategy=args.strategy)
        except KeyboardInterrupt:
            pass
        except (OSError, RuntimeError) as error:
            parser.exit(2, f'PC serial bridge error: {error}\n')


if __name__ == '__main__':
    main()
