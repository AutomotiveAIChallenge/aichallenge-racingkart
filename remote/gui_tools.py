#!/usr/bin/env python3
from __future__ import annotations

import os
import queue
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

import tkinter.font as tkfont

import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

ROOT_DIR = Path(__file__).resolve().parent
REMOTE_DIR = ROOT_DIR

DEFAULT_VEHICLE_ID = "A2"
# connect_zenoh.bash が受け付ける Vehicle ID の形式 (A1〜A8、または test- 始まり)。
# ハードコードの一覧を持つとスクリプト側の変更に追随できなくなるため、正規表現のみで軽く検証する。
VEHICLE_ID_PATTERN = r"^(A[1-8]|test-.+)$"

# --- ログ処理まわりの定数 ---
# chatty な子プロセス (zenoh-bridge, rviz, joy など) がログを高速に吐いても
# Tk のメインループを飢餓状態にしないための上限・予算値。
MAX_LOG_LINES = 2000  # 各ログウィジェットが保持する最大行数
LOG_QUEUE_MAXSIZE = 10000  # ログキューの上限。超えたら行を捨てる (producer は絶対にブロックしない)
POLL_BUDGET_SECONDS = 0.02  # 1回の _poll_log_queue にかける壁時計予算 (約20ms)
POLL_MAX_ITEMS = 2000  # 1回の _poll_log_queue で処理する最大件数
POLL_INTERVAL_IDLE_MS = 100  # キューが空になった後の再スケジュール間隔
POLL_INTERVAL_BUSY_MS = 10  # キューにまだ残っている場合の再スケジュール間隔

# --- プロセス停止まわりの定数 ---
STOP_ESCALATE_INTERVAL_MS = 200  # SIGTERM 送信後、生存確認をポーリングする間隔
STOP_ESCALATE_TIMEOUT_MS = 3000  # この時間を過ぎても生きていたら SIGKILL に昇格


# --- Devias Material Kit Pro: Chateau Green palette (approx) ---
# These values are offline approximations of the "Chateau Green" theme.
# Adjust here if you have exact tokens from the design kit.
PALETTE = {
    "bg": "#F4F6F8",          # app background
    "surface": "#FFFFFF",      # cards / frames
    "text": "#1C252E",        # primary text (slightly darker)
    "text_muted": "#637381",   # secondary text
    "border": "#DFE3E8",
    # Chateau Green core
    "primary": "#00A76F",        # Chateau Green (main)
    "primary_hover": "#008E61",  # hover (darker)
    "primary_active": "#007053", # active (even darker)
    "primary_soft": "#E6F4EF",   # subtle surface tint
    # Danger accents
    "danger": "#FF5630",
    "danger_hover": "#E04E2C",
    "danger_active": "#C24427",
}


