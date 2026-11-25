"""Reproduce V0.5-C Experiment A with the original, zero-delay controller."""
import argparse
import csv
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.state import BridgeState
from sim.mock_stm32 import STEP_SECONDS, SimulatedDevice
from v05b_compare import CHARACTER, POLICIES, WIN_THRESHOLD_RAD, replay as previous_replay


EFFORTS = (.065, .12, .18)
STARTS = (.25, .40)
PREPARE_TICKS, PUSH_TICKS = 100, 100
J = .05469492132716054
DAMPING = .04
FREE_RELEASES = ((.05, .1), (.05, .47), (.02, .3), (0., .1), (0., .47),
                 (.01633876, .46692223))
PROBE_SPEEDS = (-.05, 0., .005, .015, .025, .05, .2)


def replay(case, policy, initial_q, *, effort=0., release_omega=0.):
    """Initialize mechanical state only in this test harness."""
    profile, mode = policy
    state = BridgeState(strategy='balanced', resistance_profile=profile, return_mode=mode)
    state.receive('vr', dict(v=1, type='vr_update', seq=0, character_id=CHARACTER,
                             match_state='waiting'), 0.)
    device = SimulatedDevice()
    device.angle = -initial_q
    if case == 'free_coast':
        device.velocity = release_omega
    end = 300 if case == 'return' else 100 if case in ('free_coast', 'speed_probe') else 200
    energy = 0.
    rows = []
    for tick in range(end+1):
        now = tick * STEP_SECONDS
        if tick % 10 == 0:
            state.receive('vr', dict(v=1, type='vr_update', seq=tick+1, character_id=CHARACTER,
                                     match_state='running'), now)
        if case in ('engaged_coast', 'speed_probe') and tick == PREPARE_TICKS:
            device.velocity = release_omega
        angle, speed = device.angle, device.velocity
        state.receive('stm32', dict(v=1, type='telemetry', seq=tick, angle_rad=angle,
                                    velocity_rad_s=speed, device_status='ready'), now)
        command, status = state.outputs(now)
        target = command['target_torque_nm']
        signed = command['brake_torque_nm']
        assert status['return_mode'] == mode and status['resistance_profile'] == profile
        assert 0 <= target <= status['eligible_target_torque_nm'] <= status['requested_target_torque_nm'] <= status['base_target_torque_nm']
        assert signed*speed <= 0.

        if case == 'return' and PREPARE_TICKS <= tick < PREPARE_TICKS+PUSH_TICKS:
            phase, user = 'back', effort
        elif case != 'free_coast' and tick < PREPARE_TICKS:
            phase, user = 'prepare', 0.
        else:
            phase, user = ('probe' if case == 'speed_probe' else 'coast'), 0.

        applied = power = None
        if tick < end:
            device.receive(command, now)
            device.step(STEP_SECONDS, user, now)
            applied, power = device.brake, device.power
            energy -= power * STEP_SECONDS
            assert device.maximum_power == 0.
        rows.append(dict(case=case, profile=profile, return_mode=mode, initial_q_rad=initial_q,
                         prescribed_omega_rad_s=release_omega if case != 'return' else None,
                         effort_nm=effort, time_s=round(now, 2), phase=phase, q_rad=-angle,
                         omega_rad_s=speed, user_torque_nm=user, direction_scale=status['direction_scale'],
                         base_nm=status['base_target_torque_nm'], requested_nm=status['requested_target_torque_nm'],
                         target_nm=target, command_signed_nm=signed, simulated_brake_nm=applied,
                         simulated_power_w=power, step_end_brake_energy_j=energy, fatigue=status['fatigue_level']))
    return rows


