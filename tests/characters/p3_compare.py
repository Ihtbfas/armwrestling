"""Deterministic V0.3 comparisons for the P3.0 software simulation."""
import argparse
import csv
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.characters import CHARACTERS
from common.state import BridgeState
from sim.mock_stm32 import INPUT_SIGN, STEP_SECONDS, SimulatedDevice, parse_sequence


def replay(character, fixed_telemetry):
    state = BridgeState()
    device = SimulatedDevice()
    state.receive('vr', dict(v=1, type='vr_update', seq=0,
                            character_id=character, match_state='waiting'), 0.)
    rows = []
    actions = (action for action, ticks in parse_sequence('down:0.6,back:0.5,release:0.4')
               for _ in range(ticks))
    last_match = 'waiting'
    # Fixed telemetry: sustain load for 10 s, pause 3 s, resume for 0.5 s,
    # then start a new round. Closed-loop comparison uses the same input per role.
    for tick in range(676 if fixed_telemetry else 76):
        now = tick * STEP_SECONDS
        match = 'running'
        if fixed_telemetry and tick >= 500:
            match = 'paused' if tick < 650 else 'running' if tick < 675 else 'waiting'
        if tick % 10 == 0 or match != last_match:
            state.receive('vr', dict(v=1, type='vr_update', seq=tick+1,
                                    character_id=character, match_state=match), now)
        last_match = match
        angle, speed = (-.4, -.2) if fixed_telemetry else (device.angle, device.velocity)
        state.receive('stm32', dict(v=1, type='telemetry', seq=tick, angle_rad=angle,
                                   velocity_rad_s=speed, device_status='ready'), now)
        command, status = state.outputs(now)
        action = 'fixed' if fixed_telemetry else next(actions, 'release')
        user_torque = None if fixed_telemetry else .6 * INPUT_SIGN[action]
        if not fixed_telemetry and tick < 75:
            device.receive(command, now)
            device.step(STEP_SECONDS, user_torque, now)
        rows.append(dict(
            test='fixed' if fixed_telemetry else 'closed_loop', character=character,
            time_s=round(now, 2), match=match, action=action, user_torque_nm=user_torque,
            angle_rad=angle, velocity_rad_s=speed,
            base_nm=status['base_target_torque_nm'],
            eligible_nm=status['eligible_target_torque_nm'],
            command_nm=command['target_torque_nm'], fatigue=status['fatigue_level'],
            brake_nm=None if fixed_telemetry or tick == 75 else device.brake,
            brake_power_w=None if fixed_telemetry or tick == 75 else device.power,
        ))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows = [row for fixed in (True, False) for character in CHARACTERS
            for row in replay(character, fixed)]
    with args.output.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == '__main__':
    main()
