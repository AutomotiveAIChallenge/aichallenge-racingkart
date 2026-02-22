#!/usr/bin/env python3

from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
from typing import Optional

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool, String


_DashboardPayload = tuple[str, list[int], float, str]


class AwsimStateManager(Node):
    """Monitor AWSIM process lifecycle and trigger cleanup when it exits.

    Also handles AWSIM admin control in Sync mode by publishing
    ``std_msgs/Bool`` to ``/admin/awsim/start`` when configured trigger
    state is observed on ``/admin/awsim/state``.
    """

    _DEFAULT_AWSIM_KILL_PATTERNS = "AWSIM.x86_64,aichallenge_awsim_eval"
    _DEFAULT_ADMIN_START_TOPIC = "/admin/awsim/start"
    _DEFAULT_ADMIN_START_TRIGGER_STATE = "waitstart,ready"
    _KNOWN_ADMIN_STATES = (
        "selectmode",
        "playstart",
        "ready",
        "waitstart",
        "start",
        "lapcomplete",
        "finish",
        "finishall",
        "terminate",
    )
    _KNOWN_VEHICLE_STATES = ("spawned", "grounded", "ready", "start", "finish")
    _ADMIN_FINISH_STATES = frozenset({"finish", "finishall", "finishedall", "terminate", "terminated"})
    _DEBUG_AWSIM_STATES = (
        "BOOT",
        "WAIT_AWSIM",
        "RUNNING",
        "SHUTTING_DOWN",
        "FINISHED",
        "ERROR",
    )
    _PARAM_DEFAULTS = (
        ("awsim_kill_patterns", _DEFAULT_AWSIM_KILL_PATTERNS),
        ("shutdown_grace_sec", 2),
        ("kill_wait_sec", 10),
        ("shutdown_delay_sec", 20.0),
        ("request_launch_shutdown", True),
        ("exit_on_finish", True),
        ("shutdown_on_exit", False),
        ("enable_debug_visualization", False),
        ("admin_state_topic", "/admin/awsim/state"),
        ("admin_start_topic", _DEFAULT_ADMIN_START_TOPIC),
        ("admin_start_trigger_state", _DEFAULT_ADMIN_START_TRIGGER_STATE),
        ("admin_start_enabled", True),
        ("admin_start_once", True),
    )

    def __init__(self) -> None:
        super().__init__("awsim_state_manager")

        for key, default in self._PARAM_DEFAULTS:
            self.declare_parameter(key, default)

        self._awsim_kill_patterns = self._split_csv(str(self.get_parameter("awsim_kill_patterns").value))
        self._debug_visualization_enabled = bool(self.get_parameter("enable_debug_visualization").value)
        self._debug_panel_queue: Optional[queue.Queue[_DashboardPayload]] = None
        self._debug_panel_active = False
        self._debug_panel_error_logged = False
        self._admin_state_topic = str(self.get_parameter("admin_state_topic").value).strip() or "/admin/awsim/state"
        self._admin_start_topic = str(self.get_parameter("admin_start_topic").value).strip() or self._DEFAULT_ADMIN_START_TOPIC
        self._admin_start_trigger_states = self._normalize_admin_state_list(
            str(self.get_parameter("admin_start_trigger_state").value)
        ) or self._normalize_admin_state_list(self._DEFAULT_ADMIN_START_TRIGGER_STATE)
        self._admin_start_enabled = bool(self.get_parameter("admin_start_enabled").value)
        self._admin_start_once = bool(self.get_parameter("admin_start_once").value)
        self._admin_start_published = False
        if self._admin_start_enabled:
            unknown = [state for state in self._admin_start_trigger_states if state not in self._KNOWN_ADMIN_STATES]
            if unknown:
                self.get_logger().warn(
                    f"admin_start_trigger_state contains unknown state(s): {', '.join(unknown)}"
                    f" (known: {', '.join(self._KNOWN_ADMIN_STATES)})"
                )

        ros_domain_id = os.environ.get("ROS_DOMAIN_ID", "").strip()
        if ros_domain_id and ros_domain_id != "0":
            self.get_logger().warn(
                "awsim_state_manager is expected on ROS_DOMAIN_ID=0, "
                f"but got ROS_DOMAIN_ID={ros_domain_id}"
            )

        self._cond = threading.Condition()
        self._current_state = "BOOT"
        self._last_seen_pids: list[int] = []
        # admin_state is set via topic callback (default topic is /admin/awsim/state).
        self._last_admin_state = ""
        self._shutdown_started = False
        self._shutdown_reason: Optional[str] = None
        self._exit_code = 0

        admin_state_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self.create_subscription(String, self._admin_state_topic, self._on_admin_state, qos_profile=admin_state_qos)
        admin_start_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._pub_admin_start = self.create_publisher(Bool, self._admin_start_topic, qos_profile=admin_start_qos)

        self.get_logger().info(
            "admin state-manager start sender configured: "
            f"enabled={self._admin_start_enabled} topic={self._admin_start_topic} "
            f"trigger={','.join(self._admin_start_trigger_states)}"
        )

        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

        self.get_logger().info(f"monitoring patterns: {self._awsim_kill_patterns or ['(empty)']}")
        self.get_logger().info(f"exit_on_finish: {bool(self.get_parameter('exit_on_finish').value)}")

        if self._debug_visualization_enabled:
            self._start_debug_visualization()
            self._emit_state_snapshot()

    @staticmethod
    def _normalize_admin_state(raw: str) -> str:
        return "".join(ch for ch in (raw or "").strip().lower() if ch.isalnum())

    @staticmethod
    def _normalize_admin_state_list(raw: str) -> list[str]:
        normalized_items = []
        for item in AwsimStateManager._split_csv(raw):
            normalized = AwsimStateManager._normalize_admin_state(item)
            if normalized:
                normalized_items.append(normalized)
        return normalized_items

    @classmethod
    def _is_admin_finish_state(cls, state: str) -> bool:
        norm = cls._normalize_admin_state(state)
        if not norm:
            return False
        return norm in cls._ADMIN_FINISH_STATES

    @property
    def exit_code(self) -> int:
        return int(self._exit_code)

    def _set_exit_code(self, code: int) -> None:
        code = int(code)
        if code and self._exit_code == 0:
            self._exit_code = code

    @staticmethod
    def _split_csv(raw: str) -> list[str]:
        seen: set[str] = set()
        items: list[str] = []
        for item in str(raw or "").split(","):
            normalized = item.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            items.append(normalized)
        return items

    def _parse_int(self, name: str, default: int) -> int:
        try:
            return int(self.get_parameter(name).value)
        except Exception:
            return int(default)

    def _parse_float(self, name: str, default: float) -> float:
        try:
            return float(self.get_parameter(name).value)
        except Exception:
            return float(default)

    def _set_state(self, state: str) -> None:
        with self._cond:
            self._current_state = state
            self._cond.notify_all()
        self._emit_state_snapshot()

    def _emit_state_snapshot(self) -> None:
        if not self._debug_visualization_enabled:
            return
        queue_ref = self._debug_panel_queue
        if queue_ref is None:
            return

        with self._cond:
            state = self._current_state
            pids = list(self._last_seen_pids)
            admin_state = self._last_admin_state
        now = time.monotonic()
        payload = (state, pids, now, admin_state)
        try:
            queue_ref.put_nowait(payload)
            return
        except queue.Full:
            pass
        try:
            queue_ref.get_nowait()
            queue_ref.put_nowait(payload)
        except Exception:
            pass

    def _start_debug_visualization(self) -> None:
        def _run() -> None:
            try:
                try:
                    from PySide6 import QtCore, QtGui, QtWidgets
                except Exception:
                    from PyQt5 import QtCore, QtGui, QtWidgets
            except Exception as exc:  # noqa: BLE001
                if not self._debug_panel_error_logged:
                    self.get_logger().warn(f"failed to import Qt binding for visualization: {exc}")
                    self._debug_panel_error_logged = True
                self._debug_panel_active = False
                self._debug_panel_queue = None
                return

            class _DashboardWindow(QtWidgets.QWidget):
                def __init__(self, awsim_states: tuple[str, ...]) -> None:
                    super().__init__()
                    self._active_color = QtGui.QColor("#1E90FF")
                    self._inactive_color = QtGui.QColor("#444444")

                    self.setWindowTitle("AWSIM State Monitor")
                    self.setMinimumWidth(640)
                    self.setMinimumHeight(360)

                    layout = QtWidgets.QVBoxLayout(self)
                    layout.setContentsMargins(12, 12, 12, 12)
                    layout.setSpacing(6)

                    layout.addWidget(QtWidgets.QLabel("AWSIM Process State"))
                    self._state_label = QtWidgets.QLabel("state: --")
                    self._state_label.setWordWrap(True)
                    layout.addWidget(self._state_label)

                    layout.addWidget(QtWidgets.QLabel("Workflow"))
                    self._state_list = QtWidgets.QListWidget()
                    self._state_list.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
                    self._state_list.setFocusPolicy(QtCore.Qt.NoFocus)
                    self._state_items: list[QtWidgets.QListWidgetItem] = []
                    self._state_lookup: dict[str, QtWidgets.QListWidgetItem] = {}
                    for state in awsim_states:
                        item = QtWidgets.QListWidgetItem(state)
                        item.setForeground(self._inactive_color)
                        self._state_items.append(item)
                        self._state_lookup[state] = item
                        self._state_list.addItem(item)
                    layout.addWidget(self._state_list, 2)

                    self._state_indicator = QtWidgets.QLabel("no value yet")
                    self._state_indicator.setWordWrap(True)
                    layout.addWidget(self._state_indicator)

                    self._pids_label = QtWidgets.QLabel("pids: --")
                    self._pids_label.setWordWrap(True)
                    layout.addWidget(self._pids_label)

                    self._admin_state_label = QtWidgets.QLabel("admin state: --")
                    self._admin_state_label.setWordWrap(True)
                    layout.addWidget(self._admin_state_label)

                    self._meta = QtWidgets.QLabel("monitoring AWSIM process by kill pattern")
                    self._meta.setWordWrap(True)
                    layout.addWidget(self._meta)
                    self._resize_fonts()

                def _resize_fonts(self) -> None:
                    width = max(420, self.width())
                    height = max(260, self.height())
                    state_font = max(9, min(24, min(width // 42, height // 20)))
                    body_font = max(8, min(18, min(width // 58, height // 24)))

                    self._state_label.setFont(QtGui.QFont("Verdana", state_font))
                    self._state_indicator.setFont(QtGui.QFont("Verdana", body_font))
                    self._pids_label.setFont(QtGui.QFont("Verdana", max(7, body_font)))
                    self._admin_state_label.setFont(QtGui.QFont("Verdana", max(7, body_font)))
                    self._state_list.setFont(QtGui.QFont("Verdana", body_font))
                    self._meta.setFont(QtGui.QFont("Verdana", max(6, body_font - 2)))

                def resizeEvent(self, event: object) -> None:  # type: ignore[override]
                    super().resizeEvent(event)  # type: ignore[misc]
                    self._resize_fonts()

                def update_state(self, state: str, pids: list[int], _now: float, admin_state: str) -> None:
                    state_clean = state or "(unknown)"
                    self._state_label.setText(f"state: {state_clean}")
                    self._state_indicator.setText(f"workflow state: {state_clean}")
                    self._pids_label.setText(f"pids: {', '.join(str(pid) for pid in pids) or '(none)'}")
                    self._admin_state_label.setText(f"admin state: {admin_state or '(none)'}")

                    active_font = QtGui.QFont("Verdana", self._state_list.font().pointSize())
                    active_font.setBold(True)
                    inactive_font = QtGui.QFont("Verdana", max(8, self._state_list.font().pointSize()))

                    for item in self._state_items:
                        item.setForeground(self._inactive_color)
                        item.setFont(inactive_font)

                    if state and state in self._state_lookup:
                        active_item = self._state_lookup[state]
                        active_item.setForeground(self._active_color)
                        active_item.setFont(active_font)
                        self._state_list.setCurrentItem(active_item)
                        self._state_list.scrollToItem(active_item)

                    self._meta.setText("monitoring target: AWSIM process by pattern")

            self._debug_panel_queue = queue.Queue(maxsize=128)
            self._debug_panel_active = True

            app = QtWidgets.QApplication.instance()
            if app is None:
                app = QtWidgets.QApplication(["awsim_state_manager"])
            dashboard = _DashboardWindow(
                self._DEBUG_AWSIM_STATES,
            )

            def _on_timer() -> None:
                if not self._debug_panel_active:
                    return
                next_payload: Optional[_DashboardPayload] = None
                while True:
                    try:
                        next_payload = self._debug_panel_queue.get_nowait()
                    except queue.Empty:
                        break
                if next_payload is None:
                    return
                state, pids, now, admin_state = next_payload
                dashboard.update_state(state, pids, now, admin_state)

            timer = QtCore.QTimer()
            timer.timeout.connect(_on_timer)
            timer.start(250)

            dashboard.show()
            app.exec()
            self._debug_panel_active = False
            self._debug_panel_queue = None

        threading.Thread(target=_run, daemon=True).start()

    @staticmethod
    def _is_alive(pid: int) -> bool:
        try:
            if pid <= 1:
                return False
            os.kill(pid, 0)
            with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as f:
                parts = f.read().split()
            # /proc/<pid>/stat: 3rd field is process state
            # R/D/S/Z/T (sleeping/stopped/done etc.) / Z means zombie -> treat as dead for orchestration.
            if len(parts) > 2 and parts[2] == "Z":
                return False
            return True
        except ProcessLookupError:
            return False
        except FileNotFoundError:
            return False
        except Exception:
            return False

    def _on_admin_state(self, msg: String) -> None:
        state = (msg.data or "").strip()
        with self._cond:
            self._last_admin_state = state
            self._cond.notify_all()
        if self._should_send_admin_start(state):
            self._send_admin_start(state)
        if self._is_admin_finish_state(state) and not self._shutdown_started:
            self._shutdown_reason = f"admin state reached: {state}"
            self._start_shutdown()
        self._emit_state_snapshot()

    def _should_send_admin_start(self, state: str) -> bool:
        if not self._admin_start_enabled or not state:
            return False
        if self._admin_start_once and self._admin_start_published:
            return False
        return self._normalize_admin_state(state) in self._admin_start_trigger_states

    def _send_admin_start(self, state: str) -> None:
        msg = Bool()
        msg.data = True
        self._pub_admin_start.publish(msg)
        self._admin_start_published = True
        self.get_logger().info(f"published admin start from state={state!r} to {self._admin_start_topic}")

    def _snapshot_awsim_pids(self) -> list[int]:
        all_pids: list[int] = []
        for pattern in self._awsim_kill_patterns:
            all_pids.extend(self._find_pids(pattern))
        return sorted(set(all_pids))

    @staticmethod
    def _signal_name(sig: int) -> str:
        if sig == signal.SIGINT:
            return "SIGINT"
        if sig == signal.SIGTERM:
            return "SIGTERM"
        if sig == signal.SIGKILL:
            return "SIGKILL"
        return str(sig)

    def _send_signal(self, pid: int, sig: int) -> bool:
        try:
            # Intentionally signal only the target PID.
            # Do not signal the whole process group to avoid cascading SIGINT.
            os.kill(pid, sig)
            return True
        except ProcessLookupError:
            return False
        except Exception:
            return False

    def _wait_for_exit(self, pid: int, timeout_sec: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout_sec))
        while time.monotonic() < deadline:
            if not self._is_alive(pid):
                return True
            time.sleep(0.1)
        return not self._is_alive(pid)

    def _send_signal_and_wait(self, pid: int, sig: int, wait_sec: float) -> bool:
        if not self._is_alive(pid):
            return True

        wait_sec = max(0.0, float(wait_sec))
        self.get_logger().warning(
            f"stopping pid={pid}: send {self._signal_name(sig)} and wait up to {wait_sec}s"
        )
        sent = self._send_signal(pid, sig)
        if not sent:
            return not self._is_alive(pid)
        return self._wait_for_exit(pid, wait_sec)

    def _stop_process(self, pid: int) -> None:
        if pid == os.getpid() or not self._is_alive(pid):
            return

        kill_wait_sec = max(1.0, self._parse_float("kill_wait_sec", 10.0))
        sigint_grace_sec = max(0.0, self._parse_float("shutdown_grace_sec", 2.0))
        self.get_logger().warning(
            "stop flow order: "
            f"[1/4] SIGINT(wait={kill_wait_sec}s) -> "
            f"[2/4] grace(wait={sigint_grace_sec}s) -> "
            f"[3/4] SIGTERM(wait={kill_wait_sec}s) -> "
            "[4/4] SIGKILL (if needed) "
            f"(pid={pid})"
        )

        self.get_logger().warning(f"[1/4] stopping pid={pid} with SIGINT")
        if self._send_signal_and_wait(pid, signal.SIGINT, kill_wait_sec):
            return

        if sigint_grace_sec > 0 and self._is_alive(pid):
            self.get_logger().warning(
                f"[2/4] pid={pid} still alive after SIGINT; waiting grace {sigint_grace_sec}s"
            )
            time.sleep(sigint_grace_sec)

        self.get_logger().warning(f"[3/4] stopping pid={pid} with SIGTERM")
        if self._send_signal_and_wait(pid, signal.SIGTERM, kill_wait_sec):
            return

        if self._is_alive(pid):
            self.get_logger().warning(f"[4/4] pid={pid} still alive after SIGTERM; forcing SIGKILL")
            self._send_signal(pid, signal.SIGKILL)
            self._wait_for_exit(pid, kill_wait_sec)

    def _find_pids(self, pattern: str) -> list[int]:
        try:
            cp = subprocess.run(
                ["pgrep", "-f", pattern],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            self.get_logger().warn(f"failed to run pgrep for pattern={pattern}")
            return []

        if cp.returncode != 0:
            return []

        pids: list[int] = []
        for line in (cp.stdout or "").splitlines():
            try:
                pid = int(line.strip())
            except ValueError:
                continue
            if pid <= 1 or pid == os.getpid():
                continue
            pids.append(pid)
        return sorted(set(pids))

    def _kill_by_patterns(self, patterns: list[str], label: str) -> None:
        if not patterns:
            self.get_logger().info(f"{label}: no patterns configured")
            return

        self.get_logger().warning(f"{label}: begin")
        for pattern in patterns:
            pids = self._find_pids(pattern)
            if not pids:
                self.get_logger().info(f"{label}: no match for pattern={pattern}")
                continue
            self.get_logger().warning(f"{label}: pattern='{pattern}' pids={pids}")
            for pid in pids:
                self._stop_process(pid)
        self.get_logger().warning(f"{label}: done")

    @staticmethod
    def _read_cmdline(pid: int) -> str:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                raw = f.read()
        except Exception:
            return ""
        return " ".join(part.decode("utf-8", errors="replace") for part in raw.split(b"\x00") if part)

    def _request_launch_shutdown(self) -> None:
        parent_pid = os.getppid()
        if parent_pid <= 1:
            self.get_logger().warn("skip launch shutdown request (invalid parent pid)")
            return

        parent_cmdline = self._read_cmdline(parent_pid)
        if "ros2" not in parent_cmdline or "launch" not in parent_cmdline:
            self.get_logger().warn(
                "skip launch shutdown request (parent is not ros2 launch): "
                f"pid={parent_pid} cmdline={parent_cmdline!r}"
            )
            return

        try:
            os.kill(parent_pid, signal.SIGINT)
            self.get_logger().info(
                f"requested launch shutdown by SIGINT to parent pid={parent_pid}"
            )
        except ProcessLookupError:
            self.get_logger().warn(
                f"skip launch shutdown request (parent pid not found): pid={parent_pid}"
            )
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warn(
                f"failed to request launch shutdown: pid={parent_pid} err={exc}"
            )

    def _start_shutdown(self) -> None:
        with self._cond:
            if self._shutdown_started:
                return
            self._shutdown_started = True

        self._set_state("SHUTTING_DOWN")
        reason = self._shutdown_reason or "unknown"
        self.get_logger().warn(f"shutdown sequence started (reason={reason})")

        delay = max(0.0, self._parse_float("shutdown_delay_sec", 20.0))
        kill_wait_sec = max(1.0, self._parse_float("kill_wait_sec", 10.0))
        sigint_grace_sec = max(0.0, self._parse_float("shutdown_grace_sec", 2.0))
        self.get_logger().warn(
            "shutdown timing: "
            f"shutdown_delay_sec={delay}s, kill_wait_sec={kill_wait_sec}s, shutdown_grace_sec={sigint_grace_sec}s"
        )
        if delay > 0:
            self.get_logger().warn(f"waiting {delay}s before killing awsim processes")
            time.sleep(delay)

        self._kill_by_patterns(self._awsim_kill_patterns, "phase1-awsim")

        if bool(self.get_parameter("exit_on_finish").value):
            if bool(self.get_parameter("request_launch_shutdown").value):
                self._request_launch_shutdown()
            self._set_state("FINISHED")
            self.get_logger().info("exit_on_finish=true; shutting down ROS")
            if rclpy.ok():
                rclpy.shutdown()

        if bool(self.get_parameter("shutdown_on_exit").value):
            os._exit(0)

    def _run(self) -> None:
        if not self._awsim_kill_patterns:
            self._set_state("ERROR")
            self._set_exit_code(1)
            self._shutdown_reason = "awsim_kill_patterns is empty"
            self.get_logger().error("awsim_kill_patterns is empty; shutdown logic disabled")
            return

        self._set_state("WAIT_AWSIM")
        while rclpy.ok():
            pids = self._snapshot_awsim_pids()
            with self._cond:
                self._last_seen_pids = pids
            self._emit_state_snapshot()
            if pids:
                break
            time.sleep(1.0)

        if not rclpy.ok():
            return

        self.get_logger().info(f"awsim process detected: pids={pids}")
        self._set_state("RUNNING")
        while rclpy.ok():
            pids = self._snapshot_awsim_pids()
            with self._cond:
                self._last_seen_pids = pids
            self._emit_state_snapshot()
            if not pids:
                self._set_state("FINISHED")
                self._shutdown_reason = "awsim process disappeared"
                self.get_logger().warn("awsim process disappeared; starting shutdown")
                self._start_shutdown()
                return
            time.sleep(1.0)

    def destroy_node(self) -> bool:
        if not self._shutdown_started and bool(self.get_parameter("shutdown_on_exit").value):
            self._shutdown_reason = "destroy_node"
            self._start_shutdown()
        return super().destroy_node()


def main() -> int:
    rclpy.init()
    node = AwsimStateManager()
    exit_code = 0
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info("keyboard interrupt: triggering shutdown")
        node._start_shutdown()
    except Exception as exc:  # noqa: BLE001
        exit_code = 1
        node.get_logger().error(f"unhandled exception in manager: {exc}")
        node._start_shutdown()
    finally:
        exit_code = max(exit_code, int(getattr(node, "exit_code", 0)))
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
