"""PC bridge: two TCP listeners, latest valid state, 50 Hz brake calculation."""
import argparse
from contextlib import ExitStack
import json
import math
import selectors
import socket
import time

from common.protocol import JsonSocket
from common.state import BridgeState


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
    peers, blocked_since = {}, {}
    with ExitStack() as stack:
        selector = stack.enter_context(selectors.DefaultSelector())
        ports = {}
        for role, port in [('stm32', stm_port), ('vr', vr_port)]:
            listener = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((host, port))
            listener.listen(2)
            listener.setblocking(False)
            selector.register(listener, selectors.EVENT_READ, ('listener', role))
            ports[role] = listener.getsockname()[1]
        log(dict(event='listening', host=host, stm_port=ports['stm32'], vr_port=ports['vr']))

        def drop(role, reason):
            peer = peers.pop(role, None)
            if peer:
                selector.unregister(peer.socket)
                peer.socket.close()
            state.clear(role)
            blocked_since.pop(role, None)
            log(dict(event='disconnected', role=role, reason=reason))

        start = time.monotonic()
        next_tick, next_log = start, start
        try:
            while duration is None or time.monotonic()-start < duration:
                events = selector.select(max(0., min(.02, next_tick-time.monotonic())))
                for key, _ in events:
                    kind, role = key.data
                    if kind == 'listener':
                        try:
                            sock, _ = key.fileobj.accept()
                        except BlockingIOError:
                            continue
                        if role in peers:
                            sock.close()
                            log(dict(event='rejected', role=role, reason='role already connected'))
                            continue
                        peers[role] = JsonSocket(sock)
                        state.clear(role)
                        selector.register(sock, selectors.EVENT_READ, ('peer', role))
                        log(dict(event='connected', role=role))
                    else:
                        try:
                            for message in peers[role].receive():
                                state.receive(role, message, time.monotonic())
                        except BlockingIOError:
                            pass
                        except (OSError, EOFError, ValueError, RecursionError) as error:
                            log(dict(event='rejected', role=role, reason=str(error)))
                            drop(role, str(error))
                now = time.monotonic()
                if now >= next_tick:
                    command, status = state.outputs(now)
                    for role, message in [('stm32', command), ('vr', status)]:
                        if role in peers:
                            peers[role].queue(message)
                    if now >= next_log:
                        log(dict(status, elapsed_s=round(now-start, 3)))
                        next_log = now+.5
                    next_tick = now+.02  # No burst of old commands after a scheduler stall.
                for role, peer in list(peers.items()):
                    try:
                        peer.flush()
                        if peer.pending:
                            blocked_since.setdefault(role, now)
                            if now-blocked_since[role] > .5:
                                drop(role, 'output blocked for 0.5 s')
                        else:
                            blocked_since.pop(role, None)
                    except (OSError, EOFError) as error:
                        drop(role, str(error))
        finally:
            # Best effort only: the device watchdog also covers a killed PC process.
            state.clear('vr')
            stop, _ = state.outputs(time.monotonic())
            for role, peer in peers.items():
                try:
                    if role == 'stm32':
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
