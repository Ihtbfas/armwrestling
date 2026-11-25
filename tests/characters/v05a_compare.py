"""Reproduce the V0.5-A software-only resistance profile comparisons."""
import argparse
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.characters import CHARACTERS
from common.control import brake_state
from common.resistance_profiles import profile_target
from common.state import BridgeState
from sim.mock_stm32 import INPUT_SIGN, STEP_SECONDS, SimulatedDevice, parse_sequence
from v04_compare import SEQUENCE, WIN_THRESHOLD_RAD


CHARACTER = 'person_02'
PROFILES = ('legacy', 'narrow', 'start-resistance')
AMPLITUDES = (.045, .065, .09)
PROBE_ANGLES = (-.05, -.025, 0., .05, .10, .25, .40, WIN_THRESHOLD_RAD, .50)
BACK_START_S, BACK_END_S = 3.06, 3.28  # V0.4 sequence, same for all nine runs.


def scan(profile):
    params = CHARACTERS[CHARACTER].brake_parameters
    grid = {i / 100 for i in range(-60, 56, 2)} | set(PROBE_ANGLES) | {.55}
    for q in sorted(grid):
        base = profile_target(profile, brake_state(-q, 0., params), params)
        yield dict(case='static', profile=profile, q_rad=q, base_nm=base)


def fixed_inputs(q):
    # Prescribed telemetry holds angle fixed to isolate the response to speed sign.
    for tick in range(300):
        action, speed = ('hold', 0.) if tick < 100 else ('down', -.2) if tick < 200 else ('back', .2)
        yield action, -q, speed


def closed_inputs():
    for action, ticks in parse_sequence(SEQUENCE):
        for _ in range(ticks):
            yield action, None, None
    yield 'end', None, None


def replay(profile, inputs, amplitude=None):
    state = BridgeState(strategy='balanced', resistance_profile=profile)
    state.receive('vr', dict(v=1, type='vr_update', seq=0, character_id=CHARACTER,
                             match_state='waiting'), 0.)
    device = SimulatedDevice() if amplitude is not None else None
    energy = 0.
    for tick, (action, angle, speed) in enumerate(inputs):
        now = tick * STEP_SECONDS
        if tick % 10 == 0:
            state.receive('vr', dict(v=1, type='vr_update', seq=tick+1, character_id=CHARACTER,
                                     match_state='running'), now)
        if device is not None:
            angle, speed = device.angle, device.velocity
        state.receive('stm32', dict(v=1, type='telemetry', seq=tick, angle_rad=angle,
                                   velocity_rad_s=speed, device_status='ready'), now)
        command, status = state.outputs(now)
        base, requested = status['base_target_torque_nm'], status['requested_target_torque_nm']
        eligible, target = status['eligible_target_torque_nm'], command['target_torque_nm']
        signed = command['brake_torque_nm']
        assert status['resistance_profile'] == profile
        assert 0 <= target <= eligible <= requested <= base
        assert signed * speed <= 0
        effort, applied, power = None, signed, signed * speed
        if device is not None:
            applied = power = None
            if action != 'end':
                effort = amplitude * INPUT_SIGN[action]
                device.receive(command, now)
                device.step(STEP_SECONDS, effort, now)
                applied, power = device.brake, device.power
                energy -= power * STEP_SECONDS
                assert device.maximum_power == 0.
        yield dict(case='closed_loop' if device is not None else 'fixed', profile=profile,
                   amplitude_nm=amplitude, time_s=round(now, 2), q_rad=-angle,
                   velocity_rad_s=speed, action=action, user_torque_nm=effort,
                   base_nm=base, requested_nm=requested, eligible_nm=eligible, target_nm=target,
                   command_signed_nm=signed, applied_brake_nm=applied, brake_power_w=power,
                   brake_energy_j=energy if device is not None else None, fatigue=status['fatigue_level'])


def summarize(trace):
    peak = max(trace, key=lambda r: r['q_rad'])
    onset = next(r for r in trace if r['target_nm'] > 0.)
    threshold = next((r['time_s'] for r in trace if r['q_rad'] >= WIN_THRESHOLD_RAD), None)
    start = next(r for r in trace if r['time_s'] == BACK_START_S)
    end = next(r for r in trace if r['time_s'] == BACK_END_S)
    back = [r for r in trace if start['time_s'] <= r['time_s'] < end['time_s']]
    return dict(onset_rad=onset['q_rad'], onset_s=onset['time_s'], peak_rad=peak['q_rad'],
                peak_s=peak['time_s'], threshold_s=threshold,
                hit_limit=any(r['q_rad'] >= .55 or r['q_rad'] <= -.60 for r in trace),
                max_target_nm=max(r['target_nm'] for r in trace),
                min_power_w=min(r['brake_power_w'] for r in trace[:-1]),
                brake_energy_j=trace[-1]['brake_energy_j'], fatigue_peak=max(r['fatigue'] for r in trace),
                back_q_start_rad=start['q_rad'], back_q_end_rad=end['q_rad'],
                back_return_rad=start['q_rad'] - end['q_rad'],
                back_peak_speed_rad_s=max(r['velocity_rad_s'] for r in back))


