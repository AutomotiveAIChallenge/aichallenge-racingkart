import json

import pytest

from race_judge_py.logic.penalty import PenaltyKind, PenaltyTracker
from race_judge_py.logic.result_writer import atomic_write_json, build_details, build_summary

# AWSIM実出力 (output/20260525-124231/) と同一のキー集合
SUMMARY_VEHICLE_KEYS = {
    "vehicle_number", "vehicle_name", "final_position", "finished", "lap_count",
    "laps", "min_lap_time", "max_lap_time", "avg_lap_time", "total_lap_time",
}
DETAILS_KEYS = {
    "schema_version", "vehicle_name", "vehicle_number", "finished", "lap_count",
    "required_laps", "session_timeout", "min_lap_time", "avg_lap_time",
    "total_lap_time", "laps", "penalty_count", "penalty_total_seconds",
    "penalty_events", "penalty_by_kind",
}


def test_summary_schema_v2():
    s = build_summary(
        required_laps=6,
        timeout=480.0,
        vehicles=[
            {"vehicle_number": 1, "vehicle_name": "GoKart1", "final_position": 2,
             "finished": False, "laps": []},
            {"vehicle_number": 2, "vehicle_name": "GoKart2", "final_position": 1,
             "finished": True, "laps": [65.0, 64.0]},
        ],
    )
    assert s["schema_version"] == "v2"
    assert s["session"] == {"required_laps": 6, "timeout": 480.0, "total_vehicles": 2}
    assert set(s["vehicles"][0].keys()) == SUMMARY_VEHICLE_KEYS
    # final_position 順に並ぶ
    assert [v["final_position"] for v in s["vehicles"]] == [1, 2]
    assert s["vehicles"][0]["total_lap_time"] == pytest.approx(129.0)
    assert s["vehicles"][0]["min_lap_time"] == pytest.approx(64.0)
    # ルートの冗長フィールド(AWSIM互換)
    assert s["laps"] == [] and s["num_laps"] == 0


def test_empty_laps_stats_are_zero():
    s = build_summary(required_laps=6, timeout=480.0, vehicles=[
        {"vehicle_number": 1, "vehicle_name": "GoKart1", "final_position": 1,
         "finished": False, "laps": []}])
    v = s["vehicles"][0]
    assert v["min_lap_time"] == 0.0 and v["avg_lap_time"] == 0.0


def test_details_schema_v3():
    pt = PenaltyTracker(cooldown_sec=2.0)
    pt.trigger(PenaltyKind.WALL, lap=1, race_time=12.9)
    pt.finalize_all()
    d = build_details(
        vehicle_name="GoKart1", vehicle_number=1, finished=False,
        laps=[60.0], required_laps=6, session_timeout=480.0,
        penalty_events=pt.events, penalty_by_kind=pt.by_kind(),
        penalty_total_seconds=pt.union_total_seconds(),
    )
    assert set(d.keys()) == DETAILS_KEYS
    assert d["schema_version"] == "v3"
    assert d["penalty_count"] == 1
    assert d["penalty_events"][0]["kind"] == "wall"
    assert set(d["penalty_by_kind"].keys()) == {"crash", "wall", "over"}
    assert set(d["penalty_by_kind"]["wall"].keys()) == {"count", "total_seconds"}


def test_atomic_write(tmp_path):
    path = tmp_path / "result-summary.json"
    atomic_write_json(str(path), {"a": 1})
    assert json.loads(path.read_text()) == {"a": 1}
    assert not (tmp_path / "result-summary.json.tmp").exists()
