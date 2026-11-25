"""Reproduce the V0.5-B advance and matched-start return comparisons."""
import argparse
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.state import BridgeState
from sim.mock_stm32 import INPUT_SIGN, STEP_SECONDS, SimulatedDevice, parse_sequence
from v04_compare import SEQUENCE, WIN_THRESHOLD_RAD


CHARACTER = 'person_02'
AMPLITUDES = (.045, .065, .09, .12, .18)
DOWN_DURATIONS = (.66, 1.32)
RETURN_STARTS = (.10, .25, .40)
RETURN_EFFORT = .065
RETURN_START, RETURN_END = 2., 4.
RETURN_SEQUENCE = 'release:2,back:2,release:1'
POLICIES = (('legacy', 'symmetric'), ('narrow', 'symmetric'), ('narrow', 'relief'))
PROBE_SPEEDS = (-.20, -.05, -.025, -.005, -.001, 0., .001, .005, .025, .05, .20)


def replay(case, policy, effort, sequence, *, initial_q=0., down_duration=None, probe_speed=None):
    profile, mode = policy
    state = BridgeState(strategy='balanced', resistance_profile=profile, return_mode=mode)
    state.receive('vr', dict(v=1, type='vr_update', seq=0, character_id=CHARACTER,
                             match_state='waiting'), 0.)
    device = SimulatedDevice()
    device.angle = -initial_q
    energy = 0.
    actions = [action for action, ticks in parse_sequence(sequence) for _ in range(ticks)]
    rows = []
    for tick, action in enumerate(actions + ['end']):
        now = tick * STEP_SECONDS
        if tick % 10 == 0:
            state.receive('vr', dict(v=1, type='vr_update', seq=tick+1, character_id=CHARACTER,
                                     match_state='running'), now)
        angle, speed = device.angle, device.velocity
        if probe_speed is not None and action == 'end':
            speed = probe_speed
        state.receive('stm32', dict(v=1, type='telemetry', seq=tick, angle_rad=angle,
                                    velocity_rad_s=speed, device_status='ready'), now)
        command, status = state.outputs(now)
        target = command['target_torque_nm']
        signed = command['brake_torque_nm']
        base = status['base_target_torque_nm']
        requested = status['requested_target_torque_nm']
        eligible = status['eligible_target_torque_nm']
        assert status['return_mode'] == mode and status['resistance_profile'] == profile
        assert 0 <= target <= eligible <= requested <= base
        assert signed * speed <= 0.

        user_torque = None if probe_speed is not None or action == 'end' else effort * INPUT_SIGN[action]
        simulated_brake = power = None
        if user_torque is not None:
            device.receive(command, now)
            device.step(STEP_SECONDS, user_torque, now)
            simulated_brake, power = device.brake, device.power
            energy -= power * STEP_SECONDS
            assert device.maximum_power == 0.

        rows.append(dict(case=case, profile=profile, return_mode=mode, effort_nm=effort,
                         down_duration_s=down_duration, initial_q_rad=initial_q, time_s=round(now, 2),
                         q_rad=-angle, velocity_rad_s=speed, qdot_rad_s=status['qdot_rad_s'],
                         action='probe' if probe_speed is not None and action == 'end' else action,
                         user_torque_nm=user_torque, direction_scale=status['direction_scale'],
                         base_nm=base, requested_nm=requested, eligible_nm=eligible, target_nm=target,
                         command_signed_nm=signed, model_power_w=signed * speed,
                         simulated_brake_nm=simulated_brake, simulated_power_w=power,
                         step_end_brake_energy_j=energy, fatigue=status['fatigue_level']))
    return rows


