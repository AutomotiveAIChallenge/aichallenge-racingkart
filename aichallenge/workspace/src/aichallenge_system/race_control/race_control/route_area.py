"""Drivable route area as numpy lanelet polygons with fast containment tests.

Pure geometry (no ROS dependency). Each polygon is pre-converted to numpy
edge arrays and a bounding box, so a containment query is a bbox reject per
polygon plus one vectorized ray-cast for the few candidates that remain.
"""

import numpy as np

from race_control.lanelet_map import LaneletMap


class RouteArea:
    def __init__(self, polygons):
        """polygons: iterable of coordinate lists [(x, y), ...] (>= 3 points)."""
        self.polygons = []  # (xs_tuple, ys_tuple) — kept for visualization
        self._edges = []  # (xi, yi, xj, yj) numpy arrays per polygon
        self._bboxes = []  # (x_min, x_max, y_min, y_max) per polygon
        for coords in polygons:
            if len(coords) < 3:
                continue
            xs = np.asarray([c[0] for c in coords], dtype=np.float64)
            ys = np.asarray([c[1] for c in coords], dtype=np.float64)
            self.polygons.append((tuple(xs), tuple(ys)))
            # edge i runs from vertex j = i-1 to vertex i (same as classic ray-cast)
            self._edges.append((xs, ys, np.roll(xs, 1), np.roll(ys, 1)))
            self._bboxes.append((xs.min(), xs.max(), ys.min(), ys.max()))

    @classmethod
    def from_osm(cls, osm_path):
        """Build from a lanelet2 .osm map: left bound + reversed right bound."""
        lmap = LaneletMap(osm_path)
        polygons = [
            lmap.way_coords(left) + list(reversed(lmap.way_coords(right)))
            for _lid, left, right in lmap.lanelets
        ]
        return cls(polygons)

    def __len__(self):
        return len(self._edges)

    def contains(self, x, y):
        """True if (x, y) is inside any lanelet polygon."""
        for (xi, yi, xj, yj), (x0, x1, y0, y1) in zip(self._edges, self._bboxes):
            if not (x0 <= x <= x1 and y0 <= y <= y1):
                continue
            crossing = (yi > y) != (yj > y)
            if not crossing.any():
                continue
            with np.errstate(divide="ignore", invalid="ignore"):
                x_int = (xj - xi) * (y - yi) / (yj - yi) + xi
            if np.count_nonzero(crossing & (x < x_int)) % 2:
                return True
        return False
