#!/usr/bin/env python3

from __future__ import annotations

import os
import signal
import shlex
import subprocess
import queue
import threading
from pathlib import Path
from typing import Optional

import rclpy
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node
from std_msgs.msg import Bool
from std_msgs.msg import String
from std_srvs.srv import Trigger


_DashboardPayload = tuple[str, str, Optional[str], bool, bool, str, str]


class AutostartOrchestrator(Node):
    _STATE_BOOT = "BOOT"
    _STATE_WAIT_INITIAL_POSE = "WAIT_INITIAL_POSE"
    _STATE_REQUEST_CONTROL_MODE = "REQUEST_CONTROL_MODE"
    _STATE_IDLE = "IDLE"
    _STATE_WAIT_START = "WAIT_START"
    _STATE_RUNNING = "RUNNING"
    _STATE_RECORDING = "RECORDING"
    _STATE_WAIT_STOP = "WAIT_STOP"
    _STATE_AUTO_STOP_DISABLED = "AUTO_STOP_DISABLED"
    _STATE_STOPPING = "STOPPING"
    _STATE_FINISHED = "FINISHED"
    _STATE_POST_PROCESS = "POST_PROCESS"
    _STATE_ERROR = "ERROR"
    _WORKFLOW_STATES = (
        _STATE_BOOT,
        _STATE_WAIT_INITIAL_POSE,
        _STATE_REQUEST_CONTROL_MODE,
        _STATE_IDLE,
        _STATE_WAIT_START,
        _STATE_RUNNING,
        _STATE_RECORDING,
        _STATE_WAIT_STOP,
        _STATE_AUTO_STOP_DISABLED,
        _STATE_STOPPING,
        _STATE_POST_PROCESS,
        _STATE_FINISHED,
        _STATE_ERROR,
    )

    _ANSI_BLUE = "\033[34m"
    _ANSI_BOLD = "\033[1m"
    _ANSI_RESET = "\033[0m"

    def _require_parameter(self, name: str) -> object:
        value = self.get_parameter(name).value
        if value is None:
            raise RuntimeError(f"required parameter is not set: {name}")
        return value

    def __init__(self) -> None:
        super().__init__("autostart_orchestrator")

        cbg = ReentrantCallbackGroup()

        required_parameters = (
            "vehicle_state_topic",
            "start_on_vehicle_state",
            "stop_on_vehicle_state",
            "enable_capture",
            "enable_rosbag",
            "call_initial_pose",
            "request_control_mode",
            "initial_pose_service",
            "control_mode_request_topic",
            "capture_service",
            "rosbag_topics",
            "rosbag_output",
            "rosbag_storage_id",
            "rosbag_compression_format",
            "rosbag_compression_mode",
            "exit_on_finish",
        )
        required_param_desc = ParameterDescriptor(dynamic_typing=True)
        for name in required_parameters:
            self.declare_parameter(name, descriptor=required_param_desc)
            self._require_parameter(name)
        self.declare_parameter("enable_debug_visualization", False)
        self.declare_parameter("enable_motion_analytics", True)
        self.declare_parameter("motion_analytics_cmd", "ros2 run aichallenge_system_launch motion_analytics.py")
        self.declare_parameter("motion_analytics_input_dir", "")

        vehicle_state_topic = str(self.get_parameter("vehicle_state_topic").value).strip()
        if not vehicle_state_topic:
            raise ValueError("vehicle_state_topic must not be empty")
        self._vehicle_state_topic = vehicle_state_topic

        self._workflow_state = self._STATE_BOOT
        self._workflow_detail = ""
        self._state_lock = threading.Lock()
        self._vehicle_label = self._vehicle_label_from_domain_id(os.environ.get("ROS_DOMAIN_ID", ""))
        self._debug_visualization_enabled = bool(self.get_parameter("enable_debug_visualization").value)
        self._debug_panel_queue: Optional[queue.Queue[_DashboardPayload]] = None
        self._debug_panel_active = False
        self._debug_panel_error_logged = False
        self._debug_panel_thread: Optional[threading.Thread] = None
        self._debug_panel_stop_event: Optional[threading.Event] = None
        if self._debug_visualization_enabled:
            self._debug_panel_queue = queue.Queue(maxsize=128)
            self._start_debug_visualization()

        self._cond = threading.Condition()
        self._last_vehicle_state: Optional[str] = None

        rclpy.get_default_context().on_shutdown(self._on_rclpy_shutdown)

        self._sub = self.create_subscription(String, vehicle_state_topic, self._on_vehicle_state, 10, callback_group=cbg)

        self._cli_initial_pose = self.create_client(
            Trigger, str(self.get_parameter("initial_pose_service").value), callback_group=cbg
        )
        self._cli_capture = self.create_client(
            Trigger, str(self.get_parameter("capture_service").value), callback_group=cbg
        )
        self._pub_control_mode = self.create_publisher(
            Bool, str(self.get_parameter("control_mode_request_topic").value), 1
        )

        self._capture_started = False
        self._rosbag_proc: Optional[subprocess.Popen] = None
        self._rosbag_log_fp: Optional[object] = None
        self._motion_analytics_run_once = False
        self._motion_analytics_lock = threading.Lock()
        self._latest_link_lock = threading.Lock()

        self._exit_code = 0

        self._emit_workflow_dashboard("bootstrap")

        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

        self.get_logger().info(f"Subscribing vehicle state: {vehicle_state_topic}")

    def _set_workflow_state(self, state: str, detail: str = "") -> None:
        if state not in self._WORKFLOW_STATES:
            self.get_logger().warn(f"unknown workflow state: {state}")
            return

        if not self._debug_visualization_enabled:
            return

        detail = (detail or "").strip()
        emit = False
        with self._state_lock:
            if state != self._workflow_state or detail != self._workflow_detail:
                self._workflow_state = state
                self._workflow_detail = detail
                emit = True

        if emit:
            self._emit_workflow_dashboard(detail)

    def _emit_workflow_dashboard(self, fallback_detail: str = "") -> None:
        if not self._debug_visualization_enabled:
            return

        with self._state_lock:
            state = self._workflow_state
            detail = self._workflow_detail or fallback_detail

        payload: _DashboardPayload = (
            state,
            detail or self._workflow_detail,
            self._last_vehicle_state,
            self._capture_started,
            self._rosbag_proc is not None,
            self._vehicle_label,
            self._vehicle_state_topic,
        )
        if self._debug_panel_queue is None:
            return

        if self._debug_panel_active:
            try:
                self._debug_panel_queue.put_nowait(payload)
            except queue.Full:
                try:
                    self._debug_panel_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._debug_panel_queue.put_nowait(payload)
                except queue.Full:
                    pass
        else:
            # フォールバック: Qt 未起動時は従来ログとして可視化
            state_text_lines = []
            for item in self._WORKFLOW_STATES:
                if item == state:
                    state_token = f"{self._ANSI_BOLD}{self._ANSI_BLUE}[{item}]{self._ANSI_RESET}"
                else:
                    state_token = item
                state_text_lines.append(state_token)

            detail_fragments = [
                f"vehicle={self._vehicle_label}",
                f"vehicle_topic={self._vehicle_state_topic}",
                f"state={self._last_vehicle_state!r}",
                f"capture_started={self._capture_started}",
                f"rosbag_running={self._rosbag_proc is not None}",
            ]
            if detail:
                detail_fragments.append(f"detail={detail}")
            self.get_logger().info("workflow:\n" + "\n".join(state_text_lines) + " | " + ", ".join(detail_fragments))

    def _start_debug_visualization(self) -> None:
        self._debug_panel_stop_event = threading.Event()

        def _run(stop_event: threading.Event) -> None:
            try:
                try:
                    from PySide6 import QtCore, QtGui, QtWidgets
                except Exception:
                    from PyQt5 import QtCore, QtGui, QtWidgets

                class _DashboardWindow(QtWidgets.QWidget):  # noqa: D401
                    def __init__(self, states: tuple[str, ...]) -> None:
                        super().__init__()
                        self.setWindowTitle("Autostart Orchestrator Debug")
                        self.setMinimumWidth(680)
                        self.setMinimumHeight(240)

                        self._states = states
                        layout = QtWidgets.QVBoxLayout(self)

                        layout.addWidget(QtWidgets.QLabel("Workflow State"))
                        self._state_list = QtWidgets.QListWidget()
                        self._state_list.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
                        self._state_list.setFocusPolicy(QtCore.Qt.NoFocus)
                        self._state_list.setSpacing(1)
                        self._state_items: list[QtWidgets.QListWidgetItem] = []
                        for state in self._states:
                            state_item = QtWidgets.QListWidgetItem(state)
                            self._state_items.append(state_item)
                            self._state_list.addItem(state_item)
                        self._highlight_index = 0
                        self._active_color = QtGui.QColor("#1E90FF")
                        self._inactive_color = QtGui.QColor("#444444")
                        layout.addWidget(self._state_list, 1)

                        layout.addWidget(QtWidgets.QLabel("State Detail"))
                        self._label_detail = QtWidgets.QLabel("")
                        self._label_detail.setWordWrap(True)
                        self._label_detail.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
                        layout.addWidget(self._label_detail)

                        self._label_metrics = QtWidgets.QLabel("")
                        self._label_metrics.setWordWrap(True)
                        layout.addWidget(self._label_metrics)

                        self._resize_fonts()

                    def _resize_fonts(self) -> None:
                        width = max(320, self.width())
                        height = max(220, self.height())
                        state_font_size = max(9, min(20, min(width // 48, height // 18)))
                        detail_font_size = max(8, min(18, min(width // 58, height // 20)))

                        self._state_list.setFont(QtGui.QFont("Verdana", state_font_size))
                        self._label_detail.setFont(QtGui.QFont("Verdana", detail_font_size))
                        self._label_metrics.setFont(QtGui.QFont("Verdana", detail_font_size))

                    def resizeEvent(self, event: object) -> None:  # type: ignore[override]
                        super().resizeEvent(event)  # type: ignore[misc]
                        self._resize_fonts()

                    def update_state(
                        self,
                        state: str,
                        detail: str,
                        vehicle_state: Optional[str],
                        capture_started: bool,
                        rosbag_running: bool,
                        vehicle_label: str,
                        vehicle_topic: str,
                    ) -> None:
                        detail_text = detail
                        active_font_size = max(8, self._state_list.font().pointSize())
                        normal_font = QtGui.QFont("Verdana", active_font_size)
                        active_font = QtGui.QFont("Verdana", active_font_size)
                        active_font.setBold(True)
                        active_index = 0
                        for idx, item in enumerate(self._states):
                            if item == state:
                                self._state_items[idx].setForeground(self._active_color)
                                self._state_items[idx].setFont(active_font)
                                active_index = idx
                            else:
                                self._state_items[idx].setForeground(self._inactive_color)
                                self._state_items[idx].setFont(normal_font)
                        self._highlight_index = max(0, min(active_index, len(self._states) - 1))
                        active_item = self._state_items[self._highlight_index]
                        self._state_list.setCurrentItem(active_item)
                        self._state_list.scrollToItem(active_item)

                        if not detail_text:
                            detail_text = self._states[self._highlight_index]
                        self._label_detail.setText(
                            f"<b>state</b>: {state}<br/>"
                            f"<b>detail</b>: {detail_text}<br/>"
                            f"<b>vehicle</b>: {vehicle_label}<br/>"
                            f"<b>vehicle_state</b>: {vehicle_state!r}<br/>"
                            f"<b>vehicle_topic</b>: {vehicle_topic}<br/>"
                        )
                        self._label_metrics.setText(
                            f"capture_started={capture_started}, rosbag_running={rosbag_running}"
                        )

            except Exception as e:  # noqa: BLE001
                if not self._debug_panel_error_logged:
                    self._debug_panel_error_logged = True
                    self.get_logger().warn(f"enable_debug_visualization is true, but Qt panel could not start: {e}")
                return

            app = QtWidgets.QApplication.instance()
            if app is None:
                app = QtWidgets.QApplication(["autostart_orchestrator"])

            window = _DashboardWindow(self._WORKFLOW_STATES)
            window.show()
            self._debug_panel_active = True

            def refresh() -> _DashboardPayload:
                payload: _DashboardPayload = (
                    self._workflow_state,
                    self._workflow_detail,
                    self._last_vehicle_state,
                    self._capture_started,
                    self._rosbag_proc is not None,
                    self._vehicle_label,
                    self._vehicle_state_topic,
                )
                while self._debug_panel_queue is not None:
                    try:
                        payload = self._debug_panel_queue.get_nowait()
                    except queue.Empty:
                        break
                return payload

            while not stop_event.is_set():
                if self._debug_panel_queue is not None:
                    latest = refresh()
                    window.update_state(*latest)
                app.processEvents()
                stop_event.wait(0.12)

            window.close()
            self._debug_panel_active = False
            self._debug_panel_queue = None
            app.quit()

        self._debug_panel_thread = threading.Thread(target=_run, args=(self._debug_panel_stop_event,), daemon=True)
        self._debug_panel_thread.start()

    def _stop_debug_visualization(self) -> None:
        self._debug_panel_active = False
        stop_event = self._debug_panel_stop_event
        if stop_event is not None:
            stop_event.set()
        thread = self._debug_panel_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._debug_panel_stop_event = None
        self._debug_panel_thread = None
        self._debug_panel_queue = None

    @property
    def exit_code(self) -> int:
        return int(self._exit_code)

    def _set_exit_code(self, code: int) -> None:
        code = int(code)
        if code and self._exit_code == 0:
            self._exit_code = code

    def _on_rclpy_shutdown(self) -> None:
        with self._cond:
            self._cond.notify_all()

    def _shutdown(self) -> None:
        if rclpy.ok():
            rclpy.shutdown()

    def _on_vehicle_state(self, msg: String) -> None:
        state = (msg.data or "").strip()
        if not state:
            return
        with self._cond:
            self._last_vehicle_state = state
            self._cond.notify_all()

    @staticmethod
    def _normalize_state(raw: Optional[str]) -> str:
        return "".join(ch for ch in (raw or "").strip().lower() if ch.isalnum())

    @staticmethod
    def _vehicle_label_from_domain_id(raw: str) -> str:
        value = (raw or "").strip()
        if value.isdigit():
            domain_id = int(value)
            if domain_id > 0:
                return f"d{domain_id}"
        return "d?"

    @staticmethod
    def _normalize_state_list(raw: str) -> list[str]:
        states: list[str] = []
        for item in (raw or "").split(","):
            state = item.strip()
            if state:
                states.append(state)
        return states

    def _state_matches(self, actual: Optional[str], expected_states: list[str]) -> bool:
        actual_norm = self._normalize_state(actual)
        if not actual_norm or not expected_states:
            return False

        for expected in expected_states:
            expected_norm = self._normalize_state(expected)
            if not expected_norm:
                continue
            if actual_norm == expected_norm:
                return True
            if expected_norm in {"finish", "finished"} and actual_norm in {"finishedall", "terminate", "terminated"}:
                return True
            if expected_norm.startswith("finish") and actual_norm.startswith("finish"):
                return True
        return False

    def _wait_for_vehicle_state(self, expected: str) -> tuple[bool, Optional[str]]:
        expected_states = self._normalize_state_list(expected)
        if not expected_states:
            return True, self._last_vehicle_state
        expected_label = ", ".join(expected_states)

        with self._cond:
            while rclpy.ok():
                if self._state_matches(self._last_vehicle_state, expected_states):
                    return True, self._last_vehicle_state
                self._cond.wait(timeout=1.0)
        return False, self._last_vehicle_state

    def _do_start_initialization(self, call_initial_pose: bool, request_control_mode: bool) -> None:
        if not (call_initial_pose or request_control_mode):
            self._set_workflow_state(
                self._STATE_REQUEST_CONTROL_MODE,
                "initial pose / control mode disabled",
            )
            return

        if call_initial_pose:
            self._set_workflow_state(
                self._STATE_WAIT_INITIAL_POSE,
                f"waiting service and calling {self.get_parameter('initial_pose_service').value}",
            )
            if self._wait_for_service(self._cli_initial_pose):
                ok, msg = self._call_trigger(self._cli_initial_pose)
                self.get_logger().info(f"initial pose: success={ok} msg={msg}")
            else:
                self.get_logger().warn("skip initial pose (service not found)")

        self._set_workflow_state(self._STATE_REQUEST_CONTROL_MODE, "initial pose completed")

        if request_control_mode:
            self._set_workflow_state(
                self._STATE_REQUEST_CONTROL_MODE,
                f"requesting control mode on {self.get_parameter('control_mode_request_topic').value}",
            )
            ok, msg = self._publish_control_mode()
            if ok:
                self.get_logger().info(f"control mode request: success={ok} msg={msg}")
            else:
                self.get_logger().warn(f"skip control mode request: {msg}")

        self._set_workflow_state(self._STATE_REQUEST_CONTROL_MODE, "initialization done")

    def _wait_for_service(self, client) -> bool:
        return client.wait_for_service()

    def _call_trigger(self, client) -> tuple[bool, str]:
        event = threading.Event()
        result: tuple[bool, str] = (False, "no_response")

        future = client.call_async(Trigger.Request())

        def _done(_fut) -> None:
            nonlocal result
            try:
                resp = _fut.result()
                result = (bool(resp.success), str(resp.message))
            except Exception as e:  # noqa: BLE001
                result = (False, f"exception: {e}")
            finally:
                event.set()

        future.add_done_callback(_done)
        event.wait()
        return result

    def _publish_control_mode(self) -> tuple[bool, str]:
        p = self.get_parameter
        topic = str(p("control_mode_request_topic").value)

        msg = Bool()
        msg.data = True
        self._pub_control_mode.publish(msg)
        return True, f"published to {topic} data={msg.data}"

    def _output_dir(self) -> Path:
        path = Path.cwd()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _rosbag_argv(self) -> list[str]:
        topics = [str(t).strip() for t in (self.get_parameter("rosbag_topics").value or []) if str(t).strip()]
        if not topics:
            return []

        output = str(self.get_parameter("rosbag_output").value)
        storage_id = str(self.get_parameter("rosbag_storage_id").value)
        compression_format = str(self.get_parameter("rosbag_compression_format").value).strip()
        compression_mode = str(self.get_parameter("rosbag_compression_mode").value).strip()

        argv: list[str] = ["ros2", "bag", "record", *topics, "-o", output, "-s", storage_id]
        compression_enabled = bool(compression_format) and bool(compression_mode)
        compression_misconfigured = bool(compression_format) ^ bool(compression_mode)

        if compression_enabled:
            argv += ["--compression-format", compression_format, "--compression-mode", compression_mode]
        elif compression_misconfigured:
            self.get_logger().warn(
                "rosbag compression is partially configured; "
                f"format='{compression_format}' mode='{compression_mode}'. "
                "Skipping compression options."
            )

        return argv

    def _start_rosbag(self) -> None:
        if self._rosbag_proc is not None:
            return

        output_dir = self._output_dir()
        log_path = output_dir / "rosbag_autostart.log"
        argv = self._rosbag_argv()
        if not argv:
            self.get_logger().warn("skip rosbag start (no topics/argv configured)")
            return

        self.get_logger().info(f"start-rosbag: argv={argv} (cwd={output_dir}) -> {log_path}")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fp = open(log_path, "ab", buffering=0)  # noqa: SIM115
        self._rosbag_log_fp = log_fp
        try:
            # os.setsid and preexec_fn are only available/meaningful on POSIX systems.
            # Guard this so the code can be imported or run on non-Unix platforms.
            preexec_fn = os.setsid if hasattr(os, "setsid") else None
            self._rosbag_proc = subprocess.Popen(
                argv,
                cwd=str(output_dir),
                stdout=log_fp,
                stderr=subprocess.STDOUT,
                preexec_fn=preexec_fn,
            )
        except Exception:  # noqa: BLE001
            try:
                log_fp.close()
            finally:
                self._rosbag_log_fp = None
            raise

    def _stop_rosbag(self) -> None:
        proc = self._rosbag_proc
        log_fp = self._rosbag_log_fp
        if proc is None:
            return

        try:
            if proc.poll() is None:
                self.get_logger().info(f"stop-rosbag (SIGINT): pid={proc.pid}")
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGINT)
                except Exception:  # noqa: BLE001
                    proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=15.0)
                except subprocess.TimeoutExpired:
                    self.get_logger().warn("rosbag did not exit in time; sending SIGTERM")
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    except Exception:  # noqa: BLE001
                        proc.terminate()
                    proc.wait(timeout=10.0)
        finally:
            self._rosbag_proc = None
            self._rosbag_log_fp = None
            try:
                if log_fp is not None:
                    log_fp.close()
            except Exception:  # noqa: BLE001
                pass

    def _param_str(self, name: str, default: str = "") -> str:
        value = self.get_parameter(name).value
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default

    @staticmethod
    def _latest_existing(paths: list[Optional[Path]]) -> Optional[Path]:
        candidates = [path for path in paths if path is not None and path.is_file()]
        if not candidates:
            return None
        try:
            return max(candidates, key=lambda path: path.stat().st_mtime_ns)
        except Exception:
            return candidates[-1]

    def _motion_analytics_input_dir(self, output_dir: Path) -> Path:
        configured_input_dir = self._param_str("motion_analytics_input_dir")
        if configured_input_dir:
            return Path(configured_input_dir)

        rosbag_output = self._param_str("rosbag_output")
        return output_dir / rosbag_output if rosbag_output else output_dir

    def _run_motion_analytics(self) -> None:
        if not bool(self.get_parameter("enable_motion_analytics").value):
            return

        with self._motion_analytics_lock:
            if self._motion_analytics_run_once:
                return
            self._motion_analytics_run_once = True

        output_dir = self._output_dir()
        input_dir = self._motion_analytics_input_dir(output_dir)
        if not input_dir.exists():
            self.get_logger().warn(f"skip motion_analytics (input not found): {input_dir}")
            return

        cmd_raw = self._param_str("motion_analytics_cmd")
        if not cmd_raw:
            self.get_logger().warn("skip motion_analytics (motion_analytics_cmd is empty)")
            return

        try:
            cmd = shlex.split(cmd_raw)
        except ValueError as exc:
            self.get_logger().warn(f"skip motion_analytics (invalid motion_analytics_cmd): {exc}")
            return

        if not cmd:
            self.get_logger().warn("skip motion_analytics (motion_analytics_cmd is empty after parsing)")
            return

        argv = [*cmd, "--input", str(input_dir)]
        self.get_logger().info(f"run motion_analytics: argv={argv} (cwd={output_dir})")
        try:
            result = subprocess.run(
                argv,
                cwd=str(output_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warn(f"motion_analytics execution failed: {exc}")
            return

        if result.returncode != 0:
            output_tail = (result.stdout or "").strip()
            if output_tail:
                output_tail = output_tail[-2000:]
                self.get_logger().warn(
                    "motion_analytics exited with non-zero code: "
                    f"{result.returncode}; output_tail={output_tail!r}"
                )
            else:
                self.get_logger().warn(
                    f"motion_analytics exited with non-zero code: {result.returncode}"
                )
            return

        self.get_logger().info("motion_analytics completed successfully")

    def _stop_rosbag_with_postprocess(self, enable_motion_analytics: bool) -> None:
        had_rosbag = self._rosbag_proc is not None
        self._stop_rosbag()
        if had_rosbag and enable_motion_analytics:
            self._set_workflow_state(self._STATE_POST_PROCESS, "running motion_analytics")
            self._run_motion_analytics()

    @staticmethod
    def _latest_file_by_pattern(base_dir: Path, pattern: str) -> Optional[Path]:
        if not base_dir.exists():
            return None
        try:
            matches = [path for path in base_dir.glob(pattern) if path.is_file()]
        except Exception:
            return None
        if not matches:
            return None
        try:
            return max(matches, key=lambda path: path.stat().st_mtime_ns)
        except Exception:
            return matches[-1]

    @staticmethod
    def _replace_symlink(link_path: Path, target_path: Path) -> bool:
        try:
            if link_path.exists() and not link_path.is_symlink():
                return False
            if link_path.is_symlink():
                link_path.unlink()
            rel_target = os.path.relpath(str(target_path), str(link_path.parent))
            link_path.symlink_to(rel_target)
            return True
        except Exception:
            return False

    def _ensure_latest_root(self, output_dir: Path) -> Optional[Path]:
        run_dir = output_dir.parent
        output_root = run_dir.parent

        latest_path = output_root / "latest"
        try:
            if latest_path.is_symlink():
                latest_path.unlink()
            elif latest_path.exists() and not latest_path.is_dir():
                self.get_logger().warn(
                    f"skip latest artifact links (latest is not a directory): {latest_path}"
                )
                return None
            latest_path.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warn(f"failed to prepare latest directory: {exc}")
            return None
        return latest_path

    def _link_if_found(self, link_path: Path, target_path: Optional[Path], label: str) -> None:
        if target_path is None or not target_path.exists():
            self.get_logger().warn(f"skip latest link for {label} (target not found)")
            return
        link_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._replace_symlink(link_path, target_path):
            if link_path.exists() and not link_path.is_symlink():
                self.get_logger().info(
                    f"keep existing regular file for {label}: {link_path}"
                )
            else:
                self.get_logger().warn(
                    f"failed to create latest link for {label}: {link_path} -> {target_path}"
                )
            return
        self.get_logger().info(f"latest link updated: {link_path} -> {target_path}")

    def _resolve_result_details_target(
        self,
        output_dir: Path,
        run_dir: Path,
        vehicle_dir_name: str,
    ) -> Optional[Path]:
        result_file_name = f"{vehicle_dir_name}-result-details.json"
        return self._latest_existing([output_dir / result_file_name, run_dir / result_file_name])

    def _resolve_rosbag_target(self, output_dir: Path) -> Optional[Path]:
        rosbag_output = self._param_str("rosbag_output", "rosbag2_autoware")
        rosbag_dir = output_dir / rosbag_output
        return self._latest_existing(
            [
                rosbag_dir / f"{rosbag_output}_0.mcap",
                rosbag_dir / f"{rosbag_output}_0.mcap.zstd",
                self._latest_file_by_pattern(rosbag_dir, "*.mcap"),
                self._latest_file_by_pattern(rosbag_dir, "*.mcap.zstd"),
            ]
        )

    def _refresh_latest_artifact_links(self) -> None:
        output_dir = self._output_dir()
        with self._latest_link_lock:
            latest_root = self._ensure_latest_root(output_dir)
            if latest_root is None:
                return

            vehicle_dir_name = output_dir.name if output_dir.name.startswith("d") else self._vehicle_label
            latest_vehicle_dir = latest_root / vehicle_dir_name
            latest_vehicle_dir.mkdir(parents=True, exist_ok=True)

            run_dir = output_dir.parent
            capture_target = self._latest_file_by_pattern(output_dir / "capture", "cap-*.mp4")
            rosbag_target = self._resolve_rosbag_target(output_dir)
            targets: list[tuple[str, Optional[Path]]] = [
                ("result-details.json", self._resolve_result_details_target(output_dir, run_dir, vehicle_dir_name)),
                ("capture.mp4", capture_target),
                ("rosbag2_autoware.mcap", rosbag_target),
                ("motion_analytics.html", self._latest_file_by_pattern(output_dir, "motion_analytics-*.html")),
            ]
            for file_name, target in targets:
                self._link_if_found(latest_vehicle_dir / file_name, target, file_name)

            # Legacy link cleanup.
            legacy_cap_link = latest_vehicle_dir / "cap.mp4"
            if legacy_cap_link.is_symlink():
                try:
                    legacy_cap_link.unlink()
                except Exception as exc:  # noqa: BLE001
                    self.get_logger().warn(f"failed to remove legacy link cap.mp4: {exc}")
            legacy_rosbag0_link = latest_vehicle_dir / "rosbag2_autoware_0.mcap"
            if legacy_rosbag0_link.is_symlink():
                try:
                    legacy_rosbag0_link.unlink()
                except Exception as exc:  # noqa: BLE001
                    self.get_logger().warn(f"failed to remove legacy link rosbag2_autoware_0.mcap: {exc}")
            legacy_rosbag_link = latest_vehicle_dir / "rosbag.mcap"
            if legacy_rosbag_link.is_symlink():
                try:
                    legacy_rosbag_link.unlink()
                except Exception as exc:  # noqa: BLE001
                    self.get_logger().warn(f"failed to remove legacy link rosbag.mcap: {exc}")

            self._link_if_found(latest_vehicle_dir / "autoware.log", output_dir / "autoware.log", "autoware.log")

    def _capture(self, start: bool) -> None:
        if start and self._capture_started:
            return
        if (not start) and (not self._capture_started):
            return

        if not self._wait_for_service(self._cli_capture):
            self.get_logger().warn(f"skip capture {'start' if start else 'stop'} (service not found)")
            if not start:
                self._capture_started = False
            return
        ok, msg = self._call_trigger(self._cli_capture)
        level = "info" if ok else "warn"
        getattr(self.get_logger(), level)(f"capture {'start' if start else 'stop'}: success={ok} msg={msg}")
        if ok:
            self._capture_started = bool(start)

    def _run(self) -> None:
        enable_capture = False
        enable_rosbag = False
        enable_motion_analytics = False
        try:
            self._set_workflow_state(self._STATE_BOOT, "worker started")

            call_initial_pose = bool(self.get_parameter("call_initial_pose").value)
            request_control_mode = bool(self.get_parameter("request_control_mode").value)
            enable_capture = bool(self.get_parameter("enable_capture").value)
            enable_rosbag = bool(self.get_parameter("enable_rosbag").value)
            enable_motion_analytics = bool(self.get_parameter("enable_motion_analytics").value)

            start_on = str(self.get_parameter("start_on_vehicle_state").value or "").strip()
            stop_on = str(self.get_parameter("stop_on_vehicle_state").value or "").strip()
            exit_on_finish = bool(self.get_parameter("exit_on_finish").value)
            if start_on:
                wait_targets = ", ".join(self._normalize_state_list(start_on))
                wait_msg = f"{wait_targets} on {self._vehicle_state_topic}" if wait_targets else self._vehicle_state_topic
                self._set_workflow_state(self._STATE_WAIT_START, f"waiting for {wait_msg}")
                self.get_logger().info(f"wait start: {wait_msg}")
                ok, last = self._wait_for_vehicle_state(start_on)
                if not ok:
                    self.get_logger().error(
                        f"failed waiting start: expected_vehicle={wait_targets or start_on} "
                        f"last={last}"
                    )
                    self._set_workflow_state(
                        self._STATE_ERROR,
                        f"failed waiting start: expected_vehicle={wait_targets or start_on} last={last}",
                    )
                    self._set_exit_code(2)
                    self._shutdown()
                    return

                if last is not None:
                    self.get_logger().info(f"start condition met: {self._vehicle_state_topic} == {last}")
            else:
                self._set_workflow_state(self._STATE_WAIT_START, "start_on_vehicle_state is empty; start immediately")
                self.get_logger().info("start_on_vehicle_state is empty; starting immediately")

            self._do_start_initialization(call_initial_pose, request_control_mode)

            if not (enable_capture or enable_rosbag):
                self._set_workflow_state(self._STATE_RUNNING, "running without recording")
                self.get_logger().info("capture/rosbag are disabled; running without recording")

                if stop_on:
                    ok, last = self._wait_for_vehicle_state(stop_on)
                    if ok:
                        self._set_workflow_state(self._STATE_FINISHED, "stop condition met (no recording)")
                        if exit_on_finish:
                            self._shutdown()
                    else:
                        self._set_workflow_state(self._STATE_ERROR, f"failed waiting stop: expected={stop_on} last={last}")
                        self._set_exit_code(3)
                        self._shutdown()
                return

            self._set_workflow_state(self._STATE_RECORDING, "start condition met")

            if enable_capture:
                self._capture(True)
            if enable_rosbag:
                self._start_rosbag()
            self._set_workflow_state(self._STATE_RECORDING, "recording started")

            if not stop_on:
                self._set_workflow_state(self._STATE_AUTO_STOP_DISABLED, "stop_on_vehicle_state is empty")
                self.get_logger().info("stop_on_vehicle_state is empty; auto-stop is disabled (recording continues)")
                return

            self._set_workflow_state(self._STATE_WAIT_STOP, f"waiting for '{stop_on}' on {self._vehicle_state_topic}")
            self.get_logger().info(f"wait stop: {self._vehicle_state_topic} == {stop_on}")
            ok, last = self._wait_for_vehicle_state(stop_on)
            if not ok:
                self.get_logger().error(f"failed waiting stop: expected={stop_on} last={last}")
                self._set_workflow_state(self._STATE_STOPPING, f"stop wait failed: expected={stop_on} last={last}")
                if enable_rosbag:
                    self._stop_rosbag_with_postprocess(enable_motion_analytics)
                if enable_capture:
                    self._capture(False)
                self._set_workflow_state(self._STATE_ERROR, f"failed waiting stop: expected={stop_on} last={last}")
                self._set_exit_code(3)
                self._shutdown()
                return

            self._set_workflow_state(self._STATE_STOPPING, "stopping capture/rosbag")
            if enable_rosbag:
                self._stop_rosbag_with_postprocess(enable_motion_analytics)
            if enable_capture:
                self._capture(False)

            self._set_workflow_state(self._STATE_FINISHED, "shutdown requested")
            if exit_on_finish:
                self._shutdown()
        except Exception as e:  # noqa: BLE001
            self.get_logger().error(f"unhandled exception in worker: {e}")
            self._set_workflow_state(self._STATE_ERROR, f"unhandled exception: {e}")
            try:
                if enable_rosbag:
                    self._stop_rosbag_with_postprocess(enable_motion_analytics)
            except Exception:  # noqa: BLE001
                pass
            try:
                if enable_capture:
                    self._capture(False)
            except Exception:  # noqa: BLE001
                pass
            self._set_exit_code(10)
            self._shutdown()
        finally:
            try:
                self._refresh_latest_artifact_links()
            except Exception as exc:  # noqa: BLE001
                self.get_logger().warn(f"failed to refresh latest artifact links: {exc}")

    def destroy_node(self) -> bool:
        try:
            self._stop_debug_visualization()
            enable_motion_analytics = bool(self.get_parameter("enable_motion_analytics").value)
            self._stop_rosbag_with_postprocess(enable_motion_analytics)
            self._capture(False)
        finally:
            return super().destroy_node()


def main() -> int:
    rclpy.init()
    node = AutostartOrchestrator()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info("KeyboardInterrupt received, shutting down node gracefully.")
    finally:
        exit_code = int(getattr(node, "exit_code", 0))
        worker = getattr(node, "_worker", None)
        try:
            node.destroy_node()
        finally:
            if rclpy.ok():
                rclpy.shutdown()
        if worker is not None and worker.is_alive():
            worker.join(timeout=5.0)
        exit_code = max(exit_code, int(getattr(node, "exit_code", 0)))
        return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
