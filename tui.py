#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
import shlex
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


def require_textual() -> None:
    try:
        import textual  # noqa: F401
    except ModuleNotFoundError:
        print(
            "[tui][ERROR] textual is not installed.\n"
            "Install it with:\n"
            "  python3 -m pip install textual\n",
            file=sys.stderr,
        )
        raise SystemExit(1)


@dataclass(frozen=True)
class Task:
    key: str
    category: str
    label: str
    title: str
    description: str
    command: tuple[str, ...]
    extra_hint: str = ""
    interactive: bool = False
    requires_extra: bool = False
    required_extra_prefix: str = ""


TASKS: tuple[Task, ...] = (
    Task("setup-doctor", "setup.bash", "doctor (環境診断)", "setup.bash doctor / 環境診断", "Check host tools, Docker, repository files, AWSIM assets, GPU, and X11 settings.", ("./setup.bash", "doctor")),
    Task("setup-bootstrap", "setup.bash", "bootstrap (初期構築)", "setup.bash bootstrap / 初期構築", "Run the interactive bootstrap flow for a fresh host.", ("./setup.bash", "bootstrap"), "--yes / --skip-pull-image / --skip-awsim / --skip-build / --skip-make", True),
    Task("setup-env", "setup.bash", "env (.env作成)", "setup.bash env / .env作成", "Create .env from .env.example.", ("./setup.bash", "env"), interactive=True),
    Task("setup-pull-image", "setup.bash", "pull image (イメージ取得)", "setup.bash pull image / イメージ取得", "Pull the Autoware base image.", ("./setup.bash", "pull", "image"), "--image IMAGE"),
    Task("setup-download-awsim", "setup.bash", "download awsim (AWSIM取得)", "setup.bash download awsim / AWSIM取得", "Download and extract AWSIM assets.", ("./setup.bash", "download", "awsim"), "--url URL / --force / --keep-zip"),
    Task("setup-network-if-add", "setup.bash", "network-if add (通信IF追加)", "setup.bash network-if / 通信IF追加", "Add CycloneDDS network interface entries.", ("./setup.bash", "network-if"), "interface name, e.g. eth0", True, True),
    Task("setup-network-if-clear", "setup.bash", "network-if clear (通信IFクリア)", "setup.bash network-if / 通信IFクリア", "Remove all ai-challenge-added CycloneDDS network interface entries.", ("./setup.bash", "network-if"), interactive=True),
    Task("setup-test", "setup.bash", "test (一時環境構築)", "setup.bash test / 一時環境構築", "Bootstrap into a temporary directory for testing.", ("./setup.bash", "test"), "branch name / bootstrap options", True),
    Task("make-autoware-build", "Makefile: Build", "autoware-build", "make autoware-build / Autowareビルド", "Build the Autoware overlay using the root Makefile.", ("make", "autoware-build")),
    Task("make-autoware-vehicle", "Makefile: Build", "autoware-vehicle", "make autoware-vehicle / 実車Autoware", "Start Autoware in vehicle mode.", ("make", "autoware-vehicle")),
    Task("make-autoware-simulator", "Makefile: Build", "autoware-simulator", "make autoware-simulator / AWSIM Autoware", "Start Autoware in AWSIM mode.", ("make", "autoware-simulator")),
    Task("make-dev", "Makefile: Simulator", "dev (AWSIM+Autoware)", "make dev / 開発開始", "Launch AWSIM in dev mode and Autoware connected to it.", ("make", "dev")),
    Task("make-dev2", "Makefile: Simulator", "dev2 (2台)", "make dev2 / 2台開発", "Launch a 2-vehicle development simulation.", ("make", "dev2")),
    Task("make-dev3", "Makefile: Simulator", "dev3 (3台)", "make dev3 / 3台開発", "Launch a 3-vehicle development simulation.", ("make", "dev3")),
    Task("make-dev4", "Makefile: Simulator", "dev4 (4台)", "make dev4 / 4台開発", "Launch a 4-vehicle development simulation.", ("make", "dev4")),
    Task("make-gate1", "Makefile: Simulator", "gate1", "make gate1 / 安全ゲート1", "Start safety gate simulation 1.", ("make", "gate1")),
    Task("make-gate2", "Makefile: Simulator", "gate2", "make gate2 / 安全ゲート2", "Start safety gate simulation 2.", ("make", "gate2")),
    Task("make-gate3", "Makefile: Simulator", "gate3", "make gate3 / 安全ゲート3", "Start safety gate simulation 3.", ("make", "gate3")),
    Task("make-eval", "Makefile: Simulator", "eval (評価)", "make eval / 評価", "Start the evaluation simulation.", ("make", "eval")),
    Task("make-simulator", "Makefile: Simulator", "simulator (SIM_MODE指定)", "make simulator / SIM_MODE指定", "Start only the simulator service with an explicit SIM_MODE.", ("make", "simulator"), "SIM_MODE=dev / SIM_MODE=gate1", required_extra_prefix="SIM_MODE="),
    Task("make-driver", "Makefile: Vehicle/Remote", "driver", "make driver / 実車driver", "Start the racing kart driver service.", ("make", "driver")),
    Task("make-zenoh", "Makefile: Vehicle/Remote", "zenoh", "make zenoh / zenoh", "Start zenoh.", ("make", "zenoh")),
    Task("make-rviz2", "Makefile: Vehicle/Remote", "rviz2", "make rviz2 / 遠隔rviz2", "Restart and launch rviz2.", ("make", "rviz2")),
    Task("make-autoware-driver-zenoh", "Makefile: Vehicle/Remote", "autoware-driver-zenoh", "make autoware-driver-zenoh / 実車一式", "Start driver, Autoware, then zenoh.", ("make", "autoware-driver-zenoh")),
    Task("make-autoware-driver-zenoh-rosbag", "Makefile: Vehicle/Remote", "autoware-driver-zenoh-rosbag", "make autoware-driver-zenoh-rosbag / rosbag付き実車一式", "Start driver, Autoware, rosbag, then zenoh.", ("make", "autoware-driver-zenoh-rosbag")),
    Task("make-initialpose", "Makefile: Requests", "autoware-request-initialpose", "make autoware-request-initialpose / 初期位置要求", "Send Autoware initial pose request.", ("make", "autoware-request-initialpose")),
    Task("make-control", "Makefile: Requests", "autoware-request-control", "make autoware-request-control / 制御要求", "Send Autoware control mode request.", ("make", "autoware-request-control")),
    Task("make-awsim-start", "Makefile: Requests", "awsim-request-start", "make awsim-request-start / AWSIM開始", "Publish AWSIM start request.", ("make", "awsim-request-start")),
    Task("make-awsim-reset", "Makefile: Requests", "awsim-request-reset", "make awsim-request-reset / AWSIMリセット", "Publish AWSIM reset request.", ("make", "awsim-request-reset")),
    Task("make-ps", "Makefile: Ops", "ps (状態確認)", "make ps / 状態確認", "Show Docker Compose status for the default and numbered projects.", ("make", "ps")),
    Task("make-down", "Makefile: Ops", "down (停止)", "make down / 停止", "Stop compose projects and remove orphans.", ("make", "down")),
    Task("make-down-all", "Makefile: Ops", "down_all (全コンテナ削除)", "make down_all / 全コンテナ削除", "Force remove all Docker containers. This may ask for sudo password.", ("make", "down_all"), interactive=True),
    Task("make-autoware-bash", "Makefile: Ops", "autoware-bash", "make autoware-bash / Autowareシェル", "Open bash in the Autoware container.", ("make", "autoware-bash"), "VEHICLE_NUM=2", True),
    Task("make-download", "Makefile: Data", "download (提出データ取得)", "make download / 提出データ取得", "Download submission data. Add make variables in Extra args when needed.", ("make", "download"), "SUBMISSION_ID=123 / USER_ID=456", True),
)


