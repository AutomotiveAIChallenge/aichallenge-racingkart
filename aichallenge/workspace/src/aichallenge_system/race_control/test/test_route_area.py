import numpy as np
import pytest

from race_control.route_area import RouteArea

SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
TRIANGLE = [(20.0, 0.0), (30.0, 0.0), (25.0, 10.0)]


@pytest.fixture
def area():
    return RouteArea([SQUARE, TRIANGLE])


class TestContains:
    def test_inside_square(self, area):
        assert area.contains(5.0, 5.0) is True

    def test_inside_triangle(self, area):
        assert area.contains(25.0, 2.0) is True

    def test_outside_all(self, area):
        assert area.contains(15.0, 5.0) is False
        assert area.contains(-1.0, 5.0) is False

    def test_outside_bbox_short_circuits(self, area):
        assert area.contains(1000.0, 1000.0) is False

    def test_degenerate_polygon_dropped(self):
        area = RouteArea([[(0.0, 0.0), (1.0, 1.0)], SQUARE])
        assert len(area) == 1

    def test_matches_reference_raycast(self, area):
        """既存実装(純Python ray-cast)と結果が一致すること。"""

        def reference(x, y, poly):
            n = len(poly)
            j = n - 1
            inside = False
            for i in range(n):
                xi, yi = poly[i]
                xj, yj = poly[j]
                if ((yi > y) != (yj > y)) and (
                    x < (xj - xi) * (y - yi) / (yj - yi) + xi
                ):
                    inside = not inside
                j = i
            return inside

        rng = np.random.default_rng(0)
        pts = rng.uniform(-5.0, 35.0, size=(500, 2))
        for x, y in pts:
            expected = reference(x, y, SQUARE) or reference(x, y, TRIANGLE)
            assert area.contains(float(x), float(y)) == expected, (x, y)
