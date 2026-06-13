from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class OtherVehicle:
    vehicle_id: str
    x: float
    y: float
    stamp: float


class CollisionJudge:
    """V2X共有位置による車両間接触判定。

    AWSIMの BumperFront × BumperRear トリガーペアの置換: 中心間距離 <
    collision_distance_m で接触候補とし、相手が自車前方半平面にいる側
    (=追突側)のみにペナルティを科す。"""

    def __init__(self, collision_distance_m: float = 1.5, v2x_timeout_sec: float = 1.0):
        self.collision_distance_m = collision_distance_m
        self.v2x_timeout_sec = v2x_timeout_sec

    def rear_ended_ids(self, x: float, y: float, yaw: float, now: float, others: list) -> list:
        """自車が追突したと判定される相手 vehicle_id のリスト。"""
        hit = []
        for o in others:
            if now - o.stamp > self.v2x_timeout_sec:
                continue
            dx, dy = o.x - x, o.y - y
            if math.hypot(dx, dy) >= self.collision_distance_m:
                continue
            bearing = math.atan2(dy, dx)
            diff = (bearing - yaw + math.pi) % (2.0 * math.pi) - math.pi
            if abs(diff) < math.pi / 2.0:
                hit.append(o.vehicle_id)
        return hit
