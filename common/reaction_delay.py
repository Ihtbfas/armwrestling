"""One wait per uninterrupted positive demand in the software simulation."""
import math

MAX_REACTION_DELAY_MS = 200


class ReactionDelay:
    def __init__(self, delay_ms):
        if type(delay_ms) is not int or not 0 <= delay_ms <= MAX_REACTION_DELAY_MS:
            raise ValueError(f'reaction delay must be an integer in 0..{MAX_REACTION_DELAY_MS} ms')
        self.delay_ms = delay_ms
        self.reset()

    def reset(self):
        self._started_at = self._last_tick = None
        self.elapsed_ms = 0.

    def update(self, request, now, max_gap_s):
        if request <= 0. or not math.isfinite(now):
            self.reset()
            return 0.
        if self._last_tick is None or not 0. <= now-self._last_tick <= max_gap_s:
            self._started_at = now
        self._last_tick = now
        elapsed_ms = (now-self._started_at)*1000
        # Ignore sub-nanosecond floating error at an exact scheduled boundary.
        if elapsed_ms >= self.delay_ms-1e-6:
            self.elapsed_ms = float(self.delay_ms)
            return request
        self.elapsed_ms = elapsed_ms
        return 0.
