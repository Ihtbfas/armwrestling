"""PC bridge: STM32 TCP and VR UDP, latest valid state, 50 Hz control."""
import argparse
from contextlib import ExitStack
import json
import math
import selectors
import socket
import time

from common.protocol import JsonSocket, MAX_LINE, decode_datagram, encode_datagram, validate_incoming
from common.state import BridgeState, vr_payload


def log(message):
    # ASCII escapes keep logs writable on legacy Windows redirected consoles.
    print(json.dumps(message, ensure_ascii=True, allow_nan=False), flush=True)


def positive(value):
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError('must be finite and positive')
    return result


def serve(host, stm_port, vr_port, duration=None, telemetry_timeout=.25, vr_timeout=1.):
    state = BridgeState(telemetry_timeout, vr_timeout)
    peer = None  # The only TCP peer is the simulated STM32.
    blocked_since = None
    vr_address = None  # Reply to the most recent valid VR UDP sender.
    with ExitStack() as stack:
        selector = stack.enter_context(selectors.DefaultSelector())
        listener = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, stm_port))
        listener.listen(2)
        listener.setblocking(False)
        selector.register(listener, selectors.EVENT_READ, 'stm_listener')

        vr_socket = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
        vr_socket.bind((host, vr_port))
        vr_socket.setblocking(False)
        selector.register(vr_socket, selectors.EVENT_READ, 'vr_udp')
        log(dict(event='listening', host=host, stm_port=listener.getsockname()[1],
                 vr_port=vr_socket.getsockname()[1], stm_transport='tcp', vr_transport='udp'))

        def drop_stm(reason):
            nonlocal peer, blocked_since
            if peer is not None:
                selector.unregister(peer.socket)
                peer.socket.close()
                peer = None
            state.clear('stm32')
            blocked_since = None
            log(dict(event='disconnected', role='stm32', reason=reason))

        start = time.monotonic()
        next_tick = next_log = start
        try:
            while duration is None or time.monotonic()-start < duration:
                events = selector.select(max(0., min(.02, next_tick-time.monotonic())))
                for key, _ in events:
                    if key.data == 'stm_listener':
                        try:
                            sock, _ = listener.accept()
                        except BlockingIOError:
                            continue
                        if peer is not None:
                            sock.close()
                            log(dict(event='rejected', role='stm32', reason='role already connected'))
                            continue
                        peer = JsonSocket(sock)
                        state.clear('stm32')
                        selector.register(sock, selectors.EVENT_READ, 'stm_peer')
                        log(dict(event='connected', role='stm32', transport='tcp'))
                    elif key.data == 'vr_udp':
                        try:
                            packet, address = vr_socket.recvfrom(MAX_LINE+1)
                            message = decode_datagram(packet)
                            validate_incoming(message, 'vr')
                            if vr_address is not None and address != vr_address:
                                # A second local sender cannot overwrite the active VR state.
                                continue
                            if vr_address is None:
                                state.clear('vr')  # New UDP session may restart seq at zero.
                            state.receive('vr', message, time.monotonic())
                            if vr_address is None:
                                log(dict(event='connected', role='vr', transport='udp'))
                            vr_address = address
                        except (OSError, ValueError, UnicodeError) as error:
                            # A bad or out-of-order datagram never refreshes VR freshness.
                            log(dict(event='rejected', role='vr', reason=str(error)))
                    else:
                        try:
                            for message in peer.receive():
                                state.receive('stm32', message, time.monotonic())
                        except BlockingIOError:
                            pass
                        except (OSError, EOFError, ValueError, RecursionError) as error:
                            log(dict(event='rejected', role='stm32', reason=str(error)))
                            drop_stm(str(error))

                now = time.monotonic()
                received_at = state.received['vr']
                if vr_address is not None and received_at is not None and now-received_at >= vr_timeout:
                    # UDP has no disconnect notification; release the endpoint after timeout.
                    vr_address = None
                    log(dict(event='timeout', role='vr', reason='vr_timeout'))
                if now >= next_tick:
                    command, status = state.outputs(now)
                    if peer is not None:
                        peer.queue(command)
                    if vr_address is not None:
                        try:
                            vr_socket.sendto(encode_datagram(vr_payload(status)), vr_address)
                        except BlockingIOError:
                            pass  # Drop this status rather than block the control loop.
                        except OSError as error:
                            vr_address = None
                            state.clear('vr')
                            log(dict(event='rejected', role='vr', reason=str(error)))
                    if now >= next_log:
                        log(dict(status, elapsed_s=round(now-start, 3)))
                        next_log = now+.5
                    next_tick = now+.02
                if peer is not None:
                    try:
                        peer.flush()
                        if peer.pending:
                            blocked_since = now if blocked_since is None else blocked_since
                            if now-blocked_since > .5:
                                drop_stm('output blocked for 0.5 s')
                        else:
                            blocked_since = None
                    except (OSError, EOFError) as error:
                        drop_stm(str(error))
        finally:
            # Best effort only: the simulated device watchdog also covers killed PC.
            state.clear('vr')
            stop, _ = state.outputs(time.monotonic())
            if peer is not None:
                try:
                    peer.queue(stop)
                    peer.flush()
                except (OSError, EOFError):
                    pass
                peer.socket.close()
            log(dict(event='closed'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--stm-port', type=int, default=9101)
    parser.add_argument('--vr-port', type=int, default=9102)
    parser.add_argument('--telemetry-timeout', type=positive, default=.25)
    parser.add_argument('--vr-timeout', type=positive, default=1.)
    parser.add_argument('--duration', type=positive)
    args = parser.parse_args()
    try:
        serve(args.host, args.stm_port, args.vr_port, args.duration,
              args.telemetry_timeout, args.vr_timeout)
    except KeyboardInterrupt:
        pass
    except OSError as error:
        parser.exit(2, f'Connection/listen error: {error}\n')


if __name__ == '__main__':
    main()
