from __future__ import annotations

import numpy as np


def footprint_corners(x, y, yaw, length=2.0, width=1.3, antenna_offset_x=0.0):
    """map座標系での車体矩形の4角点。(x, y) はGNSSアンテナ位置、
    antenna_offset_x はアンテナの車体中心からの前方オフセット[m]。"""
    cx = x - antenna_offset_x * np.cos(yaw)
    cy = y - antenna_offset_x * np.sin(yaw)
    hl, hw = length / 2.0, width / 2.0
    local = np.array([[hl, hw], [hl, -hw], [-hl, -hw], [-hl, hw]])
    c, s = np.cos(yaw), np.sin(yaw)
    rot = np.array([[c, -s], [s, c]])
    return local @ rot.T + np.array([cx, cy])
