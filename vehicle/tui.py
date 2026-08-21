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
import textwrap
import time
import threading
from pathlib import Path

from tui_core import (
    DONE,
    FAILED,
    PENDING,
    REQUIRED_SERVICES,
    RUNNING,
    STEP_PREFLIGHT,
    STEP_TEARDOWN,
    STEP_UP,
    STEPS,
    Workspace,
    is_runnable,
    step_by_id,
    step_status,
    has_unmet_requirement,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# 40x12 の内訳: header 1 + step 6 行 (縦 1 列) + failures 見出し 1 + failures 1
# + log 見出し 1 + log 1 で 11 行。1 行余裕を見て 12。これ未満だと failures か
# log が 0 行になり、失敗を流さずに残すという狙いが成立しない。
# 40 桁は最長セル "1 NG check preflight" の 20 文字に対する余裕。
MIN_COLS = 40
MIN_LINES = 12
LOG_TAIL = 2000  # 保持するログ行数の上限。走行枠中に膨らみ続けないため。
FAILURE_TAIL = 40  # failures 領域に retain する上限行数。
FALLBACK_LINES = 5  # マーカーの無いステップが失敗したとき末尾から拾う行数。
# アイドル中に実測を取り直す間隔。observe() は docker compose ps を待つので、
# 描画ごと (約 8Hz) に呼ぶと UI スレッドを塞ぐ。
OBSERVE_INTERVAL_SEC = 2.0

# 2 文字固定。桁を食わせない。"? " は前提未達で、実行は可能（前提は助言）。
_MARK = {DONE: "OK", FAILED: "NG", RUNNING: ">>", PENDING: "- "}
_MARK_UNMET = "? "

def terminal_too_small(cols: int, lines: int) -> bool:
    """Whether the terminal is below the minimum the layout needs."""
    return cols < MIN_COLS or lines < MIN_LINES


def service_badge(services_running) -> str:
    """REQUIRED_SERVICES を 1 文字ずつ並べた位置固定のバッジ。

    起動中はサービス名の頭文字、停止中は '-'。`driver off  autoware off ...`
    が 47 文字だったのを 4 文字にする。位置で意味が決まるので凡例が要らない。
    括弧を付けない: 40 桁の 2 列配置でセルに収める必要がある。
    """
    return "".join(
        name[0] if name in services_running else "-" for name in REQUIRED_SERVICES
    )


def is_failure_line(line: str) -> bool:
    """失敗を報告している行か。

    setup_check.sh の FAIL マーカーに依存する。あちらのマーカーを変えると
    失敗の retain が黙って止まるので、変更するときはここも直すこと。

    インデントされた行は拾わない。チェック自身が出す失敗行はどれも行頭にマーカーが
    来るので、行頭だけを見れば足りる。字下げされたマーカーは、あるチェックが出力例や
    ヒントの中でマーカーを引用したときに現れうるもので、それを失敗として数えたくない。
    マーカーを持たないステップ（make / docker）の失敗は _fallback_failures が
    終了コードから拾う。
    """
    return line.startswith("❌")


def wrap_line(line: str, width: int) -> list:
    """1 行を width で折り返す。空行は 1 行として残す。

    切り詰めると長いパスやコンパイラ出力の末尾が読めなくなるため、
    addnstr の切り詰めではなく折り返しを使う。
    """
    if width < 1:
        return []
    if not line.strip():
        return [""]
    return textwrap.wrap(line, width) or [""]


def should_reobserve(busy: bool, now: float, observed_at: float) -> bool:
    """アイドル中の実測を取り直すべきか。

    実行中は取り直さない。observe() は描画スレッドを塞ぐし、ステップ終了時には
    どうせ取り直すため。アイドル中に取り直さないと、別のシェルで make down された
    ときにバッジと実測ステップの表示が古いまま残る。
    """
    if busy:
        return False
    return now - observed_at >= OBSERVE_INTERVAL_SEC


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
    the submission step is not in tui_core's _MEASURED set: its DONE/PENDING
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
        # 失敗行は log とは別に retain する。log は tail しか見えないので、
        # 混ぜると流れて消える（ユーザーの「Error も流れてよくわからない」）。
        self.failures: list = []
        self._log_mark = 0
        self._observed_at = 0.0
        self.log_queue: queue.Queue = queue.Queue()
        self.cursor = 0
        # 既定値で始める。observe() は docker compose ps を待つので、
        # 最初の 1 フレームを描いたあとに _loop が呼ぶ。
        self.ws = Workspace()

    @property
    def busy(self) -> bool:
        """いずれかのステップが実行中か。

        session に RUNNING が入っているかで判る。別のフラグを持つと
        run_step と drain の両方で手で同期する必要が出る。
        """
        return any(status == RUNNING for status in self.session.values())

    def observe(self) -> Workspace:
        self._observed_at = time.monotonic()
        return probe_workspace(REPO_ROOT, running_services(REPO_ROOT))

    def refresh_if_stale(self) -> None:
        """アイドルが続いても実測を追い続ける。"""
        if should_reobserve(self.busy, time.monotonic(), self._observed_at):
            self.ws = self.observe()

    # --- 実行 ---------------------------------------------------------------

    def run_step(self, step_id: str) -> None:
        step = step_by_id(step_id)
        # 前回の実行の失敗を持ち越さない。表示は常に「今の実行」のもの。
        self.failures.clear()
        self._log_mark = len(self.log)
        if step.interactive:
            self._run_interactive(step)
            return
        self.session[step_id] = RUNNING
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
        return REPO_ROOT / step.cwd

    def drain(self) -> None:
        """Move queued worker output into the log, applying exit codes."""
        while True:
            try:
                kind, payload = self.log_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "line":
                self.log.append(payload)
                if is_failure_line(payload):
                    self.failures.append(payload)
            else:
                step_id, code = payload
                self.session[step_id] = DONE if code == 0 else FAILED
                if code != 0 and not self.failures:
                    self.failures.extend(self._fallback_failures())
                self.log.append(f"[{step_id}] exit {code}")
                self.ws = self.observe()
        if len(self.log) > LOG_TAIL:
            del self.log[: len(self.log) - LOG_TAIL]
        if len(self.failures) > FAILURE_TAIL:
            del self.failures[: len(self.failures) - FAILURE_TAIL]

    def _fallback_failures(self) -> list:
        """マーカーを持たないステップが失敗したときに見せる末尾。

        make autoware-build や docker compose は setup_check.sh の ❌ を出さない
        ので、そのままでは一番長く走るステップで failures 領域が空になる。
        終了コードだけが根拠なので、そのステップの出力の末尾を拾う。
        """
        produced = [line for line in self.log[self._log_mark :] if line.strip()]
        return produced[-FALLBACK_LINES:]

    # --- 描画 ---------------------------------------------------------------

    def draw(self) -> None:
        """1 画面 = header 1 行 + ステップのグリッド + failures + log。

        failures は log とは別領域に固定する。log は tail しか映らないので、
        同じ流れに置くと失敗が押し出されて読めなくなる。
        """
        self.screen.erase()
        lines, cols = self.screen.getmaxyx()
        width = max(1, cols - 1)

        hints = "↑↓ enter q"
        title = "vehicle console"
        pad = max(1, width - len(title) - len(hints))
        self.screen.addnstr(0, 0, f"{title}{' ' * pad}{hints}", width, curses.A_BOLD)

        row = self._draw_steps(1, lines, width)

        # failures は必要な分だけ。残りの 2/3 までに抑えて log を潰さない。
        # log より失敗のほうが読まれるべきなので log に多くは残さない。
        budget = max(0, lines - row)
        fail_lines = self._wrapped(self.failures, width, budget)
        fail_rows = min(len(fail_lines) + 1, budget * 2 // 3) if fail_lines else 0
        if fail_rows > 1:
            row = self._draw_region(
                row, f"failures ({len(self.failures)})", fail_lines, fail_rows, width
            )

        log_rows = lines - row
        if log_rows > 0:
            self._draw_region(
                row, "log", self._wrapped(self.log, width, log_rows), log_rows, width
            )

        self.screen.noutrefresh()
        curses.doupdate()

    def _draw_steps(self, top: int, lines: int, width: int) -> int:
        """ステップを縦 1 列に並べ、次に使える行番号を返す。"""
        used = 0
        for idx, step in enumerate(STEPS):
            y = top + idx
            if y >= lines:
                break
            attr = curses.A_REVERSE if idx == self.cursor else curses.A_NORMAL
            self.screen.addnstr(y, 0, self._cell(idx, step), width, attr)
            used = idx + 1
        return top + used

    def _cell(self, idx: int, step) -> str:
        status = step_status(step.step_id, self.ws, self.session)
        mark = _MARK[status]
        if status == PENDING and has_unmet_requirement(
            step.step_id, self.ws, self.session
        ):
            # 前提未達。実行は妨げない（前提は助言）ので印だけ変える。
            mark = _MARK_UNMET
        return f"{idx + 1} {mark} {step.title}{self._detail(step)}"

    def _detail(self, step) -> str:
        """セルの右に足す情報。桁を食わないものだけ。

        前提未達は _cell のマークで示すので、ここには出さない。
        """
        if step.step_id in (STEP_UP, STEP_TEARDOWN):
            return " " + service_badge(self.ws.services_running)
        return ""

    def _draw_region(
        self, top: int, label: str, wrapped: list, rows: int, width: int
    ) -> int:
        """区切り 1 行 + 折り返し済み行の末尾を描き、次の行番号を返す。"""
        sep = f"-- {label} "
        self.screen.addnstr(
            top, 0, sep + "-" * max(0, width - len(sep)), width, curses.A_DIM
        )
        body = rows - 1
        for offset, line in enumerate(wrapped[-body:] if body > 0 else []):
            self.screen.addnstr(top + 1 + offset, 0, line, width)
        return top + rows

    @staticmethod
    def _wrapped(source: list, width: int, rows: int) -> list:
        """末尾 rows 行ぶんだけ折り返す。

        折り返しは行を増やすだけで減らさないので、生の行を rows 本取れば
        表示に必要な折り返し後の行は必ず足りる。全部を折り返すと log が
        LOG_TAIL まで育ったあと毎フレーム 2000 行を捨てるために折り返す
        ことになり、実測で 1 回 13.6ms・8Hz で CPU コアの約 11% を焼いた。
        """
        out = []
        for line in source[-rows:] if rows > 0 else []:
            out.extend(wrap_line(line, width))
        return out

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
            if not self.busy:
                step = STEPS[self.cursor]
                if is_runnable(step.step_id, self.ws, self.session):
                    self.run_step(step.step_id)
        return True


def _loop(screen) -> int:
    curses.curs_set(0)
    screen.nodelay(True)
    console = Console(screen)
    console.draw()  # docker を待たずにまず画面を出す
    console.ws = console.observe()
    # preflight runs on open: a CAN or GNSS fault has to surface before a build.
    console.run_step(STEP_PREFLIGHT)
    while True:
        console.drain()
        console.refresh_if_stale()
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
