#!/usr/bin/env python3
"""Pure logic for the vehicle console TUI.

Holds the step definitions, the prerequisite rules and the state derivation.
Deliberately free of curses, subprocess and filesystem access: everything the
console observes about the machine arrives as a Workspace snapshot, so the
rules can be tested without a terminal, a docker daemon or a built workspace.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, FrozenSet, Optional, Tuple

# --- ステップの状態 ---------------------------------------------------------
PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

# --- ステップ ID -----------------------------------------------------------
STEP_PREFLIGHT = "preflight"
STEP_SUBMISSION = "submission"
STEP_BUILD = "build"
STEP_UP = "up"
STEP_RUNTIME = "runtime"
STEP_AUTOWARE_DOWN = "autoware_down"
STEP_TEARDOWN = "teardown"
STEP_CLEAN = "clean"
# 運営だけに出すステップ
STEP_DRIVER = "driver"        # 土台: racing_kart_interface
STEP_ZENOH = "zenoh"          # 土台: Zenoh bridge
STEP_ROSBAG = "rosbag"        # 土台: all-topic rosbag
STEP_DRIVER_DOWN = "driver_down"
STEP_ZENOH_DOWN = "zenoh_down"
STEP_ROSBAG_DOWN = "rosbag_down"
STEP_DOWNLOAD = "download"    # 提出物を board から取る

# --- 役割 ------------------------------------------------------------------
# 参加者は autoware と提出物だけを触る。driver / zenoh / rosbag、ダウンロード、全体停止は運営。
ROLE_PARTICIPANT = "participant"
ROLE_STAFF = "staff"
ROLES = (ROLE_PARTICIPANT, ROLE_STAFF)

# 実車スタックの compose サービス。ヘッダのバッジはこの順で 1 文字ずつ並べる。
REQUIRED_SERVICES = ("driver", "autoware", "zenoh", "rosbag")


@dataclass(frozen=True)
class Workspace:
    """What the console can observe about the vehicle PC, sampled once.

    Sampled by vehicle/tui.py and passed in here so the rules stay pure. A
    field left at its default means "not observed / not present", never
    "unknown but probably fine".
    """

    install_mtime: Optional[float] = None
    submit_mtime: Optional[float] = None
    services_running: FrozenSet[str] = field(default_factory=frozenset)
    # このリポジトリから compose で起動された running なコンテナ数（プロジェクト不問）。
    # services_running は default しか見ないので、make down の「全部止まったか」はこちら。
    stack_containers: int = 0
    # aichallenge/workspace/ が checkout 直後の状態か。既定は False: 観測できなかったときに
    # cleanup を「済」と見せてはいけない。
    workspace_pristine: bool = False


def build_done(ws: Workspace) -> bool:
    """Whether install/ exists and is no older than the submission."""
    if ws.install_mtime is None or ws.submit_mtime is None:
        # Freshness is unprovable without both timestamps; report stale rather
        # than let an old install/ pass as built.
        return False
    return ws.install_mtime >= ws.submit_mtime


def _autoware_up(ws: Workspace) -> bool:
    # autoware-vehicle が上げるのは autoware だけ。driver / zenoh / rosbag の状態は
    # バッジで見せ、完了判定には入れない（運営側の作業に参加者の印が左右されない）。
    return "autoware" in ws.services_running


def _service_up(name: str) -> Callable[[Workspace], bool]:
    return lambda ws: name in ws.services_running


def _service_down(name: str) -> Callable[[Workspace], bool]:
    return lambda ws: name not in ws.services_running


def _stack_down(ws: Workspace) -> bool:
    # make down は default と -p 1..4 の全コンテナを落とす。REQUIRED_SERVICES だけ
    # 見ると simulator や別プロジェクトの autoware が残っていても OK と出てしまう。
    return ws.stack_containers == 0 and not any(
        name in ws.services_running for name in REQUIRED_SERVICES
    )


def _autoware_down(ws: Workspace) -> bool:
    return "autoware" not in ws.services_running


def _workspace_pristine(ws: Workspace) -> bool:
    # checkout 直後と同じなら済。提出物で上書きされた aichallenge_submit/ も、
    # build/ install/ log/ も、どれか残っていれば未実行。
    return ws.workspace_pristine


@dataclass(frozen=True)
class Step:
    """One row of the console.

    command は make / setup_check.sh の呼び出しそのもの。中身をここに複製しない。
    interactive なステップは端末を子プロセスへ明け渡す必要がある。
    """

    step_id: str
    title: str
    command: Tuple[str, ...]
    requires: Tuple[str, ...] = ()
    interactive: bool = False
    # 実行するディレクトリ。リポジトリルートからの相対。コマンド名から推測すると
    # 将来のステップが黙って間違った cwd を継ぐので、ステップ側で宣言させる。
    cwd: str = "."
    # 環境から完了を実測する述語。None なら実測できないステップで、合否は
    # 終了コードにしか現れないので session の記録から状態を出す。
    measure: Optional[Callable[[Workspace], bool]] = None
    # 行末に括弧書きで出す一言。押してよい場面が題名から読めないステップにだけ付ける。
    # 端末は 46 桁しかないので短く、幅計算が狂わないよう ASCII で書く。
    note: str = ""


# 参加者の並び。運営は STAFF_STEPS を別画面として持つ（参加者の続きではない）。
PARTICIPANT_STEPS = (
    Step(
        step_id=STEP_PREFLIGHT,
        title="check preflight",
        command=("./setup_check.sh", "--phase", "preflight"),
        cwd="vehicle",
    ),
    Step(
        step_id=STEP_SUBMISSION,
        title="extract",
        command=("make", "submission-extract"),
        requires=(STEP_PREFLIGHT,),
        # extract_submission.py prompts for the team id and the zip password.
        interactive=True,
        # measure を持たせない: aichallenge_submit/ は checkout 時点で 15 個の tracked な
        # パッケージが入っており常に非空。ディレクトリの有無は入れ替えの証拠にならない。
    ),
    Step(
        step_id=STEP_BUILD,
        title="build",
        command=("make", "autoware-build"),
        requires=(STEP_SUBMISSION,),
        measure=build_done,
    ),
    Step(
        step_id=STEP_UP,
        title="autoware-vehicle",
        command=("make", "autoware-vehicle"),
        requires=(STEP_BUILD,),
        measure=_autoware_up,
    ),
    Step(
        step_id=STEP_RUNTIME,
        title="check runtime",
        command=("./setup_check.sh", "--phase", "runtime"),
        cwd="vehicle",
        requires=(STEP_UP,),
        # check_imu_bias() が停止確認の y/N プロンプトを出す。端末を明け渡さないと
        # curses の getch() とプロンプトの read が同じ tty を取り合い、
        # 操作者に見えないまま応答不能でハングする。
        interactive=True,
    ),
    Step(
        step_id=STEP_AUTOWARE_DOWN,
        title="autoware-vehicle down",
        command=("docker", "compose", "down", "autoware"),
        measure=_autoware_down,
    ),
    Step(
        step_id=STEP_CLEAN,
        title="cleanup",
        command=("make", "workspace-clean"),
        measure=_workspace_pristine,
    ),
)

# 運営の並び。参加者の画面には出さない: download、土台サービスの個別の上げ下げ、
# スタック全体の停止は運営の仕事で、参加者の並びとは独立した 8 ステップだけの画面。
STAFF_STEPS = (
    Step(
        step_id=STEP_DOWNLOAD,
        title="download",
        command=("make", "download"),
        # download_submission.sh prompts for username/password and
        # download_submission.py prompts for the submission to take.
        interactive=True,
    ),
    Step(
        step_id=STEP_DRIVER,
        title="driver",
        note="always on",
        command=("make", "driver"),
        measure=_service_up("driver"),
    ),
    Step(
        step_id=STEP_ZENOH,
        title="zenoh",
        note="always on",
        command=("make", "zenoh"),
        measure=_service_up("zenoh"),
    ),
    Step(
        step_id=STEP_DRIVER_DOWN,
        title="driver down",
        note="on faults only",
        command=("docker", "compose", "down", "driver"),
        measure=_service_down("driver"),
    ),
    Step(
        step_id=STEP_ZENOH_DOWN,
        title="zenoh down",
        note="on faults only",
        command=("docker", "compose", "down", "zenoh"),
        measure=_service_down("zenoh"),
    ),
    Step(
        step_id=STEP_ROSBAG,
        title="rosbag",
        note="not during the event",
        command=("make", "rosbag"),
        measure=_service_up("rosbag"),
    ),
    Step(
        step_id=STEP_ROSBAG_DOWN,
        title="rosbag down",
        note="not during the event",
        command=("docker", "compose", "down", "rosbag"),
        measure=_service_down("rosbag"),
    ),
    Step(
        step_id=STEP_TEARDOWN,
        title="down all",
        note="end of the day",
        command=("make", "down"),
        measure=_stack_down,
    ),
)

# 全ステップ。step_by_id はこちらを見る（参加者用と運営用の和集合）。
STEPS = PARTICIPANT_STEPS + STAFF_STEPS

_STEPS_BY_ID = {s.step_id: s for s in STEPS}


def steps_for_role(role: str) -> Tuple[Step, ...]:
    """The rows the console shows for a role; ValueError on an unknown role."""
    if role == ROLE_PARTICIPANT:
        return PARTICIPANT_STEPS
    if role == ROLE_STAFF:
        return STAFF_STEPS
    raise ValueError(f"unknown role: {role!r} (expected one of {ROLES})")

def step_by_id(step_id: str) -> Step:
    """Look up a step, raising KeyError on an unknown id."""
    return _STEPS_BY_ID[step_id]


def step_status(step_id: str, ws: Workspace, session: Dict[str, str]) -> str:
    """Derive a step's state.

    A step in flight reports RUNNING regardless of anything else. Otherwise
    measured steps come from the environment, so an external `make down` shows
    through instead of this session's stale memory; the remaining steps are
    check runs whose result exists only as an exit code, so they come from the
    session.
    """
    recorded = session.get(step_id)
    if recorded == RUNNING:
        return RUNNING
    measure = step_by_id(step_id).measure
    if measure is not None:
        return DONE if measure(ws) else PENDING
    return recorded or PENDING


def is_runnable(step_id: str, ws: Workspace, session: Dict[str, str]) -> bool:
    """Whether the console may run this step now.

    Prerequisites (`Step.requires`) are advisory, not a gate: they are shown
    on screen via `has_unmet_requirement`, but do not block Enter. The operator
    is standing on the machine and can see for themselves that, say, preflight
    legitimately fails on a dev box with no CAN hardware attached -- the
    console's job is to surface that deviation, not to forbid working around
    it. Launching `make autoware-build` or the stack with an unmet
    prerequisite is a deliberate operator call, not a bug.

    The one real hazard is launching a second overlapping run of the same
    step (e.g. two concurrent `make autoware-driver-zenoh-rosbag` against the
    same compose project), so this still returns False while the step's own
    status is RUNNING. That is the only thing that blocks Enter.
    """
    return step_status(step_id, ws, session) != RUNNING


def has_unmet_requirement(
    step_id: str, ws: Workspace, session: Dict[str, str]
) -> bool:
    """Whether any of this step's prerequisites is not DONE.

    Display-only: it warns the operator that a step is being run out of the
    normal order, and does not block it. The screen shows only that a
    prerequisite is missing -- a single-character mark -- never which one, so
    a boolean is the whole contract. False when every prerequisite is DONE,
    and when there are none.
    """
    step = step_by_id(step_id)
    return any(step_status(dep, ws, session) != DONE for dep in step.requires)
