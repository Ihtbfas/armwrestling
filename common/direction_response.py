"""Continuous velocity factor for the optional narrow-profile simulation."""

DEFAULT_RETURN_MODE = 'symmetric'
RETURN_MODES = (DEFAULT_RETURN_MODE, 'relief')
RETURN_RATIO = .5


def direction_scale(mode, qdot, transition_speed):
    """Scale a request before fatigue and rise, using signed advance speed."""
    if mode == DEFAULT_RETURN_MODE:
        return 1.
    return_fraction = min(1., max(0., -qdot/transition_speed))
    return 1.-(1.-RETURN_RATIO)*return_fraction
