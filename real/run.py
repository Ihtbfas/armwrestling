"""Run the PC controller with STM32 V1.1 serial and VR UDP V0.1."""
import argparse
from contextlib import closing, nullcontext
import os
import select
import socket
import sys
import threading
import time

from common.bridge import log, positive
from common.characters import CHARACTERS
from common.console import add_console_arguments, reporter_from_args
from common.protocol import MAX_LINE, decode_datagram, encode_datagram, validate_incoming
from common.serial_protocol import FLAG_ACK_VALID, FLAG_ARM_READY
from common.state import BridgeState, vr_payload
from common.strategies import DEFAULT_STRATEGY, STRATEGY_NAMES
from real.experiment_log import ExperimentLog
from real.stm32_link import ACK_TIMEOUT_S, Stm32Link

STOP = dict(mode='stop', target_torque_nm=0., valid_for_ms=200)


def serve(port, host='127.0.0.1', vr_port=9102, baudrate=115200,
          duration=None, telemetry_timeout=.25, vr_timeout=1., reporter=None, *,
          strategy=DEFAULT_STRATEGY, monitor_only=False, vr_source_port=None,
          stop_event=None, diagnostic_interval=.5, experiment=None, hex_preview=False):
    emit = reporter or log
    trace = experiment.record if experiment is not None else None
    state = BridgeState(telemetry_timeout, vr_timeout, strategy=strategy)
    resume_required = not monitor_only
    last_vr_seq = -1
    link = vr_address = last_vr_seen = None
    recovery_reason = None
    start = time.monotonic()
    next_tick = next_log = next_open = start
    rate_from = None

    def require_resume(reason):
        nonlocal resume_required
        if monitor_only:
            return
        if not resume_required:
            emit(dict(event='debug_interlock', reason=reason))
        resume_required = True
        vr = state.latest['vr']
        if vr is not None and vr['match_state'] == 'running' and reason != 'vr_timeout':
            state.clear('vr')

    def best_effort_stop():
        try:
            link.send_command(STOP)
        except (OSError, ValueError):
            pass

    def drop_serial(now, error):
        nonlocal link, next_open, recovery_reason
        recovery_reason = 'serial_error'
        require_resume(recovery_reason)
        if link is not None:
            best_effort_stop()
            try:
                link.close()
            except OSError:
                pass
        link = None
        state.clear('stm32')
        next_open = now + .5
        emit(dict(event='serial_error', error=str(error), reason=recovery_reason))

    with closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as vr_socket:
        vr_socket.bind((host, vr_port))
        vr_socket.setblocking(False)
        meta = dict(event='listening', stm_transport='serial-v1.1', port=port, baudrate=baudrate,
                    vr_transport='udp', vr_port=vr_socket.getsockname()[1], configured_strategy=strategy,
                    monitor_only=monitor_only, experiment_log=trace is not None)
        emit(meta)
        if trace is not None:
            trace(dict(meta, type='experiment_config', pc_monotonic_s=start))
        try:
            while stop_event is None or not stop_event.is_set():
                now = time.monotonic()
                if duration is not None and now - start >= duration:
                    break
                if last_vr_seen is not None and now - last_vr_seen >= vr_timeout:
                    require_resume('vr_timeout')
                    vr_address = last_vr_seen = None
                    if vr_source_port is None:
                        last_vr_seq = -1
                if link is None and now >= next_open:
                    try:
                        link = Stm32Link.open(port, baudrate, trace=trace, telemetry_timeout=telemetry_timeout)
                        link.monitor_only = monitor_only
                        rate_from = None
                        emit(dict(event='serial_open', port=port, serial_session_id=_id(link.session_id)))
                    except OSError as error:
                        next_open = now + .5
                        recovery_reason = 'serial_error'
                        emit(dict(event='serial_unavailable', error=str(error)))

                if link is not None:
                    try:
                        was_ready, was_confirmed = link.session_ready, link.session_confirmed
                        previous_session = link.session_id
                        previous_stop = link.stop_target_zero
                        if link.matched_telemetry and not link.wire_fresh(now):
                            best_effort_stop()
                            link.start_session(now, 'telemetry_timeout')
                        elif link.sample_at is not None and not link.sample_fresh(now):
                            require_resume('sample_stale')
                        ack_age = link.ack_pending_age(now)
                        if ack_age is not None and ack_age >= ACK_TIMEOUT_S:
                            best_effort_stop()
                            link.start_session(now, 'ack_timeout')
                        link.poll(now)

                        if link.interruption is not None:
                            recovery_reason = link.interruption
                            link.interruption = None
                            require_resume(recovery_reason)
                            state.clear('stm32')
                            if previous_session != link.session_id:
                                emit(dict(event='serial_session_restart', reason=recovery_reason,
                                          serial_session_id=_id(link.session_id)))
                        if link.session_ready and (not was_ready or previous_session != link.session_id):
                            emit(dict(event='serial_session_ready', port=port,
                                      serial_session_id=_id(link.session_id)))
                        if link.stop_target_zero is False and (previous_stop is not False
                                                                 or previous_session != link.session_id):
                            emit(dict(event='serial_stop_nonzero', seq=link.last_ack_seq,
                                      applied_target_mnm=link.latest.applied_target_mnm))
                        if link.session_confirmed and (not was_confirmed or previous_session != link.session_id):
                            recovery_reason = None
                            emit(dict(event='serial_stop_confirmed', seq=link.last_ack_seq))
                        control = link.control_input(now)
                        if control is None:
                            if state.latest['stm32'] is not None:
                                state.clear('stm32')
                        elif state.latest['stm32'] is None or control['seq'] != state.latest['stm32']['seq']:
                            state.receive('stm32', control, now)
                        if not link.control_ready(now):
                            require_resume(link.reason(now))
                    except (OSError, ValueError) as error:
                        drop_serial(now, error)

                # UDP shape and single-client policy are unchanged. In both
                # formal VR and the GUI, a fresh waiting/paused is needed before
                # accepting running after a loss of serial or VR readiness.
                if select.select([vr_socket], [], [], 0)[0]:
                    try:
                        packet, address = vr_socket.recvfrom(MAX_LINE + 1)
                        expected = (vr_source_port is None or address == ('127.0.0.1', vr_source_port))
                        if not monitor_only and expected and (vr_address is None or address == vr_address):
                            message = validate_incoming(decode_datagram(packet), 'vr')
                            if message['seq'] <= last_vr_seq:
                                raise ValueError('VR seq must increase throughout the session')
                            last_vr_seq = message['seq']
                            if vr_address is None:
                                state.clear('vr')
                                emit(dict(event='vr_connected', address=address[0]))
                            vr_address, last_vr_seen = address, time.monotonic()
                            phase = message['match_state']
                            if phase in ('waiting', 'paused') and link is not None and link.control_ready(now):
                                resume_required = False
                            if phase != 'running' or not resume_required:
                                state.receive('vr', message, last_vr_seen)
                    except (OSError, ValueError) as error:
                        emit(dict(event='invalid_vr_packet', error=str(error)))

                now = time.monotonic()
                if now >= next_tick:
                    command, status = state.outputs(now)
                    if link is not None:
                        try:
                            link.send_command(command, now)
                        except (OSError, ValueError) as error:
                            drop_serial(now, error)
                            _, status = state.outputs(time.monotonic())
                    if vr_address is not None:
                        try:
                            vr_socket.sendto(encode_datagram(vr_payload(status)), vr_address)
                        except BlockingIOError:
                            pass
                    if now >= next_log:
                        rate = tx_rate = None
                        if link is not None:
                            if rate_from is not None:
                                before, rx_count, tx_count = rate_from
                                if link.wire_fresh(now):
                                    rate = round((link.telemetry_count - rx_count) / (now - before), 1)
                                tx_rate = round((link.tx_frames - tx_count) / (now - before), 1)
                            rate_from = (now, link.telemetry_count, link.tx_frames)
                        else:
                            rate_from = None
                        if link is None or link.measured_velocity is None:
                            status.update(velocity_rad_s=None, qdot_rad_s=None, brake_torque_nm=None)
                        if link is not None and link.sample_fresh(now):
                            status['telemetry_age_ms'] = _age_ms(link.sample_at, now)
                        extra = _serial_diagnostics(link, rate, now, recovery_reason, experiment)
                        params = CHARACTERS[status['active_character_id']].brake_parameters
                        extra.update(monitor_only=monitor_only, debug_resume_required=resume_required,
                                     serial_free_boundary_rad=params.free_boundary,
                                     serial_forward_sign=params.forward_sign,
                                     serial_initial_angle_rad=params.initial_angle, serial_tx_rate_hz=tx_rate)
                        if hex_preview and link is not None:
                            extra.update(serial_rx_hex=link.rx_tail.hex(' ').upper() or None,
                                         serial_tx_hex=link.tx_tail.hex(' ').upper() or None)
                        emit(dict(status, **extra, elapsed_s=round(now-start, 3)))
                        next_log = now + diagnostic_interval
                    next_tick = now + .02
                time.sleep(.002)
        finally:
            if link is not None:
                best_effort_stop()
                link.close()
            emit(dict(event='closed'))


