#!/usr/bin/env python3
"""racing_kart_manager の操作 GUI。

manager とは別プロセス。GUI が落ちても manager は joy を流し続ける。

**この GUI は判断をしない。** ボタンの活性・非活性も表示文言も manager が
送ってきた status をそのまま使う。唯一の例外は status 途絶とバージョン
不一致の検出で、これは manager 自身からは送れないため gui_gate() で行う。

仕様: docs/spec/multi-vehicle-start-stop.md の「GUI インタフェース」
"""

from __future__ import annotations

import json
import threading
import tkinter as tk
from tkinter import ttk

import rclpy
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)
from std_msgs.msg import String

from racing_kart_manager_core import SCHEMA_VERSION, gui_gate

STATUS_QOS = QoSProfile(
    depth=1,
    history=QoSHistoryPolicy.KEEP_LAST,
    reliability=QoSReliabilityPolicy.RELIABLE,
    durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
)
COMMAND_QOS = QoSProfile(
    depth=10,
    history=QoSHistoryPolicy.KEEP_LAST,
    reliability=QoSReliabilityPolicy.RELIABLE,
)

REFRESH_MS = 100

LEVEL_COLOR = {"info": "#637381", "warn": "#B76E00", "error": "#B71D18"}


class GuiBridge(Node):
    """ROS との出入りだけ。判断はしない。"""

    def __init__(self) -> None:
        super().__init__("racing_kart_manager_gui")
        self._lock = threading.Lock()
        self._status: dict | None = None
        self._status_at: float | None = None

        self.create_subscription(
            String, "/racing_kart_manager/status", self._on_status, STATUS_QOS
        )
        self._command_publisher = self.create_publisher(
            String, "/racing_kart_manager/command", COMMAND_QOS
        )

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    def _on_status(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except ValueError:
            self.get_logger().warn("status の JSON を解釈できません")
            return
        with self._lock:
            self._status = payload
            self._status_at = self._now()

    def snapshot(self) -> tuple[dict | None, float | None]:
        """最新の status と、その受信からの経過秒。"""
        with self._lock:
            if self._status_at is None:
                return None, None
            return self._status, self._now() - self._status_at

    def send(self, command: str, vehicle_id: str | None = None) -> None:
        payload = {"schema_version": SCHEMA_VERSION, "command": command}
        if vehicle_id is not None:
            payload["vehicle_id"] = vehicle_id
        message = String()
        message.data = json.dumps(payload)
        self._command_publisher.publish(message)
        self.get_logger().info(f"command sent: {payload}")


class ManagerWindow:
    def __init__(self, bridge: GuiBridge) -> None:
        self.bridge = bridge
        self.root = tk.Tk()
        self.root.title("racing_kart_manager")
        self.root.geometry("760x520")

        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        self.mode_label = ttk.Label(outer, text="—", font=("", 14, "bold"))
        self.mode_label.pack(anchor="w", pady=(0, 10))

        controls = ttk.Frame(outer)
        controls.pack(fill="x")

        # 左: 車両選択
        vehicles_frame = ttk.LabelFrame(controls, text="車両選択", padding=10)
        vehicles_frame.pack(side="left", fill="both", expand=True)

        # 車両ボタンは status を受け取ってから作る。対象車両は manager の起動引数で
        # 決まるので、GUI 側では台数も車両IDも知らない。
        self.vehicles_frame = vehicles_frame
        self.vehicle_ids: tuple[str, ...] = ()
        self.vehicle_buttons: dict[str, ttk.Button] = {}
        self.vehicle_states: dict[str, ttk.Label] = {}
        self.vehicle_reasons: dict[str, ttk.Label] = {}

        # 右: 一斉発進準備完了
        all_frame = ttk.LabelFrame(controls, text="一斉発進", padding=10)
        all_frame.pack(side="left", fill="both", expand=True, padx=(12, 0))
        self.all_button = ttk.Button(
            all_frame,
            text="一斉発進準備完了",
            command=lambda: self.bridge.send("enter_all_mode"),
        )
        self.all_button.pack(pady=6)
        self.all_reason = ttk.Label(
            all_frame, text="", font=("", 8), foreground="#B76E00", wraplength=220
        )
        self.all_reason.pack()

        # 下: メッセージ表示エリア
        messages_frame = ttk.LabelFrame(outer, text="メッセージ", padding=8)
        messages_frame.pack(fill="both", expand=True, pady=(12, 0))
        self.messages = tk.Text(messages_frame, height=8, state="disabled", wrap="word")
        self.messages.pack(fill="both", expand=True)
        for level, color in LEVEL_COLOR.items():
            self.messages.tag_configure(level, foreground=color)

        self.root.after(REFRESH_MS, self._refresh)

    def _set_enabled(self, button: ttk.Button, enabled: bool) -> None:
        button.state(["!disabled"] if enabled else ["disabled"])

    def _rebuild_vehicles(self, vehicle_ids: tuple[str, ...]) -> None:
        """対象車両が変わったらボタンを作り直す。"""
        for child in self.vehicles_frame.winfo_children():
            child.destroy()
        self.vehicle_buttons.clear()
        self.vehicle_states.clear()
        self.vehicle_reasons.clear()

        for index, vehicle_id in enumerate(vehicle_ids):
            cell = ttk.Frame(self.vehicles_frame, padding=4)
            cell.grid(row=index // 2, column=index % 2, sticky="nsew")
            button = ttk.Button(
                cell,
                text=vehicle_id,
                width=12,
                command=lambda v=vehicle_id: self.bridge.send("enter_single_mode", v),
            )
            button.pack()
            state = ttk.Label(cell, text="—", font=("", 8), wraplength=150)
            state.pack()
            reason = ttk.Label(
                cell, text="", font=("", 8), foreground="#B76E00", wraplength=150
            )
            reason.pack()
            self.vehicle_buttons[vehicle_id] = button
            self.vehicle_states[vehicle_id] = state
            self.vehicle_reasons[vehicle_id] = reason
        self.vehicle_ids = vehicle_ids

    def _refresh(self) -> None:
        payload, age = self.bridge.snapshot()
        gate = gui_gate(age, None if payload is None else payload.get("schema_version"))

        if not gate.usable:
            self.mode_label.config(text=gate.reason or "操作できません")
            self._set_enabled(self.all_button, False)
            self.all_reason.config(text="")
            for vehicle_id in self.vehicle_ids:
                self._set_enabled(self.vehicle_buttons[vehicle_id], False)
                self.vehicle_states[vehicle_id].config(text="—")
                self.vehicle_reasons[vehicle_id].config(text="")
            self._render_messages([{"level": "error", "text": gate.reason or ""}])
            self.root.after(REFRESH_MS, self._refresh)
            return

        assert payload is not None
        selected = payload.get("selected")
        self.mode_label.config(
            text=f"モード: {payload['mode']}" + (f" ({selected})" if selected else "")
        )

        messages = payload.get("messages", [])

        def reasons_for(target: str) -> str:
            return "\n".join(m["text"] for m in messages if target in m.get("targets", []))

        self._set_enabled(self.all_button, bool(payload["can_enter_all_mode"]))
        self.all_reason.config(text=reasons_for("all"))

        by_id = {v["vehicle_id"]: v for v in payload["vehicles"]}
        vehicle_ids = tuple(by_id)
        if vehicle_ids != self.vehicle_ids:
            self._rebuild_vehicles(vehicle_ids)

        for vehicle_id in vehicle_ids:
            vehicle = by_id[vehicle_id]
            self._set_enabled(
                self.vehicle_buttons[vehicle_id],
                bool(payload["can_enter_single_mode"].get(vehicle_id)),
            )
            # 文言は manager が作る。GUI は変換表を持たない (観点 F-1)
            self.vehicle_states[vehicle_id].config(text=vehicle.get("label", "—"))
            self.vehicle_reasons[vehicle_id].config(text=reasons_for(vehicle_id))

        self._render_messages(messages)
        self.root.after(REFRESH_MS, self._refresh)

    def _render_messages(self, messages: list[dict]) -> None:
        self.messages.config(state="normal")
        self.messages.delete("1.0", "end")
        for message in messages:
            level = message.get("level", "info")
            self.messages.insert("end", message.get("text", "") + "\n", level)
        self.messages.config(state="disabled")

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    rclpy.init()
    bridge = GuiBridge()
    spinner = threading.Thread(target=rclpy.spin, args=(bridge,), daemon=True)
    spinner.start()
    try:
        ManagerWindow(bridge).run()
    finally:
        bridge.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
