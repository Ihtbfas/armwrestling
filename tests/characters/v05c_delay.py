"""Reproduce the V0.5-C delay experiment with fixed telemetry and equal effort."""
import argparse
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.state import BridgeState
from sim.mock_stm32 import INPUT_SIGN, STEP_SECONDS, SimulatedDevice, parse_sequence
from v04_compare import SEQUENCE, WIN_THRESHOLD_RAD
from v05b_compare import advance_metrics


CHARACTER = 'person_02'
DELAYS_MS = (0, 100, 200)
EFFORTS = (.065, .09, .12)
FIXED_ANGLES = (.25, .40)
INTERRUPTIONS = ('cancel', 'pause', 'fault')


def fixed_inputs(q):
    for tick in range(151):
        free = tick < 30
        yield ('free' if free else 'demand', 0. if free else q, -.2, 'running', 'ready')


def interruption_inputs(interruption):
    for tick in range(61):
        phase, q, match, readiness = 'free', 0., 'running', 'ready'
        if tick >= 20:
            phase, q = 'first_demand', .25
        if 23 <= tick < 30:
            phase = interruption
            if interruption == 'cancel':
                q = 0.
            elif interruption == 'pause':
                match = 'paused'
            else:
                readiness = 'fault'
        if tick >= 30:
            phase = 'second_demand'
        yield phase, q, -.2, match, readiness


def simulated_inputs():
    for action, count in parse_sequence(SEQUENCE):
        for _ in range(count):
            yield action, None, None, 'running', 'ready'
    yield 'end', None, None, 'running', 'ready'


def replay(case, delay_ms, inputs, *, fixed_q=None, effort=None):
    controller = BridgeState(strategy='balanced', resistance_profile='narrow',
                             return_mode='symmetric', reaction_delay_ms=delay_ms)
    simulator = SimulatedDevice() if effort is not None else None
    controller.receive('vr', dict(v=1, type='vr_update', seq=0,
                           character_id=CHARACTER, match_state='waiting'), 0.)
    last_match = 'waiting'
    energy = 0.
    for tick, (phase, q, speed, match, readiness) in enumerate(inputs):
        now = tick * STEP_SECONDS
        if tick % 10 == 0 or match != last_match:
            controller.receive('vr', dict(v=1, type='vr_update', seq=tick+1,
                                   character_id=CHARACTER, match_state=match), now)
        last_match = match
        if simulator is not None:
            q, speed = -simulator.angle, simulator.velocity
        controller.receive('stm32', dict(v=1, type='telemetry', seq=tick, angle_rad=-q,
                                         velocity_rad_s=speed, device_status=readiness), now)
        command, status = controller.outputs(now)
        base, request = status['base_target_torque_nm'], status['requested_target_torque_nm']
        effective = status['effective_requested_target_torque_nm']
        eligible, target = status['eligible_target_torque_nm'], command['target_torque_nm']
        waiting = status['reaction_waiting']
        assert status['reaction_delay_ms'] == delay_ms
        assert 0 <= target <= eligible <= effective <= request <= base
        assert command['brake_torque_nm'] * speed <= 0
        if waiting:
            assert request > 0. and effective == target == status['fatigue_load_proxy'] == 0.
        if command['mode'] == 'stop':
            assert effective == target == command['brake_torque_nm'] == 0. and not waiting
        user_torque = power = None
        if simulator is not None and phase != 'end':
            user_torque = effort * INPUT_SIGN[phase]
            simulator.receive(command, now)
            simulator.step(STEP_SECONDS, user_torque, now)
            power = simulator.power
            energy -= power * STEP_SECONDS
            assert simulator.maximum_power == 0.
        yield dict(case=case, delay_ms=delay_ms, fixed_q_rad=fixed_q, effort_nm=effort,
                   time_s=round(now, 2), phase=phase, reason=command['reason'], q_rad=q,
                   velocity_rad_s=speed, user_torque_nm=user_torque, base_nm=base,
                   request_nm=request, effective_nm=effective, eligible_nm=eligible, target_nm=target,
                   waiting=waiting, waited_ms=status['reaction_elapsed_ms'],
                   signed_brake_nm=command['brake_torque_nm'], simulated_power_w=power,
                   step_end_brake_energy_j=energy if simulator is not None else None,
                   fatigue=status['fatigue_level'])


def onset(rows):
    """Report trigger, open and output for the first uninterrupted demand."""
    requested = next(i for i, row in enumerate(rows) if row['request_nm'] > 0)
    end = next((i for i in range(requested + 1, len(rows)) if rows[i]['request_nm'] == 0.), len(rows))
    demand = rows[requested:end]
    return dict(trigger_s=demand[0]['time_s'],
                opens_s=next((row['time_s'] for row in demand if row['effective_nm'] > 0), None),
                first_output_s=next((row['time_s'] for row in demand if row['target_nm'] > 0), None))


