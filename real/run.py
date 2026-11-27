"""Run the same PC controller with STM32 V1 serial + VR UDP.

Use a Linux PTY created by sim.mock_stm32_serial, or a real COM port.
"""
import argparse
from contextlib import closing
import os
import select
import socket
import sys
import threading
import time

from common.bridge import log, positive
from common.protocol import MAX_LINE, decode_datagram, encode_datagram, validate_incoming
from common.serial_protocol import FLAG_ACK_VALID
from common.state import BridgeState, vr_payload
from common.console import add_console_arguments, reporter_from_args
from common.strategies import DEFAULT_STRATEGY, STRATEGY_NAMES
from real.stm32_link import Stm32Link


def serve(port, host='127.0.0.1', vr_port=9102, baudrate=115200,
          duration=None, telemetry_timeout=.25, vr_timeout=1., reporter=None, *,
          strategy=DEFAULT_STRATEGY, monitor_only=False, vr_source_port=None,
          stop_event=None, diagnostic_interval=.5):
    emit = reporter or log
    state = BridgeState(telemetry_timeout, vr_timeout, strategy=strategy)
    debug_match = vr_source_port is not None and not monitor_only
    supervised = monitor_only or debug_match or stop_event is not None
    debug_ready = False
    last_debug_seq = -1
    link = None
    vr_address = None
    start = time.monotonic()
    next_tick = next_log = next_open = start
    opened_at = last_telemetry_at = None
    rate_from = None

    def require_resume(reason):
        nonlocal debug_ready
        if not debug_match or not debug_ready:
            return
        debug_ready = False
        if state.latest['vr']['match_state'] == 'running' and reason != 'vr_timeout':
            state.clear('vr')
        emit(dict(event='debug_interlock', reason=reason))

    def close_serial():
        if supervised:
            try:
                link.send_command(dict(mode='stop', target_torque_nm=0., valid_for_ms=200))
            except (OSError, TimeoutError, ValueError):
                pass
        # STM32's independent command watchdog covers a lost connection.
        link.close()

    def drop_serial(now, event, **details):
        nonlocal link, next_open
        require_resume(details.get('reason', event))
        close_serial()
        link = None
        state.clear('stm32')
        next_open = now + .5
        emit(dict(event=event, **details))

    with closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as vr_socket:
        vr_socket.bind((host, vr_port))
        vr_socket.setblocking(False)
        emit(dict(event='listening', stm_transport='serial-v1', port=port, baudrate=baudrate,
                 vr_transport='udp', vr_port=vr_socket.getsockname()[1], configured_strategy=strategy,
                 monitor_only=monitor_only))
        try:
            while stop_event is None or not stop_event.is_set():
                now = time.monotonic()
                if duration is not None and now - start >= duration:
                    break
                if debug_match:
                    # Check stale data before a newly arrived packet can replace it.
                    vr_received = state.received['vr']
                    if vr_received is not None and now - vr_received >= vr_timeout:
                        require_resume('vr_timeout')
                    if last_telemetry_at is not None and now - last_telemetry_at >= telemetry_timeout:
                        require_resume('telemetry_timeout')
                if link is None and now >= next_open:
                    try:
                        link = Stm32Link.open(port, baudrate, write_timeout=.02 if supervised else 0)
                        link.monitor_only = monitor_only
                        opened_at, last_telemetry_at = now, None
                        rate_from = None
                        state.clear('stm32')
                        emit(dict(event='serial_open', port=port, serial_session_id=f'0x{link.session_id:016X}'))
                    except OSError as error:
                        next_open = now + .5
                        emit(dict(event='serial_unavailable', error=str(error)))
                if link is not None:
                    try:
                        was_session_ready = link.session_ready
                        for message in link.poll(now):
                            if message['device_status'] != 'ready':
                                require_resume('device_fault')
                            state.receive('stm32', message, time.monotonic())
                            last_telemetry_at = time.monotonic()
                        if link.session_ready and not was_session_ready:
                            emit(dict(event='serial_session_ready', port=port))
                    except (OSError, TimeoutError, ValueError) as error:
                        drop_serial(now, 'serial_error', error=str(error))
                if link is not None and (
                    (last_telemetry_at is not None and now - last_telemetry_at >= telemetry_timeout)
                    or (last_telemetry_at is None and now - opened_at >= max(1., telemetry_timeout))
                ):
                    # A virtual COM peer can disappear without closing our COM
                    # handle. A fresh session is required when it comes back.
                    drop_serial(now, 'serial_timeout', reason='telemetry_timeout')

                # One latest UDP VR sender, exactly as in common.bridge.
                if select.select([vr_socket], [], [], 0)[0]:
                    try:
                        packet, address = vr_socket.recvfrom(MAX_LINE+1)
                        if not monitor_only and (not debug_match or address == ('127.0.0.1', vr_source_port)):
                            message = validate_incoming(decode_datagram(packet), 'vr')
                            if debug_match:
                                if message['seq'] <= last_debug_seq:
                                    raise ValueError('debug VR seq must increase throughout the session')
                                last_debug_seq = message['seq']
                            if vr_address is None or address == vr_address:
                                received_at = time.monotonic()
                                match = message['match_state']
                                if not debug_match or debug_ready or match != 'running':
                                    if vr_address is None:
                                        state.clear('vr')
                                        emit(dict(event='vr_connected', address=address[0]))
                                    state.receive('vr', message, received_at)
                                    vr_address = address
                                    if (debug_match and match in ('waiting', 'paused') and link is not None
                                            and link.session_ready and link.arm_ready and last_telemetry_at is not None
                                            and received_at - last_telemetry_at < telemetry_timeout):
                                        debug_ready = True
                    except (OSError, ValueError, UnicodeError) as error:
                        emit(dict(event='invalid_vr_packet', error=str(error)))
                now = time.monotonic()
                received = state.received['vr']
                if vr_address is not None and received is not None and now-received >= vr_timeout:
                    vr_address = None
                    require_resume('vr_timeout')
                if now >= next_tick:
                    command, status = state.outputs(now)
                    if link is not None:
                        try:
                            link.send_command(command, now)
                        except (OSError, TimeoutError, ValueError) as error:
                            drop_serial(now, 'serial_error', error=str(error))
                            _, status = state.outputs(time.monotonic())
                    if vr_address is not None:
                        try:
                            vr_socket.sendto(encode_datagram(vr_payload(status)), vr_address)
                        except BlockingIOError:
                            pass
                    if now >= next_log:
                        telemetry_rate = None
                        latest = link.latest if link is not None and status['telemetry_fresh'] else None
                        if link is not None:
                            if latest is not None and rate_from is not None:
                                before, count = rate_from
                                telemetry_rate = round((link.telemetry_count - count) / (now - before), 1)
                            rate_from = (now, link.telemetry_count)
                        extra = _serial_diagnostics(link, latest, telemetry_rate, now)
                        extra.update(monitor_only=monitor_only, debug_resume_required=debug_match and not debug_ready)
                        emit(dict(status, **extra, elapsed_s=round(now-start, 3)))
                        next_log = now + diagnostic_interval
                    next_tick = now + .02
                time.sleep(.002)
        finally:
            if link is not None:
                close_serial()
            emit(dict(event='closed'))


