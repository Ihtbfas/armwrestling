"""Latest valid device/VR data; monotonic receipt times decide freshness."""
from common.control import brake_state, passive_torque
from common.characters import CHARACTERS, resolve_character
from common.protocol import validate_incoming
from common.strength_response import StrengthResponse


class BridgeState:
    def __init__(self, telemetry_timeout=0.25, vr_timeout=1.0):
        self.timeouts = {'stm32': telemetry_timeout, 'vr': vr_timeout}
        self.latest = {'stm32': None, 'vr': None}
        self.received = {'stm32': None, 'vr': None}
        self.seq = 0
        self._character = None  # First valid VR update chooses the initial round.
        self._strength = StrengthResponse()

    def clear(self, role):
        self.latest[role] = None
        self.received[role] = None
        self._strength.reset()

    def receive(self, role, message, now):
        validate_incoming(message, role)
        previous = self.latest[role]
        if previous is not None and message['seq'] <= previous['seq']:
            raise ValueError('seq must increase within a connection')
        if (any(stamp is not None and now-stamp >= self.timeouts[key]
                for key, stamp in self.received.items())
                or (role == 'vr' and message['match_state'] != 'running')
                or (role == 'stm32' and message['device_status'] != 'ready')):
            # Reset even when a pause/fault/expired packet is replaced before
            # outputs() gets its next tick. Rejected packets never reach here.
            self._strength.reset()
        if role == 'vr' and (self._character is None or message['match_state'] != 'running'):
            # Validate and check seq first. During running, even a changed ID or
            # a new UDP sender cannot silently change this round's parameters.
            self._character = resolve_character(message['character_id'])
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
        character = self._character or CHARACTERS['demo']
        parameters = character.brake_parameters
        requested = vr['character_id'] if fresh['vr'] else None
        base_target = target = actual = 0.0
        if reason == 'running':
            brake = brake_state(telemetry['angle_rad'], telemetry['velocity_rad_s'], parameters)
            base_target = brake.target_torque
            target = self._strength.update(base_target, character.rise_time_constant_s, now)
            actual = passive_torque(target, telemetry['velocity_rad_s'], parameters.speed_transition)
        else:
            self._strength.reset()
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
                      base_target_torque_nm=base_target,
                      character_rise_time_constant_s=character.rise_time_constant_s,
                      telemetry_seq=command['telemetry_seq'],
                      telemetry_fresh=fresh['stm32'], vr_fresh=fresh['vr'],
                      telemetry_age_ms=None if ages['stm32'] is None else round(ages['stm32']*1000, 1),
                      vr_age_ms=None if ages['vr'] is None else round(ages['vr']*1000, 1),
                      match_state=vr['match_state'] if fresh['vr'] else None,
                      character_id=requested,
                      active_character_id=character.character_id,
                      active_character_name=character.name,
                      character_fallback=requested is not None and requested not in CHARACTERS,
                      character_selection_deferred=(requested is not None and
                          resolve_character(requested).character_id != character.character_id),
                      character_stiffness_nm_per_rad=parameters.stiffness,
                      character_max_torque_nm=parameters.max_torque)
        self.seq += 1
        return command, status


def vr_payload(status):
    """Only the fields needed by the VR V0.1 interface are sent over UDP."""
    fields = ('v', 'type', 'seq', 'angle_rad', 'mode', 'reason')
    return {name: status[name] for name in fields}
