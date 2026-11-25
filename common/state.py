"""Latest valid device/VR data; monotonic receipt times decide freshness."""
from common.control import brake_state, passive_torque
from common.characters import CHARACTERS, resolve_character
from common.protocol import validate_incoming
from common.strength_response import StrengthResponse
from common.fatigue import FatigueState
from common.resistance_profiles import DEFAULT_RESISTANCE_PROFILE, RESISTANCE_PROFILES, profile_target
from common.strategies import ActiveMatchClock, DEFAULT_STRATEGY, STRATEGY_NAMES, strategy_scale


class BridgeState:
    def __init__(self, telemetry_timeout=0.25, vr_timeout=1.0, *, strategy=DEFAULT_STRATEGY,
                 resistance_profile=DEFAULT_RESISTANCE_PROFILE):
        if strategy not in STRATEGY_NAMES:
            raise ValueError(f'unknown strategy: {strategy}')
        if resistance_profile not in RESISTANCE_PROFILES:
            raise ValueError(f'unknown resistance profile: {resistance_profile}')
        self.timeouts = {'stm32': telemetry_timeout, 'vr': vr_timeout}
        self.latest = {'stm32': None, 'vr': None}
        self.received = {'stm32': None, 'vr': None}
        self.seq = 0
        self._character = None  # First valid VR update chooses the initial round.
        self._strength = StrengthResponse()
        self._fatigue = FatigueState()
        self._strategy = strategy
        self._resistance_profile = resistance_profile
        self._match_clock = ActiveMatchClock()

    def clear(self, role):
        self.latest[role] = None
        self.received[role] = None
        self._strength.reset()
        self._fatigue.freeze()
        self._match_clock.freeze()

    def receive(self, role, message, now):
        validate_incoming(message, role)
        previous = self.latest[role]
        if previous is not None and message['seq'] <= previous['seq']:
            raise ValueError('seq must increase within a connection')
        expired = any(stamp is not None and now-stamp >= self.timeouts[key]
                      for key, stamp in self.received.items())
        if (expired
                or (role == 'vr' and message['match_state'] != 'running')
                or (role == 'stm32' and message['device_status'] != 'ready')):
            # Reset even when a pause/fault/expired packet is replaced before
            # outputs() gets its next tick. Rejected packets never reach here.
            self._strength.reset()
        if (expired or (role == 'stm32' and message['device_status'] != 'ready')
                or (role == 'vr' and (previous is None or
                    previous['match_state'] != message['match_state']))):
            # Do not integrate across a state transition or stale input. Repeated
            # paused heartbeats keep their clock so valid rest can recover.
            self._fatigue.freeze()
            self._match_clock.freeze()
        if role == 'vr' and (self._character is None or message['match_state'] != 'running'):
            # Validate and check seq first. During running, even a changed ID or
            # a new UDP sender cannot silently change this round's parameters.
            selected = resolve_character(message['character_id'])
            if (self._character is None or selected.character_id != self._character.character_id
                    or message['match_state'] == 'waiting'):
                self._fatigue.reset()
                self._match_clock.reset()
            self._character = selected
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
        base_target = requested_target = eligible = target = actual = load_proxy = scale = 0.0
        strategy = 'demo' if character.character_id == 'demo' else self._strategy
        resistance_profile = DEFAULT_RESISTANCE_PROFILE if strategy == 'demo' else self._resistance_profile
        fatigue_parameters = character.fatigue_parameters
        if reason == 'running':
            brake = brake_state(telemetry['angle_rad'], telemetry['velocity_rad_s'], parameters)
            base_target = profile_target(resistance_profile, brake, parameters)
            self._match_clock.advance(now, StrengthResponse.MAX_CONTINUOUS_GAP_S)
            scale = strategy_scale(strategy, brake.relative_angle, parameters.free_boundary,
                                   self._match_clock.elapsed)
            requested_target = base_target*scale
            # Equivalent load only, not measured muscle activation/user force.
            load_proxy = min(1., max(0., requested_target/max(parameters.max_torque, 1e-9)))
            self._fatigue.update(load_proxy, fatigue_parameters, now)
            capacity = 1-fatigue_parameters.fatigue_loss_fraction*self._fatigue.value
            eligible = requested_target*capacity
            target = self._strength.update(eligible, character.rise_time_constant_s, now)
            actual = passive_torque(target, telemetry['velocity_rad_s'], parameters.speed_transition)
        else:
            self._strength.reset()
            self._match_clock.freeze()
            if reason == 'match_paused':
                self._fatigue.update(0., fatigue_parameters, now)
            elif reason == 'match_waiting':
                self._fatigue.reset()
                self._match_clock.reset()
            else:
                # Finished retains the round; faults have unknown mechanical load.
                self._fatigue.freeze()
        capacity = 1-fatigue_parameters.fatigue_loss_fraction*self._fatigue.value
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
                      resistance_profile=resistance_profile,
                      strategy=strategy, strategy_scale=scale,
                      requested_target_torque_nm=requested_target,
                      active_match_time_s=self._match_clock.elapsed,
                      eligible_target_torque_nm=eligible,
                      fatigue_level=self._fatigue.value, fatigue_capacity=capacity,
                      fatigue_load_proxy=load_proxy,
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
