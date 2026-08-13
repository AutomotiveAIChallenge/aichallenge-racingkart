"""L1: JSON 境界のテスト (J-01 〜 J-10)。

status と command は `std_msgs/String` に JSON を載せて GUI とやり取りする。
ここは「表示と実体がずれうる箇所」なので、形の崩れを潰しておく。
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings

from conftest import VEHICLES, all_stopped, fresh_joy, park, single_mode, stopping
from test_status import _observations
from racing_kart_manager_core import (
    SCHEMA_VERSION,
    EventKind,
    parse_command,
    status,
    status_to_json,
)

STAMP = 1_770_000_000_000_000_000


def dump(state=None, observations=None, joy=None):
    state = state or park()
    observations = observations if observations is not None else all_stopped()
    joy = joy or fresh_joy()
    return json.loads(status_to_json(status(state, observations, joy, VEHICLES), STAMP))


# ==========================================================================
# status の直列化
# ==========================================================================


def test_j01_tri_is_serialized_as_a_string():
    """J-01: `Tri` を真偽値に潰さない。

    真偽値にすると UNKNOWN が表現できず、テレメトリ途絶を「停止」と
    誤表示する事故に直結する。ここが崩れると観点 F-5 が成立しない。
    """
    payload = dump(observations=all_stopped(A7=dict(velocity_age=5.0)))
    by_id = {v["vehicle_id"]: v for v in payload["vehicles"]}

    assert by_id["A2"]["stopped"] == "TRUE"
    assert by_id["A7"]["stopped"] == "UNKNOWN"
    assert isinstance(by_id["A7"]["stopped"], str)
    assert by_id["A7"]["stopped"] is not False


def test_j02_required_keys_are_present():
    """J-02: スキーマの必須キーが揃っている。"""
    payload = dump()

    for key in (
        "schema_version",
        "stamp_ns",
        "mode",
        "selected",
        "stopping_elapsed_s",
        "vehicles",
        "can_enter_all_mode",
        "can_enter_single_mode",
        "messages",
    ):
        assert key in payload, key
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["stamp_ns"] == STAMP


def test_j03_every_vehicle_appears_exactly_once():
    """J-03: 4台すべてが `vehicles` と `can_enter_single_mode` に現れる。

    欠けると GUI がその車両のボタンを描けない。
    """
    payload = dump()

    assert [v["vehicle_id"] for v in payload["vehicles"]] == list(VEHICLES)
    assert set(payload["can_enter_single_mode"]) == set(VEHICLES)


def test_j04_mode_and_selected_round_trip():
    """J-04: モードと対象車が正しく出る。"""
    payload = dump(state=single_mode("A2"))

    assert payload["mode"] == "SINGLE"
    assert payload["selected"] == "A2"


def test_j05_stopping_elapsed_is_carried():
    """J-05: 停止プロトコルの経過秒が出る。GUI が進行状況を表示できる。"""
    payload = dump(state=stopping(elapsed_s=3.5))

    assert payload["stopping_elapsed_s"] == pytest.approx(3.5)


def test_j06_messages_carry_level_and_targets():
    """J-06: 文言に level と targets が付く。GUI はこれで振り分ける。"""
    payload = dump(observations=all_stopped(A3=dict(velocity=0.5)))

    assert payload["messages"]
    for message in payload["messages"]:
        assert message["level"] in ("info", "warn", "error")
        assert isinstance(message["targets"], list)
        assert isinstance(message["text"], str) and message["text"]


@settings(max_examples=100)
@given(observations=_observations())
def test_j07_any_status_serializes_to_valid_json(observations):
    """J-07: どんな観測でも JSON として出力でき、スキーマを満たす。"""
    payload = json.loads(
        status_to_json(status(park(), observations, fresh_joy(), VEHICLES), STAMP)
    )

    assert set(payload["can_enter_single_mode"]) == set(VEHICLES)
    for vehicle in payload["vehicles"]:
        assert vehicle["stopped"] in ("TRUE", "FALSE", "UNKNOWN")
        assert vehicle["emergency"] in ("TRUE", "FALSE", "UNKNOWN")


# ==========================================================================
# command のパース
# ==========================================================================


def test_j08_valid_commands_become_events():
    """J-08: 正常なコマンドが `Event` になる。"""
    enter_all = parse_command(
        json.dumps({"schema_version": SCHEMA_VERSION, "command": "enter_all_mode"})
    )
    assert enter_all is not None
    assert enter_all.kind is EventKind.ENTER_ALL_MODE

    enter_single = parse_command(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "command": "enter_single_mode",
                "vehicle_id": "A2",
            }
        )
    )
    assert enter_single is not None
    assert enter_single.kind is EventKind.ENTER_SINGLE_MODE
    assert enter_single.vehicle_id == "A2"


@pytest.mark.parametrize(
    "payload, note",
    [
        ("{ not json", "壊れた JSON"),
        ("[]", "オブジェクトでない"),
        (json.dumps({"command": "enter_all_mode"}), "schema_version が無い"),
        (
            json.dumps({"schema_version": 999, "command": "enter_all_mode"}),
            "schema_version 不一致",
        ),
        (json.dumps({"schema_version": SCHEMA_VERSION}), "command が無い"),
        (
            json.dumps({"schema_version": SCHEMA_VERSION, "command": "shutdown"}),
            "未知の command",
        ),
        (
            json.dumps(
                {"schema_version": SCHEMA_VERSION, "command": "enter_single_mode"}
            ),
            "vehicle_id が無い",
        ),
        (
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "command": "enter_single_mode",
                    "vehicle_id": "A9",
                }
            ),
            "存在しない vehicle_id",
        ),
    ],
)
def test_j09_invalid_commands_are_dropped(payload, note):
    """J-09: 不正なコマンドはすべて破棄する。例外を投げない。

    manager が落ちると joy が止まり、5秒後に全車が緊急停止する。
    不正入力で落ちないことが安全性に直結する。
    """
    assert parse_command(payload) is None, note


def test_j10_extra_fields_are_ignored():
    """J-10: 知らないフィールドがあっても受理する。GUI 側の拡張を壊さない。"""
    event = parse_command(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "command": "enter_all_mode",
                "future_field": 123,
            }
        )
    )

    assert event is not None
    assert event.kind is EventKind.ENTER_ALL_MODE


def test_j11_vehicles_carry_control_mode_and_label():
    """J-11: `vehicles[]` に `control_mode` と `label` が入る。

    GUI が Tri から文言への変換表を持たずに描けることの確認。表を GUI 側に
    置くと、表示のロジックが manager と GUI に分かれて食い違いうる (観点 F-1)。
    """
    payload = dump()

    for vehicle in payload["vehicles"]:
        assert "control_mode" in vehicle
        assert vehicle["label"].count(" / ") == 3, vehicle["label"]
