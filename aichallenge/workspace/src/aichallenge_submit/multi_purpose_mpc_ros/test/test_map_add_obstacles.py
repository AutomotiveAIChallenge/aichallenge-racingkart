"""Map.add_obstacles must not raise when an obstacle overlaps the grid edge.

The circular mask is always (2r, 2r) but the destination slice is clipped by
numpy at the far edge and wraps / empties at the near edge (negative start),
so the boolean index no longer matches and IndexError kills the controller
the first time an obstacle (e.g. a V2X opponent) comes within r px of an edge.
"""
import numpy as np
import pytest

from multi_purpose_mpc_ros.core.map import Obstacle

from mpc_fixture import ring_map


def _reference_paint(data, cx_px, cy_px, radius_px):
    """The pre-fix algorithm, for obstacles fully inside the grid."""
    y, x = np.ogrid[-radius_px: radius_px, -radius_px: radius_px]
    index = x ** 2 + y ** 2 <= radius_px ** 2
    data[cy_px - radius_px:cy_px + radius_px,
         cx_px - radius_px:cx_px + radius_px][index] = 0


@pytest.mark.parametrize("cx, cy", [
    (39.95, 0.0),     # right edge  -> slice clipped at the far end
    (0.0, -39.95),    # bottom edge -> slice clipped at the far end
    (-39.95, 0.0),    # left edge   -> negative start wraps to an empty slice
    (0.0, 39.95),     # top edge    -> negative start wraps to an empty slice
    (-39.95, 39.95),  # corner
])
def test_obstacle_on_grid_edge_does_not_raise(cx, cy):
    m = ring_map()
    m.data[:] = 1
    m.add_obstacles([Obstacle(cx=cx, cy=cy, radius=1.25)])
    cx_px, cy_px = m.w2m(cx, cy)
    assert m.data[cy_px, cx_px] == 0          # the part inside the grid is painted
    assert (m.data == 0).sum() > 0


def test_interior_obstacle_is_painted_exactly_as_before():
    m = ring_map()
    expected = m.data.copy()
    obstacle = Obstacle(cx=30.0, cy=0.5, radius=1.25)
    radius_px = int(np.ceil(obstacle.radius / m.resolution))
    cx_px, cy_px = m.w2m(obstacle.cx, obstacle.cy)
    _reference_paint(expected, cx_px, cy_px, radius_px)

    m.add_obstacles([obstacle])

    assert np.array_equal(m.data, expected)
