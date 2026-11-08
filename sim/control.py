"""Pure angle-dependent passive brake law; all angles are in radians."""

from dataclasses import dataclass


@dataclass(frozen=True)
class BrakeParameters:
    initial_angle: float = 0.0
    forward_sign: int = -1  # Winning-side downstroke is negative joint rotation.
    free_boundary: float = 0.25
    stiffness: float = 2.0
    max_torque: float = 0.8
    speed_transition: float = 0.05

    def __post_init__(self):
        if self.forward_sign not in (-1, 1):
            raise ValueError("forward_sign must be -1 or 1")
        if self.free_boundary < 0 or self.stiffness < 0 or self.max_torque < 0:
            raise ValueError("boundary, stiffness and maximum torque must be nonnegative")
        if self.speed_transition <= 0:
            raise ValueError("speed_transition must be positive")


@dataclass(frozen=True)
class BrakeState:
    relative_angle: float
    beyond_boundary: bool
    target_torque: float
    applied_torque: float


def brake_state(angle: float, angular_speed: float, parameters: BrakeParameters) -> BrakeState:
    """Calculate brake torque. Its product with angular_speed is never positive."""
    relative_angle = parameters.forward_sign * (angle - parameters.initial_angle)
    overshoot = max(0.0, relative_angle - parameters.free_boundary)
    target_torque = min(parameters.max_torque, parameters.stiffness * overshoot)

    # A small linear transition makes torque continuous through zero speed.
    speed_fraction = max(-1.0, min(1.0, angular_speed / parameters.speed_transition))
    applied_torque = -target_torque * speed_fraction
    return BrakeState(relative_angle, overshoot > 0.0, target_torque, applied_torque)
