"""Small rising-only demonstration response; not a physiological muscle model."""
import math


class StrengthResponse:
    # A control gap longer than the default telemetry lifetime starts a new rise;
    # do not integrate unseen time even if fresh packets arrive just afterwards.
    MAX_CONTINUOUS_GAP_S = .25

    def __init__(self):
        self.reset()

    def reset(self):
        self.value = 0.
        self.updated_at = None

    def update(self, base, tau, now):
        if not all(math.isfinite(x) for x in (base, tau, now)) or base < 0 or tau < 0:
            self.reset()
            return 0.
        if tau == 0:
            self.value, self.updated_at = base, now
            return base # Exact old demo behavior, including its first tick.
        previous_time = self.updated_at
        self.updated_at = now
        if previous_time is None or now < previous_time or now-previous_time > self.MAX_CONTINUOUS_GAP_S:
            self.value = 0.
        elif base <= self.value:
            self.value = base # Drops and free-space entry are never delayed.
        else:
            alpha = -math.expm1(-(now-previous_time)/tau)
            self.value = min(base, max(0., self.value + alpha*(base-self.value)))
        return self.value
