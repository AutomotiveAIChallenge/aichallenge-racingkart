#!/usr/bin/env python3
from __future__ import annotations

import queue
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

import tkinter.font as tkfont

import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

ROOT_DIR = Path(__file__).resolve().parent
REMOTE_DIR = (ROOT_DIR / "remote").resolve()

DEFAULT_VEHICLE_ID = "A2"
DEFAULT_USERNAME = ""  # Deprecated: SSH User input removed.


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


def apply_devias_green_theme(root: tk.Tk) -> ttk.Style:
    """Apply a Devias-like green theme using ttk.Style.

    This function sets the base theme to 'clam' for consistent styling and
    customizes widgets' colors, fonts, and padding. Buttons receive primary
    (green), outline, and danger variants.
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

    return style

@dataclass
class CommandSpec:
    label: str
    command: str | None = None
    log_key: str | None = None
    requires_vehicle: bool = False
    requires_username: bool = False
    stop_before: bool = False
    note: str | None = None
    formatter: Optional[Callable[[str, str], str]] = None
    kind: str = "command"  # command, stop, stop_all

    def render(self, vehicle_id: str, username: str) -> str:
        if self.kind != "command":
            return ""
        if self.formatter:
            return self.formatter(vehicle_id, username)
        values = {
            "vehicle_id": vehicle_id,
            "username": username,
        }
        assert self.command is not None
        return self.command.format(**values)

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
        command="bash -lc \"pkill -f 'ros2 run joy joy_node' || true; ./joy.bash\"",
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

class RemoteGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Remote Vehicle Helper")

        # Apply Devias-inspired theme before building UI
        self.style = apply_devias_green_theme(self.root)

        if not REMOTE_DIR.exists():
            messagebox.showerror(
                "Configuration error",
                f"Remote directory not found: {REMOTE_DIR}",
            )
            raise SystemExit(1)

        self.vehicle_id_var = tk.StringVar(value=DEFAULT_VEHICLE_ID)
        # SSH user was removed from UI; keep empty string for compatibility.

        self.processes: Dict[str, subprocess.Popen[str]] = {}
        self.process_threads: Dict[str, threading.Thread] = {}
        self.log_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()

        self._build_ui()
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
                    
                    ttk.Button(
                        col_frame,
                        text=spec.label,
                        command=lambda s=spec: self._handle_command(s),
                        width=20,
                        style=btn_style,
                    ).pack(pady=4, fill=tk.X)
            else:
                for btn_label in buttons:
                    spec = SPEC_MAP[btn_label]
                    btn_style = "DeviasPrimary.TButton"
                    if spec.kind == "stop" or spec.label.lower().startswith("stop"):
                        btn_style = "DeviasDanger.TButton"
                    elif spec.label.lower().startswith("restart"):
                        btn_style = "DeviasOutline.TButton"

                    ttk.Button(
                        col_frame,
                        text=spec.label,
                        command=lambda s=spec: self._handle_command(s),
                        width=18,
                        style=btn_style,
                    ).pack(pady=4, fill=tk.X)
        
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
        username = ""

        if spec.requires_vehicle and not vehicle_id:
            messagebox.showwarning("入力不足", "Vehicle ID を指定してください。")
            return

        # SSH user requirement removed.

        if spec.kind == "stop":
            if not spec.log_key:
                return
            self._update_preview(REMOTE_DIR, f"[Stop] {spec.log_key}", spec.note or "")
            self._handle_stop_single(spec.log_key)
            return

        command_text = spec.render(vehicle_id, username)
        working_dir = REMOTE_DIR
        note = spec.note or ""
        self._update_preview(working_dir, command_text, note)

        log_key = spec.log_key
        if log_key is None:
            return

        if spec.stop_before:
            self._stop_process(log_key)

        if self._process_running(log_key):
            messagebox.showinfo(
                "Process running",
                f"{LOG_AREAS.get(log_key, log_key)} でコマンドが実行中です。先に停止してください。",
            )
            return

        try:
            process = subprocess.Popen(
                ["bash", "-lc", command_text],
                cwd=str(working_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            messagebox.showerror("Command error", f"bash が見つかりませんでした。")
            return
        except Exception as exc:  # pragma: no cover - defensive
            messagebox.showerror("Command error", str(exc))
            return

        thread = threading.Thread(
            target=self._stream_output,
            args=(log_key, process),
            daemon=True,
        )
        self.processes[log_key] = process
        self.process_threads[log_key] = thread
        thread.start()
        self._append_log(log_key, f"$ {command_text}\n")

    def _stream_output(self, log_key: str, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        for line in iter(process.stdout.readline, ""):
            self.log_queue.put((log_key, line))
        process.wait()
        exit_msg = f"[process exited with code {process.returncode}]\n"
        self.log_queue.put((log_key, exit_msg))
        self.processes.pop(log_key, None)
        self.process_threads.pop(log_key, None)

    def _stop_process(self, log_key: str) -> None:
        process = self.processes.pop(log_key, None)
        self.process_threads.pop(log_key, None)
        if not process:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        self._append_log(log_key, "[process terminated]\n")

    def _process_running(self, log_key: str) -> bool:
        proc = self.processes.get(log_key)
        return proc is not None and proc.poll() is None

    def _update_preview(self, working_dir: Path, command: str, note: str) -> None:
        self.directory_label.config(text=f"Directory: {working_dir}")
        self.command_label.config(text=f"Command: {command}")
        self.note_label.config(text=f"Note: {note}" if note else "Note: -")

    def _append_log(self, log_key: str, text: str) -> None:
        widget = self.log_widgets.get(log_key)
        if not widget:
            return
        widget.configure(state=tk.NORMAL)
        widget.insert(tk.END, text)
        widget.see(tk.END)
        widget.configure(state=tk.DISABLED)

    def _poll_log_queue(self) -> None:
        while True:
            try:
                log_key, line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            else:
                self._append_log(log_key, line)
        self.root.after(100, self._poll_log_queue)

def main() -> None:
    root = tk.Tk()
    app = RemoteGui(root)
    root.mainloop()

if __name__ == "__main__":
    main()
