"""L1: 車両1台分の表示のテスト (V-01 〜 V-09)。

対応: docs/spec/multi-vehicle-start-stop-test.md 第9.2節・第9.3節

各車両ボタンの下に出す1行。文言は manager 側で作り、GUI は受け取った文字列を
描くだけにする (観点 F-1)。GUI に Tri から文言への変換表を置かない。
"""

from __future__ import annotations

import pytest

from racing_kart_manager_core import (
    CONTROL_MODE_NAMES,
    TELEMETRY_TIMEOUT_S,
    Tri,
    VehicleObservation,
    control_mode_of,
    vehicle_label,
)


def observation(control_mode=4, age=0.1) -> VehicleObservation:
    return VehicleObservation(
        vehicle_id="A2", control_mode=control_mode, control_mode_age_s=age
    )


# ==========================================================================
# 9.2 vehicle_label
# ==========================================================================

#: 設計書「車両1台分の表示」の例をそのまま持ってくる。
#: 設計書の表とテストが1対1で対応し、片方だけ変わると落ちる。
EXAMPLES = [
    (
        ("MANUAL", Tri.TRUE, Tri.TRUE, False),
        "MANUAL / 停止中 / 緊急停止 有効 / joy 送信なし",
        "パーク中の正常な状態",
    ),
    (
        ("MANUAL", Tri.TRUE, Tri.TRUE, True),
        "MANUAL / 停止中 / 緊急停止 有効 / joy 送信中",
        "一斉モードに入った直後。LSB+RSB 待ち",
    ),
    (
        ("MANUAL", Tri.TRUE, Tri.FALSE, True),
        "MANUAL / 停止中 / 緊急停止 解除 / joy 送信中",
        "解除済み。ButtonY で発進する",
    ),
    (
        ("MANUAL", Tri.TRUE, Tri.FALSE, False),
        "MANUAL / 停止中 / 緊急停止 解除 / joy 送信なし",
        "止まっているが動きうる。manager から介入できない",
    ),
    (
        ("AUTONOMOUS", Tri.FALSE, Tri.FALSE, True),
        "AUTONOMOUS / 走行中 / 緊急停止 解除 / joy 送信中",
        "自動走行中",
    ),
    (
        (None, Tri.UNKNOWN, Tri.UNKNOWN, False),
        "不明 / 不明 / 緊急停止 不明 / joy 送信なし",
        "テレメトリ途絶",
    ),
]


@pytest.mark.parametrize("args, expected, note", EXAMPLES)
def test_v01_matches_the_documented_examples(args, expected, note):
    """V-01: 設計書に載せた6例と一致する。"""
    assert vehicle_label(*args) == expected, note


@pytest.mark.parametrize("stopped", list(Tri))
@pytest.mark.parametrize("emergency", list(Tri))
@pytest.mark.parametrize("receiving_joy", [True, False])
@pytest.mark.parametrize("control_mode", ["MANUAL", None])
def test_v02_always_has_four_items(control_mode, stopped, emergency, receiving_joy):
    """V-02: どんな入力でも4項目を省略しない。

    項目が消えると、何が表示されていないのかオペレータに分からない。
    """
    label = vehicle_label(control_mode, stopped, emergency, receiving_joy)

    assert label.count(" / ") == 3, label
    assert all(part.strip() for part in label.split(" / ")), label


def test_v03_missing_control_mode_shows_unknown():
    """V-03: 制御モードが途絶したら「不明」。既定値に倒さない。

    MANUAL などに倒すと、実際は分からないのに分かっているように見える。
    """
    label = vehicle_label(None, Tri.TRUE, Tri.TRUE, False)

    assert label.startswith("不明 / ")


@pytest.mark.parametrize(
    "stopped, expected", [(Tri.TRUE, "停止中"), (Tri.FALSE, "走行中"), (Tri.UNKNOWN, "不明")]
)
def test_v04_motion_text(stopped, expected):
    """V-04: 走行状態の3値が対応する語になる。"""
    assert vehicle_label("MANUAL", stopped, Tri.TRUE, False).split(" / ")[1] == expected


@pytest.mark.parametrize(
    "emergency, expected", [(Tri.TRUE, "有効"), (Tri.FALSE, "解除"), (Tri.UNKNOWN, "不明")]
)
def test_v04b_emergency_text(emergency, expected):
    """V-04: 緊急停止の3値が対応する語になる。"""
    part = vehicle_label("MANUAL", Tri.TRUE, emergency, False).split(" / ")[2]

    assert part == f"緊急停止 {expected}"


@pytest.mark.parametrize(
    "receiving_joy, expected", [(True, "joy 送信中"), (False, "joy 送信なし")]
)
def test_v05_joy_text(receiving_joy, expected):
    """V-05: joy の送信有無が対応する語になる。

    これだけは車両の状態ではなく manager 側の情報。
    """
    assert vehicle_label("MANUAL", Tri.TRUE, Tri.TRUE, receiving_joy).endswith(expected)


def test_v06_undefined_control_mode_is_not_an_exception():
    """V-06: ControlModeReport に無い値でも落ちず、値が分かる形で出す。

    上流が定数を増やしたときに GUI が落ちると、joy が止まっていなくても
    操作不能になる。
    """
    name = control_mode_of(observation(control_mode=7))

    assert name is not None
    assert "7" in name
    assert vehicle_label(name, Tri.TRUE, Tri.TRUE, False).startswith(name)


# ==========================================================================
# 9.3 control_mode_of
# ==========================================================================


@pytest.mark.parametrize("value, expected", sorted(CONTROL_MODE_NAMES.items()))
def test_v07_defined_modes_map_to_names(value, expected):
    """V-07: 定義済みの値が名前になる。表記は英語のまま。"""
    assert control_mode_of(observation(control_mode=value)) == expected


@pytest.mark.parametrize(
    "age, expected",
    [
        (TELEMETRY_TIMEOUT_S - 0.01, "MANUAL"),
        (TELEMETRY_TIMEOUT_S + 0.01, None),
    ],
)
def test_v08_staleness_boundary(age, expected):
    """V-08: 受信からの経過時間の境界。"""
    assert control_mode_of(observation(age=age)) == expected


def test_v09_never_received_is_none():
    """V-09: 一度も受信していなければ None。

    stopped / emergency と同じく、無音を既定値に倒さない。
    """
    assert control_mode_of(VehicleObservation(vehicle_id="A2")) is None
    assert control_mode_of(observation(control_mode=4, age=None)) is None
