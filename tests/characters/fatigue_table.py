"""30-second deterministic PC model comparison; no human/hardware measurement."""
import argparse
import csv
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from common.characters import CHARACTERS
from common.state import BridgeState
from common.strength_response import StrengthResponse


def response_rows():
    rows = []
    for character, profile in CHARACTERS.items():
        state, reference = BridgeState(), StrengthResponse()
        for i in range(301):
            now = i*.1
            if now < 12:
                phase, match, angle = 'loaded', 'running', -.4
            elif now < 16:
                phase, match, angle = 'low_load', 'running', -.26
            elif now < 22:
                phase, match, angle = 'pause_recovery', 'paused', -.4
            elif now < 28:
                phase, match, angle = 'resume', 'running', -.4
            else:
                phase, match, angle = 'new_round', 'waiting', -.4
            state.receive('stm32', dict(v=1, type='telemetry', seq=i,
                angle_rad=angle, velocity_rad_s=-.2, device_status='ready'), now)
            state.receive('vr', dict(v=1, type='vr_update', seq=i,
                character_id=character, match_state=match), now)
            command, status = state.outputs(now)
            if match == 'running':
                baseline = reference.update(status['base_target_torque_nm'], profile.rise_time_constant_s, now)
            else:
                reference.reset()
                baseline = 0.
            assert 0 <= command['target_torque_nm'] <= status['eligible_target_torque_nm'] <= status['base_target_torque_nm']
            assert 0 <= status['fatigue_level'] <= 1
            assert command['brake_torque_nm'] * -.2 <= 0
            row = dict(character_id=character, time_s=round(now, 1), phase=phase,
                angle_rad=angle, velocity_rad_s=-.2,
                base_target_torque_nm=status['base_target_torque_nm'],
                eligible_target_torque_nm=status['eligible_target_torque_nm'],
                output_torque_nm=command['target_torque_nm'],
                v02_reference_torque_nm=baseline,
                fatigue_level=status['fatigue_level'], fatigue_capacity=status['fatigue_capacity'],
                load_proxy=status['fatigue_load_proxy'],
                brake_torque_nm=command['brake_torque_nm'], brake_power_w=command['brake_torque_nm']*-.2)
            rows.append({key: round(value, 8) if isinstance(value, float) else value for key,value in row.items()})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='CSV destination; otherwise print CSV')
    args = parser.parse_args()
    rows = response_rows()
    stream = args.output.open('w', encoding='utf-8', newline='') if args.output else sys.stdout
    try:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    finally:
        if args.output:
            stream.close()


if __name__ == '__main__':
    main()
