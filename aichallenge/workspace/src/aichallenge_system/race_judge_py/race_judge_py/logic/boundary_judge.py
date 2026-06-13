from __future__ import annotations

import numpy as np


def point_in_polygon(x: float, y: float, poly: np.ndarray) -> bool:
    """ray-casting法。poly は (N,2)。route_safety_monitor と同一アルゴリズム。"""
    px, py = poly[:, 0], poly[:, 1]
    pxj, pyj = np.roll(px, 1), np.roll(py, 1)
    crossing = (py > y) != (pyj > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xint = (pxj - px) * (y - py) / (pyj - py) + px
    return bool(np.count_nonzero(crossing & (x < xint)) % 2)


class BoundaryJudge:
    """laneletポリゴン包含によるコース境界(壁相当)判定。"""

    def __init__(self, polygons: list):
        self.polygons = [np.asarray(p, dtype=float) for p in polygons]

    def is_inside(self, x: float, y: float) -> bool:
        return any(point_in_polygon(x, y, poly) for poly in self.polygons)

    def footprint_outside(self, corners: np.ndarray) -> bool:
        """footprint のいずれかの角が全laneletの外に出たら True。"""
        return any(not self.is_inside(cx, cy) for cx, cy in corners)