def advance_metrics(rows):
    first = next((r['time_s'] for r in rows if r['q_rad'] >= WIN_THRESHOLD_RAD), None)
    streak = longest = 0
    for row in rows:
        streak = streak+1 if row['q_rad'] >= WIN_THRESHOLD_RAD else 0
        longest = max(longest, streak)
    hold = round(max(0, longest-1)*STEP_SECONDS, 2)
    return dict(peak_q_rad=max(r['q_rad'] for r in rows), threshold_s=first, longest_hold_s=hold,
                reaches_hold_035=hold >= .35,
                hit_limit=any(r['q_rad'] >= .55 or r['q_rad'] <= -.60 for r in rows),
                max_target_nm=max(r['target_nm'] for r in rows),
                brake_energy_j=rows[-1]['step_end_brake_energy_j'],
                peak_fatigue=max(r['fatigue'] for r in rows))


def return_metrics(rows):
    start = next(r for r in rows if r['time_s'] == RETURN_START)
    end = next(r for r in rows if r['time_s'] == RETURN_END)
    pushing = [r for r in rows if RETURN_START <= r['time_s'] < RETURN_END]
    observed = [r for r in rows if r['time_s'] >= RETURN_START]

    def first_at(q):
        return next((round(r['time_s']-RETURN_START, 2) for r in observed if r['q_rad'] <= q), None)

    return dict(initial_omega_rad_s=start['velocity_rad_s'], initial_target_nm=start['target_nm'],
                initial_fatigue=start['fatigue'], q_after_push_rad=end['q_rad'],
                q_at_end_rad=rows[-1]['q_rad'], retreat_rad=start['q_rad']-end['q_rad'],
                to_q005_s=first_at(.05), to_zero_s=first_at(0.),
                min_velocity_rad_s=min(r['velocity_rad_s'] for r in observed),
                max_velocity_rad_s=max(r['velocity_rad_s'] for r in observed),
                peak_target_during_push_nm=max(r['target_nm'] for r in pushing),
                peak_applied_during_push_nm=max(abs(r['simulated_brake_nm']) for r in pushing),
                brake_energy_during_push_j=pushing[-1]['step_end_brake_energy_j'],
                brake_energy_after_release_j=rows[-1]['step_end_brake_energy_j']
                                            - pushing[-1]['step_end_brake_energy_j'],
                hit_return_limit=any(r['q_rad'] <= -.60 for r in observed))


