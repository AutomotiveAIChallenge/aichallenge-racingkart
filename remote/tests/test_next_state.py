"""L1: next_state のテスト (N-01 〜 N-22)。

対応: docs/spec/multi-vehicle-start-stop-test.md 第8章
ハザード: HZ-2 (止められない), HZ-4 (許可すべきでない遷移を許す)

設計書は関数名を next_mode としていたが、Mode だけでは SINGLE の対象車と
STOPPING の送信先を表せないため ManagerState を返す next_state にしている。
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from conftest import (
    VEHICLES,
    ENTER_ALL,
    JOY_EVENT,
    JOY_FULL,
    TICK,
    all_mode,
    all_stopped,
    enter_single,
    fresh_joy,
    joy_with_buttons,
    park,
    single_mode,
    stopping,
)
from racing_kart_manager_core import (
    EMERGENCY_BUTTONS,
    EMERGENCY_CONFIRM_TIMEOUT_S,
    INITIAL_STATE,
    Mode,
    next_state,
)

EMERGENCY_JOY = fresh_joy(joy_with_buttons(EMERGENCY_BUTTONS[0]))


# ==========================================================================
# 起動時と正常系
# ==========================================================================


def test_n01_starts_in_park():
    """N-01: 起動直後は必ずパーク。GUI 操作前に joy を送り始めない。"""
    assert INITIAL_STATE.mode is Mode.PARK


def test_n02_park_to_all_mode_when_allowed():
    """N-02: 前提を満たしていれば一斉モードへ入る。"""
    result = next_state(park(), ENTER_ALL, all_stopped(), fresh_joy(), VEHICLES)

    assert result.mode is Mode.ALL


def test_n03_park_stays_when_all_mode_is_blocked():
    """N-03: 前提を満たさなければ入らない。GUI の押下だけでは広がらない。"""
    result = next_state(
        park(), ENTER_ALL, all_stopped(A3=dict(velocity=0.5)), fresh_joy()
    ,
        VEHICLES)

    assert result.mode is Mode.PARK


def test_n04_park_to_single_mode_when_allowed():
    """N-04: 車両選択で単車操作へ入り、対象が記録される。"""
    result = next_state(park(), enter_single("A2"), all_stopped(), fresh_joy(), VEHICLES)

    assert result.mode is Mode.SINGLE
    assert result.selected == "A2"


# ==========================================================================
# 禁止遷移
# ==========================================================================


def test_n05_all_mode_ignores_vehicle_selection():
    """N-05: 一斉モードから単車操作へは直接行けない。"""
    result = next_state(all_mode(), enter_single("A2"), all_stopped(), fresh_joy(), VEHICLES)

    assert result.mode is Mode.ALL


def test_n06_single_mode_ignores_switching_to_another_vehicle():
    """N-06: 単車操作から別の単車操作へは直接行けない。

    前の車が最後に受け取った joy のまま最大5秒走り続けるため、
    必ずパークを経由する。
    """
    result = next_state(
        single_mode("A2"), enter_single("A3"), all_stopped(), fresh_joy()
    ,
        VEHICLES)

    assert result.mode is Mode.SINGLE
    assert result.selected == "A2"


# ==========================================================================
# 緊急停止と停止プロトコル
# ==========================================================================


@pytest.mark.parametrize("button", EMERGENCY_BUTTONS)
def test_n07_all_mode_to_stopping_on_emergency_button(button):
    """N-07: 一斉モード中に緊急停止ボタンを押したら停止中へ。"""
    result = next_state(
        all_mode(), JOY_EVENT, all_stopped(), fresh_joy(joy_with_buttons(button))
    ,
        VEHICLES)

    assert result.mode is Mode.STOPPING
    assert result.stopping_destinations == frozenset(VEHICLES)


def test_n08_single_mode_to_stopping_on_emergency_button():
    """N-08: 単車操作中も同じ。送信先は縮める前のまま保つ。"""
    result = next_state(single_mode("A2"), JOY_EVENT, all_stopped(), EMERGENCY_JOY, VEHICLES)

    assert result.mode is Mode.STOPPING
    assert result.stopping_destinations == frozenset({"A2"})


def test_n09_stopping_stays_until_every_vehicle_confirms():
    """N-09: 1台でも emergency を確認できないうちはパークへ行かない。

    publish を止めると、その車は最後の joy のまま最大5秒走り続ける。
    """
    result = next_state(
        stopping(), TICK, all_stopped(A7=dict(emergency=False)), EMERGENCY_JOY
    ,
        VEHICLES)

    assert result.mode is Mode.STOPPING


def test_n10_stopping_to_park_once_all_confirmed():
    """N-10: 全車の emergency を確認できたらパークへ落とす。"""
    result = next_state(stopping(), TICK, all_stopped(), EMERGENCY_JOY, VEHICLES)

    assert result.mode is Mode.PARK
    assert result.stopping_destinations == frozenset()


def test_n11_stopping_ignores_gui_interruptions():
    """N-11: 停止プロトコル中の GUI 操作は無視する。先に停止を通し切る。"""
    for event in (ENTER_ALL, enter_single("A2")):
        result = next_state(
            stopping(), event, all_stopped(A7=dict(emergency=False)), EMERGENCY_JOY
        ,
        VEHICLES)
        assert result.mode is Mode.STOPPING, event


def test_n12_stopping_persists_past_the_confirm_timeout():
    """N-12: 5秒を超えても publish は止めない。警告を出しつつ送り続ける。

    諦めて止めると最後の joy で走り続けるため、止めない方が安全。
    """
    result = next_state(
        stopping(elapsed_s=EMERGENCY_CONFIRM_TIMEOUT_S + 1.0),
        TICK,
        all_stopped(A7=dict(emergency=False)),
        EMERGENCY_JOY,
    VEHICLES,
)

    assert result.mode is Mode.STOPPING


# ==========================================================================
# 自発フォールバック (モードごとに監視対象が違う)
# ==========================================================================


def test_n13_single_mode_falls_back_when_another_vehicle_moves():
    """N-13: 対象以外が動き出したら停止プロトコルへ。"""
    result = next_state(
        single_mode("A2"), TICK, all_stopped(A3=dict(velocity=0.5)), fresh_joy()
    ,
        VEHICLES)

    assert result.mode is Mode.STOPPING


def test_n14_single_mode_falls_back_when_another_vehicle_is_unknown():
    """N-14: 確認できない場合も安全側に倒す。無音を停止扱いしない。"""
    result = next_state(
        single_mode("A2"), TICK, all_stopped(A3=dict(velocity_age=5.0)), fresh_joy()
    ,
        VEHICLES)

    assert result.mode is Mode.STOPPING


def test_n15_single_mode_falls_back_when_another_emergency_is_cleared():
    """N-15: 止まっていても emergency が解除されていたら落とす。"""
    result = next_state(
        single_mode("A2"), TICK, all_stopped(A3=dict(emergency=False)), fresh_joy()
    ,
        VEHICLES)

    assert result.mode is Mode.STOPPING


def test_n16_single_mode_tolerates_the_target_moving():
    """N-16: 対象車自身は監視対象に含めない。操縦中なので動いてよい。"""
    result = next_state(
        single_mode("A2"), TICK, all_stopped(A2=dict(velocity=3.0)), fresh_joy()
    ,
        VEHICLES)

    assert result.mode is Mode.SINGLE


def test_n17_single_mode_tolerates_the_target_being_unknown():
    """N-17: 対象車のテレメトリ途絶も落とさない。警告のみ。"""
    result = next_state(
        single_mode("A2"),
        TICK,
        all_stopped(A2=dict(velocity_age=5.0, debug_age=5.0)),
        fresh_joy(),
    VEHICLES,
)

    assert result.mode is Mode.SINGLE


def test_n18_all_mode_tolerates_moving_vehicles():
    """N-18: 一斉モードでは4台とも走ってよい。速度で落とさない。"""
    result = next_state(
        all_mode(),
        TICK,
        all_stopped(**{v: dict(velocity=8.0, emergency=False) for v in VEHICLES}),
        fresh_joy(),
    VEHICLES,
)

    assert result.mode is Mode.ALL


def test_n19_all_mode_tolerates_lost_telemetry():
    """N-19: 一斉モードではテレメトリ途絶でも落とさない。

    joy が届いている状況で自動フォールバックすると正常なレースを止めてしまう
    (REQ-05)。joy も途絶しているなら driver 側が5秒で緊急停止する (REQ-04)。
    """
    result = next_state(
        all_mode(),
        TICK,
        all_stopped(**{v: dict(velocity_age=9.0, debug_age=9.0) for v in VEHICLES}),
        fresh_joy(),
    VEHICLES,
)

    assert result.mode is Mode.ALL


def test_n20_park_stays_even_when_a_vehicle_moves():
    """N-20: パークでは joy を送っていないので介入手段がない。警告のみ。

    joy を送り始めるのは「パーク = joy 送信なし」の定義を壊す。
    """
    result = next_state(
        park(), TICK, all_stopped(A3=dict(velocity=2.0, emergency=False)), fresh_joy()
    ,
        VEHICLES)

    assert result.mode is Mode.PARK


def test_n21_park_does_not_widen_without_gui_action():
    """N-21: テレメトリ更新だけでは宛先が広がらない。"""
    result = next_state(park(), TICK, all_stopped(), fresh_joy(), VEHICLES)

    assert result.mode is Mode.PARK
    assert result.selected is None


# ==========================================================================
# 網羅
# ==========================================================================

_states = st.sampled_from(
    [park(), all_mode(), single_mode("A2"), stopping(elapsed_s=0.0)]
)
_events = st.sampled_from(
    [TICK, JOY_EVENT, ENTER_ALL, *[enter_single(v) for v in VEHICLES]]
)


@settings(max_examples=200)
@given(state=_states, event=_events)
def test_n22_unlisted_transitions_are_no_ops(state, event):
    """N-22: 遷移表に無い組み合わせは現状維持。想定外の遷移を作らない。

    joy に緊急停止が入っていない前提での確認。
    """
    result = next_state(state, event, all_stopped(), fresh_joy(JOY_FULL), VEHICLES)

    allowed = {state.mode}
    if state.mode is Mode.PARK:
        allowed |= {Mode.ALL, Mode.SINGLE}
    elif state.mode is Mode.STOPPING:
        allowed |= {Mode.PARK}

    assert result.mode in allowed


def test_n23_park_ignores_emergency_button():
    """N-23: パークで緊急停止ボタンを押してもパークのまま。

    joy を送っていないので停止プロトコルを始めても送り先が無い。
    """
    result = next_state(park(), JOY_EVENT, all_stopped(), EMERGENCY_JOY, VEHICLES)

    assert result.mode is Mode.PARK
    assert result.stopping_destinations == frozenset()
