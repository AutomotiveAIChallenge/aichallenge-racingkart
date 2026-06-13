import math

from race_judge_py.logic.collision_judge import CollisionJudge, OtherVehicle


def make_judge():
    return CollisionJudge(collision_distance_m=1.5, v2x_timeout_sec=1.0)


def test_rear_end_penalizes_self():
    # 自車が東向き、他車が1m前方 → 自車が追突側
    j = make_judge()
    others = [OtherVehicle("d2", 1.0, 0.0, stamp=10.0)]
    assert j.rear_ended_ids(0.0, 0.0, 0.0, now=10.0, others=others) == ["d2"]


def test_being_hit_from_behind_no_penalty():
    # 他車が1m後方 → 自車は被追突側、ペナルティなし
    j = make_judge()
    others = [OtherVehicle("d2", -1.0, 0.0, stamp=10.0)]
    assert j.rear_ended_ids(0.0, 0.0, 0.0, now=10.0, others=others) == []


def test_far_vehicle_ignored():
    j = make_judge()
    others = [OtherVehicle("d2", 5.0, 0.0, stamp=10.0)]
    assert j.rear_ended_ids(0.0, 0.0, 0.0, now=10.0, others=others) == []


def test_stale_v2x_ignored():
    j = make_judge()
    others = [OtherVehicle("d2", 1.0, 0.0, stamp=5.0)]   # 5秒前の情報
    assert j.rear_ended_ids(0.0, 0.0, 0.0, now=10.0, others=others) == []


def test_yaw_determines_front_halfplane():
    # 自車が北向き(yaw=pi/2)、他車は真北1m → 追突側
    j = make_judge()
    others = [OtherVehicle("d2", 0.0, 1.0, stamp=0.0)]
    assert j.rear_ended_ids(0.0, 0.0, math.pi / 2.0, now=0.0, others=others) == ["d2"]
    # 自車が南向きなら他車は後方 → ペナルティなし
    assert j.rear_ended_ids(0.0, 0.0, -math.pi / 2.0, now=0.0, others=others) == []