def task_by_key(key: str) -> Task:
    for task in TASKS:
        if task.key == key:
            return task
    raise KeyError(key)


def command_preview(task: Task, extra: str = "") -> str:
    parts = list(task.command)
    if extra.strip():
        parts.extend(shlex.split(extra))
    return " ".join(shlex.quote(part) for part in parts)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Textual TUI for setup.bash and Makefile commands.")
    parser.add_argument(
        "--theme",
        choices=("dark", "light"),
        default=os.environ.get("AIC_TUI_THEME", "dark"),
        help="Initial theme. Default: AIC_TUI_THEME or dark.",
    )
    return parser.parse_args()


if any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    parse_args()
    raise SystemExit(0)


require_textual()

from textual import on  # noqa: E402
from textual.app import App, ComposeResult  # noqa: E402
from textual.binding import Binding  # noqa: E402
from textual.containers import Container, Horizontal, Vertical  # noqa: E402
from textual.screen import ModalScreen  # noqa: E402
from textual.widgets import Button, Footer, Header, Input, Label, RichLog, Static, Tree  # noqa: E402


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("enter", "confirm", "Run", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
    }

    #confirm-dialog {
        width: 78;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }

    #confirm-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }

    #confirm-command {
        margin: 1 0;
        padding: 1;
        border: round $panel;
        background: $boost;
    }

    #confirm-actions {
        align-horizontal: right;
        height: auto;
        margin-top: 1;
    }
    """

    def __init__(self, preview: str, interactive: bool) -> None:
        super().__init__()
        self.preview = preview
        self.interactive = interactive

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static("Run command? / 実行しますか？", id="confirm-title")
            yield Static(self.preview, id="confirm-command")
            if self.interactive:
                yield Static("This task may ask questions in the terminal. / 通常端末で対話入力が必要な場合があります。")
            else:
                yield Static("Click Run to start. / Runで開始します。")
            with Horizontal(id="confirm-actions"):
                yield Button("Cancel / 戻る", id="confirm-cancel")
                yield Button("Run / 実行", id="confirm-run", variant="primary")

    @on(Button.Pressed, "#confirm-run")
    def run(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_confirm()

    @on(Button.Pressed, "#confirm-cancel")
    def cancel(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_cancel()

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class RacingKartTUI(App[None]):
    CSS = """
    Screen {
        background: $background;
    }

    #body {
        height: 100%;
    }

    #left {
        width: 34%;
        min-width: 30;
        border-right: solid $panel;
        padding: 0 1;
        background: $surface;
    }

    #right {
        width: 1fr;
        padding: 0 1;
    }

    .panel-title {
        height: 1;
        text-style: bold;
        color: $accent;
    }

    #task-tree {
        height: 1fr;
        border: round $panel;
        background: $surface;
    }

    #top {
        height: 58%;
    }

    #details {
        width: 34%;
        min-width: 24;
        border: round $panel;
        padding: 1;
        background: $surface;
    }

    #controls {
        width: 1fr;
        border: round $panel;
        padding: 1 2;
        background: $surface;
    }

    #task-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }

    #task-description {
        height: 1fr;
    }

    #command-preview {
        height: auto;
        margin: 1 0;
        padding: 1;
        border: round $panel;
        background: $boost;
    }

    #extra {
        margin-bottom: 1;
    }

    #action-bar {
        height: 3;
        border-top: solid $panel;
        padding-top: 1;
    }

    Button {
        margin-right: 1;
        min-width: 12;
    }

    #output {
        height: 1fr;
        border: round $panel;
        background: $surface;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "run_selected", "Run"),
        Binding("s", "stop_process", "Stop"),
        Binding("t", "toggle_theme", "Theme"),
        Binding("c", "clear_output", "Clear"),
    ]

    TITLE = "AI Challenge Racing Kart"

    def __init__(self, initial_theme: str) -> None:
        super().__init__()
        self._theme_name = initial_theme
        self.theme = self.textual_theme_name(initial_theme)
        self.selected = task_by_key("make-dev")
        self.repo_root = Path(__file__).resolve().parent
        self.process: asyncio.subprocess.Process | None = None
        self.task_busy = False
        self.output_history: list[str] = []

    @staticmethod
    def textual_theme_name(theme_name: str) -> str:
        return "textual-light" if theme_name == "light" else "textual-dark"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield Label("Tasks", classes="panel-title")
                yield self.build_tree()
            with Vertical(id="right"):
                with Horizontal(id="top"):
                    with Container(id="details"):
                        yield Label("Details", classes="panel-title")
                        yield Static("", id="details-text")
                    with Container(id="controls"):
                        yield Label("Task Controls", classes="panel-title")
                        yield Static("", id="task-title")
                        yield Static("", id="task-description")
                        yield Static("", id="command-preview")
                        yield Input(placeholder="Extra args: SUBMISSION_ID=123 / VEHICLE_NUM=2 / SIM_MODE=dev", id="extra")
                        with Horizontal(id="action-bar"):
                            yield Button("▶ Run / 実行", id="run", variant="primary")
                            yield Button("● Stop / 停止", id="stop", variant="error")
                            yield Button("View Logs / ログ表示", id="logs")
                            yield Button("Theme", id="theme")
                yield Label("Output", classes="panel-title")
                yield RichLog(id="output", wrap=True, highlight=True, markup=True)
        yield Footer()

    def build_tree(self) -> Tree[str]:
        tree: Tree[str] = Tree("Tasks", id="task-tree")
        tree.root.expand()
        groups: dict[str, object] = {}
        for task in TASKS:
            group_node = groups.get(task.category)
            if group_node is None:
                group_node = tree.root.add(task.category, expand=True)
                groups[task.category] = group_node
            group_node.add_leaf(task.label, data=task.key)
        return tree

    def on_mount(self) -> None:
        self.dark = self._theme_name != "light"
        self.theme = self.textual_theme_name(self._theme_name)
        self.refresh_task()

    def refresh_task(self) -> None:
        extra = self.query_one("#extra", Input).value
        preview = command_preview(self.selected, extra)
        self.query_one("#details-text", Static).update(self.details_text())
        self.query_one("#task-title", Static).update(self.selected.title)
        self.query_one("#task-description", Static).update(self.selected.description)
        self.query_one("#command-preview", Static).update(f"[b]Command[/b]\n{preview}")

    def details_text(self) -> str:
        lines = [
            "[b]Source[/b]",
            self.selected.command[0],
            "",
            "[b]Task[/b]",
            self.selected.label,
            "",
            "[b]Command[/b]",
            " ".join(self.selected.command),
        ]
        if self.selected.extra_hint:
            lines.extend(["", "[b]Extra[/b]", self.selected.extra_hint])
        return "\n".join(lines)

    @on(Tree.NodeSelected, "#task-tree")
    def select_task(self, event: Tree.NodeSelected[str]) -> None:
        if event.node.data is None:
            event.node.toggle()
            return
        self.selected = task_by_key(event.node.data)
        self.query_one("#extra", Input).value = ""
        self.refresh_task()

    @on(Input.Changed, "#extra")
    def extra_changed(self) -> None:
        self.refresh_task()

    @on(Button.Pressed, "#run")
    def run_button(self) -> None:
        self.action_run_selected()

    @on(Button.Pressed, "#stop")
    async def stop_button(self) -> None:
        await self.action_stop_process()

    @on(Button.Pressed, "#logs")
    def logs_button(self) -> None:
        self.query_one("#output", RichLog).focus()

    @on(Button.Pressed, "#theme")
    def theme_button(self) -> None:
        self.action_toggle_theme()

    def action_run_selected(self) -> None:
        if self.task_busy or (self.process is not None and self.process.returncode is None):
            self.notify("A task is already running.", severity="warning")
            return

        extra = self.query_one("#extra", Input).value.strip()
        validation_error = self.validate_extra(self.selected, extra)
        if validation_error:
            self.notify(validation_error, severity="error")
            return
        try:
            preview = command_preview(self.selected, extra)
        except ValueError as exc:
            self.notify(f"Invalid extra args: {exc}", severity="error")
            return

        args = list(self.selected.command)
        if extra:
            try:
                args.extend(shlex.split(extra))
            except ValueError as exc:
                self.notify(f"Invalid extra args: {exc}", severity="error")
                return
        task = self.selected
        self.task_busy = True

        def confirmed(should_run: bool | None) -> None:
            if not should_run:
                self.task_busy = False
                return
            self.run_worker(
                self.run_confirmed_task(task, args, preview),
                group="task-runner",
                exclusive=True,
                exit_on_error=False,
            )

        try:
            self.push_screen(ConfirmScreen(preview, task.interactive), callback=confirmed)
        except Exception:
            self.task_busy = False
            raise

    def validate_extra(self, task: Task, extra: str) -> str | None:
        if task.requires_extra and not extra:
            return f"{task.label} requires Extra args."
        if task.required_extra_prefix:
            try:
                parts = shlex.split(extra)
            except ValueError as exc:
                return f"Invalid extra args: {exc}"
            if not any(part.startswith(task.required_extra_prefix) for part in parts):
                return f"{task.label} requires {task.required_extra_prefix}..."
        return None

    async def run_confirmed_task(self, task: Task, args: list[str], preview: str) -> None:
        try:
            if task.interactive:
                await self.run_interactive_process(task, args, preview)
            else:
                await self.run_process(task, args, preview)
        except Exception as exc:
            self.write_output(f"[red]Unhandled task error: {type(exc).__name__}: {exc}[/red]")
            self.notify("Task crashed.", severity="error")
        finally:
            self.task_busy = False

    async def run_process(self, task: Task, args: list[str], preview: str) -> None:
        output = self.query_one("#output", RichLog)
        self.write_output(f"[bold cyan]Task: {task.title}[/bold cyan]")
        self.write_output(f"[bold]$ {preview}[/bold]")
        try:
            self.process = await asyncio.create_subprocess_exec(
                *args,
                cwd=self.repo_root,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            self.write_output(f"[red]Command not found: {exc.filename}[/red]")
            self.process = None
            return

        try:
            assert self.process.stdout is not None
            async for raw_line in self.process.stdout:
                self.write_output(raw_line.decode(errors="replace").rstrip("\n"))

            rc = await self.process.wait()
            self.write_output(f"[bold]Exit code: {rc}[/bold]")
            if rc == 0:
                self.notify("Task completed.")
            else:
                self.notify(f"Task failed: {rc}", severity="error")
        finally:
            self.process = None
            output.focus()

    async def run_interactive_process(self, task: Task, args: list[str], preview: str) -> None:
        self.write_output(f"[bold cyan]Task: {task.title}[/bold cyan]")
        self.write_output(f"[bold]$ {preview}[/bold]")
        self.write_output("[yellow]Interactive task opened in the terminal. Return here after it exits.[/yellow]")
        try:
            with self.suspend():
                print(f"$ {preview}")
                rc = subprocess.run(args, cwd=self.repo_root).returncode
                print("")
                input("Press Enter to return to the TUI...")
        except Exception as exc:
            self.write_output(f"[red]Interactive run failed: {exc}[/red]")
            self.notify("Interactive task failed to start.", severity="error")
            return
        self.write_output(f"[bold]Exit code: {rc}[/bold]")
        if rc == 0:
            self.notify("Task completed.")
        else:
            self.notify(f"Task failed: {rc}", severity="error")

    def write_output(self, message: str) -> None:
        self.output_history.append(message)
        self.query_one("#output", RichLog).write(message)

    async def action_stop_process(self) -> None:
        if self.process is None or self.process.returncode is not None:
            if self.task_busy:
                self.notify("Interactive task is running in the terminal.", severity="warning")
                return
            self.notify("No running task.", severity="warning")
            return
        await self.stop_running_process()

    async def stop_running_process(self) -> None:
        if self.process is None or self.process.returncode is not None:
            return
        self.write_output("[yellow]Stopping task...[/yellow]")
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(self.process.wait(), timeout=5)
        except asyncio.TimeoutError:
            os.killpg(self.process.pid, signal.SIGKILL)
            await self.process.wait()

    async def action_quit(self) -> None:
        if self.process is not None and self.process.returncode is None:
            await self.stop_running_process()
        self.exit()

    def on_unmount(self) -> None:
        if self.process is None or self.process.returncode is not None:
            return
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return

    def action_toggle_theme(self) -> None:
        self._theme_name = "light" if self._theme_name == "dark" else "dark"
        self.dark = self._theme_name != "light"
        self.theme = self.textual_theme_name(self._theme_name)
        self.notify(f"Theme: {self._theme_name}")

    def action_clear_output(self) -> None:
        self.output_history.clear()
        self.query_one("#output", RichLog).clear()


def main() -> int:
    args = parse_args()
    RacingKartTUI(initial_theme=args.theme).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
