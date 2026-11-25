"""Reproduce V0.4 fixed-telemetry and equal-user-input comparisons."""
import argparse
import csv
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.state import BridgeState
from sim.mock_stm32 import INPUT_SIGN, STEP_SECONDS, SimulatedDevice, parse_sequence


STRATEGIES = ('balanced', 'defensive', 'rhythmic')
CHARACTER = 'person_02'
WIN_THRESHOLD_RAD = math.radians(25)
SEQUENCE = 'release:1.2,down:0.66,release:1.2,back:0.22,release:1.72'
EFFORT = .065


def fixed_inputs(angle=-.4, duration=26.):
    for tick in range(round(duration / STEP_SECONDS) + 1):
        now = tick * STEP_SECONDS
        match = 'running' if now < 20 or 23 <= now < 26 else 'paused' if now < 23 else 'waiting'
        yield match, angle, -.2, None


def replay(strategy, character, inputs, device=None):
    controller = BridgeState(strategy=strategy)
    controller.receive('vr', dict(v=1, type='vr_update', seq=0,
                                 character_id=character, match_state='waiting'), 0.)
    previous_match = 'waiting'
    for tick, (match, angle, velocity, effort) in enumerate(inputs):
        now = tick * STEP_SECONDS
        if tick % 10 == 0 or match != previous_match:
            controller.receive('vr', dict(v=1, type='vr_update', seq=tick+1,
                                         character_id=character, match_state=match), now)
        previous_match = match
        if device is not None:
            angle, velocity = device.angle, device.velocity
        controller.receive('stm32', dict(v=1, type='telemetry', seq=tick,
                           angle_rad=angle, velocity_rad_s=velocity, device_status='ready'), now)
        command, status = controller.outputs(now)
        base = status['base_target_torque_nm']
        requested = status['requested_target_torque_nm']
        eligible = status['eligible_target_torque_nm']
        target = command['target_torque_nm']
        assert 0 <= target <= eligible <= requested <= base
        assert command['brake_torque_nm'] * velocity <= 0
        if device is not None:
            device.receive(command, now)
            applied, power = device.brake, device.power
            if effort is not None:
                device.step(STEP_SECONDS, effort, now)
                applied, power = device.brake, device.power
            assert device.maximum_power == 0
        else:
            applied, power = command['brake_torque_nm'], command['brake_torque_nm'] * velocity
        yield dict(character=character, strategy=strategy, time_s=round(now, 2),
                   match=match, angle_rad=angle, velocity_rad_s=velocity,
                   user_torque_nm=effort, scale=status['strategy_scale'],
                   active_match_time_s=status['active_match_time_s'], base_nm=base,
                   requested_nm=requested, eligible_nm=eligible, target_nm=target,
                   fatigue=status['fatigue_level'], brake_nm=applied, brake_power_w=power)


def closed_loop(strategy, amplitude=EFFORT, sequence=SEQUENCE):
    inputs = [('running', None, None, amplitude * INPUT_SIGN[action])
              for action, ticks in parse_sequence(sequence) for _ in range(ticks)]
    # Final sample observes the state after the last integration interval.
    inputs.append(('running', None, None, None))
    return list(replay(strategy, CHARACTER, inputs, SimulatedDevice()))


def summarize(traj):
    peak = max(traj, key=lambda row: -row['angle_rad'])
    first = next((row['time_s'] for row in traj if -row['angle_rad'] >= WIN_THRESHOLD_RAD), None)
    return dict(peak_advance_rad=-peak['angle_rad'], peak_time_s=peak['time_s'],
                threshold_time_s=first,
                seconds_in_resistance_zone=sum(row['angle_rad'] < -.25 for row in traj[:-1]) * STEP_SECONDS,
                hit_limit=any(row['angle_rad'] <= -.55 or row['angle_rad'] >= .6 for row in traj),
                max_command_nm=max(row['target_nm'] for row in traj),
                max_fatigue=max(row['fatigue'] for row in traj),
                min_velocity_rad_s=min(row['velocity_rad_s'] for row in traj),
                max_velocity_rad_s=max(row['velocity_rad_s'] for row in traj),
                min_power_w=min(row['brake_power_w'] for row in traj),
                max_power_w=max(row['brake_power_w'] for row in traj))