def return_metrics(rows):
    release_tick = PREPARE_TICKS + PUSH_TICKS
    start, released = rows[PREPARE_TICKS], rows[release_tick]
    after_start = rows[PREPARE_TICKS:]

    def crossing(q):
        return next((dict(time_s=round(r['time_s']-start['time_s'], 2), phase=r['phase'],
                          omega_rad_s=r['omega_rad_s']) for r in after_start if r['q_rad'] <= q), None)

    # Hold uses first-to-last 20 ms sample spacing, as in V0.5-B.
    streak = longest = 0
    for row in after_start:
        streak = streak+1 if row['q_rad'] <= -WIN_THRESHOLD_RAD else 0
        longest = max(longest, streak)
    hold = round(max(0, longest-1)*STEP_SECONDS, 2)
    return dict(
        initial_target_nm=start['target_nm'], initial_fatigue=start['fatigue'],
        release_q_rad=released['q_rad'], release_omega_rad_s=released['omega_rad_s'],
        release_target_nm=released['target_nm'], release_fatigue=released['fatigue'],
        q005=crossing(.05), zero=crossing(0.), opposite_line=crossing(-WIN_THRESHOLD_RAD),
        opposite_hold_s=hold, holds_035=hold >= .35,
        return_limit=crossing(-.60),
        q_1s_rad=rows[release_tick+50]['q_rad'], q_2s_rad=rows[-1]['q_rad'],
        coast_1s_retreat_rad=released['q_rad']-rows[release_tick+50]['q_rad'],
        coast_2s_retreat_rad=released['q_rad']-rows[-1]['q_rad'],
        peak_omega_rad_s=max(r['omega_rad_s'] for r in after_start),
        push_brake_energy_j=rows[release_tick-1]['step_end_brake_energy_j'],
        coast_brake_energy_j=rows[-1]['step_end_brake_energy_j']-rows[release_tick-1]['step_end_brake_energy_j'],
    )


def analytic(q0, omega0, seconds):
    decay = math.exp(-DAMPING*seconds/J)
    return q0-omega0*J/DAMPING*(1.-decay), omega0*decay


def graph(path, returns, free, engaged):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 3, figsize=(12.4, 6.6), layout='constrained')
    colors, styles = ('#175d9a', '#ae510b', '#167456'), ('-', '--', '-.')
    labels = ('Legacy', 'Narrow', 'Narrow + relief')
    for col, effort in enumerate(EFFORTS):
        axis = axes[0, col]
        for policy, color, style, label in zip(POLICIES, colors, styles, labels):
            rows = returns[(policy, .40, effort)][PREPARE_TICKS:]
            axis.plot([r['time_s']-2. for r in rows], [r['q_rad'] for r in rows],
                      color=color, linestyle=style, label=label, linewidth=1.65)
        axis.axvspan(0, 2., color=colors[0], alpha=.08)
        axis.axhline(-WIN_THRESHOLD_RAD, color='.5', linestyle=':')
        axis.axhline(-.60, color='.6', linestyle=':')
        axis.set(title=f'Return from q = 0.40, input {effort:.3f} N·m', xlabel='Time after return start (s)',
                 ylabel='Advance q (rad)', ylim=(-.65, .45))
    axes[0, 0].legend(fontsize=8)
    axis = axes[1, 0]
    for policy, color, style, label in zip(POLICIES, colors, styles, labels):
        rows = returns[(policy, .25, .065)][PREPARE_TICKS:]
        axis.plot([r['time_s']-2. for r in rows], [r['q_rad'] for r in rows],
                  color=color, linestyle=style, label=label, linewidth=1.65)
    axis.axvspan(0, 2., color=colors[0], alpha=.08)
    axis.axhline(0, color='.6', linestyle=':')
    axis.set(title='Natural release, q = 0.25 start', xlabel='Time after return start (s)', ylabel='Advance q (rad)')

    axis = axes[1, 1]
    q0, omega = FREE_RELEASES[-1]
    for policy, color, style, label in zip(POLICIES[1:], colors[1:], styles[1:], labels[1:]):
        rows = free[(policy, q0, omega)]
        axis.plot([r['time_s'] for r in rows], [r['q_rad'] for r in rows],
                  color=color, linestyle=style, label=label, linewidth=1.65)
    times = [r['time_s'] for r in rows]
    axis.plot(times, [analytic(q0, omega, t)[0] for t in times],
              color='.15', linestyle=':', label='Analytic', linewidth=1.05)
    axis.set(title='Matched release in free zone', xlabel='Time after release (s)', ylabel='Advance q (rad)')
    axis.legend(fontsize=8)
    axis = axes[1, 2]
    for policy, color, style, label in zip(POLICIES[1:], colors[1:], styles[1:], labels[1:]):
        rows = engaged[policy][PREPARE_TICKS:]
        axis.plot([r['time_s']-2. for r in rows], [r['q_rad'] for r in rows],
                  color=color, linestyle=style, label=label, linewidth=1.65)
    axis.axhline(.05, color='.5', linestyle=':', label='Free-zone boundary')
    axis.set(title='Matched release in braking zone', xlabel='Time after release (s)', ylabel='Advance q (rad)')
    axis.legend(fontsize=8)
    for axis in axes.flat:
        axis.grid(alpha=.18)
    fig.suptitle('V0.5-C Experiment A  |  person_02 + balanced  |  0 ms delay', fontweight='bold')
    fig.savefig(path, dpi=145)
    plt.close(fig)


