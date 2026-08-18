"""racing_kart_manager の純粋ロジック。

ROS に依存しない。sensor_msgs/Joy との相互変換はノード側 (racing_kart_manager.py) が行う。
これにより L1 のテストは ROS を起動せずに pytest だけで完走できる。

仕様: docs/spec/multi-vehicle-start-stop.md
テスト: docs/spec/multi-vehicle-start-stop-test.md
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Mapping, Optional

# --------------------------------------------------------------------------
# 対象車両
#
# 台数も車両IDも固定しない。起動時に引数で渡す。使わない車両を固定リストに
# 残すと、その車の停止確認が永久に取れず全操作が塞がれる。
# --------------------------------------------------------------------------

#: 運用に存在する車両ID。起動引数の検証にだけ使う。
#: remote/connect_zenoh.bash のポート表と揃えること。
KNOWN_VEHICLE_IDS: tuple[str, ...] = ("A1", "A2", "A3", "A5", "A6", "A7", "A8")


def parse_vehicles(args) -> Optional[tuple[str, ...]]:
    """起動引数から対象車両を決める。解釈できなければ None。

    指定した順を保つ (GUI のボタン並びに効く)。空・未知のID・重複は拒否する。
    重複を許すと、片方の観測がもう片方を上書きして判定が壊れる。
    """
    vehicles = tuple(args)
    if not vehicles:
        return None
    if any(vehicle_id not in KNOWN_VEHICLE_IDS for vehicle_id in vehicles):
        return None
    if len(set(vehicles)) != len(vehicles):
        return None
    return vehicles


# --------------------------------------------------------------------------
# キーバインド
#
# racing_kart_interface/src/racing_kart_driver/src/keybind/joystick.hpp と
# keybind/mapping.hpp の複製。別リポジトリのため import できない。
# 変更するときは両方を直すこと。
# --------------------------------------------------------------------------

NUM_AXES = 8
NUM_BUTTONS = 11

AXIS_STEER = 0  # LeftStickHorizontal
AXIS_BRAKE = 2  # LeftTrigger
AXIS_ACCEL = 5  # RightTrigger
AXIS_DPAD_H = 6  # DpadHorizontal
AXIS_DPAD_V = 7  # DpadVertical

BUTTON_LB = 4
BUTTON_RB = 5
BUTTON_START = 6
BUTTON_BACK = 7

#: driver はこの4つを OR で見て is_emergency_ を立てる
#: (racing_kart_driver_node.cpp:227-238)
EMERGENCY_BUTTONS: tuple[int, ...] = (BUTTON_LB, BUTTON_RB, BUTTON_START, BUTTON_BACK)

#: 無操作の実値。driver はアクセル・ブレーキを clamp((1.0 - axes[i]) / 2.0, 0, 1) で
#: 解釈するため (racing_kart_driver_node.cpp:345,367)、無操作は 0.0 ではなく +1.0。
#: ゼロ埋めするとアクセル50%・ブレーキ50%を踏んだ扱いになる。
NO_INPUT_AXES: tuple[float, ...] = (
    0.0,  # 0 Steer
    0.0,  # 1 LeftStickVertical (未使用)
    +1.0,  # 2 Brake
    0.0,  # 3 RightStickHorizontal (未使用)
    0.0,  # 4 RightStickVertical (未使用)
    +1.0,  # 5 Accel
    0.0,  # 6 DpadHorizontal (ギア)
    0.0,  # 7 DpadVertical (ギア)
)


# --------------------------------------------------------------------------
# パラメータ
# --------------------------------------------------------------------------

#: これを超えたテレメトリは Tri.UNKNOWN 扱い。zenoh が全トピックを 10Hz に
#: 間引く (remote/zenoh-user.json5.template の pub_max_frequencies) ため十分長くとる。
TELEMETRY_TIMEOUT_S = 1.0

#: VelocityReport.longitudinal_velocity の絶対値がこれ未満なら停止とみなす
STOPPED_SPEED_THRESHOLD_MPS = 0.1

#: 停止プロトコルで emergency を確認できないまま経過したら警告を出すまでの秒数
EMERGENCY_CONFIRM_TIMEOUT_S = 5.0

#: joy 入力が途絶したとみなすまでの秒数。driver 側の joy_delay_threshold (5.0) より
#: 十分短くして、車両が落ちる前にオペレータへ知らせる。
JOY_TIMEOUT_S = 1.0

#: 単車操作に入る前提のスティック無操作の判定
NO_INPUT_STEER_TOLERANCE = 0.1
NO_INPUT_TRIGGER_MIN = 0.9

#: autoware_auto_vehicle_msgs/ControlModeReport の値 → 表示名。
#: Autoware のログや rviz と突き合わせやすいよう英語のまま出す。
#: 表示にのみ使い、モード遷移の判定には使わない。
CONTROL_MODE_NAMES: Mapping[int, str] = {
    0: "NO_COMMAND",
    1: "AUTONOMOUS",
    2: "AUTONOMOUS_STEER_ONLY",
    3: "AUTONOMOUS_VELOCITY_ONLY",
    4: "MANUAL",
    5: "DISENGAGED",
    6: "NOT_READY",
}


# --------------------------------------------------------------------------
# 値の型
# --------------------------------------------------------------------------


class Tri(Enum):
    """テレメトリ由来の三値。UNKNOWN を TRUE にも FALSE にも倒さない。"""

    TRUE = auto()
    FALSE = auto()
    UNKNOWN = auto()


class Mode(Enum):
    """モード = joy をどう変換するか。"""

    PARK = auto()  # 宛先なし
    ALL = auto()  # 4台へ、軸無操作
    SINGLE = auto()  # 1台へ、軸実値
    STOPPING = auto()  # 縮める前の宛先へ、軸無操作 + 緊急停止ボタン強制


class BlockerCode(Enum):
    """モードに入れない理由。"""

    VEHICLE_MOVING = auto()  # 速度が閾値以上
    VEHICLE_STATE_UNKNOWN = auto()  # テレメトリ途絶で停止 / emergency を判定できない
    VEHICLE_EMERGENCY_CLEARED = auto()  # emergency == false (解除されている)
    STICK_IN_USE = auto()
    JOY_STALE = auto()
    NOT_IN_PARK = auto()  # パーク以外からの遷移は不可


class AlertCode(Enum):
    """進行中の異常。遷移可否とは独立に出す。"""

    EMERGENCY_CONFIRM_TIMEOUT = auto()
    TELEMETRY_LOST = auto()
    JOY_STALE = auto()


@dataclass(frozen=True)
class JoyValue:
    """sensor_msgs/Joy の中身。ROS 型に依存しないための入れ物。"""

    axes: tuple[float, ...]
    buttons: tuple[int, ...]
    stamp_ns: int = 0


@dataclass(frozen=True)
class TransformSpec:
    """joy をどう変換するか。モードから一意に決まる。"""

    destinations: frozenset[str]
    suppress_axes: bool
    force_emergency: bool


@dataclass(frozen=True)
class VehicleObservation:
    """1台分のテレメトリ観測。age が None なら一度も受信していない。"""

    vehicle_id: str
    velocity_mps: Optional[float] = None
    velocity_age_s: Optional[float] = None
    emergency: Optional[bool] = None
    debug_age_s: Optional[float] = None
    control_mode: Optional[int] = None
    control_mode_age_s: Optional[float] = None


@dataclass(frozen=True)
class JoyObservation:
    """joy 入力の観測。"""

    joy: Optional[JoyValue] = None
    age_s: Optional[float] = None


@dataclass(frozen=True)
class ManagerState:
    """モードと、モードに付随する状態。

    Mode だけでは SINGLE の対象車と STOPPING の送信先を表せないため、
    遷移関数はこの型を受け渡す。
    """

    mode: Mode = Mode.PARK
    selected: Optional[str] = None
    #: STOPPING のとき、縮める前の宛先。ここへ緊急停止を送り続ける。
    stopping_destinations: frozenset[str] = frozenset()
    #: STOPPING に入ってからの経過秒
    stopping_elapsed_s: Optional[float] = None


#: 起動直後の状態。必ずパークから始まる (観点 D-1)。
INITIAL_STATE = ManagerState(mode=Mode.PARK)


@dataclass(frozen=True)
class Blocker:
    code: BlockerCode
    vehicles: tuple[str, ...] = ()


@dataclass(frozen=True)
class Alert:
    code: AlertCode
    vehicles: tuple[str, ...] = ()


@dataclass(frozen=True)
class VehicleStatus:
    vehicle_id: str
    receiving_joy: bool
    stopped: Tri
    emergency: Tri
    velocity_age_s: Optional[float]
    debug_age_s: Optional[float]
    #: 制御モード名。途絶していれば None
    control_mode: Optional[str] = None
    #: GUI にそのまま出す1行。文言は manager 側で作る (観点 F-1)
    label: str = ""


@dataclass(frozen=True)
class Status:
    """GUI 描画と遷移判定の両方が使う唯一の判断材料。"""

    mode: Mode
    selected: Optional[str]
    vehicles: tuple[VehicleStatus, ...]
    alerts: tuple[Alert, ...]
    stopping_elapsed_s: Optional[float]

    # モードに入れない理由。空なら入れる。
    enter_all_mode_blockers: tuple[Blocker, ...] = ()
    enter_single_mode_blockers: Mapping[str, tuple[Blocker, ...]] = field(
        default_factory=dict
    )

    @property
    def can_enter_all_mode(self) -> bool:
        """可否は保持せず blocker から導出する。食い違いを構造的に防ぐ。"""
        return not self.enter_all_mode_blockers

    def can_enter_single_mode(self, vehicle_id: str) -> bool:
        return not self.enter_single_mode_blockers[vehicle_id]


class EventKind(Enum):
    JOY = auto()  # joy を受信した
    ENTER_ALL_MODE = auto()  # GUI の一斉発進準備完了ボタン
    ENTER_SINGLE_MODE = auto()  # GUI の車両選択
    TICK = auto()  # テレメトリ更新など、外部操作を伴わない再評価


@dataclass(frozen=True)
class Event:
    kind: EventKind
    vehicle_id: Optional[str] = None


# --------------------------------------------------------------------------
# 観測の解釈
# --------------------------------------------------------------------------


def stopped_of(observation: VehicleObservation) -> Tri:
    """速度観測から停止しているかを求める。途絶していれば UNKNOWN。

    無音を停止扱いしない。UNKNOWN と FALSE は別物として扱う (観点 F-5)。
    """
    if observation.velocity_mps is None or observation.velocity_age_s is None:
        return Tri.UNKNOWN
    if observation.velocity_age_s > TELEMETRY_TIMEOUT_S:
        return Tri.UNKNOWN
    if abs(observation.velocity_mps) < STOPPED_SPEED_THRESHOLD_MPS:
        return Tri.TRUE
    return Tri.FALSE


def emergency_of(observation: VehicleObservation) -> Tri:
    """debug/status の VehicleDebug.emergency。途絶していれば UNKNOWN。"""
    if observation.emergency is None or observation.debug_age_s is None:
        return Tri.UNKNOWN
    if observation.debug_age_s > TELEMETRY_TIMEOUT_S:
        return Tri.UNKNOWN
    return Tri.TRUE if observation.emergency else Tri.FALSE


def control_mode_of(observation: VehicleObservation) -> Optional[str]:
    """ControlModeReport の値を表示名にする。途絶していれば None。

    stopped / emergency と同じく、無音を既定値に倒さない。定義に無い値でも
    例外にせず値が分かる形で返す。上流が定数を増やしたときに GUI が落ちると、
    joy が止まっていなくても操作不能になるため。
    """
    if observation.control_mode is None or observation.control_mode_age_s is None:
        return None
    if observation.control_mode_age_s > TELEMETRY_TIMEOUT_S:
        return None
    return CONTROL_MODE_NAMES.get(
        observation.control_mode, f"UNDEFINED({observation.control_mode})"
    )


def emergency_pressed(joy: JoyValue) -> bool:
    """緊急停止ボタン4種のいずれかが押されているか。"""
    return any(
        index < len(joy.buttons) and joy.buttons[index] for index in EMERGENCY_BUTTONS
    )


def stick_no_input(joy: JoyValue) -> bool:
    """単車操作に入る前提を満たすスティック無操作か。

    アクセル・ブレーキは +1.0 が無操作なので「閾値以上」で判定する。
    """
    if len(joy.axes) < NUM_AXES:
        return False
    if abs(joy.axes[AXIS_STEER]) > NO_INPUT_STEER_TOLERANCE:
        return False
    if joy.axes[AXIS_ACCEL] < NO_INPUT_TRIGGER_MIN:
        return False
    if joy.axes[AXIS_BRAKE] < NO_INPUT_TRIGGER_MIN:
        return False
    return True


def _observation_of(
    observations: Mapping[str, VehicleObservation], vehicle_id: str
) -> VehicleObservation:
    """観測が無い車両は「一度も受信していない」として扱う。"""
    return observations.get(vehicle_id) or VehicleObservation(vehicle_id=vehicle_id)


def _joy_is_stale(joy_observation: JoyObservation) -> bool:
    if joy_observation.joy is None or joy_observation.age_s is None:
        return True
    return joy_observation.age_s > JOY_TIMEOUT_S


# --------------------------------------------------------------------------
# joy 変換
# --------------------------------------------------------------------------


def spec_for(state: ManagerState, vehicles: tuple[str, ...]) -> TransformSpec:
    """モードから joy 変換の仕様を決める。

    スティックで操縦するのは単車操作だけ。一斉と停止中は無操作値で上書きする。
    送信先の台数では切り替えない。台数基準にすると、対象車両が1台のときだけ
    一斉が単車操作と同じ挙動になり、モードの意味が崩れる。
    """
    if state.mode is Mode.PARK:
        destinations: frozenset[str] = frozenset()
    elif state.mode is Mode.ALL:
        destinations = frozenset(vehicles)
    elif state.mode is Mode.SINGLE:
        destinations = frozenset({state.selected}) if state.selected else frozenset()
    else:  # STOPPING
        destinations = state.stopping_destinations

    return TransformSpec(
        destinations=destinations,
        suppress_axes=state.mode is not Mode.SINGLE,
        force_emergency=state.mode is Mode.STOPPING,
    )


def transform(joy: JoyValue, spec: TransformSpec) -> dict[str, JoyValue]:
    """joy を宛先ごとに変換する。宛先が空なら何も返さない。

    軸を素通しするときは配列をそのまま渡す。サイズが異常な入力はそのまま流れ、
    driver 側の is_joystick_available() が停止指令に落とす (安全側)。
    """
    if not spec.destinations:
        return {}

    axes = NO_INPUT_AXES if spec.suppress_axes else tuple(joy.axes)
    buttons = tuple(joy.buttons)

    if spec.force_emergency:
        pressed = list(buttons)
        if len(pressed) < NUM_BUTTONS:
            pressed.extend([0] * (NUM_BUTTONS - len(pressed)))
        for index in EMERGENCY_BUTTONS:
            pressed[index] = 1
        buttons = tuple(pressed)

    outgoing = JoyValue(axes=axes, buttons=buttons, stamp_ns=joy.stamp_ns)
    return {vehicle_id: outgoing for vehicle_id in spec.destinations}


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------


#: 車両1台分の表示に使う語彙
_MOTION_TEXT: Mapping[Tri, str] = {
    Tri.TRUE: "停止中",
    Tri.FALSE: "走行中",
    Tri.UNKNOWN: "不明",
}
_EMERGENCY_TEXT: Mapping[Tri, str] = {
    Tri.TRUE: "有効",
    Tri.FALSE: "解除",
    Tri.UNKNOWN: "不明",
}


def vehicle_label(
    control_mode: Optional[str], stopped: Tri, emergency: Tri, receiving_joy: bool
) -> str:
    """車両1台分の状態を1行にする。4項目を常に出す。

        <制御モード> / <走行中|停止中|不明> / 緊急停止 <有効|解除|不明> / joy <送信中|送信なし>

    緊急停止を別項目として省かないのは、driver が emergency のとき制御モードに
    MANUAL を強制するため (racing_kart_driver_node.cpp:241-242)。MANUAL だけでは
    「緊急停止で止まっている（安全）」と「解除済みでいつでも動きうる（注意）」が
    区別できない。後者は単車操作に入れない条件であり、自発フォールバックの
    発火条件でもある。

    joy だけは車両の状態ではなく manager 側の情報（その車を送信先に含めているか）。
    由来は違うが、開発中は1行で追えるほうが有用なので同じ行に並べる。
    """
    return " / ".join(
        [
            control_mode or "不明",
            _MOTION_TEXT[stopped],
            f"緊急停止 {_EMERGENCY_TEXT[emergency]}",
            f"joy {'送信中' if receiving_joy else '送信なし'}",
        ]
    )


def _vehicle_blockers(
    vehicle_ids: tuple[str, ...],
    stopped: Mapping[str, Tri],
    emergency: Mapping[str, Tri],
) -> tuple[Blocker, ...]:
    """対象車両群のうち、停止が確認できないものを理由ごとにまとめる。"""
    moving = tuple(v for v in vehicle_ids if stopped[v] is Tri.FALSE)
    unknown = tuple(
        v
        for v in vehicle_ids
        if stopped[v] is Tri.UNKNOWN or emergency[v] is Tri.UNKNOWN
    )
    cleared = tuple(v for v in vehicle_ids if emergency[v] is Tri.FALSE)

    blockers = []
    if moving:
        blockers.append(Blocker(BlockerCode.VEHICLE_MOVING, moving))
    if unknown:
        blockers.append(Blocker(BlockerCode.VEHICLE_STATE_UNKNOWN, unknown))
    if cleared:
        blockers.append(Blocker(BlockerCode.VEHICLE_EMERGENCY_CLEARED, cleared))
    return tuple(blockers)


def status(
    state: ManagerState,
    observations: Mapping[str, VehicleObservation],
    joy_observation: JoyObservation,
    vehicles: tuple[str, ...],
) -> Status:
    """現在の状態と観測から Status を組み立てる。

    GUI 描画と遷移判定の両方がこれだけを見る。可否は blocker から導出され、
    別に保持しないので表示と判定が食い違わない。
    """
    stopped = {}
    emergency = {}
    destinations = spec_for(state, vehicles).destinations
    vehicle_statuses = []
    for vehicle_id in vehicles:
        observation = _observation_of(observations, vehicle_id)
        stopped[vehicle_id] = stopped_of(observation)
        emergency[vehicle_id] = emergency_of(observation)
        control_mode = control_mode_of(observation)
        receiving_joy = vehicle_id in destinations
        vehicle_statuses.append(
            VehicleStatus(
                vehicle_id=vehicle_id,
                receiving_joy=receiving_joy,
                stopped=stopped[vehicle_id],
                emergency=emergency[vehicle_id],
                velocity_age_s=observation.velocity_age_s,
                debug_age_s=observation.debug_age_s,
                control_mode=control_mode,
                label=vehicle_label(
                    control_mode,
                    stopped[vehicle_id],
                    emergency[vehicle_id],
                    receiving_joy,
                ),
            )
        )
    vehicle_statuses = tuple(vehicle_statuses)

    joy_stale = _joy_is_stale(joy_observation)
    common: list[Blocker] = []
    if state.mode is not Mode.PARK:
        common.append(Blocker(BlockerCode.NOT_IN_PARK))
    if joy_stale:
        common.append(Blocker(BlockerCode.JOY_STALE))

    # 一斉モードへは4台すべての停止確認が要る
    enter_all = tuple(common) + _vehicle_blockers(vehicles, stopped, emergency)

    # 単車操作へは対象以外の3台の停止確認が要る。対象車自身は含めない
    enter_single: dict[str, tuple[Blocker, ...]] = {}
    for target in vehicles:
        blockers = list(common)
        if joy_observation.joy is not None and not stick_no_input(joy_observation.joy):
            blockers.append(Blocker(BlockerCode.STICK_IN_USE))
        others = tuple(v for v in vehicles if v != target)
        blockers.extend(_vehicle_blockers(others, stopped, emergency))
        enter_single[target] = tuple(blockers)

    alerts: list[Alert] = []
    if joy_stale:
        alerts.append(Alert(AlertCode.JOY_STALE))
    lost = tuple(
        v
        for v in vehicles
        if stopped[v] is Tri.UNKNOWN or emergency[v] is Tri.UNKNOWN
    )
    if lost:
        alerts.append(Alert(AlertCode.TELEMETRY_LOST, lost))
    if (
        state.mode is Mode.STOPPING
        and state.stopping_elapsed_s is not None
        and state.stopping_elapsed_s >= EMERGENCY_CONFIRM_TIMEOUT_S
    ):
        unconfirmed = tuple(
            v
            for v in vehicles
            if v in state.stopping_destinations and emergency[v] is not Tri.TRUE
        )
        if unconfirmed:
            alerts.append(Alert(AlertCode.EMERGENCY_CONFIRM_TIMEOUT, unconfirmed))

    return Status(
        mode=state.mode,
        selected=state.selected,
        vehicles=vehicle_statuses,
        alerts=tuple(alerts),
        stopping_elapsed_s=state.stopping_elapsed_s,
        enter_all_mode_blockers=enter_all,
        enter_single_mode_blockers=enter_single,
    )


# --------------------------------------------------------------------------
# 遷移
# --------------------------------------------------------------------------


def _enter_stopping(state: ManagerState, vehicles: tuple[str, ...]) -> ManagerState:
    """縮める前の宛先を保ったまま停止中へ移る。

    宛先を先に狭めてはならない。publish を止めた車両は最後に届いた joy の
    まま最大5秒走り続けるため。
    """
    return ManagerState(
        mode=Mode.STOPPING,
        selected=state.selected,
        stopping_destinations=spec_for(state, vehicles).destinations,
        stopping_elapsed_s=0.0,
    )


def next_state(
    state: ManagerState,
    event: Event,
    observations: Mapping[str, VehicleObservation],
    joy_observation: JoyObservation,
    vehicles: tuple[str, ...],
) -> ManagerState:
    """遷移。表に無い組み合わせは現状維持する。"""
    emergency = {
        vehicle_id: emergency_of(_observation_of(observations, vehicle_id))
        for vehicle_id in vehicles
    }
    stopped = {
        vehicle_id: stopped_of(_observation_of(observations, vehicle_id))
        for vehicle_id in vehicles
    }

    # 停止プロトコル中は、全車の emergency を確認できるまで留まる。
    # GUI 操作は無視し、確認タイムアウトでも publish を止めない。
    if state.mode is Mode.STOPPING:
        unconfirmed = [
            v for v in state.stopping_destinations if emergency[v] is not Tri.TRUE
        ]
        if unconfirmed:
            return state
        return ManagerState(mode=Mode.PARK)

    # 緊急停止ボタンはいつでも受け付ける。パークは宛先が無いので何もしない。
    joy = joy_observation.joy
    if joy is not None and emergency_pressed(joy):
        if state.mode in (Mode.ALL, Mode.SINGLE):
            return _enter_stopping(state, vehicles)
        return state

    # 自発フォールバック。監視対象は単車操作のときの他3台だけ。
    # 一斉は4台とも走ってよく、パークは joy を送っていないので介入できない。
    if state.mode is Mode.SINGLE:
        others = [v for v in vehicles if v != state.selected]
        if any(
            stopped[v] is not Tri.TRUE or emergency[v] is not Tri.TRUE for v in others
        ):
            return _enter_stopping(state, vehicles)

    current = status(state, observations, joy_observation, vehicles)

    if event.kind is EventKind.ENTER_ALL_MODE and current.can_enter_all_mode:
        return ManagerState(mode=Mode.ALL)

    if (
        event.kind is EventKind.ENTER_SINGLE_MODE
        and event.vehicle_id in vehicles
        and current.can_enter_single_mode(event.vehicle_id)
    ):
        return ManagerState(mode=Mode.SINGLE, selected=event.vehicle_id)

    return state


# --------------------------------------------------------------------------
# GUI 向けの表示
#
# 観点 F-1 を守るため、表示文言まで manager 側で作る。GUI は受け取ったものを
# 並べるだけで、条件式を1つも持たない。
# --------------------------------------------------------------------------


class Level(Enum):
    INFO = auto()
    WARN = auto()
    ERROR = auto()


@dataclass(frozen=True)
class Message:
    """GUI に出す1件の文言。

    targets は、この文言がどの操作に紐づくかを表す。"all" は一斉発進準備完了
    ボタン、車両ID はその車両の選択ボタン。空なら特定の操作に紐づかない。

    同じ理由が複数の操作を塞ぐことがある（A3 が動いていると一斉発進も
    A2/A6/A7 の選択も塞がる）ため、文言は1件にまとめて対象を並べる。
    操作ごとに1件ずつ作ると、メッセージ表示エリアに同じ文が何度も並ぶ。
    """

    level: Level
    text: str
    targets: tuple[str, ...] = ()


#: "all" は一斉発進準備完了ボタンを指す予約語
TARGET_ALL = "all"

_BLOCKER_LEVEL: Mapping[BlockerCode, Level] = {
    BlockerCode.VEHICLE_MOVING: Level.WARN,
    BlockerCode.VEHICLE_STATE_UNKNOWN: Level.WARN,
    BlockerCode.VEHICLE_EMERGENCY_CLEARED: Level.WARN,
    BlockerCode.STICK_IN_USE: Level.WARN,
    BlockerCode.JOY_STALE: Level.ERROR,
    BlockerCode.NOT_IN_PARK: Level.INFO,
}

_ALERT_LEVEL: Mapping[AlertCode, Level] = {
    AlertCode.EMERGENCY_CONFIRM_TIMEOUT: Level.ERROR,
    AlertCode.TELEMETRY_LOST: Level.WARN,
    AlertCode.JOY_STALE: Level.ERROR,
}


def _join(vehicles: tuple[str, ...]) -> str:
    return " / ".join(vehicles)


def blocker_text(code: BlockerCode, vehicles: tuple[str, ...]) -> str:
    """操作を許可できない理由の文言。"""
    if code is BlockerCode.VEHICLE_MOVING:
        return f"{_join(vehicles)} が停止していません"
    if code is BlockerCode.VEHICLE_STATE_UNKNOWN:
        return f"{_join(vehicles)} の状態が不明です（テレメトリ途絶）"
    if code is BlockerCode.VEHICLE_EMERGENCY_CLEARED:
        return f"{_join(vehicles)} の緊急停止が解除されています"
    if code is BlockerCode.STICK_IN_USE:
        return "スティックとトリガーを離してください"
    if code is BlockerCode.JOY_STALE:
        return "ジョイスティックの入力が途絶しています"
    if code is BlockerCode.NOT_IN_PARK:
        return "パークに戻してから操作してください"
    raise KeyError(code)


def alert_text(code: AlertCode, vehicles: tuple[str, ...]) -> str:
    """進行中の異常の文言。"""
    if code is AlertCode.EMERGENCY_CONFIRM_TIMEOUT:
        return (
            f"{_join(vehicles)} の緊急停止を確認できません"
            f"（{EMERGENCY_CONFIRM_TIMEOUT_S:.0f}秒経過。送信は継続中）"
        )
    if code is AlertCode.TELEMETRY_LOST:
        return f"{_join(vehicles)} のテレメトリが途絶しています"
    if code is AlertCode.JOY_STALE:
        return "ジョイスティックの入力が途絶しています"
    raise KeyError(code)


def render_messages(status_value: Status) -> tuple[Message, ...]:
    """Status から表示文言を作る。

    同じ理由は1件にまとめ、塞いでいる操作を targets に並べる。
    """
    targets_by_key: dict[tuple[BlockerCode, tuple[str, ...]], set[str]] = {}
    order: list[tuple[BlockerCode, tuple[str, ...]]] = []

    def collect(blocker: Blocker, target: str) -> None:
        key = (blocker.code, blocker.vehicles)
        if key not in targets_by_key:
            targets_by_key[key] = set()
            order.append(key)
        targets_by_key[key].add(target)

    for blocker in status_value.enter_all_mode_blockers:
        collect(blocker, TARGET_ALL)
    vehicle_ids = tuple(v.vehicle_id for v in status_value.vehicles)
    for vehicle_id in vehicle_ids:
        for blocker in status_value.enter_single_mode_blockers.get(vehicle_id, ()):
            collect(blocker, vehicle_id)

    ordered_targets = (TARGET_ALL,) + vehicle_ids
    messages = [
        Message(
            level=_BLOCKER_LEVEL[code],
            text=blocker_text(code, vehicles),
            targets=tuple(t for t in ordered_targets if t in targets_by_key[(code, vehicles)]),
        )
        for code, vehicles in order
    ]

    messages.extend(
        Message(
            level=_ALERT_LEVEL[alert.code],
            text=alert_text(alert.code, alert.vehicles),
        )
        for alert in status_value.alerts
    )
    return tuple(messages)


# --------------------------------------------------------------------------
# GUI との JSON 境界
#
# racing_kart_msgs は別リポジトリで専用 .msg を作るとビルドが必要になるため、
# std_msgs/String に JSON を載せる。
# --------------------------------------------------------------------------

SCHEMA_VERSION = 1

#: GUI がこれを超えて status を受け取らなければ全ボタンを非活性にする
STATUS_TIMEOUT_S = 1.0

_LEVEL_NAMES: Mapping[Level, str] = {
    Level.INFO: "info",
    Level.WARN: "warn",
    Level.ERROR: "error",
}


def status_to_json(status_value: Status, stamp_ns: int) -> str:
    """Status を GUI 向けの JSON にする。

    Tri は文字列のまま出す。真偽値に潰すと UNKNOWN が表現できず、
    テレメトリ途絶を「停止」と誤表示する事故につながる。
    """
    payload = {
        "schema_version": SCHEMA_VERSION,
        "stamp_ns": stamp_ns,
        "mode": status_value.mode.name,
        "selected": status_value.selected,
        "stopping_elapsed_s": status_value.stopping_elapsed_s,
        "vehicles": [
            {
                "vehicle_id": vehicle.vehicle_id,
                "receiving_joy": vehicle.receiving_joy,
                "stopped": vehicle.stopped.name,
                "emergency": vehicle.emergency.name,
                "velocity_age_s": vehicle.velocity_age_s,
                "debug_age_s": vehicle.debug_age_s,
                "control_mode": vehicle.control_mode,
                "label": vehicle.label,
            }
            for vehicle in status_value.vehicles
        ],
        "can_enter_all_mode": status_value.can_enter_all_mode,
        "can_enter_single_mode": {
            vehicle.vehicle_id: status_value.can_enter_single_mode(vehicle.vehicle_id)
            for vehicle in status_value.vehicles
        },
        "messages": [
            {
                "level": _LEVEL_NAMES[message.level],
                "targets": list(message.targets),
                "text": message.text,
            }
            for message in render_messages(status_value)
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def parse_command(payload: str) -> Optional[Event]:
    """GUI からのコマンドを Event にする。解釈できなければ None。

    不正入力で例外を投げない。manager が落ちると joy が止まり、5秒後に
    全車が緊急停止するため、落ちないことが安全性に直結する。
    """
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("schema_version") != SCHEMA_VERSION:
        return None

    command = data.get("command")
    if command == "enter_all_mode":
        return Event(kind=EventKind.ENTER_ALL_MODE)
    if command == "enter_single_mode":
        vehicle_id = data.get("vehicle_id")
        # ここでは既知のIDかだけを見る。対象車両に入っているかは next_state が判定する
        if vehicle_id in KNOWN_VEHICLE_IDS:
            return Event(kind=EventKind.ENTER_SINGLE_MODE, vehicle_id=vehicle_id)
    return None


# --------------------------------------------------------------------------
# GUI 側に唯一許すロジック
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GuiGate:
    """GUI が操作を受け付けてよいか。"""

    usable: bool
    reason: Optional[str] = None


def gui_gate(
    status_age_s: Optional[float], schema_version: Optional[int]
) -> GuiGate:
    """status の鮮度とスキーマ版から、GUI 全体を止めるかを決める。

    manager が落ちても GUI には最後の status が残り続ける。これは manager
    自身からは送れないので GUI が検出する。古い状態をそのまま表示し続ける
    のが最も危険なため、疑わしければ止める。
    """
    if status_age_s is None or status_age_s > STATUS_TIMEOUT_S:
        return GuiGate(False, "manager と通信できていません")
    if schema_version != SCHEMA_VERSION:
        return GuiGate(False, "manager と GUI のバージョンが一致しません")
    return GuiGate(True)