def _id(session_id):
    return f'0x{session_id:016X}'


def _age_ms(stamp, now):
    return round(max(0., now - stamp) * 1000, 1) if stamp is not None else None


def _serial_diagnostics(link, rate, now, recovery, experiment):
    raw = link.latest if link is not None and link.wire_fresh(now) else None
    reason = link.reason(now) if link is not None else 'serial_error'
    if recovery and reason == 'waiting_handshake':
        reason = recovery
    verified_ack = (raw is not None and raw.session_id == link.session_id
                    and raw.status_flags & FLAG_ACK_VALID and raw.last_command_seq == link.last_ack_seq)
    pending_age = link.ack_pending_age(now) if link is not None else None
    return dict(
        serial_open=link is not None,
        serial_session_ready=link is not None and link.session_ready,
        serial_session_confirmed=link is not None and link.session_confirmed,
        serial_stop_acknowledged=link is not None and link.stop_target_zero is not None,
        serial_stop_target_zero=link.stop_target_zero if link is not None else None,
        serial_session_id=_id(link.session_id) if link is not None else None,
        serial_peer_session_id=_id(raw.session_id) if raw is not None else None,
        serial_arm_ready=raw is not None and bool(raw.status_flags & FLAG_ARM_READY),
        serial_enable_ready=link is not None and link.control_ready(now),
        serial_status_reason=reason,
        serial_status_flags=raw.status_flags if raw is not None else None,
        serial_telemetry_rate_hz=rate,
        serial_telemetry_seq=link.last_wire_seq if raw is not None else None,
        serial_wire_fresh=raw is not None,
        serial_wire_age_ms=_age_ms(link.last_wire_at, now) if link is not None else None,
        serial_mcu_sample_ms=raw.mcu_sample_ms if raw is not None else None,
        serial_sample_state=link.sample_state if raw is not None else 'unavailable',
        serial_sample_age_ms=_age_ms(link.sample_at, now) if link is not None else None,
        serial_raw_angle_rad=raw.angle_rad if raw is not None else None,
        serial_raw_velocity_rad_s=raw.velocity_rad_s if raw is not None else None,
        serial_force_n=raw.force_newtons if raw is not None else None,
        serial_last_ack_seq=link.last_ack_seq if verified_ack else None,
        serial_reported_ack_seq=(raw.last_command_seq
                                 if raw is not None and raw.status_flags & FLAG_ACK_VALID else None),
        serial_ack_progress_age_ms=_age_ms(link.last_ack_at, now) if link is not None else None,
        serial_ack_pending_age_ms=round(pending_age * 1000, 1) if pending_age is not None else None,
        serial_ack_pending=len(link.outstanding) if link is not None else 0,
        serial_ack_rejected=link.ack_rejected if link is not None else None,
        serial_applied_target_nm=raw.applied_target_mnm / 1000 if raw is not None else None,
        serial_last_sent=link.last_sent_command if link is not None else None,
        serial_last_sent_age_ms=_age_ms(link.last_sent_at, now) if link is not None else None,
        serial_rx_bytes=link.rx_bytes if link is not None else None,
        serial_tx_bytes=link.tx_bytes if link is not None else None,
        serial_valid_frames=link.rx_frames if link is not None else None,
        serial_tx_frames=link.tx_frames if link is not None else None,
        serial_diagnostic_telemetry=link.telemetry_count if link is not None else None,
        serial_matched_telemetry=link.matched_samples if link is not None else None,
        serial_valid_telemetry=link.valid_samples if link is not None else None,
        serial_sync_discards=link.parser.sync_discards if link is not None else None,
        serial_rx_age_ms=_age_ms(link.last_rx_at, now) if link is not None else None,
        serial_tx_age_ms=_age_ms(link.last_tx_at, now) if link is not None else None,
        serial_crc_errors=link.parser.crc_errors if link is not None else None,
        serial_header_errors=link.parser.header_errors if link is not None else None,
        serial_frame_timeouts=link.parser.timeouts if link is not None else None,
        serial_sequence_drops=link.sequence_drops if link is not None else None,
        serial_session_drops=link.session_drops if link is not None else None,
        serial_payload_errors=link.payload_errors if link is not None else None,
        serial_experiment_dropped=experiment.dropped if experiment is not None else None,
        serial_experiment_error=experiment.error if experiment is not None else None,
    )


