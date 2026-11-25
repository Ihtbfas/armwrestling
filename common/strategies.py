"""Demonstration policies for using a fraction of a character's capacity."""
import math

DEFAULT_STRATEGY = 'balanced'
STRATEGY_NAMES = (DEFAULT_STRATEGY, 'defensive', 'rhythmic')
DEFENSIVE_MIN = .60
# Current VR demonstration threshold (30 visual degrees at 1.2x), not a hardware limit.
DEFENSIVE_FULL_RAD = math.radians(25)
RHYTHMIC_MIN = .65
RHYTHMIC_PERIOD_S = 4.


def strategy_scale(strategy, progress_rad, free_boundary, active_time_s):
    if strategy == 'balanced' or strategy == 'demo':
        return 1.
    if strategy == 'defensive':
        if progress_rad >= DEFENSIVE_FULL_RAD:
            return 1.
        fraction = max(0., progress_rad-free_boundary) / max(1e-9, DEFENSIVE_FULL_RAD-free_boundary)
        return DEFENSIVE_MIN + (1-DEFENSIVE_MIN)*fraction
    if strategy == 'rhythmic':
        phase = (active_time_s % RHYTHMIC_PERIOD_S) / RHYTHMIC_PERIOD_S
        return RHYTHMIC_MIN + (1-RHYTHMIC_MIN)*(.5+.5*math.cos(math.tau*phase))
    raise ValueError(f'unknown strategy: {strategy}')


class ActiveMatchClock:
    """Accumulate only observed, uninterrupted running intervals."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.elapsed = 0.
        self.freeze()

    def freeze(self):
        self._last_tick = None

    def advance(self, now, max_gap):
        if not math.isfinite(now):
            self.freeze()
            return
        if self._last_tick is not None:
            dt = now-self._last_tick
            if 0 < dt <= max_gap:
                self.elapsed += dt
        self._last_tick = now