def make_plot(path, fixed, closed):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(10, 6.3), layout='constrained')
    ax_torque, ax_fatigue, ax_angle, ax_closed_torque = axes.flat
    for name, color, style in zip(STRATEGIES, ('#1864AB', '#BD5409', '#6A3D9A'),
                                  ('-', '--', '-.')):
        for rows, axis, key, sign in (
            (fixed[name], ax_torque, 'target_nm', 1),
            (fixed[name], ax_fatigue, 'fatigue', 1),
            (closed[name], ax_angle, 'angle_rad', -1),
            (closed[name], ax_closed_torque, 'target_nm', 1),
        ):
            axis.plot([r['time_s'] for r in rows], [sign*r[key] for r in rows],
                      label=name.capitalize(), color=color, linestyle=style, linewidth=1.6)
    for axis, title, ylabel in (
        (ax_torque, 'A1  Fixed telemetry: output', 'Target (N·m)'),
        (ax_fatigue, 'A2  Fixed telemetry: fatigue', 'Equivalent fatigue'),
        (ax_angle, 'B1  Same user effort: advance', 'Advance q (rad)'),
        (ax_closed_torque, 'B2  Same user effort: output', 'Target (N·m)'),
    ):
        axis.set(title=title, xlabel='Time (s)', ylabel=ylabel)
        axis.grid(alpha=.25)
    for axis in (ax_torque, ax_fatigue):
        axis.axvspan(20, 23, color='0.6', alpha=.15, zorder=0)
        axis.text(21.5, .07, 'Paused', transform=axis.get_xaxis_transform(), ha='center', fontsize=9)
    ax_angle.axhline(.25, color='0.55', linestyle=':', label='Resistance starts')
    ax_angle.axhline(WIN_THRESHOLD_RAD, color='0.35', linestyle=':', label='VR threshold')
    ax_angle.axhline(.55, color='0.15', linestyle=':', label='Software limit')
    for axis in (ax_angle, ax_closed_torque):
        axis.axvspan(1.2, 1.86, color='#1864AB', alpha=.10, zorder=0)
        axis.axvspan(3.06, 3.28, color='#BD5409', alpha=.10, zorder=0)
    ax_torque.legend(loc='lower center', fontsize=8)
    ax_angle.legend(handles=ax_angle.get_lines()[-3:], fontsize=7, loc='lower right')
    fig.suptitle('V0.4 strategy comparison  |  person_02', fontweight='bold')
    fig.savefig(path, dpi=155)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='CSV (100 ms samples)')
    parser.add_argument('--plot', type=Path, help='Optional PNG; requires matplotlib')
    args = parser.parse_args()
    fixed = {name: list(replay(name, CHARACTER, fixed_inputs())) for name in STRATEGIES}
    closed = {name: closed_loop(name) for name in STRATEGIES}
    rows = []
    for scenario, groups in (('fixed', fixed), ('closed_loop', closed)):
        for trace in groups.values():
            for row in trace[::5]:
                rows.append(dict(test=scenario, **row))
    for angle in (-.25, -.27, -.4, -WIN_THRESHOLD_RAD):
        row = list(replay('defensive', CHARACTER, fixed_inputs(angle, 2.)))[-1]
        rows.append(dict(test='angle_probe', **row))
    for character in ('person_01', 'person_02', 'person_03'):
        row = list(replay('defensive', character, fixed_inputs(duration=2.)))[-1]
        rows.append(dict(test='character_probe', **row))
    with args.output.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({key: round(value, 8) if isinstance(value, float) else value
                          for key, value in row.items()} for row in rows)
    for name, trace in closed.items():
        print(name, summarize(trace))
    if args.plot:
        make_plot(args.plot, fixed, closed)


if __name__ == '__main__':
    main()
