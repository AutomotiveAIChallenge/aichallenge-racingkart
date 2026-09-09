#!/usr/bin/env python3
"""Pure logic for the vehicle console TUI.

Holds the step definitions, the prerequisite rules and the state derivation.
Deliberately free of curses, subprocess and filesystem access: everything the
console observes about the machine arrives as a Workspace snapshot, so the
rules can be tested without a terminal, a docker daemon or a built workspace.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Optional, Tuple

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
# 単体で止めたり入れ替えたりする compose サービス。REQUIRED_SERVICES の一員でも
# あるが、autoware だけを扱うステップがこの名前を直接必要とする。
AUTOWARE_SERVICE = "autoware"


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
    # aichallenge/workspace/ が checkout 直後の状態か（tracked に差分が無く、
    # untracked も ignored な生成物も無い）。既定は False: 観測できなかったときに
    # cleanup を「済」と見せてはいけない。
    workspace_pristine: bool = False


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
    ),
    Step(
        step_id=STEP_BUILD,
        title="build",
        command=("make", "autoware-build"),
        requires=(STEP_SUBMISSION,),
    ),
    Step(
        step_id=STEP_UP,
        title="autoware",
        command=("make", "autoware-driver-zenoh-rosbag"),
        requires=(STEP_BUILD,),
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
    ),
    Step(
        step_id=STEP_AUTOWARE_DOWN,
        title="autoware down",
        command=("make", "autoware-down"),
    ),
    Step(
        step_id=STEP_TEARDOWN,
        title="down all",
        command=("make", "down"),
    ),
    Step(
        step_id=STEP_CLEAN,
        title="cleanup",
        command=("make", "workspace-clean"),
    ),
)

_STEPS_BY_ID = {s.step_id: s for s in STEPS}

# 環境から実測できるステップ。session の記録より実測を優先する。
#
# STEP_SUBMISSION は含めない: aichallenge_submit/ はこのリポジトリの
# checkout そのものに 15 個の tracked な参加者パッケージが入っており、
# ダウンロード前から常に非空である。ディレクトリの有無は「取得済み」の
# 証拠にならないので、実測ではなく session の記録（ダウンロードを実際に
# 実行して成功したか）から状態を出す。うっかり実測へ戻さないこと。
_MEASURED = frozenset(
    {STEP_BUILD, STEP_UP, STEP_AUTOWARE_DOWN, STEP_TEARDOWN, STEP_CLEAN}
)

# STEP_RESTART は実測しない: 「入れ替え済み」は autoware が running かどうかでは
# 区別できず（起動しっぱなしでも running）、成否は終了コードにしか現れない。


def step_by_id(step_id: str) -> Step:
    """Look up a step, raising KeyError on an unknown id."""
    return _STEPS_BY_ID[step_id]


def build_done(ws: Workspace) -> bool:
    """Whether install/ exists and is no older than the submission."""
    if ws.install_mtime is None or ws.submit_mtime is None:
        # Freshness is unprovable without both timestamps; report stale rather
        # than let an old install/ pass as built.
        return False
    return ws.install_mtime >= ws.submit_mtime


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
    if step_id in _MEASURED:
        return DONE if _measured_done(step_id, ws) else PENDING
    return recorded or PENDING


def _measured_done(step_id: str, ws: Workspace) -> bool:
    if step_id == STEP_BUILD:
        return build_done(ws)
    if step_id == STEP_UP:
        return all(name in ws.services_running for name in REQUIRED_SERVICES)
    if step_id == STEP_AUTOWARE_DOWN:
        return AUTOWARE_SERVICE not in ws.services_running
    if step_id == STEP_TEARDOWN:
        return not any(name in ws.services_running for name in REQUIRED_SERVICES)
    if step_id == STEP_CLEAN:
        # checkout 直後と同じなら済。提出物で上書きされた aichallenge_submit/ も、
        # build/ install/ log/ も、どれか残っていれば未実行。
        return ws.workspace_pristine
    raise KeyError(step_id)


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
