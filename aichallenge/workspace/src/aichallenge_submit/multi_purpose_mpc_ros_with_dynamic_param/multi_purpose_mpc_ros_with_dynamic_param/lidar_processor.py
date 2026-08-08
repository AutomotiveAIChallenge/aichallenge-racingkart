#!/usr/bin/env python3
"""LiDAR scan processor for state-machine decisions.

Subscribes to ``/sensing/lidar/scan`` (``sensor_msgs/LaserScan``) produced
by ``laserscan_generator``, and provides processed metrics:

* **forward clearance** — closest range in the forward cone.
* **left / right overtake width** — available space on each side of a
  detected forward obstacle (Phase 2).

The processor is intentionally stateless between scans; each call to a
``get_*`` method works on the latest cached scan.
"""

from typing import Optional, Tuple
import math
import numpy as np

from rclpy.node import Node
from sensor_msgs.msg import LaserScan


class LidarProcessor:
    """Lightweight LiDAR scan analyser for the state machine."""

    def __init__(self, node: Node, scan_topic: str = "/sensing/lidar/scan") -> None:
        self._node = node
        self._scan: Optional[LaserScan] = None

        self._sub = node.create_subscription(
            LaserScan, scan_topic, self._callback, 1
        )

    # ------------------------------------------------------------------
    # ROS callback
    # ------------------------------------------------------------------

    def _callback(self, msg: LaserScan) -> None:
        self._scan = msg

    # ------------------------------------------------------------------
    # Public queries
    # ------------------------------------------------------------------

    @property
    def has_scan(self) -> bool:
        return self._scan is not None

    def get_forward_clearance(
        self, half_angle_deg: float = 30.0
    ) -> Optional[float]:
        """Minimum range in the forward cone (±``half_angle_deg``).

        Returns ``None`` when no scan is available or no valid ranges
        exist in the cone.
        """
        if self._scan is None:
            return None

        scan = self._scan
        half_rad = math.radians(half_angle_deg)

        ranges = np.array(scan.ranges, dtype=np.float64)
        n = len(ranges)
        if n == 0:
            return None

        # Build angle array
        angles = np.arange(n) * scan.angle_increment + scan.angle_min

        # Forward cone: angles near 0 (±half_rad)
        mask = np.abs(angles) <= half_rad

        # Valid range values
        valid = mask & (ranges >= scan.range_min) & (ranges <= scan.range_max)
        if not np.any(valid):
            return None

        return float(np.min(ranges[valid]))

    def get_overtake_widths(
        self,
        obstacle_angle_deg: float = 0.0,
        search_half_angle_deg: float = 60.0,
    ) -> Tuple[float, float]:
        """Estimate left / right clearance around a forward obstacle.

        This is a simplified heuristic for Phase 2:
        * Partition the scan into *left* (positive angles) and *right*
          (negative angles) relative to the obstacle bearing.
        * For each side, take the **maximum** range within a search cone as
          a proxy for available width.

        Returns ``(left_width, right_width)`` in metres. Defaults to
        ``(0.0, 0.0)`` when no scan is available.
        """
        if self._scan is None:
            return (0.0, 0.0)

        scan = self._scan
        ranges = np.array(scan.ranges, dtype=np.float64)
        n = len(ranges)
        if n == 0:
            return (0.0, 0.0)

        angles = np.arange(n) * scan.angle_increment + scan.angle_min
        obs_rad = math.radians(obstacle_angle_deg)
        half_rad = math.radians(search_half_angle_deg)

        valid = (ranges >= scan.range_min) & (ranges <= scan.range_max)

        # Left side: angles > obstacle bearing, within search cone
        left_mask = valid & (angles > obs_rad) & (angles <= obs_rad + half_rad)
        # Right side: angles < obstacle bearing, within search cone
        right_mask = valid & (angles < obs_rad) & (angles >= obs_rad - half_rad)

        left_width = float(np.max(ranges[left_mask])) if np.any(left_mask) else 0.0
        right_width = float(np.max(ranges[right_mask])) if np.any(right_mask) else 0.0

        return (left_width, right_width)