def _watch_stdin():
    stopped = threading.Event()

    def wait_for_eof():
        try:
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
    parser.add_argument('--hex-preview', action='store_true', help='include bounded RX/TX tails in live JSON only')
    parser.add_argument('--experiment-log', help='optional bounded raw V1.1 telemetry/command JSONL trace')
    add_console_arguments(parser)
    args = parser.parse_args()
    if args.vr_source_port is not None and not 1 <= args.vr_source_port <= 65535:
        parser.error('--vr-source-port must be in [1, 65535]')
    stop_event = _watch_stdin() if args.exit_on_stdin_eof else None
    try:
        with reporter_from_args('PC 上位机（V1.1 串口 + Unity UDP）', args) as reporter:
            with (ExperimentLog(args.experiment_log) if args.experiment_log else nullcontext()) as experiment:
                try:
                    serve(args.port, args.host, args.vr_port, args.baudrate, args.duration,
                          reporter=reporter, strategy=args.strategy, monitor_only=args.monitor_only,
                          vr_source_port=args.vr_source_port, stop_event=stop_event,
                          diagnostic_interval=args.diagnostic_interval, experiment=experiment, hex_preview=args.hex_preview)
                except KeyboardInterrupt:
                    pass
            if experiment is not None and (experiment.error or experiment.dropped):
                reporter(dict(event='experiment_log_incomplete', dropped=experiment.dropped,
                              error=experiment.error))
    except KeyboardInterrupt:
        pass
    except (OSError, RuntimeError) as error:
        parser.exit(2, f'PC serial bridge error: {error}\n')


if __name__ == '__main__':
    main()
