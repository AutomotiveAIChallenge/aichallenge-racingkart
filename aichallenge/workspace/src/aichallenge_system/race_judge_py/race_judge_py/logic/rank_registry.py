from __future__ import annotations


def compute_ranks(total_progress: dict) -> dict:
    """vehicle_number -> 1-based 順位。totalProgress(=lap数+progress01) 降順、
    同値は車番昇順 (AWSIM VehicleRankRegistry と同一)。"""
    order = sorted(total_progress.items(), key=lambda kv: (-kv[1], kv[0]))
    return {vehicle: i + 1 for i, (vehicle, _) in enumerate(order)}


class RankTracker:
    """persistence_sec 持続した順位変動のみ確定する(GNSSノイズによる
    順位フリッカ抑止、AWSIM RankChangeBannerLogic の persistence と同思想)。"""

    def __init__(self, persistence_sec: float = 1.0):
        self.persistence_sec = persistence_sec
        self._confirmed: dict | None = None
        self._pending: dict | None = None
        self._pending_since: float | None = None

    def update(self, ranks: dict, t: float) -> dict:
        if self._confirmed is None:
            self._confirmed = dict(ranks)
        elif ranks == self._confirmed:
            self._pending = None
            self._pending_since = None
        elif ranks != self._pending:
            self._pending = dict(ranks)
            self._pending_since = t
        elif t - self._pending_since >= self.persistence_sec:
            self._confirmed = self._pending
            self._pending = None
            self._pending_since = None
        return dict(self._confirmed)
