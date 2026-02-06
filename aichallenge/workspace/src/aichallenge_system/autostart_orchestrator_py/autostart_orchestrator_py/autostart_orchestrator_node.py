#!/usr/bin/env python3

from __future__ import annotations

import os
import signal
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node
from rclpy.parameter import Parameter
from rcl_interfaces.msg import ParameterDescriptor
from std_msgs.msg import Bool
from std_msgs.msg import String
from std_srvs.srv import Trigger


class AutostartOrchestrator(Node):
    def __init__(self) -> None:
        super().__init__("autostart_orchestrator")

        default_vehicle_ns = "d1"
        ros_domain_id = os.environ.get("ROS_DOMAIN_ID", "").strip()
        if ros_domain_id.isdigit() and int(ros_domain_id) > 0:
            default_vehicle_ns = f"d{ros_domain_id}"
        elif ros_domain_id == "0":
            self.get_logger().warn(f"ROS_DOMAIN_ID is {ros_domain_id}; defaulting vehicle_ns to d1")

        str_arr_desc = ParameterDescriptor(type=Parameter.Type.STRING_ARRAY.value)
        for spec in [
            ("vehicle_ns", default_vehicle_ns),
            ("vehicle_state_topic", "/awsim/state"),
            # Default: start immediately (no wait).
            ("start_on_vehicle_state", ""),
            ("stop_on_vehicle_state", "Finish"),
            ("enable_capture", False),
            ("enable_rosbag", False),
            ("call_initial_pose", True),
            ("request_control_mode", True),
            ("initial_pose_service", "/set_initial_pose"),
            ("control_mode", 1),  # 1: AUTONOMOUS, 0: MANUAL
            ("control_mode_request_topic", "/awsim/control_mode_request_topic"),
            ("capture_service", "/debug/service/capture_screen"),
            ("wait_service_timeout_sec", 60),
            ("call_timeout_sec", 10),
            ("finish_wait_timeout_sec", 1800),
            ("fail_on_timeout", True),
            ("output_dir", ""),  # default: $OUTPUT_RUN_DIR or "."
            (
                "rosbag_topics",
                [
                    "/awsim/control_cmd",
                    "/clock",
                    "/localization/acceleration",
                    "/localization/kinematic_state",
                ],
            ),
            ("rosbag_output", "rosbag2_autoware"),
            ("rosbag_storage_id", "mcap"),
            ("rosbag_compression_format", "zstd"),
            ("rosbag_compression_mode", "file"),
            ("rosbag_extra_args", [], str_arr_desc),
            ("rosbag_argv_override", [], str_arr_desc),
            # Deprecated: kept for backward compatibility; parsed with shlex (no shell execution).
            ("rosbag_cmd", ""),
            ("rosbag_log_file", "rosbag_autostart.log"),
            ("exit_on_finish", True),
        ]:
            if len(spec) == 2:
                name, default = spec
                self.declare_parameter(name, default)
            else:
                name, default, desc = spec
                self.declare_parameter(name, default, desc)

        cbg = ReentrantCallbackGroup()

        vehicle_state_topic = str(self.get_parameter("vehicle_state_topic").value or "").strip()
        if not vehicle_state_topic:
            vehicle_state_topic = f"/awsim/state"
        self._vehicle_state_topic = vehicle_state_topic

        self._cond = threading.Condition()
        self._last_vehicle_state: Optional[str] = None

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

        self._exit_code = 0

        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

        self.get_logger().info(f"Subscribing vehicle state: {vehicle_state_topic}")

    @property
    def exit_code(self) -> int:
        return int(self._exit_code)

    def _set_exit_code(self, code: int) -> None:
        code = int(code)
        if code and self._exit_code == 0:
            self._exit_code = code

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

    def _wait_for_vehicle_state(self, expected: str, timeout_sec: int) -> tuple[bool, Optional[str]]:
        expected = (expected or "").strip()
        if not expected:
            return True, self._last_vehicle_state

        deadline = time.monotonic() + max(1, int(timeout_sec))
        with self._cond:
            while rclpy.ok():
                if self._last_vehicle_state == expected:
                    return True, self._last_vehicle_state
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False, self._last_vehicle_state
                self._cond.wait(timeout=min(0.5, remaining))
        return False, self._last_vehicle_state

    def _wait_for_service(self, client, name: str, timeout_sec: int) -> bool:
        deadline = time.monotonic() + max(1, int(timeout_sec))
        while rclpy.ok():
            if client.wait_for_service(timeout_sec=0.5):
                return True
            if time.monotonic() >= deadline:
                self.get_logger().warn(f"timeout waiting for service: {name} ({timeout_sec}s)")
                return False
        return False

    def _call_trigger(self, client, timeout_sec: int) -> tuple[bool, str]:
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
        event.wait(timeout=max(1, int(timeout_sec)))
        return result

    def _publish_control_mode(self) -> tuple[bool, str]:
        p = self.get_parameter
        mode = int(p("control_mode").value)
        topic = str(p("control_mode_request_topic").value)

        msg = Bool()
        if mode == 1:
            msg.data = True
        elif mode == 0:
            msg.data = False
        else:
            return False, f"unsupported_mode_for_topic: {mode}"

        self._pub_control_mode.publish(msg)
        return True, f"published to {topic} data={msg.data}"

    def _output_dir(self) -> Path:
        output_dir = str(self.get_parameter("output_dir").value or "").strip()
        if not output_dir:
            output_dir = os.environ.get("OUTPUT_RUN_DIR", ".")
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _rosbag_argv(self) -> list[str]:
        p = self.get_parameter
        argv_override = [str(x) for x in (p("rosbag_argv_override").value or []) if str(x).strip()]
        if argv_override:
            return argv_override

        rosbag_cmd = str(p("rosbag_cmd").value or "").strip()
        if rosbag_cmd:
            self.get_logger().warn("rosbag_cmd is deprecated; prefer rosbag_* parameters (executing without shell)")
            return shlex.split(rosbag_cmd)

        topics = [str(t).strip() for t in (p("rosbag_topics").value or []) if str(t).strip()]
        if not topics:
            return []

        output = str(p("rosbag_output").value)
        storage_id = str(p("rosbag_storage_id").value)
        compression_format = str(p("rosbag_compression_format").value)
        compression_mode = str(p("rosbag_compression_mode").value)
        extra_args = [str(x) for x in (p("rosbag_extra_args").value or []) if str(x).strip()]

        argv: list[str] = ["ros2", "bag", "record", *topics, "-o", output, "-s", storage_id]
        argv += ["--compression-format", compression_format, "--compression-mode", compression_mode, *extra_args]
        return argv

    def _start_rosbag(self) -> None:
        if self._rosbag_proc is not None:
            return

        output_dir = self._output_dir()
        log_path = output_dir / str(self.get_parameter("rosbag_log_file").value)
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

    def _capture(self, start: bool, wait_s: int, call_s: int) -> None:
        if start and self._capture_started:
            return
        if (not start) and (not self._capture_started):
            return

        name = str(self.get_parameter("capture_service").value)
        if not self._wait_for_service(self._cli_capture, name, wait_s):
            self.get_logger().warn(f"skip capture {'start' if start else 'stop'} (service not found)")
            if not start:
                self._capture_started = False
            return
        ok, msg = self._call_trigger(self._cli_capture, call_s)
        level = "info" if ok else "warn"
        getattr(self.get_logger(), level)(f"capture {'start' if start else 'stop'}: success={ok} msg={msg}")
        if ok:
            self._capture_started = bool(start)

    def _run(self) -> None:
        enable_capture = False
        enable_rosbag = False
        try:
            p = self.get_parameter
            wait_s = int(p("wait_service_timeout_sec").value)
            call_s = int(p("call_timeout_sec").value)
            finish_wait_s = int(p("finish_wait_timeout_sec").value)
            fail_on_timeout = bool(p("fail_on_timeout").value)

            call_initial_pose = bool(p("call_initial_pose").value)
            request_control_mode = bool(p("request_control_mode").value)
            enable_capture = bool(p("enable_capture").value)
            enable_rosbag = bool(p("enable_rosbag").value)

            start_on = str(p("start_on_vehicle_state").value or "").strip()
            stop_on = str(p("stop_on_vehicle_state").value or "").strip()
            exit_on_finish = bool(p("exit_on_finish").value)

            if call_initial_pose:
                name = str(p("initial_pose_service").value)
                if self._wait_for_service(self._cli_initial_pose, name, wait_s):
                    ok, msg = self._call_trigger(self._cli_initial_pose, call_s)
                    self.get_logger().info(f"initial pose: success={ok} msg={msg}")
                else:
                    self.get_logger().warn("skip initial pose (service not found)")

            if request_control_mode:
                ok, msg = self._publish_control_mode()
                if ok:
                    self.get_logger().info(f"control mode request: success={ok} msg={msg}")
                else:
                    self.get_logger().warn(f"skip control mode request: {msg}")

            if not (enable_capture or enable_rosbag):
                self.get_logger().info("capture/rosbag are disabled; orchestrator is idle")
                return

            if start_on:
                self.get_logger().info(f"wait start: {self._vehicle_state_topic} == {start_on} (timeout={finish_wait_s}s)")
                ok, last = self._wait_for_vehicle_state(start_on, finish_wait_s)
                if not ok:
                    self.get_logger().error(f"timeout waiting start: expected={start_on} last={last}")
                    if fail_on_timeout:
                        self._set_exit_code(2)
                        self._shutdown()
                        return
                    self.get_logger().warn("continuing despite start timeout (fail_on_timeout=false)")

            if enable_capture:
                self._capture(True, wait_s, call_s)
            if enable_rosbag:
                self._start_rosbag()

            if not stop_on:
                self.get_logger().info("stop_on_vehicle_state is empty; auto-stop is disabled (recording continues)")
                return

            self.get_logger().info(f"wait stop: {self._vehicle_state_topic} == {stop_on} (timeout={finish_wait_s}s)")
            ok, last = self._wait_for_vehicle_state(stop_on, finish_wait_s)
            if not ok:
                self.get_logger().error(f"timeout waiting stop: expected={stop_on} last={last}")
                if enable_rosbag:
                    self._stop_rosbag()
                if enable_capture:
                    self._capture(False, wait_s, call_s)
                if fail_on_timeout:
                    self._set_exit_code(3)
                    self._shutdown()
                return

            if enable_rosbag:
                self._stop_rosbag()
            if enable_capture:
                self._capture(False, wait_s, call_s)

            if exit_on_finish:
                self._shutdown()
        except Exception as e:  # noqa: BLE001
            self.get_logger().error(f"unhandled exception in worker: {e}")
            try:
                if enable_rosbag:
                    self._stop_rosbag()
            except Exception:  # noqa: BLE001
                pass
            try:
                if enable_capture:
                    wait_s = int(self.get_parameter("wait_service_timeout_sec").value)
                    call_s = int(self.get_parameter("call_timeout_sec").value)
                    self._capture(False, wait_s, call_s)
            except Exception:  # noqa: BLE001
                pass
            self._set_exit_code(10)
            self._shutdown()

    def destroy_node(self) -> bool:
        try:
            self._stop_rosbag()
            wait_s = int(self.get_parameter("wait_service_timeout_sec").value)
            call_s = int(self.get_parameter("call_timeout_sec").value)
            self._capture(False, wait_s, call_s)
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
        try:
            node.destroy_node()
        finally:
            if rclpy.ok():
                rclpy.shutdown()
        return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