def _serial_diagnostics(link, latest, telemetry_rate, now):
    return dict(
        serial_open=link is not None,
        serial_session_ready=link is not None and link.session_ready,
        serial_session_id=f'0x{link.session_id:016X}' if link is not None else None,
        serial_arm_ready=latest is not None and latest.can_enable,
        serial_status_flags=latest.status_flags if latest is not None else None,
        serial_telemetry_rate_hz=telemetry_rate,
        serial_force_n=latest.force_newtons if latest is not None else None,
        serial_last_ack_seq=(latest.last_command_seq
                             if latest is not None and latest.status_flags & FLAG_ACK_VALID else None),
        serial_applied_target_nm=latest.applied_target_mnm / 1000 if latest is not None else None,
        serial_last_sent=link.last_sent_command if link is not None else None,
        serial_last_sent_age_ms=(round(max(0., now - link.last_sent_at) * 1000, 1)
                                 if link is not None and link.last_sent_at is not None else None),
    )


def _watch_stdin():
    stopped = threading.Event()

    def wait_for_eof():
        try:
            # A daemon blocked in BufferedReader can hold its lock during
            # interpreter shutdown, for example when the user presses Ctrl+C.
            while os.read(sys.stdin.fileno(), 4096):
                pass
        except (OSError, ValueError):
            pass
        finally:
            stopped.set()

    threading.Thread(target=wait_for_eof, daemon=True).start()
    return stopped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True, help='COM5, /dev/ttyUSB0, /dev/pts/3, etc')
    parser.add_argument('--baudrate', type=int, default=115200)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--vr-port', type=int, default=9102)
    parser.add_argument('--duration', type=positive)
    parser.add_argument('--strategy', choices=STRATEGY_NAMES, default=DEFAULT_STRATEGY,
                        help='PC-local opponent policy (default: balanced)')
    parser.add_argument('--monitor-only', action='store_true', help='ignore VR and only send STOP after handshake')
    parser.add_argument('--vr-source-port', type=int, help='accept GUI VR updates only from this localhost UDP port')
    parser.add_argument('--exit-on-stdin-eof', action='store_true', help='close when the owning GUI closes the input pipe')
    parser.add_argument('--diagnostic-interval', type=positive, default=.5,
                        help='seconds between local status snapshots (default: 0.5)')
    add_console_arguments(parser)
    args = parser.parse_args()
    if args.vr_source_port is not None and not 1 <= args.vr_source_port <= 65535:
        parser.error('--vr-source-port must be in [1, 65535]')
    stop_event = _watch_stdin() if args.exit_on_stdin_eof else None
    with reporter_from_args('PC 上位机（V1 串口 + Unity UDP）', args) as reporter:
        try:
            serve(args.port, args.host, args.vr_port, args.baudrate, args.duration,
                  reporter=reporter, strategy=args.strategy, monitor_only=args.monitor_only,
                  vr_source_port=args.vr_source_port, stop_event=stop_event,
                  diagnostic_interval=args.diagnostic_interval)
        except KeyboardInterrupt:
            pass
        except (OSError, RuntimeError) as error:
            parser.exit(2, f'PC serial bridge error: {error}\n')


if __name__ == '__main__':
    main()
