#!/usr/bin/env python3
"""Vehicle console: the operations TUI for the kart's on-board PC.

Drives the repository's existing entry points -- make targets and
setup_check.sh -- instead of reimplementing them, and shows the order they
are meant to run in without enforcing it: the operator can run any step out
of order, and the console warns rather than blocks. The rules live in
tui_core; this module does the I/O.

Usage:
    vehicle/tui.py
"""
from __future__ import annotations

import curses
import queue
import shutil
import subprocess
import threading
from pathlib import Path

from tui_core import (
    DONE,
    FAILED,
    PENDING,
    REQUIRED_SERVICES,
    RUNNING,
    STEP_PREFLIGHT,
    STEP_SUBMISSION,
    STEP_TEARDOWN,
    STEP_UP,
    STEPS,
    Workspace,
    is_runnable,
    step_by_id,
    step_status,
    unmet_requirements,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

MIN_COLS = 80
MIN_LINES = 24
LOG_TAIL = 2000  # 保持するログ行数の上限。走行枠中に膨らみ続けないため。

_MARK = {DONE: "OK ", FAILED: "NG ", RUNNING: ">> ", PENDING: "-- "}


def terminal_too_small(cols: int, lines: int) -> bool:
    """Whether the terminal is below the minimum the layout needs."""
    return cols < MIN_COLS or lines < MIN_LINES


def probe_workspace(repo_root: Path, services_running: frozenset) -> Workspace:
    """Sample the workspace on disk.

    Filesystem only -- the docker query is passed in -- so this stays cheap
    enough to call on every redraw and testable in a temp dir. A missing
    workspace reads as "nothing present", not an error: the console has to
    render before anything has been downloaded.

    Known limitation: submit_mtime is submit_dir.stat().st_mtime, i.e. the
    directory's own mtime. That changes when an entry is added to or removed
    from aichallenge_submit/, but not when the contents of a file already
    inside it are edited. Editing a package's source in place therefore does
    not make build_done() report stale.

    Note: aichallenge_submit/ ships with 15 git-tracked participant packages,
    so it is never actually empty on a checkout -- whether it *has* entries
    proves nothing about whether a download has run. That is exactly why
    STEP_SUBMISSION is not in tui_core's _MEASURED set: its DONE/PENDING
    comes from the session (did `make download` exit 0 this run), not from
    this probe. submit_mtime is still sampled here because build_done() uses
    it to judge whether install/ is stale relative to the submission.
    """
    ws_dir = repo_root / "aichallenge" / "workspace"
    setup_bash = ws_dir / "install" / "setup.bash"
    submit_dir = ws_dir / "src" / "aichallenge_submit"

    install_present = setup_bash.is_file()
    submit_has_entries = submit_dir.is_dir() and any(submit_dir.iterdir())

    return Workspace(
        install_setup_bash=install_present,
        install_mtime=setup_bash.stat().st_mtime if install_present else None,
        submit_mtime=submit_dir.stat().st_mtime if submit_has_entries else None,
        services_running=services_running,
    )


def running_services(repo_root: Path) -> frozenset:
    """Which compose services are up right now.

    A docker failure yields an empty set rather than raising: the console must
    still render, and let the operator run preflight, on a machine whose
    daemon is down -- which is exactly when preflight is worth running.
    """
    try:
        out = subprocess.run(
            [
                "docker", "compose", "ps",
                "--status", "running",
                "--format", "{{.Service}}",
            ],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    if out.returncode != 0:
        return frozenset()
    return frozenset(line.strip() for line in out.stdout.splitlines() if line.strip())


class Console:
    """The curses console: one step list, one log pane.

    Owns the session's step results, the log buffer and the worker thread that
    runs a step. Step state is re-measured on every redraw; only check results
    (which exist solely as an exit code) are remembered here.
    """

    def __init__(self, screen) -> None:
        self.screen = screen
        self.session: dict = {}
        self.log: list = []
        self.log_queue: queue.Queue = queue.Queue()
        self.running_step = None
        self.cursor = 0
        self.ws = self.observe()

    def observe(self) -> Workspace:
        return probe_workspace(REPO_ROOT, running_services(REPO_ROOT))

    # --- 実行 ---------------------------------------------------------------

    def run_step(self, step_id: str) -> None:
        step = step_by_id(step_id)
        if step.interactive:
            self._run_interactive(step)
            return
        self.session[step_id] = RUNNING
        self.running_step = step_id
        self.log.append(f"$ {' '.join(step.command)}")
        threading.Thread(target=self._stream, args=(step,), daemon=True).start()

    def _stream(self, step) -> None:
        """Run a step in a worker thread, queueing each output line."""
        try:
            proc = subprocess.Popen(
                list(step.command),
                cwd=str(self._cwd_for(step)),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            self.log_queue.put(("line", f"起動できません: {exc}"))
            self.log_queue.put(("exit", (step.step_id, 127)))
            return
        assert proc.stdout is not None
        for line in iter(proc.stdout.readline, ""):
            self.log_queue.put(("line", line.rstrip("\n")))
        proc.wait()
        self.log_queue.put(("exit", (step.step_id, proc.returncode)))

    def _run_interactive(self, step) -> None:
        """Give the real terminal to a step that prompts.

        download_submission.sh reads a hidden password and
        download_submission.py asks which submission to take; both need a real
        tty, so curses is torn down and rebuilt around the call.
        """
        curses.endwin()
        print(f"\n$ {' '.join(step.command)}\n", flush=True)
        try:
            code = subprocess.call(list(step.command), cwd=str(self._cwd_for(step)))
        except OSError as exc:
            print(f"起動できません: {exc}", flush=True)
            code = 127
        input("\n[Enter] でコンソールに戻ります ")
        self.session[step.step_id] = DONE if code == 0 else FAILED
        self.log.append(f"$ {' '.join(step.command)}  -> exit {code}")
        self.ws = self.observe()
        self.screen.clear()

    @staticmethod
    def _cwd_for(step) -> Path:
        # setup_check.sh lives in vehicle/; every make target runs from the root.
        return REPO_ROOT / "vehicle" if step.command[0].endswith(".sh") else REPO_ROOT

    def drain(self) -> None:
        """Move queued worker output into the log, applying exit codes."""
        while True:
            try:
                kind, payload = self.log_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "line":
                self.log.append(payload)
            else:
                step_id, code = payload
                self.session[step_id] = DONE if code == 0 else FAILED
                self.log.append(f"[{step_id}] exit {code}")
                self.running_step = None
                self.ws = self.observe()
        if len(self.log) > LOG_TAIL:
            del self.log[: len(self.log) - LOG_TAIL]

    # --- 描画 ---------------------------------------------------------------

    def draw(self) -> None:
        self.screen.erase()
        lines, cols = self.screen.getmaxyx()

        self.screen.addnstr(
            0, 0, "Racing Kart Vehicle Console", cols - 1, curses.A_BOLD
        )

        for idx, step in enumerate(STEPS):
            status = step_status(step.step_id, self.ws, self.session)
            text = f" {idx + 1}  {_MARK[status]}{step.title:<16}{self._detail(step)}"
            attr = curses.A_REVERSE if idx == self.cursor else curses.A_NORMAL
            self.screen.addnstr(2 + idx, 0, text, cols - 1, attr)

        self.screen.addnstr(
            3 + len(STEPS),
            0,
            " up/down 選択   Enter 実行   q 終了",
            cols - 1,
            curses.A_DIM,
        )

        log_top = 5 + len(STEPS)
        room = max(0, lines - log_top - 1)
        for offset, line in enumerate(self.log[-room:] if room else []):
            self.screen.addnstr(log_top + offset, 0, line, cols - 1)

        self.screen.noutrefresh()
        curses.doupdate()

    def _detail(self, step) -> str:
        if step.step_id in (STEP_UP, STEP_TEARDOWN):
            detail = "  ".join(
                f"{name} {'on' if name in self.ws.services_running else 'off'}"
                for name in REQUIRED_SERVICES
            )
        elif step.step_id == STEP_SUBMISSION:
            # No filesystem-derived detail: aichallenge_submit/ ships tracked
            # packages, so its presence is not evidence a download happened.
            # Its status marker (from the session) is the only signal.
            detail = ""
        else:
            detail = ""
        unmet = unmet_requirements(step.step_id, self.ws, self.session)
        if unmet:
            names = ", ".join(step_by_id(dep).title for dep in unmet)
            warning = f"⚠ {names} 未完了"  # ⚠ <name> 未完了
            detail = f"{detail}  {warning}" if detail else warning
        return detail

    # --- 入力 ---------------------------------------------------------------

    def handle_key(self, key: int) -> bool:
        """Handle one keypress. Returns False to quit."""
        if key in (ord("q"), ord("Q")):
            return False
        if key == curses.KEY_UP:
            self.cursor = max(0, self.cursor - 1)
        elif key == curses.KEY_DOWN:
            self.cursor = min(len(STEPS) - 1, self.cursor + 1)
        elif key in (curses.KEY_ENTER, ord("\n"), ord("\r")):
            if self.running_step is None:
                step = STEPS[self.cursor]
                if is_runnable(step.step_id, self.ws, self.session):
                    self.run_step(step.step_id)
        return True


def _loop(screen) -> int:
    curses.curs_set(0)
    screen.nodelay(True)
    console = Console(screen)
    # preflight runs on open: a CAN or GNSS fault has to surface before a build.
    console.run_step(STEP_PREFLIGHT)
    while True:
        console.drain()
        console.draw()
        try:
            key = screen.getch()
        except curses.error:
            key = -1
        if key != -1 and not console.handle_key(key):
            return 0
        curses.napms(120)


def main() -> int:
    size = shutil.get_terminal_size(fallback=(0, 0))
    if terminal_too_small(size.columns, size.lines):
        print(
            f"端末が狭すぎます（{size.columns}x{size.lines}）。"
            f"最低 {MIN_COLS}x{MIN_LINES} が必要です。",
            flush=True,
        )
        return 2
    return curses.wrapper(_loop)


if __name__ == "__main__":
    raise SystemExit(main())
