"""Export the actual shared PC state response at identical mechanical inputs."""
import argparse
import csv
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from common.characters import CHARACTERS
from common.state import BridgeState


def response_rows():
    rows = []
    for character in CHARACTERS:
        state = BridgeState()
        vr_seq = 0
        next_vr = 0.
        for i in range(101):
            now = i*.02
            state.receive('stm32', dict(v=1, type='telemetry', seq=i,
                angle_rad=-.4, velocity_rad_s=-.2, device_status='ready'), now)
            if now+1e-12 >= next_vr:
                state.receive('vr', dict(v=1, type='vr_update', seq=vr_seq,
                    character_id=character, match_state='running'), now)
                vr_seq += 1
                next_vr = now+.2
            command, status = state.outputs(now)
            rows.append(dict(time_s=round(now, 2), character_id=character,
                rise_time_constant_s=CHARACTERS[character].rise_time_constant_s,
                angle_rad=-.4, velocity_rad_s=-.2,
                base_target_torque_nm=status['base_target_torque_nm'],
                target_torque_nm=command['target_torque_nm'],
                established_fraction=(command['target_torque_nm']/status['base_target_torque_nm']
                    if status['base_target_torque_nm'] else 0.),
                brake_torque_nm=command['brake_torque_nm'],
                brake_power_w=command['brake_torque_nm']*-.2))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='CSV file; omitted prints CSV to stdout')
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
