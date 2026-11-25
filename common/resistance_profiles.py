"""Angle-to-target curves for the V0.5-A software experiment."""
import math

DEFAULT_RESISTANCE_PROFILE = 'legacy'
RESISTANCE_PROFILES = (DEFAULT_RESISTANCE_PROFILE, 'narrow', 'start-resistance')
WINNING_PROGRESS_RAD = math.radians(25)  # VR demo mapping, not a hardware limit.
NARROW_FREE_RAD = .05
START_FADE_RAD = .05
START_FRACTION = .15


def profile_target(profile, brake, parameters):
    """Use the original curve from the shared anchor onward."""
    if profile not in RESISTANCE_PROFILES:
        raise ValueError(f'unknown resistance profile: {profile}')
    q = brake.relative_angle
    if profile == DEFAULT_RESISTANCE_PROFILE or q >= WINNING_PROGRESS_RAD:
        return brake.target_torque

    anchor = min(parameters.max_torque,
                 parameters.stiffness*max(0., WINNING_PROGRESS_RAD-parameters.free_boundary))
    if profile == 'narrow':
        return anchor*max(0., q-NARROW_FREE_RAD)/(WINNING_PROGRESS_RAD-NARROW_FREE_RAD)

    at_zero = START_FRACTION*anchor
    if q < 0.:
        return at_zero*max(0., (q+START_FADE_RAD)/START_FADE_RAD)
    return at_zero+(anchor-at_zero)*q/WINNING_PROGRESS_RAD
