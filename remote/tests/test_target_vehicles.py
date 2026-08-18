"""L1: 対象車両リストのテスト (P-01 〜 P-09)。

対応: docs/spec/multi-vehicle-start-stop-test.md 第10章

対象車両は起動時引数で、台数も車両IDも固定しない。使わない車両を固定リストに
残すと、その車の停止確認が永久に取れず全操作が塞がれる。
"""

from __future__ import annotations

import pytest

from conftest import all_stopped, all_mode, fresh_joy, obs, park, single_mode
from racing_kart_manager_core import (
    KNOWN_VEHICLE_IDS,
    AXIS_ACCEL,
    AXIS_STEER,
    NO_INPUT_AXES,
    Mode,
    parse_vehicles,
    spec_for,
    status,
    transform,
)
from conftest import JOY_FULL


# ==========================================================================
# 10.1 起動引数の検証
# ==========================================================================


def test_p01_accepts_a_valid_list_in_order():
    """P-01: 指定した順を保つ。GUI のボタン並びに効く。"""
    assert parse_vehicles(["A7", "A2", "A3"]) == ("A7", "A2", "A3")


def test_p02_rejects_an_empty_list():
    """P-02: 1台も指定しないのは拒否する。

    台数0で起動すると、何も送れないノードが黙って立ち上がる。
    """
    assert parse_vehicles([]) is None


def test_p03_rejects_unknown_vehicle_ids():
    """P-03: 知らない車両IDは拒否する。"""
    assert parse_vehicles(["A2", "A9"]) is None
    assert parse_vehicles(["a2"]) is None


def test_p04_rejects_duplicates():
    """P-04: 重複は拒否する。

    同じIDが2つあると、片方の観測がもう片方を上書きして判定が壊れる。
    """
    assert parse_vehicles(["A2", "A3", "A2"]) is None


def test_p04b_known_ids_cover_the_operational_vehicles():
    """P-04b: 運用で使う車両IDがすべて既知として扱われる。"""
    for vehicle_id in ("A1", "A2", "A3", "A5", "A6", "A7", "A8"):
        assert vehicle_id in KNOWN_VEHICLE_IDS


# ==========================================================================
# 10.2 対象外の車両を無視すること
# ==========================================================================

TARGETS = ("A2", "A3", "A7")


def test_p05_vehicles_outside_the_targets_are_not_reported():
    """P-05: 対象車両に入っていない車両は status に出ない。"""
    observations = all_stopped(TARGETS)
    observations["A6"] = obs("A6", velocity=9.0, emergency=False)

    result = status(park(), observations, fresh_joy(), TARGETS)

    assert [v.vehicle_id for v in result.vehicles] == list(TARGETS)


def test_p06_vehicles_outside_the_targets_do_not_affect_judgements():
    """P-06: 対象外の車両の状態で操作の可否が変わってはならない。

    対象車両に入っていない車が走っていようが emergency が解除されていようが、
    こちらの判断材料にしない。混ざると、対象外の車の状態で操作が塞がったり
    逆に通ったりする。
    """
    clean = status(park(), all_stopped(TARGETS), fresh_joy(), TARGETS)

    polluted_observations = all_stopped(TARGETS)
    polluted_observations["A6"] = obs("A6", velocity=9.0, emergency=False)
    polluted = status(park(), polluted_observations, fresh_joy(), TARGETS)

    assert polluted.can_enter_all_mode == clean.can_enter_all_mode is True
    for vehicle_id in TARGETS:
        assert polluted.can_enter_single_mode(vehicle_id) is True


# ==========================================================================
# 10.3 台数固有のふるまい
# ==========================================================================


def test_p07_all_mode_suppresses_axes_even_with_one_vehicle():
    """P-07: 対象車両が1台でも一斉モードでは無操作値にする。

    送信先の台数で切り替えると、1台構成のときだけ一斉が単車操作と同じ挙動に
    なり、モードの意味が崩れる。一斉は「スティックで操縦しない」モード。
    """
    solo = ("A2",)
    spec = spec_for(all_mode(), solo)

    assert spec.suppress_axes is True
    out = transform(JOY_FULL, spec)
    assert out["A2"].axes == NO_INPUT_AXES


def test_p07b_single_mode_passes_axes_through():
    """P-07b: 単車操作だけが実値を通す。台数によらない。"""
    spec = spec_for(single_mode("A2"), ("A2", "A3"))

    assert spec.suppress_axes is False
    assert transform(JOY_FULL, spec)["A2"].axes == JOY_FULL.axes


def test_p08_one_target_can_enter_single_mode():
    """P-08: 対象車両が1台なら「対象車以外」が空集合なので入れる。

    他に止めるべき車がいない。
    """
    solo = ("A2",)
    result = status(park(), all_stopped(solo), fresh_joy(), solo)

    assert result.can_enter_single_mode("A2") is True


def test_p09_two_targets_check_the_single_other():
    """P-09: 対象車両が2台なら「対象車以外」は1台。その1台を見る。"""
    pair = ("A2", "A3")

    ok = status(park(), all_stopped(pair), fresh_joy(), pair)
    assert ok.can_enter_single_mode("A2") is True

    moving = all_stopped(pair)
    moving["A3"] = obs("A3", velocity=2.0)
    blocked = status(park(), moving, fresh_joy(), pair)

    assert blocked.can_enter_single_mode("A2") is False
    # A3 を選ぶぶんには、対象車自身は条件に含めないので通る
    assert blocked.can_enter_single_mode("A3") is True


@pytest.mark.parametrize("size", [1, 2, 3, 4])
def test_p09b_status_covers_exactly_the_targets(size):
    """P-09b: 台数を変えても status に出るのは対象車両だけ。"""
    targets = ("A2", "A3", "A6", "A7")[:size]
    result = status(park(), all_stopped(targets), fresh_joy(), targets)

    assert [v.vehicle_id for v in result.vehicles] == list(targets)
    assert set(result.enter_single_mode_blockers) == set(targets)
