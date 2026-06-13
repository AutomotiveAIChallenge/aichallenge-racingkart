import math

import numpy as np
import pytest

from race_judge_py.geometry.footprint import footprint_corners
from race_judge_py.logic.boundary_judge import BoundaryJudge, point_in_polygon

SQUARE = np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]])


def test_point_in_polygon_inside():
    assert point_in_polygon(5.0, 5.0, SQUARE)


def test_point_in_polygon_outside():
    assert not point_in_polygon(15.0, 5.0, SQUARE)


def test_footprint_corners_axis_aligned():
    corners = footprint_corners(5.0, 5.0, 0.0, length=2.0, width=1.0)
    assert corners.shape == (4, 2)
    xs, ys = corners[:, 0], corners[:, 1]
    assert xs.max() == pytest.approx(6.0)
    assert xs.min() == pytest.approx(4.0)
    assert ys.max() == pytest.approx(5.5)
    assert ys.min() == pytest.approx(4.5)


def test_footprint_antenna_offset():
    # アンテナが車体中心より0.5m前方 → 車体中心は(4.5, 5.0)
    corners = footprint_corners(5.0, 5.0, 0.0, length=2.0, width=1.0, antenna_offset_x=0.5)
    assert corners[:, 0].max() == pytest.approx(5.5)


def test_footprint_rotated_90deg():
    corners = footprint_corners(0.0, 0.0, math.pi / 2.0, length=2.0, width=1.0)
    assert corners[:, 1].max() == pytest.approx(1.0)
    assert corners[:, 0].max() == pytest.approx(0.5)


def test_boundary_judge_inside_outside():
    judge = BoundaryJudge([SQUARE])
    inside = footprint_corners(5.0, 5.0, 0.0, length=2.0, width=1.0)
    sticking_out = footprint_corners(9.5, 5.0, 0.0, length=2.0, width=1.0)
    assert not judge.footprint_outside(inside)
    assert judge.footprint_outside(sticking_out)


def test_boundary_judge_multiple_polygons():
    poly2 = SQUARE + np.array([10.0, 0.0])  # x:10..20 の隣接ポリゴン
    judge = BoundaryJudge([SQUARE, poly2])
    # 角が「どのポリゴンにも入っていない」時のみ外
    corners = footprint_corners(10.0, 5.0, 0.0, length=2.0, width=1.0)
    assert not judge.footprint_outside(corners)
