"""Fictional people and explicitly assigned demonstration-only brake settings.

No age/sex/training-to-strength formula is used. These values are neither
population measurements nor hardware-calibrated human safety limits.
"""
from dataclasses import dataclass
import math
from types import MappingProxyType

from common.control import BrakeParameters, brake_state
from common.fatigue import FatigueParameters


@dataclass(frozen=True)
class Character:
    character_id: str
    name: str
    age_years: int | None
    sex: str
    training_background: str
    brake_parameters: BrakeParameters
    parameter_source: str = 'demonstration_only'
    rise_time_constant_s: float = 0.
    fatigue_parameters: FatigueParameters = FatigueParameters()

    def __post_init__(self):
        if not math.isfinite(self.rise_time_constant_s) or self.rise_time_constant_s < 0:
            raise ValueError('rise_time_constant_s must be finite and nonnegative')


CHARACTERS = MappingProxyType({
    'demo': Character('demo', '原控制基线', None, '未设定', '无人物设定', BrakeParameters()),
    'person_01': Character('person_01', '示例人物甲', 24, '女',
        '休闲运动，未做专项训练（虚构设定）', BrakeParameters(stiffness=1.0, max_torque=.20),
        rise_time_constant_s=.15, fatigue_parameters=FatigueParameters(.06, .12, .50)),
    'person_02': Character('person_02', '示例人物乙', 38, '男',
        '规律力量训练（虚构设定）', BrakeParameters(stiffness=1.5, max_torque=.35),
        rise_time_constant_s=.4, fatigue_parameters=FatigueParameters(.04, .10, .45)),
    'person_03': Character('person_03', '示例人物丙', 52, '女',
        '有力量训练经历（虚构设定）', BrakeParameters(stiffness=1.8, max_torque=.50),
        rise_time_constant_s=.8, fatigue_parameters=FatigueParameters(.03, .08, .40)),
})


def resolve_character(character_id: str) -> Character:
    """Unknown protocol-valid IDs use the unchanged demo baseline."""
    return CHARACTERS.get(character_id, CHARACTERS['demo'])


def main():
    print('DEMONSTRATION VALUES ONLY - not human measurements or safety calibration.')
    print('Static base target M_base (N*m); boundary=-0.25 rad, velocity=-0.2 rad/s')
    print(f"{'angle_rad':>10}" + ''.join(f'{key:>12}' for key in CHARACTERS))
    for angle in (0., -.20, -.25, -.40, -.55):
        values = [brake_state(angle, -.2, person.brake_parameters).target_torque
                  for person in CHARACTERS.values()]
        print(f'{angle:10.2f}' + ''.join(f'{value:12.3f}' for value in values))


if __name__ == '__main__':
    main()
