"""Bounded equivalent fatigue; demonstration parameters, not human measurements."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class FatigueParameters:
    fatigue_rate: float = 0.          # 1/s; zero defaults disable demo fatigue.
    recovery_rate: float = 0.         # 1/s
    fatigue_loss_fraction: float = 0.

    def __post_init__(self):
        for name in ('fatigue_rate', 'recovery_rate', 'fatigue_loss_fraction'):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f'{name} must be finite and in [0, 1]')


class FatigueState:
    MAX_CONTINUOUS_GAP_S = .25

    def __init__(self):
        self.reset()

    def reset(self):
        self.value = 0.
        self.updated_at = None

    def freeze(self):
        """Preserve fatigue, discard the clock anchor for an unknown interval."""
        self.updated_at = None

    def update(self, load_proxy, parameters, now):
        if not math.isfinite(now) or not math.isfinite(load_proxy):
            self.freeze()
            return self.value
        previous = self.updated_at
        self.updated_at = now
        if previous is None or not 0 <= now-previous <= self.MAX_CONTINUOUS_GAP_S:
            return self.value
        load = min(1., max(0., load_proxy))
        buildup = parameters.fatigue_rate * load
        recovery = parameters.recovery_rate * (1-load)
        rate = buildup + recovery
        if rate:
            # Exact first-order step for this tick's constant load assumption.
            # No Euler overshoot; equivalent dt partitions give the same result.
            alpha = -math.expm1(-rate * (now-previous))
            self.value = min(1., max(0., self.value + alpha*(buildup/rate-self.value)))
        return self.value
