"""Fictional people and explicitly assigned demonstration-only brake settings.

No age/sex/training-to-strength formula is used. These values are neither
population measurements nor hardware-calibrated human safety limits.
"""
from dataclasses import dataclass
from types import MappingProxyType

from common.control import BrakeParameters, brake_state


@dataclass(frozen=True)
class Character:
    character_id: str
    name: str
    age_years: int | None
    sex: str
    training_background: str
    brake_parameters: BrakeParameters
    parameter_source: str = 'demonstration_only'


CHARACTERS = MappingProxyType({
    'demo': Character('demo', '原控制基线', None, '未设定', '无人物设定', BrakeParameters()),
    'person_01': Character('person_01', '示例人物甲', 24, '女',
        '休闲运动，未做专项训练（虚构设定）', BrakeParameters(stiffness=1.0, max_torque=.20)),
    'person_02': Character('person_02', '示例人物乙', 38, '男',
        '规律力量训练（虚构设定）', BrakeParameters(stiffness=1.5, max_torque=.35)),
    'person_03': Character('person_03', '示例人物丙', 52, '女',
        '有力量训练经历（虚构设定）', BrakeParameters(stiffness=1.8, max_torque=.50)),
})


def resolve_character(character_id: str) -> Character:
    """Unknown protocol-valid IDs use the unchanged demo baseline."""
    return CHARACTERS.get(character_id, CHARACTERS['demo'])


def main():
    print('DEMONSTRATION VALUES ONLY - not human measurements or safety calibration.')
    print('Target brake magnitude (N*m); common boundary=-0.25 rad, velocity=-0.2 rad/s')
    print(f"{'angle_rad':>10}" + ''.join(f'{key:>12}' for key in CHARACTERS))
    for angle in (0., -.20, -.25, -.40, -.55):
        values = [brake_state(angle, -.2, person.brake_parameters).target_torque
                  for person in CHARACTERS.values()]
        print(f'{angle:10.2f}' + ''.join(f'{value:12.3f}' for value in values))


if __name__ == '__main__':
    main()
