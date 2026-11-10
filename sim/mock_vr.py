"""VR UDP client: send character/match heartbeat, receive latest PC state."""
import argparse
import select
import socket
import time

from common.bridge import log, positive
from common.protocol import MAX_LINE, decode_datagram, encode_datagram, envelope, validate_incoming


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=9102)
    parser.add_argument('--match-state', choices=['waiting', 'running', 'paused', 'finished'], default='running')
    parser.add_argument('--demo-states', action='store_true', help='alternate running 3 s / paused 1 s')
    parser.add_argument('--duration', type=positive)
    args = parser.parse_args()
    message = dict(v=1, type='vr_update', seq=0, character_id='demo',
                   match_state=args.match_state)
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
                    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    peer.connect((args.host, args.port))
                    peer.setblocking(False)
                    message['seq'] = 0
                    next_send = now
                    log(dict(event='udp_client_open', role='vr'))
                except OSError:
                    if peer is not None:
                        peer.close()
                    peer = None
                    next_connect = now+.5
            if peer is not None:
                try:
                    if args.demo_states:
                        message['match_state'] = 'running' if (now-start)%4 < 3 else 'paused'
                    if now >= next_send:
                        payload = encode_datagram(message)
                        if peer.send(payload) != len(payload):
                            raise OSError('incomplete UDP send')
                        message['seq'] += 1
                        next_send = now+.2
                    if select.select([peer], [], [], 0)[0]:
                        status = decode_datagram(peer.recv(MAX_LINE+1))
                        envelope(status, 'status')
                        if now >= next_log:
                            log(dict(status, elapsed_s=round(now-start, 3)))
                            next_log = now+.5
                except BlockingIOError:
                    pass  # Retry on the next loop; heartbeat is not considered sent.
                except (OSError, ValueError, UnicodeError) as error:
                    peer.close()
                    peer = None
                    next_connect = time.monotonic()+.5
                    log(dict(event='disconnected', role='vr', reason=str(error)))
            time.sleep(.005)
    except KeyboardInterrupt:
        pass
    finally:
        if peer is not None:
            peer.close()


if __name__ == '__main__':
    main()