def plot(path, scans, fixed, closed):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 3, figsize=(12.6, 6.8), layout='constrained')
    static, reverse, fatigue = axes[0]
    for profile, color, style in zip(PROFILES, ('#1d5d99', '#ab510c', '#743c92'), ('-', '--', '-.')):
        def draw(axis, rows, x, y):
            axis.plot([r[x] for r in rows], [r[y] for r in rows], color=color,
                      linestyle=style, label=profile, linewidth=1.75)

        draw(static, scans[profile], 'q_rad', 'base_nm')
        draw(reverse, fixed[(profile, .10)], 'time_s', 'applied_brake_nm')
        draw(fatigue, fixed[(profile, .10)], 'time_s', 'fatigue')
        for amplitude, axis in zip(AMPLITUDES, axes[1]):
            draw(axis, closed[(profile, amplitude)], 'time_s', 'q_rad')

    static.set(title='Static resistance profile', xlabel='Advance q (rad)', ylabel='Base target (N·m)')
    static.axhline(CHARACTERS[CHARACTER].brake_parameters.max_torque, color='0.45', linestyle=':', label='Person cap')
    for q, label, top in ((0., '0', .90), (.05, '0.05', .79), (.25, '0.25', .90),
                          (WIN_THRESHOLD_RAD, '25°', .79)):
        static.axvline(q, color='0.5', linewidth=.8, linestyle=':')
        static.text(q-.008, top, label, transform=static.get_xaxis_transform(), fontsize=8, ha='right')
    static.legend(loc='upper left', fontsize=8)
    reverse.set(title='Fixed q = 0.10: hold, down, back', xlabel='Time (s)', ylabel='Signed brake (N·m)')
    fatigue.set(title='Fixed q = 0.10: fatigue', xlabel='Time (s)', ylabel='Equivalent fatigue')
    for axis in (reverse, fatigue):
        for t in (2., 4.):
            axis.axvline(t, color='0.65', linestyle=':')
    for amplitude, axis in zip(AMPLITUDES, axes[1]):
        axis.set(title=f'Same user effort: {amplitude:.3f} N·m', xlabel='Time (s)', ylabel='Advance q (rad)',
                 ylim=(-.035, .58))
        axis.axhline(WIN_THRESHOLD_RAD, color='0.5', linestyle=':', label='25° line')
        axis.axhline(.55, color='0.25', linestyle=':', label='Simulator limit')
        axis.axvspan(1.2, 1.86, color='#336699', alpha=.12)
        axis.axvspan(BACK_START_S, BACK_END_S, color='#ab510c', alpha=.14)
    axes[1, 0].legend(handles=axes[1, 0].lines[-2:], loc='lower right', fontsize=8)
    for axis in axes.flat:
        axis.grid(alpha=.2)
    fig.suptitle('V0.5-A  |  person_02 + balanced', fontweight='bold')
    fig.savefig(path, dpi=145)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='CSV for static, fixed and closed-loop runs')
    parser.add_argument('--plot', type=Path, help='Optional PNG (matplotlib)')
    args = parser.parse_args()
    scans = {p: list(scan(p)) for p in PROFILES}
    fixed = {(p, q): list(replay(p, fixed_inputs(q))) for p in PROFILES for q in PROBE_ANGLES}
    closed = {(p, a): list(replay(p, closed_inputs(), a)) for a in AMPLITUDES for p in PROFILES}
    rows = [r for trace in scans.values() for r in trace]
    # Keep each phase/sign boundary and settled observation even when downsampling.
    rows += [r for trace in fixed.values() for i, r in enumerate(trace)
             if i % 25 == 0 or i in (99, 199, 299)]
    rows += [r for trace in closed.values() for i, r in enumerate(trace)
             if i % 5 == 0 or r['time_s'] in (BACK_START_S, BACK_END_S)]
    with args.output.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(closed[(PROFILES[0], AMPLITUDES[0])][0]))
        writer.writeheader()
        writer.writerows({k: round(v, 9) if isinstance(v, float) else v for k, v in r.items()}
                         for r in rows)
    for (profile, amplitude), trace in closed.items():
        print(json.dumps(dict(profile=profile, amplitude_nm=amplitude, **summarize(trace))))
    if args.plot:
        plot(args.plot, scans, fixed, closed)


if __name__ == '__main__':
    main()
