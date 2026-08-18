"""L1 テストの共通データとビルダ。

racing_kart_manager_core は ROS に依存しないので、このテスト群は ROS を
起動せずに動く。実行例:

    uv run --with pytest --with hypothesis pytest remote/tests -q
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from racing_kart_manager_core import (  # noqa: E402
    NO_INPUT_AXES,
    NUM_BUTTONS,
    Event,
    EventKind,
    JoyObservation,
    JoyValue,
    ManagerState,
    Mode,
    VehicleObservation,
)

#: テスト既定の対象車両。台数依存のケースだけ明示的に別の並びを渡す。
VEHICLES: tuple[str, ...] = ("A2", "A3", "A7")


# --------------------------------------------------------------------------
# joy のテストデータ
# --------------------------------------------------------------------------

NO_BUTTONS: tuple[int, ...] = (0,) * NUM_BUTTONS

#: 無操作。アクセル・ブレーキは +1.0 が無操作であることに注意。
JOY_NO_INPUT = JoyValue(axes=NO_INPUT_AXES, buttons=NO_BUTTONS)

#: アクセル全開・右ステアリング・ギアD
JOY_FULL = JoyValue(
    axes=(0.7, 0.0, +1.0, 0.0, 0.0, -1.0, 0.0, +1.0),
    buttons=NO_BUTTONS,
)

#: ゼロ埋め。driver はアクセル50%・ブレーキ50%と解釈する (HZ-1 の再現用)。
JOY_ZEROS = JoyValue(axes=(0.0,) * 8, buttons=NO_BUTTONS)


def joy_with_buttons(*indices: int, base: JoyValue = JOY_NO_INPUT) -> JoyValue:
    """指定した index のボタンだけを押した joy を作る。"""
    buttons = list(base.buttons)
    for i in indices:
        buttons[i] = 1
    return JoyValue(axes=base.axes, buttons=tuple(buttons), stamp_ns=base.stamp_ns)


def fresh_joy(joy: JoyValue = JOY_NO_INPUT, age_s: float = 0.1) -> JoyObservation:
    return JoyObservation(joy=joy, age_s=age_s)


def stale_joy(joy: JoyValue = JOY_NO_INPUT, age_s: float = 3.0) -> JoyObservation:
    return JoyObservation(joy=joy, age_s=age_s)


# --------------------------------------------------------------------------
# 車両観測のビルダ
# --------------------------------------------------------------------------


def obs(
    vehicle_id: str,
    *,
    velocity: float | None = 0.0,
    velocity_age: float | None = 0.1,
    emergency: bool | None = True,
    debug_age: float | None = 0.1,
) -> VehicleObservation:
    """既定は「停止していて emergency 済み、テレメトリ新鮮」。"""
    return VehicleObservation(
        vehicle_id=vehicle_id,
        velocity_mps=velocity,
        velocity_age_s=velocity_age,
        emergency=emergency,
        debug_age_s=debug_age,
    )


def all_stopped(vehicles=VEHICLES, **per_vehicle) -> dict[str, VehicleObservation]:
    """対象車両すべてが正常停止。per_vehicle で個別に上書きする。

        all_stopped(A3=dict(velocity=0.5))        # A3 だけ動いている
        all_stopped(("A2", "A3"))                 # 2台だけを対象にする
    """
    result = {}
    for vehicle_id in vehicles:
        overrides = per_vehicle.get(vehicle_id, {})
        result[vehicle_id] = obs(vehicle_id, **overrides)
    return result


# --------------------------------------------------------------------------
# 状態とイベントのビルダ
# --------------------------------------------------------------------------


def park() -> ManagerState:
    return ManagerState(mode=Mode.PARK)


def all_mode() -> ManagerState:
    return ManagerState(mode=Mode.ALL)


def single_mode(vehicle_id: str) -> ManagerState:
    return ManagerState(mode=Mode.SINGLE, selected=vehicle_id)


def stopping(
    destinations: frozenset[str] | None = None,
    elapsed_s: float = 0.0,
    selected: str | None = None,
) -> ManagerState:
    return ManagerState(
        mode=Mode.STOPPING,
        selected=selected,
        stopping_destinations=(
            destinations if destinations is not None else frozenset(VEHICLES)
        ),
        stopping_elapsed_s=elapsed_s,
    )


TICK = Event(kind=EventKind.TICK)
JOY_EVENT = Event(kind=EventKind.JOY)
ENTER_ALL = Event(kind=EventKind.ENTER_ALL_MODE)


def enter_single(vehicle_id: str) -> Event:
    return Event(kind=EventKind.ENTER_SINGLE_MODE, vehicle_id=vehicle_id)


# --------------------------------------------------------------------------
# アサーション補助
# --------------------------------------------------------------------------


def blocker_codes(blockers) -> set:
    return {b.code for b in blockers}


def alert_codes(alerts) -> set:
    return {a.code for a in alerts}


def vehicles_for(items, code) -> tuple[str, ...]:
    """指定コードの blocker / alert が挙げている車両を返す。"""
    for item in items:
        if item.code == code:
            return item.vehicles
    return ()
