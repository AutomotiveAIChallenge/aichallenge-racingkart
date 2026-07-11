"""Pure start-line crossing / lap timing logic (no ROS dependency)."""

import math


class LapTracker:
    """Counts start-line crossings with segment-extent and debounce checks.

    Behavior contract (must match the original lap_counter node exactly):
    - first crossing arms lap 0; each later valid crossing increments lap_count
    - a crossing closer than min_lap_time to lap_start is ignored entirely
      (lap_start is NOT reset by a debounced crossing)
    """

    def __init__(self, line_a, line_b, margin=2.0, min_lap_time=10.0):
        self._a = line_a
        self._ab = (line_b[0] - line_a[0], line_b[1] - line_a[1])
        ab_len = math.hypot(*self._ab)
        self._ab_len2 = ab_len * ab_len
        self._margin_t = margin / ab_len
        self._min_lap_time = min_lap_time
        self._prev_side = None
        self.lap_start = None
        self.lap_count = -1
        self.lap_times = []

    def update(self, x, y, stamp):
        """Feed one position sample; returns True when a crossing registers."""
        apx, apy = x - self._a[0], y - self._a[1]
        side = self._ab[0] * apy - self._ab[1] * apx > 0.0
        t = (self._ab[0] * apx + self._ab[1] * apy) / self._ab_len2
        on_segment = -self._margin_t <= t <= 1.0 + self._margin_t

        crossed = False
        if self._prev_side is not None and side != self._prev_side and on_segment:
            crossed = self._register(stamp)
        self._prev_side = side
        return crossed

    def _register(self, stamp):
        if self.lap_start is not None:
            lap_time = stamp - self.lap_start
            if lap_time < self._min_lap_time:
                return False
            self.lap_times.append(lap_time)
        self.lap_count += 1
        self.lap_start = stamp
        return True
