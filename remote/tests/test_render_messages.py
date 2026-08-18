"""L1: render_messages のテスト (F-01 〜 F-08)。

観点 F: GUI メッセージ。表示が危険側に誤ると、オペレータが誤った状況認識の
まま操作する。文言そのものではなく「可否と表示が常に対応すること」を検証する。
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings

from conftest import (
    VEHICLES,
    JOY_FULL,
    all_mode,
    all_stopped,
    fresh_joy,
    park,
    stopping,
)
from test_status import _observations
from racing_kart_manager_core import (
    AlertCode,
    BlockerCode,
    Level,
    render_messages,
    status,
)


def texts_for(messages, target):
    """指定した操作に紐づく文言を集める。"""
    return [m.text for m in messages if target in m.targets]


# ==========================================================================
# 文言の網羅
# ==========================================================================


@pytest.mark.parametrize("code", list(BlockerCode))
def test_f01_every_blocker_code_has_text(code):
    """F-01: すべての `BlockerCode` に文言がある。

    新しいコードを足して文言を忘れると、理由が出ないまま操作が禁止される。
    """
    from racing_kart_manager_core import blocker_text

    assert blocker_text(code, ("A2",)).strip()


@pytest.mark.parametrize("code", list(AlertCode))
def test_f02_every_alert_code_has_text(code):
    """F-02: すべての `AlertCode` に文言がある。"""
    from racing_kart_manager_core import alert_text

    assert alert_text(code, ("A2",)).strip()


# ==========================================================================
# 可否と表示の対応
# ==========================================================================


@settings(max_examples=200)
@given(observations=_observations())
def test_f03_blocked_actions_always_have_a_reason(observations):
    """F-03: 操作できないなら、必ずその操作に紐づく理由が表示される。

    理由の出ない不許可を作らない。オペレータが「なぜ押せないのか」を
    画面から判断できなくなるため。
    """
    result = status(park(), observations, fresh_joy(), VEHICLES)
    messages = render_messages(result)

    if not result.can_enter_all_mode:
        assert texts_for(messages, "all"), "一斉発進が不許可なのに理由が無い"
    for vehicle_id in VEHICLES:
        if not result.can_enter_single_mode(vehicle_id):
            assert texts_for(messages, vehicle_id), f"{vehicle_id} が不許可なのに理由が無い"


@settings(max_examples=200)
@given(observations=_observations())
def test_f04_allowed_actions_have_no_reason(observations):
    """F-04: 操作できるなら、その操作に紐づく理由は表示されない。

    押せるのに「できません」と出ていたら、オペレータは押さない。
    """
    result = status(park(), observations, fresh_joy(), VEHICLES)
    messages = render_messages(result)

    if result.can_enter_all_mode:
        assert not texts_for(messages, "all")
    for vehicle_id in VEHICLES:
        if result.can_enter_single_mode(vehicle_id):
            assert not texts_for(messages, vehicle_id)


def test_f05_healthy_state_shows_nothing():
    """F-05: 全部正常なら何も出さない。常時警告が出ていると誰も読まなくなる。"""
    result = status(park(), all_stopped(), fresh_joy(), VEHICLES)

    assert render_messages(result) == ()


# ==========================================================================
# 危険側の誤表示
# ==========================================================================


def test_f06_unknown_is_never_shown_as_stopped():
    """F-06: テレメトリ途絶の車両を「停止」と表示しない。「不明」と出す。

    停止と誤表示すると、動いているかもしれない車両を停止扱いして操作に入る。
    """
    result = status(park(), all_stopped(A7=dict(velocity_age=5.0)), fresh_joy(), VEHICLES)
    messages = render_messages(result)
    joined = " ".join(m.text for m in messages)

    assert "A7" in joined
    assert "不明" in joined
    assert "A7 が停止しています" not in joined


def test_f07_confirm_timeout_names_the_vehicles():
    """F-07: 緊急停止の確認が取れない車両IDを文言に含める。

    どの車両が止まっていないのか分からないと、オペレータが動けない。
    """
    result = status(
        stopping(elapsed_s=6.0),
        all_stopped(A3=dict(emergency=False), A7=dict(emergency=False)),
        fresh_joy(),
    VEHICLES,
)
    messages = render_messages(result)
    errors = [m.text for m in messages if m.level is Level.ERROR]

    assert any("A3" in t and "A7" in t for t in errors), errors


def test_f08_message_clears_once_the_cause_is_gone():
    """F-08: 条件が解消したら文言は消える。解消していないのに消えない。"""
    blocked = status(park(), all_stopped(A3=dict(velocity=0.5)), fresh_joy(), VEHICLES)
    assert texts_for(render_messages(blocked), "all")

    cleared = status(park(), all_stopped(), fresh_joy(), VEHICLES)
    assert not texts_for(render_messages(cleared), "all")


# ==========================================================================
# 重複の抑制
# ==========================================================================


def test_f09_shared_reason_appears_once_with_multiple_targets():
    """F-09: 同じ理由は1件にまとめ、対象を並べる。

    「A3 が停止していません」は一斉発進と A2/A7 の選択を同時に塞ぐ。
    操作ごとに1件ずつ作るとメッセージ表示エリアに同じ文が何度も並ぶ。
    """
    result = status(park(), all_stopped(A3=dict(velocity=0.5)), fresh_joy(), VEHICLES)
    messages = render_messages(result)

    moving = [m for m in messages if "A3" in m.text and "停止していません" in m.text]
    assert len(moving) == 1, [m.text for m in messages]
    assert set(moving[0].targets) == {"all", "A2", "A7"}


def test_f10_stick_in_use_is_reported_for_single_mode_only():
    """F-10: スティック操作中は単車操作だけが塞がれ、一斉発進は塞がれない。"""
    result = status(all_stopped_park := park(), all_stopped(), fresh_joy(JOY_FULL), VEHICLES)
    messages = render_messages(result)

    assert not texts_for(messages, "all")
    for vehicle_id in VEHICLES:
        assert texts_for(messages, vehicle_id)


def test_f11_not_in_park_is_reported_for_every_action():
    """F-11: パーク以外では、全操作に対して理由が出る。"""
    result = status(all_mode(), all_stopped(), fresh_joy(), VEHICLES)
    messages = render_messages(result)

    assert texts_for(messages, "all")
    for vehicle_id in VEHICLES:
        assert texts_for(messages, vehicle_id)


@pytest.mark.parametrize("renderer", ["blocker_text", "alert_text"])
def test_f12_unknown_code_raises_instead_of_returning_blank(renderer):
    """F-12: 文言の無いコードは例外にする。黙って空文字を返さない。

    新しい BlockerCode / AlertCode を足して文言を忘れたとき、空のまま
    表示されると「理由の無い不許可」になり、オペレータが判断できない。
    F-01 / F-02 が既存コードの網羅を見ているので、ここは取りこぼしたときの
    挙動を固定する。
    """
    import racing_kart_manager_core as core

    with pytest.raises(KeyError):
        getattr(core, renderer)(object(), ("A2",))