def select_return_rows(rows):
    indices = set(range(0, len(rows), 10)) | {99, 100, 199, 200, 250, 300}
    for threshold in (.05, 0., -WIN_THRESHOLD_RAD, -.60):
        first = next((i for i, row in enumerate(rows[PREPARE_TICKS:], PREPARE_TICKS)
                      if row['q_rad'] <= threshold), None)
        if first is not None:
            indices.update((first-1, first))
    return [rows[i] for i in sorted(indices)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--plot', type=Path, help='Optional PNG (matplotlib)')
    args = parser.parse_args()
    returns = {(policy, q, effort): replay('return', policy, q, effort=effort)
               for q in STARTS for effort in EFFORTS for policy in POLICIES}
    free = {(policy, q, omega): replay('free_coast', policy, q, release_omega=omega)
            for q, omega in FREE_RELEASES for policy in POLICIES[1:]}
    engaged = {policy: replay('engaged_coast', policy, .25, release_omega=.3) for policy in POLICIES[1:]}
    probes = [replay('speed_probe', policy, .40, release_omega=w)[-1] for w in PROBE_SPEEDS for policy in POLICIES]
    rows = []
    for (policy, q, effort), trace in returns.items():
        if effort == .065:
            original = previous_replay('return', policy, effort, 'release:2,back:2,release:1', initial_q=q)
            fields = ('q_rad', 'target_nm', 'fatigue')
            assert all(new['omega_rad_s'] == old['velocity_rad_s']
                       and all(new[key] == old[key] for key in fields)
                       for new, old in zip(trace, original))
        if policy == POLICIES[2]:
            symmetric = returns[(POLICIES[1], q, effort)]
            assert all(all(a[key] == b[key] for key in a if key != 'return_mode')
                       for a, b in zip(trace[:PREPARE_TICKS+1], symmetric[:PREPARE_TICKS+1]))
        print(json.dumps(dict(case='return', policy=policy, q0=q, effort_nm=effort, **return_metrics(trace))))
        rows.extend(select_return_rows(trace))
    natural = return_metrics(returns[(POLICIES[2], .25, .065)])
    assert abs(natural['release_q_rad']-.01633876) < 1e-8
    assert abs(natural['q_1s_rad']-(-.314528537)) < 1e-8
    for q, omega in FREE_RELEASES:
        a, b = (free[(policy, q, omega)] for policy in POLICIES[1:])
        fields = ('q_rad', 'omega_rad_s', 'target_nm', 'fatigue', 'step_end_brake_energy_j')
        assert all(all(x[key] == y[key] for key in fields) for x, y in zip(a, b))
        assert all(r['target_nm'] == 0. for r in a)
        q_theory, w_theory = analytic(q, omega, 1.)
        assert a[-1]['q_rad'] > -.60 and abs(a[50]['q_rad']-q_theory) < .001
        assert abs(a[50]['omega_rad_s']-w_theory) < .001
        print(json.dumps(dict(case='free_coast', q0=q, omega0=omega, q_1s=a[50]['q_rad'],
                              omega_1s=a[50]['omega_rad_s'], analytic_q_1s=q_theory,
                              analytic_omega_1s=w_theory, q_2s=a[-1]['q_rad'], omega_2s=a[-1]['omega_rad_s'])))
    for trace in free.values():
        rows.extend(trace[::10])
    for tick in range(PREPARE_TICKS):
        a, b = (engaged[policy][tick] for policy in POLICIES[1:])
        assert all(a[k] == b[k] for k in a if k != 'return_mode')
    for trace in engaged.values():
        rows.extend(row for i, row in enumerate(trace) if i % 10 == 0 or i == PREPARE_TICKS-1)
    rows.extend(probes)
    with args.output.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({k: round(v, 9) if isinstance(v, float) else v for k,v in r.items()} for r in rows)
    if args.plot:
        graph(args.plot, returns, free, engaged)


if __name__ == '__main__':
    main()
