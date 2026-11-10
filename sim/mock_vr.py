"""VR TCP client: character parameters/match heartbeat in, latest PC state out."""
import argparse
import select
import socket
import time

from common.bridge import log, positive
from common.protocol import JsonSocket, envelope, validate_incoming


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=9102)
    parser.add_argument('--stiffness', type=float, default=2.)
    parser.add_argument('--max-torque', type=float, default=.8)
    parser.add_argument('--match-state', choices=['waiting', 'running', 'paused', 'finished'], default='running')
    parser.add_argument('--demo-states', action='store_true', help='alternate running 3 s / paused 1 s')
    parser.add_argument('--duration', type=positive)
    args = parser.parse_args()
    message = dict(v=1, type='vr_update', seq=0, character_id='demo', match_state=args.match_state,
                   stiffness_nm_per_rad=args.stiffness, max_torque_nm=args.max_torque)
    try:
        validate_incoming(message, 'vr')
    except ValueError as error:
        parser.error(str(error))
    peer = None
    start = next_send = next_log = next_connect = time.monotonic()
    try:
        while args.duration is None or time.monotonic()-start < args.duration:
            now = time.monotonic()
            if peer is None and now >= next_connect:
                try:
                    peer = JsonSocket(socket.create_connection((args.host, args.port), timeout=.2))
                    message['seq'] = 0
                    next_send = now
                    log(dict(event='connected', role='vr'))
                except OSError:
                    next_connect = now+.5
            if peer is not None:
                try:
                    if args.demo_states:
                        message['match_state'] = 'running' if (now-start)%4 < 3 else 'paused'
                    if now >= next_send:
                        peer.queue(message)
                        message['seq'] += 1
                        next_send = now+.2  # Heartbeat even when match/character do not change.
                    peer.flush()
                    if select.select([peer.socket], [], [], 0)[0]:
                        for status in peer.receive():
                            envelope(status, 'status')
                            if now >= next_log:
                                log(dict(status, elapsed_s=round(now-start, 3)))
                                next_log = now+.5
                except (OSError, EOFError, ValueError, RecursionError) as error:
                    peer.socket.close()
                    peer = None
                    next_connect = now+.5
                    log(dict(event='disconnected', role='vr', reason=str(error)))
            time.sleep(.005)
    except KeyboardInterrupt:
        pass
    finally:
        if peer is not None:
            peer.socket.close()


if __name__ == '__main__':
    main()
