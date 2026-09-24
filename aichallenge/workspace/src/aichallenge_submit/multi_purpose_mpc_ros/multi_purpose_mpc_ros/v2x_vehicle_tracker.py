"""Per-vehicle finite-difference velocity tracker for V2X positions.

This module is intentionally pure Python with no rclpy dependency: it
operates on duck-typed messages whose attributes match
``v2x_msgs/V2XVehiclePositionArray``. That keeps it cheap to unit-test
and reusable from non-ROS contexts (e.g. offline replay of rosbag CSVs).
"""

import math
from collections import deque
from typing import Deque, Dict, List, Tuple


def _stamp_to_seconds(stamp) -> float:
    return float(stamp.sec) + float(stamp.nanosec) * 1e-9


class V2XVehicleTracker:
    """Tracks the latest two samples per ``vehicle_id`` and exposes
    constant-velocity predictions over a caller-provided time grid."""

    def __init__(self, v_max_safety: float, position_jump_threshold: float, warn_callback=None,
                 hold_time_s: float = 0.0):
        self._v_max_safety = float(v_max_safety)
        self._jump_thresh = float(position_jump_threshold)
        self._warn = warn_callback if warn_callback is not None else (lambda _msg: None)
        self._samples: Dict[str, Deque[Tuple[float, float, float]]] = {}
        self._velocities: Dict[str, Tuple[float, float]] = {}
        self._active: List[str] = []
        # Dropout hold: keep a vehicle missing from the latest message for
        # hold_time_s after its last sample (fail-closed). 0.0 = off, i.e. the
        # active set is exactly the latest message (historical behaviour).
        # 最新メッセージに無い車両を hold_time_s の間だけ保持する（0.0 で従来挙動）。
        self._hold_time_s = max(0.0, float(hold_time_s))
        self._last_seen: Dict[str, float] = {}
        self._newest_t: float = 0.0

    def update(self, msg) -> None:
        active: List[str] = []
        # Advance the tracker clock from the array header even when
        # ``vehicles`` is empty, so held vehicles can still expire. Per-vehicle
        # stamps (used for velocity) are tracked separately below.
        # vehicles が空でも配列ヘッダーの時刻で時計を進め、保持中の車両を失効させる。
        header_t = _stamp_to_seconds(msg.header.stamp)
        if header_t > self._newest_t:
            self._newest_t = header_t
        for v in msg.vehicles:
            vid = v.vehicle_id
            t = _stamp_to_seconds(v.header.stamp)
            x = float(v.position.x)
            y = float(v.position.y)
            buf = self._samples.setdefault(vid, deque(maxlen=2))

            # Detect a position jump against the previous sample (if any).
            jumped = False
            if buf:
                _t_prev, x_prev, y_prev = buf[-1]
                if math.hypot(x - x_prev, y - y_prev) > self._jump_thresh:
                    buf.clear()
                    jumped = True
                    self._warn(
                        f"V2X: position jump for vehicle '{vid}' "
                        f"(>{self._jump_thresh} m) — velocity reset")

            buf.append((t, x, y))

            if jumped or len(buf) < 2:
                self._velocities[vid] = (0.0, 0.0)
            else:
                t0, x0, y0 = buf[0]
                t1, x1, y1 = buf[1]
                dt = t1 - t0
                if dt > 0.0:
                    vx = (x1 - x0) / dt
                    vy = (y1 - y0) / dt
                    if math.hypot(vx, vy) > self._v_max_safety:
                        self._velocities[vid] = (0.0, 0.0)
                        self._warn(
                            f"V2X: velocity for vehicle '{vid}' exceeds "
                            f"{self._v_max_safety} m/s — clamped to zero")
                    else:
                        self._velocities[vid] = (vx, vy)
                else:
                    self._velocities[vid] = (0.0, 0.0)
            active.append(vid)
            self._last_seen[vid] = t
            if t > self._newest_t:
                self._newest_t = t
        self._active = active

    def velocity(self, vehicle_id: str) -> Tuple[float, float]:
        return self._velocities.get(vehicle_id, (0.0, 0.0))

    def predict_positions(
        self, vehicle_id: str, t_samples
    ) -> List[Tuple[float, float]]:
        buf = self._samples.get(vehicle_id)
        if not buf:
            return []
        t_last, x_last, y_last = buf[-1]
        vx, vy = self._velocities.get(vehicle_id, (0.0, 0.0))
        # A held vehicle is extrapolated from the age of its last sample, so it
        # is predicted where it is now, not where it was last seen.
        age = max(0.0, self._newest_t - t_last) if self._hold_time_s > 0.0 else 0.0
        return [(x_last + vx * (age + t), y_last + vy * (age + t)) for t in t_samples]

    def active_vehicle_ids(self) -> List[str]:
        if self._hold_time_s <= 0.0:
            return list(self._active)
        out = list(self._active)
        seen = set(out)
        for vid, t_seen in self._last_seen.items():
            if vid not in seen and self._newest_t - t_seen <= self._hold_time_s:
                out.append(vid)
        return out

    def held_vehicle_ids(self) -> List[str]:
        """Vehicles kept only by the hold (absent from the latest message)."""
        seen = set(self._active)
        return [vid for vid in self.active_vehicle_ids() if vid not in seen]

    def predict_all(self, t_samples) -> Dict[str, List[Tuple[float, float]]]:
        return {vid: self.predict_positions(vid, t_samples)
                for vid in self.active_vehicle_ids()}


def predictions_to_obstacles(predictions, vehicle_radius: float, obstacle_cls=None):
    """Flatten a ``{vehicle_id: [(x, y), ...]}`` mapping into a list of
    circular obstacles consumable by ``multi_purpose_mpc_ros.core.map``.

    ``obstacle_cls`` is injectable for testability; production callers
    leave it as ``None`` to use ``core.map.Obstacle``. The deferred
    import keeps this module's load time fast and lets the unit tests
    on hosts without ``scikit-image`` exercise the helper with a stub
    dataclass.
    """
    if obstacle_cls is None:
        from multi_purpose_mpc_ros.core.map import Obstacle as obstacle_cls
    out = []
    for _vid, points in predictions.items():
        for x, y in points:
            out.append(obstacle_cls(cx=x, cy=y, radius=vehicle_radius))
    return out
