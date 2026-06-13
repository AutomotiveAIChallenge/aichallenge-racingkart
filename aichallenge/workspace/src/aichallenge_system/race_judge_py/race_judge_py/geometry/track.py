from __future__ import annotations

import numpy as np


class Track:
    """Closed-loop course centerline with arc-length parameterization.

    Port of AWSIM RacingTrack (pure geometry, no physics engine).
    points は map 座標系の (N,2)。最後の点から最初の点へ自動的に閉じる。
    """

    def __init__(self, points: np.ndarray):
        pts = np.asarray(points, dtype=float)
        if pts.ndim != 2 or pts.shape[0] < 3 or pts.shape[1] != 2:
            raise ValueError("points must be (N>=3, 2)")
        seg = np.roll(pts, -1, axis=0) - pts
        keep = np.linalg.norm(seg, axis=1) > 1e-9
        self.points = pts[keep]
        self._seg_vec = np.roll(self.points, -1, axis=0) - self.points
        self._seg_len = np.linalg.norm(self._seg_vec, axis=1)
        self.cum = np.concatenate(([0.0], np.cumsum(self._seg_len)))
        self.total_length = float(self.cum[-1])
        self._origin_s = 0.0

    def project(self, p, hint: int | None = None, window: int = 20):
        """p に最も近いトラック上の点を返す: (弧長s, セグメント番号, 距離)。

        hint(セグメント番号)があれば ±window 個のセグメントだけ探索する。"""
        p = np.asarray(p, dtype=float)
        n = len(self.points)
        if hint is None or window * 2 + 1 >= n:
            idxs = np.arange(n)
        else:
            idxs = np.arange(hint - window, hint + window + 1) % n
        a = self.points[idxs]
        v = self._seg_vec[idxs]
        seg_len2 = np.maximum((v * v).sum(axis=1), 1e-12)
        t = np.clip(((p - a) * v).sum(axis=1) / seg_len2, 0.0, 1.0)
        proj = a + v * t[:, None]
        d2 = ((proj - p) ** 2).sum(axis=1)
        k = int(np.argmin(d2))
        i = int(idxs[k])
        s = float(self.cum[i] + t[k] * self._seg_len[i])
        return s % self.total_length, i, float(np.sqrt(d2[k]))

    def set_origin(self, p) -> None:
        """進捗0の基準点（スタートライン位置）を p の投影点に設定する。"""
        s, _, _ = self.project(p)
        self._origin_s = s

    def progress_at(self, p, hint: int | None = None):
        """(progress01 in [0,1), セグメント番号) を返す。"""
        s, i, _ = self.project(p, hint=hint)
        rel = (s - self._origin_s) % self.total_length
        return rel / self.total_length, i
