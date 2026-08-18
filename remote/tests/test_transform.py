"""L1: transform のテスト (T-01 〜 T-10)。

対応: docs/spec/multi-vehicle-start-stop-test.md 第6章
ハザード: HZ-1 (無操作のつもりで踏んだ joy を送る), HZ-2, HZ-3
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from conftest import VEHICLES, JOY_FULL, JOY_NO_INPUT, NO_BUTTONS, joy_with_buttons
from racing_kart_manager_core import (
    AXIS_ACCEL,
    AXIS_BRAKE,
    AXIS_DPAD_H,
    AXIS_DPAD_V,
    AXIS_STEER,
    EMERGENCY_BUTTONS,
    NO_INPUT_AXES,
    NUM_AXES,
    NUM_BUTTONS,
    JoyValue,
    TransformSpec,
    transform,
)

ALL_DESTS = frozenset(VEHICLES)


def spec(destinations, suppress=False, force=False) -> TransformSpec:
    return TransformSpec(
        destinations=frozenset(destinations),
        suppress_axes=suppress,
        force_emergency=force,
    )


# --------------------------------------------------------------------------
# T-01 / T-02: 一斉モードの無操作値での上書き
# --------------------------------------------------------------------------


def test_t01_all_mode_overrides_axes_with_no_input():
    """T-01: 全開 joy を入れても、4台向けの出力は全軸が無操作値になる。

    アクセル・ブレーキの無操作は 0.0 ではなく +1.0。ここを取り違えると
    アクセル50%・ブレーキ50%を踏んだ joy を4台へ送ることになる (HZ-1)。
    """
    out = transform(JOY_FULL, spec(ALL_DESTS, suppress=True))

    assert set(out) == ALL_DESTS
    for vehicle_id, joy in out.items():
        assert joy.axes[AXIS_ACCEL] == pytest.approx(+1.0), vehicle_id
        assert joy.axes[AXIS_BRAKE] == pytest.approx(+1.0), vehicle_id
        assert joy.axes[AXIS_STEER] == pytest.approx(0.0), vehicle_id
        assert joy.axes[AXIS_DPAD_H] == pytest.approx(0.0), vehicle_id
        assert joy.axes[AXIS_DPAD_V] == pytest.approx(0.0), vehicle_id


def test_t02_output_always_has_exact_array_sizes():
    """T-02: driver は buttons 11 / axes 8 を厳密に要求する。

    違うと is_joystick_available() が偽になり停止指令に落ちる
    (racing_kart_driver_node.cpp:186-187)。
    """
    specs = [
        spec(ALL_DESTS, suppress=True),
        spec({"A2"}),
        spec(ALL_DESTS, suppress=True, force=True),
    ]
    for s in specs:
        for joy in (JOY_NO_INPUT, JOY_FULL):
            for out_joy in transform(joy, s).values():
                assert len(out_joy.axes) == NUM_AXES
                assert len(out_joy.buttons) == NUM_BUTTONS


# --------------------------------------------------------------------------
# T-03 / T-04: 単車操作の素通しと、複数台時の不変性
# --------------------------------------------------------------------------


def test_t03_single_mode_passes_axes_through_unchanged():
    """T-03: 単車操作では軸を改変しない。"""
    out = transform(JOY_FULL, spec({"A2"}))

    assert set(out) == {"A2"}
    assert out["A2"].axes == JOY_FULL.axes


@given(
    axes=st.lists(
        st.floats(min_value=-1.0, max_value=1.0, allow_nan=False),
        min_size=NUM_AXES,
        max_size=NUM_AXES,
    )
)
def test_t04_multi_destination_axes_never_leave_no_input(axes):
    """T-04: 宛先が2台以上なら、入力の軸をどう動かしても出力は無操作のまま。

    スティック1本で複数台をステアリングすることはできないため。
    """
    joy = JoyValue(axes=tuple(axes), buttons=NO_BUTTONS)
    out = transform(joy, spec(ALL_DESTS, suppress=True))

    for out_joy in out.values():
        assert out_joy.axes == NO_INPUT_AXES


# --------------------------------------------------------------------------
# T-05 / T-06 / T-06b: 緊急停止
# --------------------------------------------------------------------------


@pytest.mark.parametrize("button", EMERGENCY_BUTTONS)
def test_t05_emergency_buttons_pass_through_to_every_destination(button):
    """T-05: 緊急停止ボタン4種はいずれも、宛先の全車へ素通しされる。"""
    joy = joy_with_buttons(button)
    out = transform(joy, spec(ALL_DESTS, suppress=True))

    assert set(out) == ALL_DESTS
    for vehicle_id, out_joy in out.items():
        assert out_joy.buttons[button] == 1, vehicle_id


def test_t06_force_emergency_sets_all_four_buttons():
    """T-06: 自発フォールバックでは緊急停止ボタン4つすべてを 1 にする。

    driver は OR で見るので1つでも足りるが、4つ立てることで取りこぼしを無くし、
    かつ「4つ同時 = manager が合成した緊急停止」の署名として rosbag から判別できる。
    """
    out = transform(JOY_NO_INPUT, spec(ALL_DESTS, suppress=True, force=True))

    for vehicle_id, out_joy in out.items():
        for button in EMERGENCY_BUTTONS:
            assert out_joy.buttons[button] == 1, f"{vehicle_id} buttons[{button}]"
        assert out_joy.axes == NO_INPUT_AXES, vehicle_id


def test_t06b_force_emergency_is_idempotent():
    """T-06b: オペレータが既に押している場合も出力は同じ (冪等)。"""
    already = joy_with_buttons(EMERGENCY_BUTTONS[0])
    s = spec(ALL_DESTS, suppress=True, force=True)

    assert transform(already, s) == transform(JOY_NO_INPUT, s)


# --------------------------------------------------------------------------
# T-07 / T-08: 宛先
# --------------------------------------------------------------------------


def test_t07_single_destination_reaches_only_that_vehicle():
    """T-07: 単車操作では対象1台にしか出力しない。他3台には1件も出さない。"""
    out = transform(JOY_FULL, spec({"A2"}))

    assert set(out) == {"A2"}
    for other in ("A3", "A6", "A7"):
        assert other not in out


def test_t08_park_publishes_nothing():
    """T-08: 宛先が空なら何も出さない。"""
    assert transform(JOY_FULL, spec(frozenset())) == {}


# --------------------------------------------------------------------------
# T-09 / T-10: 素通し性と追跡性
# --------------------------------------------------------------------------


@given(
    buttons=st.lists(
        st.integers(min_value=0, max_value=1),
        min_size=NUM_BUTTONS,
        max_size=NUM_BUTTONS,
    )
)
def test_t09_buttons_pass_through_when_not_forcing(buttons):
    """T-09: force_emergency でなければボタンは完全素通し。

    ButtonY をマスクしない (joy を解釈しない方針) ことの確認でもある。
    """
    joy = JoyValue(axes=NO_INPUT_AXES, buttons=tuple(buttons))
    out = transform(joy, spec(ALL_DESTS, suppress=True))

    for out_joy in out.values():
        assert out_joy.buttons == tuple(buttons)


def test_t10_stamp_is_carried_over():
    """T-10: header.stamp は入力から引き継ぐ。

    driver は自身の now() を使うので安全性には影響しないが、
    zenoh 経由の遅延計測に使えるようにする。
    """
    joy = JoyValue(axes=NO_INPUT_AXES, buttons=NO_BUTTONS, stamp_ns=123_456_789)
    out = transform(joy, spec(ALL_DESTS, suppress=True))

    for out_joy in out.values():
        assert out_joy.stamp_ns == 123_456_789


# --------------------------------------------------------------------------
# T-11: 分岐の穴埋め
# --------------------------------------------------------------------------


def test_t11_force_emergency_pads_short_button_arrays():
    """T-11: buttons が足りない入力でも緊急停止ボタンを立てられる。

    サイズ異常の入力自体は素通しの方針だが、こちらが値を作る場面では
    IndexError で落とさない。
    """
    short = JoyValue(axes=NO_INPUT_AXES, buttons=(0, 0))
    out = transform(short, spec(ALL_DESTS, suppress=True, force=True))

    for out_joy in out.values():
        assert len(out_joy.buttons) == NUM_BUTTONS
        for button in EMERGENCY_BUTTONS:
            assert out_joy.buttons[button] == 1