def plot(path, forward, reverse, probes):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 3, figsize=(12.6, 6.8), layout='constrained')
    colors, styles = ('#1d5d99', '#a8510c', '#21735e'), ('-', '--', '-.')
    labels = ('Legacy, symmetric', 'Narrow, symmetric', 'Narrow, relief')
    for col, duration in enumerate(DOWN_DURATIONS):
        axis = axes[0, col]
        for i, policy in enumerate(POLICIES[:2]):
            peaks = [advance_metrics(forward[(policy, effort, duration)])['peak_q_rad']
                     for effort in AMPLITUDES]
            axis.plot(AMPLITUDES, peaks, color=colors[i], linestyle=styles[i], marker='o', label=labels[i])
        axis.axhline(WIN_THRESHOLD_RAD, color='0.5', linestyle=':', label='25° line')
        axis.axhline(.55, color='0.3', linestyle=':', label='Simulator limit')
        axis.set(title=f'Advance with {duration:.2f} s push', xlabel='Input effort (N·m)', ylabel='Peak q (rad)',
                 ylim=(0, .59))
    axes[0, 0].legend(fontsize=8, loc='lower right')
    for i, policy in enumerate(POLICIES):
        axes[0, 2].plot(PROBE_SPEEDS, [probes[(policy, speed)]['target_nm'] for speed in PROBE_SPEEDS],
                        color=colors[i], linestyle=styles[i], marker='.', label=labels[i])
    axes[0, 2].set(title='Fixed q = 0.25: direction response', xlabel='Mechanical speed (rad/s)',
                   ylabel='Command target (N·m)')
    axes[0, 2].set_xticks((-.2, -.1, 0, .1, .2))
    for col, initial in enumerate(RETURN_STARTS):
        axis = axes[1, col]
        for i, policy in enumerate(POLICIES):
            rows = [row for row in reverse[(policy, initial)] if row['time_s'] >= RETURN_START]
            axis.plot([row['time_s']-RETURN_START for row in rows], [row['q_rad'] for row in rows],
                      color=colors[i], linestyle=styles[i], label=labels[i])
        axis.axhline(.05, color='0.5', linestyle=':')
        axis.axhline(0, color='0.65', linestyle=':')
        axis.axvspan(0, RETURN_END-RETURN_START, color='#4684b8', alpha=.09)
        axis.set(title=f'Return from q = {initial:.2f} rad', xlabel='Time after return input (s)', ylabel='q (rad)')
    axes[1, 0].legend(fontsize=7, loc='lower right')
    for axis in axes.flat:
        axis.grid(alpha=.18)
    fig.suptitle('V0.5-B  |  person_02 + balanced  |  software simulation', fontweight='bold')
    fig.savefig(path, dpi=145)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='CSV data')
    parser.add_argument('--plot', type=Path, help='Optional PNG (matplotlib)')
    args = parser.parse_args()

    forward = {}
    for duration in DOWN_DURATIONS:
        sequence = SEQUENCE if duration == .66 else SEQUENCE.replace('down:0.66', 'down:1.32')
        for effort in AMPLITUDES:
            for policy in POLICIES[:2]:
                forward[(policy, effort, duration)] = replay('advance', policy, effort, sequence,
                                                               down_duration=duration)
    # Additional same-input round trip to isolate modulation across direction changes.
    forward[(POLICIES[2], .065, .66)] = replay('advance', POLICIES[2], .065, SEQUENCE, down_duration=.66)
    reverse = {(policy, initial): replay('return', policy, RETURN_EFFORT, RETURN_SEQUENCE, initial_q=initial)
               for initial in RETURN_STARTS for policy in POLICIES}
    probes = {(policy, speed): replay('probe', policy, None, 'release:2', initial_q=.25, probe_speed=speed)[-1]
              for policy in POLICIES for speed in PROBE_SPEEDS}

    def assert_same_start(first, second, stop_action):
        for left, right in zip(first, second):
            if left['action'] == stop_action:
                break
            assert all(left[key] == right[key] for key in left if key != 'return_mode')

    for initial in RETURN_STARTS:
        assert_same_start(reverse[(POLICIES[1], initial)], reverse[(POLICIES[2], initial)], 'back')
    assert_same_start(forward[(POLICIES[1], .065, .66)], forward[(POLICIES[2], .065, .66)], 'back')
    for speed in PROBE_SPEEDS:
        prior, relief = (probes[(policy, speed)] for policy in POLICIES[1:])
        assert relief['requested_nm'] <= prior['requested_nm']
        if speed <= 0.:
            assert relief['target_nm'] == prior['target_nm']

    rows = []
    for (policy, effort, duration), trace in forward.items():
        print(json.dumps(dict(case='advance', profile=policy[0], return_mode=policy[1],
                              effort_nm=effort, down_duration_s=duration, **advance_metrics(trace))))
        boundaries = set()
        pos = 0
        for _, ticks in parse_sequence(SEQUENCE.replace('down:0.66', f'down:{duration}')):
            pos += ticks
            boundaries.update((pos-1, pos))
        rows.extend(row for i, row in enumerate(trace) if i % 10 == 0 or i in boundaries)
    for (policy, initial), trace in reverse.items():
        print(json.dumps(dict(case='return', profile=policy[0], return_mode=policy[1], initial_q_rad=initial,
                              **return_metrics(trace))))
        rows.extend(row for i, row in enumerate(trace) if i % 10 == 0 or i in (99, 199))
    rows.extend(probes.values())

    with args.output.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({k: round(v, 9) if isinstance(v, float) else v for k, v in row.items()} for row in rows)
    if args.plot:
        plot(args.plot, forward, reverse, probes)


if __name__ == '__main__':
    main()