def apply_devias_green_theme(root: tk.Tk) -> tuple[ttk.Style, tkfont.Font]:
    """Apply a Devias-like green theme using ttk.Style.

    This function sets the base theme to 'clam' for consistent styling and
    customizes widgets' colors, fonts, and padding. Buttons receive primary
    (green), outline, and danger variants. Returns the style and the fixed-width
    font so callers can apply it to log widgets (ScrolledText 等) directly.
    """
    style = ttk.Style(root)
    # Ensure a predictable style base
    try:
        style.theme_use("clam")
    except Exception:
        pass

    # Root background
    root.configure(bg=PALETTE["bg"])

    # Base fonts
    base_font = tkfont.nametofont("TkDefaultFont")
    base_font.configure(size=10)
    try:
        heading_font = tkfont.nametofont("TkHeadingFont")
        heading_font.configure(size=11, weight="bold")
    except Exception:
        heading_font = tkfont.Font(family=base_font.cget("family"), size=11, weight="bold")
    try:
        fixed_font = tkfont.nametofont("TkFixedFont")
        fixed_font.configure(size=10)
    except Exception:
        fixed_font = tkfont.Font(family="Monospace", size=10)

    # Frames and labels
    style.configure(
        "TFrame",
        background=PALETTE["bg"],
    )
    style.configure(
        "Card.TFrame",
        background=PALETTE["surface"],
        bordercolor=PALETTE["border"],
        relief="flat",
    )
    style.configure(
        "TLabel",
        background=PALETTE["bg"],
        foreground=PALETTE["text"],
        font=base_font,
    )
    style.configure(
        "Muted.TLabel",
        background=PALETTE["bg"],
        foreground=PALETTE["text_muted"],
        font=base_font,
    )
    style.configure(
        "Header.TLabel",
        background=PALETTE["bg"],
        foreground=PALETTE["text"],
        font=heading_font,
    )
    style.configure(
        "TLabelframe",
        background=PALETTE["surface"],
        foreground=PALETTE["text"],
        bordercolor=PALETTE["border"],
        relief="groove",
    )
    style.configure(
        "TLabelframe.Label",
        background=PALETTE["surface"],
        foreground=PALETTE["text"],
        font=heading_font,
    )

    # Entry fields
    style.configure(
        "TEntry",
        fieldbackground=PALETTE["surface"],
        background=PALETTE["surface"],
        foreground=PALETTE["text"],
        bordercolor=PALETTE["border"],
        lightcolor=PALETTE["primary"],
        darkcolor=PALETTE["border"],
        relief="flat",
        padding=6,
    )

    # Buttons - Primary (solid green)
    style.configure(
        "DeviasPrimary.TButton",
        background=PALETTE["primary"],
        foreground="#FFFFFF",
        bordercolor=PALETTE["primary"],
        focusthickness=0,
        padding=(10, 6),
        relief="flat",
    )
    style.map(
        "DeviasPrimary.TButton",
        background=[
            ("active", PALETTE["primary_hover"]),
            ("pressed", PALETTE["primary_active"]),
            ("disabled", PALETTE["border"]),
        ],
        foreground=[("disabled", "#FFFFFF")],
        bordercolor=[
            ("active", PALETTE["primary_hover"]),
            ("pressed", PALETTE["primary_active"]),
            ("disabled", PALETTE["border"]),
        ],
    )

    # Buttons - Outline (green outline on white)
    style.configure(
        "DeviasOutline.TButton",
        background=PALETTE["surface"],
        foreground=PALETTE["primary"],
        bordercolor=PALETTE["primary"],
        focusthickness=0,
        padding=(10, 6),
        relief="solid",
        borderwidth=1,
    )
    style.configure(
        "DeviasOutlineTall.TButton",
        background=PALETTE["surface"],
        foreground=PALETTE["primary"],
        bordercolor=PALETTE["primary"],
        focusthickness=0,
        padding=(10, 48),
        relief="solid",
        borderwidth=1,
    )
    style.map(
        "DeviasOutline.TButton",
        background=[
            ("active", PALETTE["primary_soft"]),
            ("pressed", PALETTE["primary_soft"]),
        ],
        bordercolor=[
            ("active", PALETTE["primary"]),
            ("pressed", PALETTE["primary"]),
        ],
        foreground=[
            ("disabled", PALETTE["text_muted"]),
        ],
    )
    style.map(
        "DeviasOutlineTall.TButton",
        background=[
            ("active", PALETTE["primary_soft"]),
            ("pressed", PALETTE["primary_soft"]),
        ],
        bordercolor=[
            ("active", PALETTE["primary"]),
            ("pressed", PALETTE["primary"]),
        ],
        foreground=[
            ("disabled", PALETTE["text_muted"]),
        ],
    )

    # Buttons - Danger (stop)
    style.configure(
        "DeviasDanger.TButton",
        background=PALETTE["danger"],
        foreground="#FFFFFF",
        bordercolor=PALETTE["danger"],
        focusthickness=0,
        padding=(10, 6),
        relief="flat",
    )
    style.map(
        "DeviasDanger.TButton",
        background=[
            ("active", PALETTE["danger_hover"]),
            ("pressed", PALETTE["danger_active"]),
            ("disabled", PALETTE["border"]),
        ],
        foreground=[("disabled", "#FFFFFF")],
        bordercolor=[
            ("active", PALETTE["danger_hover"]),
            ("pressed", PALETTE["danger_active"]),
            ("disabled", PALETTE["border"]),
        ],
    )

    # Separator
    style.configure("TSeparator", background=PALETTE["border"])

    return style, fixed_font

@dataclass
class CommandSpec:
    label: str
    command: str | None = None
    log_key: str | None = None
    requires_vehicle: bool = False
    stop_before: bool = False
    note: str | None = None
    kind: str = "command"  # command, stop, stop_all

    def render(self, vehicle_id: str) -> str:
        if self.kind != "command":
            return ""
        assert self.command is not None
        return self.command.format(vehicle_id=vehicle_id)

