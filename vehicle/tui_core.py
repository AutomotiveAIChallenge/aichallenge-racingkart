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
STEP_RESTART = "restart"
STEP_AUTOWARE_DOWN = "autoware_down"
STEP_TEARDOWN = "teardown"
STEP_CLEAN = "clean"

# autoware-driver-zenoh-rosbag が起動する compose サービス。この全部が running
# ならスタックが上がっているとみなす。
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
    # このリポジトリから compose で起動された running なコンテナの数。プロジェクトを
    # 問わない（default も -p 1..4 も）。services_running は default プロジェクトしか
    # 見ないので、make down が落とす範囲の「全部止まったか」はこちらで判る。
    stack_containers: int = 0
    # aichallenge/workspace/ が checkout 直後の状態か（tracked に差分が無く、
    # untracked も ignored な生成物も無い）。既定は False: 観測できなかったときに
    # cleanup を「済」と見せてはいけない。
    workspace_pristine: bool = False


def build_done(ws: Workspace) -> bool:
    """Whether install/ exists and is no older than the submission."""
    if ws.install_mtime is None or ws.submit_mtime is None:
        # Freshness is unprovable without both timestamps; report stale rather
        # than let an old install/ pass as built.
        return False
    return ws.install_mtime >= ws.submit_mtime


def _stack_up(ws: Workspace) -> bool:
    return all(name in ws.services_running for name in REQUIRED_SERVICES)


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
    # 行の右に driver/autoware/zenoh/rosbag のバッジを出すか。compose サービスを
    # 起動・停止するステップだけ True。
    shows_service_badge: bool = False


STEPS = (
    Step(
        step_id=STEP_PREFLIGHT,
        title="check preflight",
        command=("./setup_check.sh", "--phase", "preflight"),
        cwd="vehicle",
    ),
    Step(
        step_id=STEP_SUBMISSION,
        title="download",
        command=("make", "download"),
        requires=(STEP_PREFLIGHT,),
        # download_submission.sh prompts for username/password and
        # download_submission.py prompts for the submission to take.
        interactive=True,
        # measure を持たせない: aichallenge_submit/ はこのリポジトリの checkout
        # そのものに 15 個の tracked な参加者パッケージが入っており、ダウンロード前
        # から常に非空である。ディレクトリの有無は「取得済み」の証拠にならない。
        # うっかり実測へ戻さないこと。
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
        title="autoware",
        command=("make", "autoware-driver-zenoh-rosbag"),
        requires=(STEP_BUILD,),
        measure=_stack_up,
        shows_service_badge=True,
    ),
    Step(
        step_id=STEP_RUNTIME,
        title="check runtime",
        command=("./setup_check.sh", "--phase", "runtime"),
        cwd="vehicle",
        requires=(STEP_UP,),
    ),
    Step(
        step_id=STEP_RESTART,
        title="autoware restart",
        command=("make", "autoware-restart"),
        requires=(STEP_UP,),
        # measure を持たせない: 「入れ替え済み」は autoware が running かどうかでは
        # 区別できず（起動しっぱなしでも running）、成否は終了コードにしか現れない。
        shows_service_badge=True,
    ),
    Step(
        step_id=STEP_AUTOWARE_DOWN,
        title="autoware down",
        command=("make", "autoware-down"),
        measure=_autoware_down,
        shows_service_badge=True,
    ),
    Step(
        step_id=STEP_TEARDOWN,
        title="down all",
        command=("make", "down"),
        measure=_stack_down,
        shows_service_badge=True,
    ),
    Step(
        step_id=STEP_CLEAN,
        title="cleanup",
        command=("make", "workspace-clean"),
        measure=_workspace_pristine,
    ),
)

_STEPS_BY_ID = {s.step_id: s for s in STEPS}

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