def compressed(rows):
    """Keep 100 ms samples and exact phase, gate, first-output and angle events."""
    keep = {0, len(rows)-1, *range(0, len(rows), 5)}
    for tick in range(1, len(rows)):
        previous, row = rows[tick-1], rows[tick]
        if (any(previous[key] != row[key] for key in ('phase', 'reason', 'waiting'))
                or any((previous[key] > 0.) != (row[key] > 0.)
                       for key in ('request_nm', 'effective_nm', 'target_nm'))
                or any((previous['q_rad'] >= q) != (row['q_rad'] >= q)
                       for q in (WIN_THRESHOLD_RAD, .55))
                or (previous['q_rad'] <= -.60) != (row['q_rad'] <= -.60)):
            keep.update((tick-1, tick))
    return (rows[i] for i in sorted(keep))


def plot(path, fixed, closed):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(10.2, 6.6), layout='constrained')
    target, fatigue, advance, command = axes.flat
    for delay, color, style in zip(DELAYS_MS, ('#1864ab', '#b05212', '#6b4494'), ('-', '--', '-.')):
        fixed_run = fixed[(delay, .25)]
        closed_run = closed[(delay, .09)]
        for axis, trace, key in ((target, fixed_run, 'target_nm'), (fatigue, fixed_run, 'fatigue'),
                                  (advance, closed_run, 'q_rad'), (command, closed_run, 'target_nm')):
            axis.plot([r['time_s'] for r in trace], [r[key] for r in trace],
                      label=f'{delay} ms', color=color, linestyle=style, linewidth=1.7)
    baseline = fixed[(0, .25)]
    target.plot([r['time_s'] for r in baseline], [r['request_nm'] for r in baseline],
                color='0.55', linestyle=':', label='Demand')
    for axis in (target, fatigue):
        axis.axvline(.6, color='0.5', linestyle=':', linewidth=.9)
    target.set_xlim(.42, 1.8)
    target.legend(loc='lower right', fontsize=8)
    advance.axhline(WIN_THRESHOLD_RAD, color='0.55', linestyle=':', label='25° line')
    advance.axhline(.55, color='0.3', linestyle=':', label='Simulator limit')
    advance.legend(loc='lower right', fontsize=8)
    for axis, title, ylabel in (
        (target, 'Fixed telemetry at q = 0.25: response', 'Target (N·m)'),
        (fatigue, 'Fixed telemetry at q = 0.25: fatigue', 'Equivalent fatigue'),
        (advance, 'Same 0.09 N·m effort: advance', 'Advance q (rad)'),
        (command, 'Same 0.09 N·m effort: brake target', 'Target (N·m)'),
    ):
        axis.set(title=title, xlabel='Time (s)', ylabel=ylabel)
        axis.grid(alpha=.2)
    fig.suptitle('V0.5-C reaction delay | person_02, narrow, symmetric', fontweight='bold')
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='CSV for fixed and closed-loop runs')
    parser.add_argument('--plot', type=Path, help='Optional PNG (matplotlib)')
    args = parser.parse_args()

    fixed = {(delay, q): list(replay('fixed', delay, fixed_inputs(q), fixed_q=q))
             for q in FIXED_ANGLES for delay in DELAYS_MS}
    interrupted = {(delay, name): list(replay(name, delay, interruption_inputs(name), fixed_q=.25))
                   for name in INTERRUPTIONS for delay in DELAYS_MS}
    closed = {(delay, effort): list(replay('closed_loop', delay, simulated_inputs(), effort=effort))
              for effort in EFFORTS for delay in DELAYS_MS}
    for (delay, q), trace in fixed.items():
        times = onset(trace)
        assert times == dict(trigger_s=.6, opens_s=round(.6+delay/1000, 2),
                             first_output_s=round(.62+delay/1000, 2) if delay else .6), times
        print(json.dumps(dict(case='fixed', q_rad=q, delay_ms=delay, **times,
                              peak_target_nm=max(r['target_nm'] for r in trace),
                              fatigue_end=trace[-1]['fatigue'])))
    for (delay, name), trace in interrupted.items():
        if delay:
            assert onset(trace)['opens_s'] is None
        times = onset([r for r in trace if r['phase'] == 'second_demand'])
        first = .6 if delay == 0 and name == 'cancel' else round(.62+delay/1000, 2)
        assert times == dict(trigger_s=.6, opens_s=round(.6+delay/1000, 2), first_output_s=first), times
        print(json.dumps(dict(case=name, delay_ms=delay, **times)))
    for (delay, effort), trace in closed.items():
        print(json.dumps(dict(case='closed_loop', delay_ms=delay, effort_nm=effort,
                              **onset(trace), **advance_metrics(trace))))

    rows = [r for group in (fixed, interrupted, closed) for trace in group.values()
            for r in compressed(trace)]
    with args.output.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({k: round(v, 9) if isinstance(v, float) else v for k, v in row.items()}
                         for row in rows)
    if args.plot:
        plot(args.plot, fixed, closed)


if __name__ == '__main__':
    main()