COMMANDS: List[CommandSpec] = [
    CommandSpec(
        label="Start Zenoh",
        command="./connect_zenoh.bash {vehicle_id}",
        log_key="zenoh",
        requires_vehicle=True,
        note="指定した Vehicle ID の zenoh-bridge へ接続します。",
    ),
    CommandSpec(
        label="Stop Zenoh",
        log_key="zenoh",
        kind="stop",
        requires_vehicle=True,
        note="GUI で起動した Zenoh プロセスを終了します (Ctrl+C 相当)。",
    ),
    CommandSpec(
        label="Restart Zenoh",
        command="./connect_zenoh.bash {vehicle_id}",
        log_key="zenoh",
        requires_vehicle=True,
        stop_before=True,
        note="既存プロセス停止後に zenoh bridge を再接続します。",
    ),
    CommandSpec(
        label="Restart Zenoh and RViz",
        command="./restart.bash {vehicle_id}",
        log_key="zenoh",
        requires_vehicle=True,
        stop_before=True,
        note="RViz と Zenoh bridge を再起動します。",
    ),
    CommandSpec(
        label="Start RViz",
        command="./rviz.bash",
        log_key="rviz",
        note="RViz 用コンテナを起動します。",
    ),
    CommandSpec(
        label="Stop RViz",
        command="./rviz.bash down",
        log_key="rviz",
        stop_before=True,
        note="RViz コンテナを停止します。",
    ),
    CommandSpec(
        label="Restart RViz",
        command="./rviz.bash restart",
        log_key="rviz",
        note="RViz コンテナを再起動します。",
    ),
    CommandSpec(
        label="Start Joy",
        command="./joy.bash",
        log_key="joy",
        note="ゲームパッドノードを起動します。",
    ),
    CommandSpec(
        label="Stop Joy",
        log_key="joy",
        kind="stop",
        note="GUI で起動した joy プロセスを終了します (Ctrl+C 相当)。",
    ),
    CommandSpec(
        label="Restart Joy",
        command="./joy.bash",
        log_key="joy",
        stop_before=True,
        note="joy ノードを再起動します。",
    ),
]

SPEC_MAP: Dict[str, CommandSpec] = {spec.label: spec for spec in COMMANDS}

COLUMN_LAYOUT = [
    ("Zenoh", ["Start Zenoh", "Stop Zenoh", "Restart Zenoh"]),
    ("RViz", ["Start RViz", "Stop RViz", "Restart RViz"]),
    ("Joy", ["Start Joy", "Stop Joy", "Restart Joy"]),
    ("Zenoh and RViz", ["Restart Zenoh and RViz"]),
]

LOG_AREAS = {
    "zenoh": "Zenoh Log",
    "rviz": "RViz Log",
    "joy": "Joy Log",
}

@dataclass
class _ProcessEntry:
    """起動世代 (token) 付きで Popen を保持する。

    Restart などで古いリーダースレッドが新しいプロセス登録後に終了しても、
    token が一致しない限り self.processes から新しいプロセスを消さないようにする (RC2)。
    """

    token: int
    process: subprocess.Popen[str]


class RemoteGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Remote Vehicle Helper")

        # Apply Devias-inspired theme before building UI
        self.style, self.fixed_font = apply_devias_green_theme(self.root)

        if not REMOTE_DIR.exists():
            messagebox.showerror(
                "Configuration error",
                f"Remote directory not found: {REMOTE_DIR}",
            )
            raise SystemExit(1)

        self.vehicle_id_var = tk.StringVar(value=DEFAULT_VEHICLE_ID)
        # SSH user input was removed from the UI entirely; there is nothing to keep here anymore.

        self.processes: Dict[str, _ProcessEntry] = {}
        self.process_threads: Dict[str, threading.Thread] = {}
        self.log_queue: "queue.Queue[tuple[str, str]]" = queue.Queue(maxsize=LOG_QUEUE_MAXSIZE)
        self._log_dropped: Dict[str, int] = {}
        self._next_token = 0
        self._closing = False
        # SIGINT/SIGTERM ハンドラが直接 Tk API を叩かず、ここにフラグだけ立てる (RC10)。
        # 実際の後始末は root.after で定期実行される _poll_log_queue 側から行う。
        self._pending_shutdown = False
        # Restart 系 (stop_before=True) で、旧プロセスの終了待ちの間 True になる (RC12)。
        # このフラグが立っている log_key の Start/Restart ボタンは _refresh_button_states で無効化する。
        self._pending_launch: Dict[str, bool] = {}
        # _poll_stop_escalation で SIGTERM/SIGKILL の生存確認をポーリング中のプロセス。
        # _on_close / _terminate_all がウィンドウを閉じる際、self.processes から既に
        # 取り除かれてしまった (停止処理の途中の) プロセスも確実に畳めるようにするための保険 (RC12)。
        self._escalating: Dict[str, subprocess.Popen[str]] = {}
        self.buttons: Dict[str, ttk.Button] = {}

        self._build_ui()
        self._refresh_button_states()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._poll_log_queue)

    def _build_ui(self) -> None:
        # Top banner (subtle spacing)
        container = ttk.Frame(self.root)
        container.pack(fill=tk.BOTH, expand=True)

        top_frame = ttk.Frame(container, style="TFrame")
        top_frame.pack(fill=tk.X, padx=16, pady=(16, 8))

        ttk.Label(top_frame, text="Vehicle ID:", style="TLabel").pack(side=tk.LEFT)
        vehicle_entry = ttk.Entry(top_frame, textvariable=self.vehicle_id_var, width=14)
        vehicle_entry.pack(side=tk.LEFT, padx=(6, 16))

        # SSH User input removed per request.

        preview_frame = ttk.Frame(container, style="Card.TFrame")
        preview_frame.pack(fill=tk.X, padx=16, pady=(0, 8))

        self.directory_label = ttk.Label(preview_frame, text="Directory: -", style="TLabel")
        self.directory_label.pack(anchor=tk.W)
        self.command_label = ttk.Label(preview_frame, text="Command: -", style="TLabel")
        self.command_label.pack(anchor=tk.W)
        self.note_label = ttk.Label(preview_frame, text="Note: -", style="Muted.TLabel")
        self.note_label.pack(anchor=tk.W)

        button_container = ttk.Frame(container)
        button_container.pack(fill=tk.X, padx=16, pady=8)

        for col_idx, (label, buttons) in enumerate(COLUMN_LAYOUT):
            col_frame = ttk.Frame(button_container)
            col_frame.grid(row=0, column=col_idx, padx=8, pady=6, sticky=tk.N)

            ttk.Label(col_frame, text=label, style="Header.TLabel").pack(pady=(0, 8))

            if label == "Zenoh and RViz":
                for btn_label in buttons:
                    spec = SPEC_MAP[btn_label]
                    btn_style = "DeviasOutlineTall.TButton"

                    button = ttk.Button(
                        col_frame,
                        text=spec.label,
                        command=lambda s=spec: self._handle_command(s),
                        width=20,
                        style=btn_style,
                    )
                    button.pack(pady=4, fill=tk.X)
                    self.buttons[spec.label] = button
            else:
                for btn_label in buttons:
                    spec = SPEC_MAP[btn_label]
                    btn_style = "DeviasPrimary.TButton"
                    if spec.kind == "stop" or spec.label.lower().startswith("stop"):
                        btn_style = "DeviasDanger.TButton"
                    elif spec.label.lower().startswith("restart"):
                        btn_style = "DeviasOutline.TButton"

                    button = ttk.Button(
                        col_frame,
                        text=spec.label,
                        command=lambda s=spec: self._handle_command(s),
                        width=18,
                        style=btn_style,
                    )
                    button.pack(pady=4, fill=tk.X)
                    self.buttons[spec.label] = button
        
        for i in range(len(COLUMN_LAYOUT)):
            button_container.columnconfigure(i, weight=1)

        logs_frame = ttk.Frame(container)
        logs_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(4, 16))

        self.log_widgets: Dict[str, ScrolledText] = {}
        for idx, (key, title) in enumerate(LOG_AREAS.items()):
            frame = ttk.LabelFrame(logs_frame, text=title, style="TLabelframe")
            frame.grid(row=0, column=idx, padx=4, pady=4, sticky=tk.NSEW)
            logs_frame.columnconfigure(idx, weight=1)

            text_widget = ScrolledText(
                frame,
                height=18,
                width=40,
                state=tk.DISABLED,
                background=PALETTE["surface"],
                foreground=PALETTE["text"],
                insertbackground=PALETTE["text"],
                borderwidth=1,
                relief="solid",
                font=self.fixed_font,
            )
            text_widget.pack(fill=tk.BOTH, expand=True)
            self.log_widgets[key] = text_widget

        logs_frame.rowconfigure(0, weight=1)

    def _handle_stop_single(self, log_key: str) -> None:
        if not self._process_running(log_key):
            self._append_log(log_key, '[no running process]\n')
            return
        self._append_log(log_key, '[stop requested]\n')
        self._stop_process(log_key)

    def _handle_command(self, spec: CommandSpec) -> None:
        vehicle_id = self.vehicle_id_var.get().strip()

        if spec.requires_vehicle:
            if not vehicle_id:
                messagebox.showwarning("入力不足", "Vehicle ID を指定してください。")
                return
            if not re.match(VEHICLE_ID_PATTERN, vehicle_id):
                messagebox.showwarning(
                    "Vehicle ID が不正です",
                    "Vehicle ID は A1〜A8、または test- から始まる文字列で指定してください。"
                    f"\n(入力値: {vehicle_id!r})",
                )
                return

        if spec.kind == "stop":
            if not spec.log_key:
                return
            self._update_preview(REMOTE_DIR, f"[Stop] {spec.log_key}", spec.note or "")
            self._handle_stop_single(spec.log_key)
            self._refresh_button_states()
            return

        command_text = spec.render(vehicle_id)
        working_dir = REMOTE_DIR
        note = spec.note or ""
        self._update_preview(working_dir, command_text, note)

        log_key = spec.log_key
        if log_key is None:
            return

        if spec.stop_before:
            # 旧プロセスの終了を待ってから新プロセスを起動する (RC12)。
            # メインスレッドはブロックしない: _stop_process は非ブロッキングで、
            # 実際の起動 (_launch) は終了確認後に _poll_stop_escalation からコールバックされる。
            self._pending_launch[log_key] = True
            self._append_log(log_key, "[restart: waiting for previous process to exit]\n")
            self._refresh_button_states()
            self._stop_process(
                log_key,
                on_terminated=lambda: self._launch(log_key, command_text, working_dir),
            )
            return

        if self._process_running(log_key):
            messagebox.showinfo(
                "Process running",
                f"{LOG_AREAS.get(log_key, log_key)} でコマンドが実行中です。先に停止してください。",
            )
            return

        self._launch(log_key, command_text, working_dir)

    def _launch(self, log_key: str, command_text: str, working_dir: Path) -> None:
        """実際に子プロセスを起動する。

        Restart 系 (stop_before=True) では、旧プロセスの終了を確認した後の
        コールバックとしてもここに来る (RC12)。ウィンドウを閉じた後 (`self._closing`)
        に呼ばれた場合は新プロセスを起動せずに no-op で抜ける。
        """
        self._pending_launch.pop(log_key, None)
        if self._closing:
            self._refresh_button_states()
            return

        if self._process_running(log_key):
            # 通常はボタンが無効化されているので起きないはずだが、念のための保険。
            messagebox.showinfo(
                "Process running",
                f"{LOG_AREAS.get(log_key, log_key)} でコマンドが実行中です。先に停止してください。",
            )
            self._refresh_button_states()
            return

        try:
            process = subprocess.Popen(
                ["bash", "-lc", command_text],
                cwd=str(working_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                # 起動するのは bash -> スクリプト -> ros2 run -> 実体 の多段で、
                # ros2 run は joy_node を別プロセスとして起こす。専用のプロセス
                # グループに入れておかないと、停止時に親だけが死んで実体が孤児
                # として残り (親が systemd に引き取られる)、GUI から止められなく
                # なる。同じ理由で scripts/run_remote.bash もグループで畳んでいる。
                start_new_session=True,
            )
        except FileNotFoundError:
            messagebox.showerror("Command error", "bash が見つかりませんでした。")
            self._refresh_button_states()
            return
        except Exception as exc:  # pragma: no cover - defensive
            messagebox.showerror("Command error", str(exc))
            self._refresh_button_states()
            return

        self._next_token += 1
        token = self._next_token
        thread = threading.Thread(
            target=self._stream_output,
            args=(log_key, process, token),
            daemon=True,
        )
        self.processes[log_key] = _ProcessEntry(token, process)
        self.process_threads[log_key] = thread
        thread.start()
        self._append_log(log_key, f"$ {command_text}\n")
        self._refresh_button_states()

    def _enqueue_log(self, log_key: str, line: str) -> None:
        """ログ1行 (または通知) をキューへ積む。キューが満杯でも絶対にブロックしない (RC1)。"""
        try:
            self.log_queue.put_nowait((log_key, line))
        except queue.Full:
            dropped = self._log_dropped.get(log_key, 0) + 1
            self._log_dropped[log_key] = dropped
            # 溜まり続けている間も定期的に状況を知らせる (キューが常に満杯でも通知が出るように)
            if dropped % 500 == 1:
                try:
                    self.log_queue.put_nowait((log_key, f"[{dropped} lines dropped]\n"))
                except queue.Full:
                    pass
            return
        # 直前までドロップが発生していた場合、キューに空きが戻った時点で一度だけ知らせる
        dropped = self._log_dropped.pop(log_key, 0)
        if dropped:
            try:
                self.log_queue.put_nowait((log_key, f"[{dropped} lines dropped]\n"))
            except queue.Full:
                self._log_dropped[log_key] = dropped

    def _stream_output(self, log_key: str, process: subprocess.Popen[str], token: int) -> None:
        assert process.stdout is not None
        try:
            for line in iter(process.stdout.readline, ""):
                self._enqueue_log(log_key, line)
        except ValueError:
            # _stop_process 側で stdout を close した直後などに readline が投げうる (RC5)
            pass
        finally:
            try:
                process.wait()
            except Exception:
                pass
            exit_msg = f"[process exited with code {process.returncode}]\n"
            self._enqueue_log(log_key, exit_msg)
            try:
                process.stdout.close()
            except Exception:
                pass
            # このスレッドが積んだ token と現在の登録が一致する場合のみ取り除く (RC2)
            entry = self.processes.get(log_key)
            if entry is not None and entry.token == token:
                self.processes.pop(log_key, None)
            if self.process_threads.get(log_key) is threading.current_thread():
                self.process_threads.pop(log_key, None)

    def _stop_process(
        self, log_key: str, on_terminated: Optional[Callable[[], None]] = None
    ) -> None:
        """プロセスを (非ブロッキングで) 停止する。

        `on_terminated` を渡すと、プロセスの消滅を確認できた時点で (SIGTERM だけで
        済んだ場合は即座に、粘った場合は SIGKILL 後の消滅確認を経て) 呼び出す。
        Restart 系 (RC12) が「旧プロセスの終了後に新プロセスを起動する」ために使う。
        """
        entry = self.processes.pop(log_key, None)
        if entry is None:
            self._refresh_button_states()
            if on_terminated is not None:
                on_terminated()
            return
        process = entry.process
        if process.poll() is None:
            self._signal_process_group(process, signal.SIGTERM)
            self._append_log(log_key, "[stop: SIGTERM sent]\n")
            self._refresh_button_states()
            # process.wait() はメインスレッドをブロックするので使わず、after で非同期に監視する (RC3)
            self._escalating[log_key] = process
            self.root.after(
                STOP_ESCALATE_INTERVAL_MS,
                lambda: self._poll_stop_escalation(log_key, process, time.monotonic(), on_terminated),
            )
        else:
            self._append_log(log_key, "[process terminated]\n")
            self._refresh_button_states()
            if on_terminated is not None:
                on_terminated()

    def _poll_stop_escalation(
        self,
        log_key: str,
        process: subprocess.Popen[str],
        start_time: float,
        on_terminated: Optional[Callable[[], None]] = None,
        sigkill_sent: bool = False,
    ) -> None:
        if process.poll() is not None:
            self._escalating.pop(log_key, None)
            self._append_log(log_key, "[process terminated]\n")
            self._refresh_button_states()
            if on_terminated is not None:
                on_terminated()
            return
        if not sigkill_sent and time.monotonic() - start_time >= STOP_ESCALATE_TIMEOUT_MS / 1000:
            self._signal_process_group(process, signal.SIGKILL)
            self._append_log(log_key, "[stop: SIGKILL sent]\n")
            sigkill_sent = True
        # SIGKILL を送っただけでは終了したとは限らない (uninterruptible sleep 等) ので、
        # 実際に poll() が None でなくなるまでポーリングを続けてから on_terminated を呼ぶ。
        try:
            self.root.after(
                STOP_ESCALATE_INTERVAL_MS,
                lambda: self._poll_stop_escalation(
                    log_key, process, start_time, on_terminated, sigkill_sent
                ),
            )
        except tk.TclError:
            # ウィンドウが既に破棄されている場合は諦める (_on_close / _terminate_all 側で後始末される)
            pass

    @staticmethod
    def _signal_process_group(process: subprocess.Popen[str], sig: int) -> None:
        """子孫ごと畳む。start_new_session=True で作ったグループに送る。"""
        try:
            os.killpg(os.getpgid(process.pid), sig)
        except (ProcessLookupError, PermissionError):
            # グループが既に消えている場合などは、直接の子だけに送って諦める。
            if sig == signal.SIGKILL:
                process.kill()
            else:
                process.terminate()

    def _process_running(self, log_key: str) -> bool:
        entry = self.processes.get(log_key)
        return entry is not None and entry.process.poll() is None

    def _refresh_button_states(self) -> None:
        """ボタンの有効/無効をプロセスの実行状態に同期する (RC6)。

        "Start X" は実行中は無効 (二重起動防止)、GUI 管理のプロセスを畳む "Stop X"
        (kind="stop") は未実行なら無効。"Restart X" は常に有効。RC12 の起動待ち中
        (`_pending_launch`) はそのグループのボタンを一律無効にして誤操作を防ぐ。

        注意: "Stop RViz" は kind="stop" ではなく `./rviz.bash down` を実行する
        kind="command" で、GUI 外で起動されたコンテナも畳める。これを Start 扱いにすると
        RViz 実行中に押せなくなるため、ラベルが stop で始まる command は常に有効にする。
        値が変わらない限り configure しない (churn 防止)。
        """
        for label, button in self.buttons.items():
            spec = SPEC_MAP[label]
            log_key = spec.log_key
            running = self._process_running(log_key) if log_key else False
            pending = bool(log_key and self._pending_launch.get(log_key))
            lowered = spec.label.lower()
            if pending:
                desired = tk.DISABLED
            elif spec.kind == "stop":
                desired = tk.NORMAL if running else tk.DISABLED
            elif lowered.startswith(("stop", "restart")):
                desired = tk.NORMAL
            else:
                desired = tk.DISABLED if running else tk.NORMAL
            if str(button.cget("state")) != str(desired):
                button.configure(state=desired)

    def _update_preview(self, working_dir: Path, command: str, note: str) -> None:
        self.directory_label.config(text=f"Directory: {working_dir}")
        self.command_label.config(text=f"Command: {command}")
        self.note_label.config(text=f"Note: {note}" if note else "Note: -")

    def _append_log(self, log_key: str, text: str) -> None:
        """バッチ化されたテキストを1回の insert でウィジェットに追記する (RC1)。"""
        widget = self.log_widgets.get(log_key)
        if not widget:
            return
        # 挿入前に「最下部までスクロールされているか」を判定しておく。
        # ユーザーが上にスクロールして読んでいる場合、勝手に末尾へ飛ばさない。
        was_at_bottom = widget.yview()[1] >= 0.999
        widget.configure(state=tk.NORMAL)
        widget.insert(tk.END, text)
        # 保持行数の上限を超えたら古い行から削除する
        line_count = int(widget.index("end-1c").split(".")[0])
        if line_count > MAX_LOG_LINES:
            excess = line_count - MAX_LOG_LINES
            widget.delete("1.0", f"{excess + 1}.0")
        if was_at_bottom:
            widget.see(tk.END)
        widget.configure(state=tk.DISABLED)

    def _poll_log_queue(self) -> None:
        if self._closing:
            return
        if self._pending_shutdown:
            # シグナルハンドラが立てたフラグをここ (Tk のイベントループの中) で拾い、
            # ウィンドウを閉じたときと同じ後始末経路 (_on_close) に委譲する (RC10)。
            self._pending_shutdown = False
            self._on_close()
            return
        # 壁時計予算と件数上限の両方でキューを drain する。
        # 同じ log_key の連続する行は1回の _append_log 呼び出し (= 1回の insert) にまとめる。
        deadline = time.monotonic() + POLL_BUDGET_SECONDS
        batches: Dict[str, List[str]] = {}
        count = 0
        while count < POLL_MAX_ITEMS:
            try:
                log_key, line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            batches.setdefault(log_key, []).append(line)
            count += 1
            if time.monotonic() >= deadline:
                break
        for log_key, lines in batches.items():
            self._append_log(log_key, "".join(lines))
        # プロセスが自然終了したケース (Stop を押していない) もここで拾ってボタン状態に反映する。
        # 値が変わらない限り configure しないので、100ms ごとに呼んでも負荷は無視できる。
        self._refresh_button_states()

        if self._closing:
            return
        # まだキューに残っている場合は次のイベントループを待たずに早めに再開する
        delay_ms = POLL_INTERVAL_BUSY_MS if not self.log_queue.empty() else POLL_INTERVAL_IDLE_MS
        try:
            self.root.after(delay_ms, self._poll_log_queue)
        except tk.TclError:
            # ウィンドウが破棄済み
            pass

    def _collect_running_processes(self) -> List[subprocess.Popen[str]]:
        """追跡中の全プロセスへ SIGTERM を送り、self.processes を空にして生存プロセス一覧を返す。"""
        still_running: List[subprocess.Popen[str]] = []
        for log_key in list(self.processes.keys()):
            entry = self.processes.pop(log_key, None)
            if entry is None:
                continue
            process = entry.process
            if process.poll() is None:
                self._signal_process_group(process, signal.SIGTERM)
                still_running.append(process)
        # RC12: Restart の「旧プロセス終了待ち」中は self.processes から既に外れているが、
        # まだ生きている可能性があるプロセスが self._escalating に残っている。
        # ここで回収しないと、ウィンドウを閉じたときにそれらだけ後始末されずに孤児化する。
        for log_key, process in list(self._escalating.items()):
            self._escalating.pop(log_key, None)
            if process.poll() is None and process not in still_running:
                # 既に SIGTERM 送信済みなので再送はしない。以降の SIGKILL 昇格判断は
                # 呼び出し元 (_finish_close / _terminate_all) に委ねる。
                still_running.append(process)
        return still_running

    def _terminate_all(self, blocking: bool) -> None:
        """追跡中の全プロセスを SIGTERM → (猶予後) SIGKILL で畳む (RC4/RC10)。

        blocking=False: ウィンドウを閉じる操作 (`_on_close`) 専用。イベントループがまだ
        生きているので `root.after` による非同期エスカレーションで待つ。
        blocking=True: `main()` の finally やシグナルハンドラ専用。この時点で mainloop は
        既に終了しており GUI は表示されていないため、短いブロッキング `wait()` を許容する。
        """
        still_running = self._collect_running_processes()
        if not blocking:
            self._finish_close(still_running, time.monotonic())
            return

        deadline = time.monotonic() + STOP_ESCALATE_TIMEOUT_MS / 1000
        for process in still_running:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                pass
        for process in still_running:
            if process.poll() is None:
                self._signal_process_group(process, signal.SIGKILL)
        for process in still_running:
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass

    def _on_close(self) -> None:
        """ウィンドウを閉じるときは、追跡中の全プロセスを SIGTERM → (猶予後) SIGKILL で畳んでから破棄する (RC4)。"""
        self._closing = True
        try:
            # 閉じる操作をすぐ視覚的に反映する (RC11): 後始末の完了 (最大3秒) を待つ間もウィンドウを
            # 表示したまま操作を受け付けているように見せない。
            self.root.withdraw()
        except tk.TclError:
            pass
        self._terminate_all(blocking=False)

    def _finish_close(self, processes: List[subprocess.Popen[str]], start_time: float) -> None:
        alive = [p for p in processes if p.poll() is None]
        if alive and time.monotonic() - start_time < STOP_ESCALATE_TIMEOUT_MS / 1000:
            try:
                self.root.after(
                    STOP_ESCALATE_INTERVAL_MS,
                    lambda: self._finish_close(processes, start_time),
                )
                return
            except tk.TclError:
                pass
        for p in alive:
            self._signal_process_group(p, signal.SIGKILL)
        try:
            self.root.destroy()
        except tk.TclError:
            pass

def main() -> None:
    root = tk.Tk()
    app = RemoteGui(root)

    def _handle_termination_signal(signum: int, frame: object) -> None:
        # シグナルハンドラの中で Tk API (root.quit() など) を直接叩くのは避け、
        # フラグを立てるだけにする。実際の後始末は root.after で常時回っている
        # _poll_log_queue がフラグを見て _on_close 経由で行う (RC10)。
        app._pending_shutdown = True

    signal.signal(signal.SIGINT, _handle_termination_signal)
    signal.signal(signal.SIGTERM, _handle_termination_signal)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        # 端末からの Ctrl+C がシグナルハンドラより先に素通りしてきた場合の保険。
        app._closing = True
        try:
            root.withdraw()
        except tk.TclError:
            pass
    finally:
        # Ctrl+C (SIGINT)・SIGTERM・ウィンドウを閉じ忘れた異常系のいずれでも、
        # 子プロセスグループを確実に畳んでからプロセスを終了する (RC10)。
        # _on_close 経由の後始末が既に完了していれば self.processes は空なので、
        # ここは安全に no-op になる。
        app._terminate_all(blocking=True)

if __name__ == "__main__":
    main()
