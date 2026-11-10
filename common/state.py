"""Latest valid device/VR data; monotonic receipt times decide freshness."""
from common.control import BrakeParameters, brake_state
from common.protocol import validate_incoming


class BridgeState:
    def __init__(self, telemetry_timeout=0.25, vr_timeout=1.0):
        self.timeouts = {'stm32': telemetry_timeout, 'vr': vr_timeout}
        self.latest = {'stm32': None, 'vr': None}
        self.received = {'stm32': None, 'vr': None}
        self.seq = 0

    def clear(self, role):
        self.latest[role] = None
        self.received[role] = None

    def receive(self, role, message, now):
        validate_incoming(message, role)
        previous = self.latest[role]
        if previous is not None and message['seq'] <= previous['seq']:
            raise ValueError('seq must increase within a connection')
        self.latest[role] = dict(message)
        self.received[role] = now

    def outputs(self, now):
        telemetry, vr = self.latest['stm32'], self.latest['vr']
        ages = {role: None if stamp is None else max(0., now-stamp)
                for role, stamp in self.received.items()}
        fresh = {role: age is not None and age < self.timeouts[role]
                 for role, age in ages.items()}
        if telemetry is None:
            reason = 'telemetry_missing'
        elif not fresh['stm32']:
            reason = 'telemetry_timeout'
        elif telemetry['device_status'] != 'ready':
            reason = 'device_fault'
        elif vr is None:
            reason = 'vr_missing'
        elif not fresh['vr']:
            reason = 'vr_timeout'
        elif vr['match_state'] != 'running':
            reason = 'match_'+vr['match_state']
        else:
            reason = 'running'
        parameters = BrakeParameters()
        target = actual = 0.0
        if reason == 'running':
            brake = brake_state(telemetry['angle_rad'], telemetry['velocity_rad_s'], parameters)
            target, actual = brake.target_torque, brake.applied_torque
        mode = 'enabled' if reason == 'running' else 'stop'
        command = dict(v=1, type='brake_command', seq=self.seq, mode=mode, reason=reason,
                       target_torque_nm=target, brake_torque_nm=actual, valid_for_ms=200,
                       speed_transition_rad_s=parameters.speed_transition,
                       telemetry_seq=telemetry['seq'] if fresh['stm32'] else None)
        status = dict(v=1, type='status', seq=self.seq, mode=mode, reason=reason,
                      angle_rad=telemetry['angle_rad'] if fresh['stm32'] else None,
                      velocity_rad_s=telemetry['velocity_rad_s'] if fresh['stm32'] else None,
                      q_rad=-telemetry['angle_rad'] if fresh['stm32'] else None,
                      target_torque_nm=target, brake_torque_nm=actual,
                      telemetry_seq=command['telemetry_seq'],
                      telemetry_fresh=fresh['stm32'], vr_fresh=fresh['vr'],
                      telemetry_age_ms=None if ages['stm32'] is None else round(ages['stm32']*1000, 1),
                      vr_age_ms=None if ages['vr'] is None else round(ages['vr']*1000, 1),
                      match_state=vr['match_state'] if fresh['vr'] else None,
                      character_id=vr['character_id'] if fresh['vr'] else None)
        self.seq += 1
        return command, status


def vr_payload(status):
    """Only the fields needed by the VR V0.1 interface are sent over UDP."""
    fields = ('v', 'type', 'seq', 'angle_rad', 'mode', 'reason')
    return {name: status[name] for name in fields}
