from __future__ import annotations


class LapCounter:
    """進捗wrap検出によるラップ計測 (AWSIM 0.70/0.30 ヒステリシス移植)。

    後退横断は debt として積み、ライン前後の往復でラップを稼げないようにする。
    横断時刻は前後サンプルの線形補間で推定する。"""

    def __init__(self, high: float = 0.70, low: float = 0.30):
        self.high = high
        self.low = low
        self.lap_count = 0
        self.lap_times: list[float] = []
        self.started = False
        self._lap_start_time: float | None = None
        self._prev: tuple[float, float] | None = None
        self._debt = 0

    def start(self, t: float) -> None:
        self.started = True
        self._lap_start_time = t

    def current_lap_elapsed(self, t: float) -> float:
        if not self.started or self._lap_start_time is None:
            return 0.0
        return t - self._lap_start_time

    def update(self, progress: float, t: float) -> bool:
        """進捗サンプルを1つ与える。ラップ完了時に True。"""
        completed = False
        if self._prev is not None:
            prev_p, prev_t = self._prev
            if prev_p >= self.high and progress <= self.low:
                gap = (1.0 - prev_p) + progress
                frac = (1.0 - prev_p) / gap if gap > 0 else 0.0
                t_cross = prev_t + frac * (t - prev_t)
                if self._debt > 0:
                    self._debt -= 1
                elif self.started:
                    self.lap_times.append(t_cross - self._lap_start_time)
                    self._lap_start_time = t_cross
                    self.lap_count += 1
                    completed = True
            elif prev_p <= self.low and progress >= self.high:
                self._debt += 1
        self._prev = (progress, t)
        return completed
