"""L1: status のテスト (S-01 〜 S-23)。

対応: docs/spec/multi-vehicle-start-stop-test.md 第7章
ハザード: HZ-4 (停止していない車両がいるのに操作を許可する)

不変条件 INV-1 〜 INV-6 は docs/spec/multi-vehicle-start-stop.md の
「Status が満たすべき不変条件」を参照。
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from conftest import (
    VEHICLES,
    JOY_FULL,
    all_stopped,
    alert_codes,
    blocker_codes,
    fresh_joy,
    obs,
    park,
    single_mode,
    all_mode,
    stale_joy,
    stopping,
    vehicles_for,
)
from racing_kart_manager_core import (
    EMERGENCY_CONFIRM_TIMEOUT_S,
    STOPPED_SPEED_THRESHOLD_MPS,
    TELEMETRY_TIMEOUT_S,
    AlertCode,
    BlockerCode,
    Tri,
    VehicleObservation,
    status,
)


def vehicle_status(result, vehicle_id):
    return next(v for v in result.vehicles if v.vehicle_id == vehicle_id)


# ==========================================================================
# 7.1 基本
# ==========================================================================


def test_s01_stale_velocity_makes_state_unknown_and_blocks_all_mode():
    """S-01: 速度テレメトリが途絶した車両は UNKNOWN になり、一斉へ入れない。"""
    result = status(
        park(),
        all_stopped(A2=dict(velocity_age=2.0)),
        fresh_joy(),
    VEHICLES,
)

    assert vehicle_status(result, "A2").stopped is Tri.UNKNOWN
    assert result.can_enter_all_mode is False
    assert BlockerCode.VEHICLE_STATE_UNKNOWN in blocker_codes(
        result.enter_all_mode_blockers
    )
    assert "A2" in vehicles_for(
        result.enter_all_mode_blockers, BlockerCode.VEHICLE_STATE_UNKNOWN
    )


def test_s02_stale_debug_status_makes_emergency_unknown():
    """S-02: debug/status が途絶した車両は emergency が UNKNOWN になる。"""
    result = status(
        park(),
        all_stopped(A2=dict(debug_age=2.0)),
        fresh_joy(),
    VEHICLES,
)

    assert vehicle_status(result, "A2").emergency is Tri.UNKNOWN
    assert result.can_enter_all_mode is False


def test_s03_all_healthy_and_parked_allows_every_transition():
    """S-03: 全車停止・emergency 済み・テレメトリ新鮮なら全部入れる。"""
    result = status(park(), all_stopped(), fresh_joy(), VEHICLES)

    assert result.enter_all_mode_blockers == ()
    assert result.can_enter_all_mode is True
    for vehicle_id in VEHICLES:
        assert result.can_enter_single_mode(vehicle_id) is True


def test_s04_moving_vehicle_blocks_selecting_the_others():
    """S-04: A3 が動いていると、A3 以外を選ぼうとしたときにブロックされる。"""
    result = status(park(), all_stopped(A3=dict(velocity=0.5)), fresh_joy(), VEHICLES)

    assert result.can_enter_single_mode("A2") is False
    blockers = result.enter_single_mode_blockers["A2"]
    assert BlockerCode.VEHICLE_MOVING in blocker_codes(blockers)
    assert "A3" in vehicles_for(blockers, BlockerCode.VEHICLE_MOVING)


def test_s05_cleared_emergency_blocks_selecting_the_others():
    """S-05: 止まっていても emergency が解除されている車があればブロックする。

    いつ動いてもおかしくないため。
    """
    result = status(park(), all_stopped(A3=dict(emergency=False)), fresh_joy(), VEHICLES)

    assert result.can_enter_single_mode("A2") is False
    blockers = result.enter_single_mode_blockers["A2"]
    assert BlockerCode.VEHICLE_EMERGENCY_CLEARED in blocker_codes(blockers)
    assert "A3" in vehicles_for(blockers, BlockerCode.VEHICLE_EMERGENCY_CLEARED)


def test_s06_stick_in_use_blocks_single_mode():
    """S-06: スティックが無操作でなければ単車操作に入れない。"""
    result = status(park(), all_stopped(), fresh_joy(JOY_FULL), VEHICLES)

    for vehicle_id in VEHICLES:
        assert result.can_enter_single_mode(vehicle_id) is False
        assert BlockerCode.STICK_IN_USE in blocker_codes(
            result.enter_single_mode_blockers[vehicle_id]
        )


def test_s07_stale_joy_blocks_and_alerts():
    """S-07: joy 入力が途絶したら遷移を止め、警告も出す。"""
    result = status(park(), all_stopped(), stale_joy(), VEHICLES)

    assert result.can_enter_all_mode is False
    assert BlockerCode.JOY_STALE in blocker_codes(result.enter_all_mode_blockers)
    assert AlertCode.JOY_STALE in alert_codes(result.alerts)


# ==========================================================================
# 7.2 禁止遷移
# ==========================================================================


def test_s08_all_mode_blocks_everything_with_not_in_park():
    """S-08: 一斉モード中はどのモードにも入れない。"""
    result = status(all_mode(), all_stopped(), fresh_joy(), VEHICLES)

    assert result.can_enter_all_mode is False
    assert BlockerCode.NOT_IN_PARK in blocker_codes(result.enter_all_mode_blockers)
    for vehicle_id in VEHICLES:
        assert result.can_enter_single_mode(vehicle_id) is False


def test_s09_single_mode_cannot_switch_to_another_vehicle():
    """S-09: 単車操作から別の単車操作へ直接は行けない。

    前の車が最後に受け取った joy のまま最大5秒走り続けるため。
    """
    result = status(single_mode("A2"), all_stopped(), fresh_joy(), VEHICLES)

    assert result.can_enter_single_mode("A3") is False
    assert BlockerCode.NOT_IN_PARK in blocker_codes(
        result.enter_single_mode_blockers["A3"]
    )


def test_s10_stopping_blocks_everything():
    """S-10: 停止プロトコル実行中はどのモードにも入れない。"""
    result = status(stopping(), all_stopped(), fresh_joy(), VEHICLES)

    assert result.can_enter_all_mode is False
    for vehicle_id in VEHICLES:
        assert result.can_enter_single_mode(vehicle_id) is False


# ==========================================================================
# 7.3 停止プロトコルの警告
# ==========================================================================


def test_s11_emergency_confirm_timeout_names_the_vehicle():
    """S-11: 5秒たっても emergency を確認できない車両IDを警告に含める。"""
    result = status(
        stopping(elapsed_s=EMERGENCY_CONFIRM_TIMEOUT_S + 0.1),
        all_stopped(A7=dict(emergency=False)),
        fresh_joy(),
    VEHICLES,
)

    assert AlertCode.EMERGENCY_CONFIRM_TIMEOUT in alert_codes(result.alerts)
    assert vehicles_for(result.alerts, AlertCode.EMERGENCY_CONFIRM_TIMEOUT) == ("A7",)


def test_s12_no_timeout_alert_before_the_threshold():
    """S-12: 5秒に達していなければ警告を出さない。"""
    result = status(
        stopping(elapsed_s=EMERGENCY_CONFIRM_TIMEOUT_S - 0.1),
        all_stopped(A7=dict(emergency=False)),
        fresh_joy(),
    VEHICLES,
)

    assert AlertCode.EMERGENCY_CONFIRM_TIMEOUT not in alert_codes(result.alerts)


def test_s13_timeout_alert_clears_once_all_confirmed():
    """S-13: 条件が解消したら警告は消える。"""
    result = status(stopping(elapsed_s=6.0), all_stopped(), fresh_joy(), VEHICLES)

    assert AlertCode.EMERGENCY_CONFIRM_TIMEOUT not in alert_codes(result.alerts)


def test_s14_timeout_alert_lists_every_unconfirmed_vehicle():
    """S-14: 未確認の車両が複数あれば全部挙げる。1台だけにしない。"""
    result = status(
        stopping(elapsed_s=6.0),
        all_stopped(A3=dict(emergency=False), A7=dict(emergency=None, debug_age=None)),
        fresh_joy(),
    VEHICLES,
)

    named = vehicles_for(result.alerts, AlertCode.EMERGENCY_CONFIRM_TIMEOUT)
    assert set(named) == {"A3", "A7"}


# ==========================================================================
# 7.4 境界値
# ==========================================================================


@pytest.mark.parametrize(
    "velocity, expected",
    [
        (STOPPED_SPEED_THRESHOLD_MPS - 0.001, Tri.TRUE),
        (STOPPED_SPEED_THRESHOLD_MPS + 0.001, Tri.FALSE),
        (-(STOPPED_SPEED_THRESHOLD_MPS + 0.001), Tri.FALSE),
    ],
)
def test_s15_speed_threshold_boundary(velocity, expected):
    """S-15: 停止判定の境界。後退も動いているとみなす。"""
    result = status(park(), all_stopped(A2=dict(velocity=velocity)), fresh_joy(), VEHICLES)

    assert vehicle_status(result, "A2").stopped is expected


@pytest.mark.parametrize(
    "age, expected",
    [
        (TELEMETRY_TIMEOUT_S - 0.01, Tri.TRUE),
        (TELEMETRY_TIMEOUT_S + 0.01, Tri.UNKNOWN),
    ],
)
def test_s16_telemetry_age_boundary(age, expected):
    """S-16: テレメトリの受信からの経過時間の境界。"""
    result = status(park(), all_stopped(A2=dict(velocity_age=age)), fresh_joy(), VEHICLES)

    assert vehicle_status(result, "A2").stopped is expected


def test_s17_lost_telemetry_is_unknown_not_false():
    """S-17: 途絶を FALSE ではなく UNKNOWN にする。両者を区別すること。

    FALSE に倒すと「動いている」と誤って表示され、UNKNOWN の意味が失われる。
    """
    result = status(park(), all_stopped(A2=dict(velocity_age=5.0)), fresh_joy(), VEHICLES)

    assert vehicle_status(result, "A2").stopped is Tri.UNKNOWN
    assert vehicle_status(result, "A2").stopped is not Tri.FALSE


def test_s18_never_received_is_unknown():
    """S-18: 一度も受信していない場合も UNKNOWN。"""
    result = status(
        park(),
        all_stopped(A2=dict(velocity=None, velocity_age=None)),
        fresh_joy(),
    VEHICLES,
)

    vs = vehicle_status(result, "A2")
    assert vs.velocity_age_s is None
    assert vs.stopped is Tri.UNKNOWN


# ==========================================================================
# 7.5 プロパティテスト
# ==========================================================================

_velocity = st.one_of(st.none(), st.floats(min_value=-5.0, max_value=5.0))
_age = st.one_of(st.none(), st.floats(min_value=0.0, max_value=3.0))
_emergency = st.one_of(st.none(), st.booleans())


@st.composite
def _observations(draw):
    result = {}
    for vehicle_id in VEHICLES:
        result[vehicle_id] = VehicleObservation(
            vehicle_id=vehicle_id,
            velocity_mps=draw(_velocity),
            velocity_age_s=draw(_age),
            emergency=draw(_emergency),
            debug_age_s=draw(_age),
        )
    return result


@settings(max_examples=200)
@given(observations=_observations())
def test_s19_any_unknown_blocks_all_mode(observations):
    """S-19 (INV-1): UNKNOWN が1台でもあれば一斉へ入れない。"""
    result = status(park(), observations, fresh_joy(), VEHICLES)

    has_unknown = any(
        v.stopped is Tri.UNKNOWN or v.emergency is Tri.UNKNOWN for v in result.vehicles
    )
    if has_unknown:
        assert result.can_enter_all_mode is False


@settings(max_examples=200)
@given(observations=_observations())
def test_s20_non_stopped_vehicles_are_always_named(observations):
    """S-20 (INV-2): 停止が確認できない車両は必ずどれかの blocker に現れる。

    理由の出ない不許可を作らない。
    """
    result = status(park(), observations, fresh_joy(), VEHICLES)

    named = set()
    for blocker in result.enter_all_mode_blockers:
        named.update(blocker.vehicles)

    for v in result.vehicles:
        if v.stopped is not Tri.TRUE:
            assert v.vehicle_id in named


@settings(max_examples=200)
@given(observations=_observations())
def test_s21_single_mode_requires_the_other_three_confirmed(observations):
    """S-21 (INV-3): 単車操作に入れるなら、対象以外の3台は停止かつ emergency 済み。

    対象車自身は条件に含めない (動いている車をつかまえて操縦できるようにするため)。
    """
    result = status(park(), observations, fresh_joy(), VEHICLES)
    by_id = {v.vehicle_id: v for v in result.vehicles}

    for target in VEHICLES:
        if result.can_enter_single_mode(target):
            for other in VEHICLES:
                if other == target:
                    continue
                assert by_id[other].stopped is Tri.TRUE
                assert by_id[other].emergency is Tri.TRUE


@settings(max_examples=200)
@given(observations=_observations())
def test_s22_vehicle_blockers_always_name_vehicles(observations):
    """S-22 (INV-6): VEHICLE_* 系の blocker は必ず車両を挙げる。"""
    result = status(park(), observations, fresh_joy(), VEHICLES)

    vehicle_codes = {
        BlockerCode.VEHICLE_MOVING,
        BlockerCode.VEHICLE_STATE_UNKNOWN,
        BlockerCode.VEHICLE_EMERGENCY_CLEARED,
    }
    groups = [result.enter_all_mode_blockers, *result.enter_single_mode_blockers.values()]
    for blockers in groups:
        for blocker in blockers:
            if blocker.code in vehicle_codes:
                assert blocker.vehicles, blocker


@settings(max_examples=200)
@given(observations=_observations())
def test_s23_can_enter_is_derived_from_blockers(observations):
    """S-23 (F-2): 可否は blocker から導出され、別に保持されていない。"""
    result = status(park(), observations, fresh_joy(), VEHICLES)

    assert result.can_enter_all_mode == (len(result.enter_all_mode_blockers) == 0)
    for vehicle_id in VEHICLES:
        assert result.can_enter_single_mode(vehicle_id) == (
            len(result.enter_single_mode_blockers[vehicle_id]) == 0
        )


# ==========================================================================
# 7.6 スティック無操作の判定の分岐
# ==========================================================================


@pytest.mark.parametrize(
    "axes, no_input, note",
    [
        ((0.0, 0.0, +1.0, 0.0, 0.0, +1.0, 0.0, 0.0), True, "完全に無操作"),
        ((0.7, 0.0, +1.0, 0.0, 0.0, +1.0, 0.0, 0.0), False, "ステアリングを切っている"),
        ((0.0, 0.0, +1.0, 0.0, 0.0, -1.0, 0.0, 0.0), False, "アクセルを踏んでいる"),
        ((0.0, 0.0, -1.0, 0.0, 0.0, +1.0, 0.0, 0.0), False, "ブレーキを踏んでいる"),
        ((0.0, 0.0, +1.0, 0.0, 0.0, +1.0), False, "軸が足りない"),
    ],
)
def test_s24_stick_no_input_branches(axes, no_input, note):
    """S-24: スティック無操作の判定の全分岐。

    ステアリングが無操作でもアクセルやブレーキを踏んでいれば単車操作に入れない。
    ステアリングだけで判定を打ち切ると、この姿勢を見逃す。
    """
    from racing_kart_manager_core import JoyValue as JV
    from racing_kart_manager_core import stick_no_input

    assert stick_no_input(JV(axes=axes, buttons=(0,) * 11)) is no_input, note


def test_s25_joy_never_received_blocks_everything():
    """S-25: joy を一度も受信していない状態（manager 起動直後）では何もできない。

    経過時間による途絶判定とは別の分岐。起動直後に GUI からいきなり
    一斉発進準備完了を押せてしまわないことの確認。
    """
    from racing_kart_manager_core import JoyObservation

    result = status(park(), all_stopped(), JoyObservation(joy=None, age_s=None), VEHICLES)

    assert result.can_enter_all_mode is False
    assert BlockerCode.JOY_STALE in blocker_codes(result.enter_all_mode_blockers)
    assert AlertCode.JOY_STALE in alert_codes(result.alerts)
    for vehicle_id in VEHICLES:
        assert result.can_enter_single_mode(vehicle_id) is False
