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
STEP_TEARDOWN = "teardown"

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

    install_setup_bash: bool = False
    submit_dir_populated: bool = False
    install_mtime: Optional[float] = None
    submit_mtime: Optional[float] = None
    services_running: FrozenSet[str] = field(default_factory=frozenset)


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


STEPS = (
    Step(
        step_id=STEP_PREFLIGHT,
        title="preflight",
        command=("./setup_check.sh", "--phase", "preflight"),
    ),
    Step(
        step_id=STEP_SUBMISSION,
        title="提出物",
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
        title="スタック起動",
        # CHECK=0: this console runs preflight first and runtime after, so the
        # target's own embedded checks would run them a second time.
        command=("make", "autoware-driver-zenoh-rosbag", "CHECK=0"),
        requires=(STEP_BUILD,),
    ),
    Step(
        step_id=STEP_RUNTIME,
        title="runtime check",
        command=("./setup_check.sh", "--phase", "runtime"),
        requires=(STEP_UP,),
    ),
    Step(
        step_id=STEP_TEARDOWN,
        title="片付け",
        command=("make", "down"),
    ),
)

_STEPS_BY_ID = {s.step_id: s for s in STEPS}

# 環境から実測できるステップ。session の記録より実測を優先する。
_MEASURED = frozenset({STEP_SUBMISSION, STEP_BUILD, STEP_UP, STEP_TEARDOWN})


def step_by_id(step_id: str) -> Step:
    """Look up a step, raising KeyError on an unknown id."""
    return _STEPS_BY_ID[step_id]


def build_done(ws: Workspace) -> bool:
    """Whether install/ exists and is no older than the submission."""
    if not ws.install_setup_bash:
        return False
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
    if step_id == STEP_SUBMISSION:
        return ws.submit_dir_populated
    if step_id == STEP_BUILD:
        return build_done(ws)
    if step_id == STEP_UP:
        return all(name in ws.services_running for name in REQUIRED_SERVICES)
    if step_id == STEP_TEARDOWN:
        return not any(name in ws.services_running for name in REQUIRED_SERVICES)
    raise KeyError(step_id)


def is_runnable(step_id: str, ws: Workspace, session: Dict[str, str]) -> bool:
    """Whether the console may run this step now.

    Not runnable while the step itself is already RUNNING, so the console
    never launches a second overlapping run of the same step. Otherwise
    runnable once every prerequisite reports DONE; the step's own status
    being PENDING, DONE or FAILED is fine either way, so re-running a
    finished step and retrying a failed one both stay possible.
    """
    if step_status(step_id, ws, session) == RUNNING:
        return False
    step = step_by_id(step_id)
    return all(step_status(dep, ws, session) == DONE for dep in step.requires)
