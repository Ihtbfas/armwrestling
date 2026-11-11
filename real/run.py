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
from real.stm32_link import Stm32Link


def serve(port, host='127.0.0.1', vr_port=9102, baudrate=115200,
          duration=None, telemetry_timeout=.25, vr_timeout=1.):
    state = BridgeState(telemetry_timeout, vr_timeout)
    link = None
    vr_address = None
    start = time.monotonic()
    next_tick = next_log = next_open = start
    with closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as vr_socket:
        vr_socket.bind((host, vr_port))
        vr_socket.setblocking(False)
        log(dict(event='listening', stm_transport='serial-v1', port=port,
                 vr_transport='udp', vr_port=vr_socket.getsockname()[1]))
        try:
            while duration is None or time.monotonic() - start < duration:
                now = time.monotonic()
                if link is None and now >= next_open:
                    try:
                        link = Stm32Link.open(port, baudrate)
                        state.clear('stm32')
                        log(dict(event='serial_open', port=port))
                    except OSError as error:
                        next_open = now + .5
                        log(dict(event='serial_unavailable', error=str(error)))
                if link is not None:
                    try:
                        for message in link.poll(now):
                            state.receive('stm32', message, time.monotonic())
                    except (OSError, TimeoutError, ValueError) as error:
                        link.close()
                        link = None
                        state.clear('stm32')
                        next_open = now + .5
                        log(dict(event='serial_error', error=str(error)))

                # One latest UDP VR sender, exactly as in common.bridge.
                if select.select([vr_socket], [], [], 0)[0]:
                    try:
                        packet, address = vr_socket.recvfrom(MAX_LINE+1)
                        message = decode_datagram(packet)
                        validate_incoming(message, 'vr')
                        if vr_address is None or address == vr_address:
                            if vr_address is None:
                                state.clear('vr')
                                log(dict(event='vr_connected', address=address[0]))
                            state.receive('vr', message, time.monotonic())
                            vr_address = address
                    except (OSError, ValueError, UnicodeError) as error:
                        log(dict(event='invalid_vr_packet', error=str(error)))
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
                            log(dict(event='serial_error', error=str(error)))
                    if vr_address is not None:
                        try:
                            vr_socket.sendto(encode_datagram(vr_payload(status)), vr_address)
                        except BlockingIOError:
                            pass
                    if now >= next_log:
                        log(dict(status, elapsed_s=round(now-start, 3)))
                        next_log = now + .5
                    next_tick = now + .02
                time.sleep(.002)
        finally:
            if link is not None:
                # Close and rely on STM32's independent command watchdog.
                # Do not assume serial close immediately changes brake current.
                link.close()
            log(dict(event='closed'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True, help='COM5, /dev/ttyUSB0, /dev/pts/3, etc')
    parser.add_argument('--baudrate', type=int, default=115200)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--vr-port', type=int, default=9102)
    parser.add_argument('--duration', type=positive)
    args = parser.parse_args()
    try:
        serve(args.port, args.host, args.vr_port, args.baudrate, args.duration)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
