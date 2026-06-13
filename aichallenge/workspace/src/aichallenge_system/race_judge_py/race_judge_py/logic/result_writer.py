from __future__ import annotations

import json
import os


def _lap_stats(laps: list) -> dict:
    if not laps:
        return {"min_lap_time": 0.0, "max_lap_time": 0.0, "avg_lap_time": 0.0,
                "total_lap_time": 0.0}
    return {
        "min_lap_time": min(laps),
        "max_lap_time": max(laps),
        "avg_lap_time": sum(laps) / len(laps),
        "total_lap_time": sum(laps),
    }


def atomic_write_json(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=4)
    os.replace(tmp, path)


def build_summary(required_laps: int, timeout: float, vehicles: list) -> dict:
    """result-summary.json schema v2 (AWSIM互換)。
    vehicles: vehicle_number/vehicle_name/final_position/finished/laps[] を持つ dict のリスト。"""
    out_vehicles = []
    for v in sorted(vehicles, key=lambda x: x["final_position"]):
        entry = {
            "vehicle_number": v["vehicle_number"],
            "vehicle_name": v["vehicle_name"],
            "final_position": v["final_position"],
            "finished": v["finished"],
            "lap_count": len(v["laps"]),
            "laps": list(v["laps"]),
        }
        entry.update(_lap_stats(v["laps"]))
        out_vehicles.append(entry)
    return {
        "schema_version": "v2",
        "session": {
            "required_laps": required_laps,
            "timeout": timeout,
            "total_vehicles": len(vehicles),
        },
        "vehicles": out_vehicles,
        "laps": [],
        "min_time": 0.0,
        "total_lap_time": 0.0,
        "num_laps": 0,
    }


def build_details(vehicle_name: str, vehicle_number: int, finished: bool, laps: list,
                  required_laps: int, session_timeout: float, penalty_events: list,
                  penalty_by_kind: dict, penalty_total_seconds: float) -> dict:
    """d{N}-result-details.json schema v3 (AWSIM互換)。"""
    stats = _lap_stats(laps)
    return {
        "schema_version": "v3",
        "vehicle_name": vehicle_name,
        "vehicle_number": vehicle_number,
        "finished": finished,
        "lap_count": len(laps),
        "required_laps": required_laps,
        "session_timeout": session_timeout,
        "min_lap_time": stats["min_lap_time"],
        "avg_lap_time": stats["avg_lap_time"],
        "total_lap_time": stats["total_lap_time"],
        "laps": list(laps),
        "penalty_count": len(penalty_events),
        "penalty_total_seconds": penalty_total_seconds,
        "penalty_events": [e.to_dict() for e in penalty_events],
        "penalty_by_kind": penalty_by_kind,
    }
