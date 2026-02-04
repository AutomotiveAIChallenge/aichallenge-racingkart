#!/usr/bin/env python3

from __future__ import annotations

import os
import signal
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
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


@dataclass
class _ServiceResult:
    success: bool
    message: str


class AutostartOrchestrator(Node):
    def __init__(self) -> None:
        super().__init__("autostart_orchestrator")

        autonomous_mode = 1
        manual_mode = 0

        ros_domain_id = os.environ.get("ROS_DOMAIN_ID", "").strip()
        default_vehicle_ns = "d1"
        if ros_domain_id.isdigit():
            if int(ros_domain_id) > 0:
                default_vehicle_ns = f"d{ros_domain_id}"
            else:
                self.get_logger().warn(f"ROS_DOMAIN_ID is {ros_domain_id}; defaulting vehicle_ns to d1")
        self.declare_parameter("vehicle_ns", default_vehicle_ns)
        self.declare_parameter("vehicle_state_topic", "")
        self.declare_parameter("start_on_vehicle_state", "TimingStart")
        self.declare_parameter("stop_on_vehicle_state", "Finish")

        self.declare_parameter("enable_capture", False)
        self.declare_parameter("enable_rosbag", False)

        self.declare_parameter("call_initial_pose", True)
        self.declare_parameter("request_control_mode", True)

        self.declare_parameter("initial_pose_service", "/set_initial_pose")
        self.declare_parameter("control_mode", autonomous_mode)  # 1: AUTONOMOUS, 0: MANUAL
        self.declare_parameter("control_mode_request_topic", "/awsim/control_mode_request_topic")
        self.declare_parameter("capture_service", "/debug/service/capture_screen")

        self.declare_parameter("wait_service_timeout_sec", 60)
        self.declare_parameter("call_timeout_sec", 10)
        self.declare_parameter("finish_wait_timeout_sec", 1800)

        self.declare_parameter("output_dir", "")  # default: $OUTPUT_RUN_DIR or "."
        # rosbag recording (no shell; executed via subprocess argv)
        self.declare_parameter(
            "rosbag_topics",
            [
                "/awsim/control_cmd",
                "/clock",
                "/localization/acceleration",
                "/localization/kinematic_state",
            ],
        )
        self.declare_parameter("rosbag_output", "rosbag2_autoware")
        self.declare_parameter("rosbag_storage_id", "mcap")
        self.declare_parameter("rosbag_compression_format", "zstd")
        self.declare_parameter("rosbag_compression_mode", "file")
        self.declare_parameter(
            "rosbag_extra_args",
            [],
            ParameterDescriptor(type=Parameter.Type.STRING_ARRAY.value),
        )
        # Optional: fully override argv (useful for tests / advanced usage).
        self.declare_parameter(
            "rosbag_argv_override",
            [],
            ParameterDescriptor(type=Parameter.Type.STRING_ARRAY.value),
        )
        # Deprecated: kept for backward compatibility; parsed with shlex (no shell execution).
        self.declare_parameter("rosbag_cmd", "")
        self.declare_parameter("rosbag_log_file", "rosbag_autostart.log")

        self.declare_parameter("exit_on_finish", True)

        cbg = ReentrantCallbackGroup()

        vehicle_ns = self.get_parameter("vehicle_ns").value
        vehicle_state_topic = (self.get_parameter("vehicle_state_topic").value or "").strip()
        if not vehicle_state_topic:
            vehicle_state_topic = f"/{vehicle_ns}/awsim/state"
        self._vehicle_state_topic = vehicle_state_topic

        self._cond = threading.Condition()
        self._last_vehicle_state: Optional[str] = None

        self._sub = self.create_subscription(String, vehicle_state_topic, self._on_vehicle_state, 10, callback_group=cbg)

        self._cli_initial_pose = self.create_client(Trigger, self.get_parameter("initial_pose_service").value, callback_group=cbg)
        self._cli_capture = self.create_client(Trigger, self.get_parameter("capture_service").value, callback_group=cbg)

        self._pub_control_mode = self.create_publisher(
            Bool, str(self.get_parameter("control_mode_request_topic").value), 1
        )

        self._capture_started = False
        self._rosbag_proc: Optional[subprocess.Popen] = None

        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

        self.get_logger().info(f"Subscribing vehicle state: {vehicle_state_topic}")

    def _on_vehicle_state(self, msg: String) -> None:
        state = (msg.data or "").strip()
        if not state:
            return
        with self._cond:
            self._last_vehicle_state = state
            self._cond.notify_all()

    def _wait_for_vehicle_state(self, expected: str, timeout_sec: int) -> Optional[str]:
        expected = (expected or "").strip()
        if not expected:
            return self._last_vehicle_state

        deadline = time.monotonic() + max(1, timeout_sec)
        with self._cond:
            while rclpy.ok():
                if self._last_vehicle_state == expected:
                    return self._last_vehicle_state
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._last_vehicle_state
                self._cond.wait(timeout=min(0.5, remaining))
        return self._last_vehicle_state

    def _wait_for_service(self, client, name: str, timeout_sec: int) -> bool:
        deadline = time.monotonic() + max(1, timeout_sec)
        while rclpy.ok():
            if client.wait_for_service(timeout_sec=0.5):
                return True
            if time.monotonic() >= deadline:
                self.get_logger().warn(f"timeout waiting for service: {name} ({timeout_sec}s)")
                return False
        return False

    def _call_trigger(self, client, name: str, timeout_sec: int) -> _ServiceResult:
        event = threading.Event()
        result: _ServiceResult = _ServiceResult(False, "no_response")

        future = client.call_async(Trigger.Request())

        def _done(_fut) -> None:
            nonlocal result
            try:
                resp = _fut.result()
                result = _ServiceResult(bool(resp.success), str(resp.message))
            except Exception as e:  # noqa: BLE001
                result = _ServiceResult(False, f"exception: {e}")
            finally:
                event.set()

        future.add_done_callback(_done)
        event.wait(timeout=max(1, timeout_sec))
        return result

    def _publish_control_mode(self) -> _ServiceResult:
        mode = int(self.get_parameter("control_mode").value)
        topic = str(self.get_parameter("control_mode_request_topic").value)

        msg = Bool()
        if mode == 1:
            msg.data = True
        elif mode == 0:
            msg.data = False
        else:
            return _ServiceResult(False, f"unsupported_mode_for_topic: {mode}")

        self._pub_control_mode.publish(msg)
        return _ServiceResult(True, f"published to {topic} data={msg.data}")

    def _output_dir(self) -> Path:
        output_dir = (self.get_parameter("output_dir").value or "").strip()
        if not output_dir:
            output_dir = os.environ.get("OUTPUT_RUN_DIR", ".")
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _rosbag_argv(self) -> list[str]:
        argv_override = list(self.get_parameter("rosbag_argv_override").value or [])
        argv_override = [str(x) for x in argv_override if str(x).strip()]
        if argv_override:
            return argv_override

        rosbag_cmd = str(self.get_parameter("rosbag_cmd").value or "").strip()
        if rosbag_cmd:
            self.get_logger().warn("rosbag_cmd is deprecated; prefer rosbag_* parameters (executing without shell)")
            return shlex.split(rosbag_cmd)

        topics = list(self.get_parameter("rosbag_topics").value or [])
        topics = [str(t).strip() for t in topics if str(t).strip()]
        if not topics:
            return []

        output = str(self.get_parameter("rosbag_output").value)
        storage_id = str(self.get_parameter("rosbag_storage_id").value)
        compression_format = str(self.get_parameter("rosbag_compression_format").value)
        compression_mode = str(self.get_parameter("rosbag_compression_mode").value)
        extra_args = list(self.get_parameter("rosbag_extra_args").value or [])
        extra_args = [str(x) for x in extra_args if str(x).strip()]

        argv: list[str] = ["ros2", "bag", "record"]
        argv.extend(topics)
        argv.extend(["-o", output, "-s", storage_id])
        argv.extend(["--compression-format", compression_format, "--compression-mode", compression_mode])
        argv.extend(extra_args)
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

        try:
            self._rosbag_proc = subprocess.Popen(
                argv,
                cwd=str(output_dir),
                stdout=log_fp,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid,
            )
        except Exception:  # noqa: BLE001
            log_fp.close()
            raise

    def _stop_rosbag(self) -> None:
        proc = self._rosbag_proc
        if proc is None:
            return

        try:
            if proc.poll() is not None:
                return

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

    def _start_capture(self) -> None:
        if self._capture_started:
            return
        name = str(self.get_parameter("capture_service").value)
        if not self._wait_for_service(self._cli_capture, name, int(self.get_parameter("wait_service_timeout_sec").value)):
            self.get_logger().warn("skip capture start (service not found)")
            return
        res = self._call_trigger(self._cli_capture, name, int(self.get_parameter("call_timeout_sec").value))
        self.get_logger().info(f"capture start: success={res.success} msg={res.message}")
        self._capture_started = True

    def _stop_capture(self) -> None:
        if not self._capture_started:
            return
        name = str(self.get_parameter("capture_service").value)
        if not self._wait_for_service(self._cli_capture, name, int(self.get_parameter("wait_service_timeout_sec").value)):
            self.get_logger().warn("skip capture stop (service not found)")
            self._capture_started = False
            return
        res = self._call_trigger(self._cli_capture, name, int(self.get_parameter("call_timeout_sec").value))
        self.get_logger().info(f"capture stop: success={res.success} msg={res.message}")
        self._capture_started = False

    def _run(self) -> None:
        wait_s = int(self.get_parameter("wait_service_timeout_sec").value)
        call_s = int(self.get_parameter("call_timeout_sec").value)
        finish_wait_s = int(self.get_parameter("finish_wait_timeout_sec").value)

        call_initial_pose = bool(self.get_parameter("call_initial_pose").value)
        request_control_mode = bool(self.get_parameter("request_control_mode").value)
        enable_capture = bool(self.get_parameter("enable_capture").value)
        enable_rosbag = bool(self.get_parameter("enable_rosbag").value)

        start_on = str(self.get_parameter("start_on_vehicle_state").value)
        stop_on = str(self.get_parameter("stop_on_vehicle_state").value)
        exit_on_finish = bool(self.get_parameter("exit_on_finish").value)

        if call_initial_pose:
            name = str(self.get_parameter("initial_pose_service").value)
            if self._wait_for_service(self._cli_initial_pose, name, wait_s):
                res = self._call_trigger(self._cli_initial_pose, name, call_s)
                self.get_logger().info(f"initial pose: success={res.success} msg={res.message}")
            else:
                self.get_logger().warn("skip initial pose (service not found)")

        if request_control_mode:
            res = self._publish_control_mode()
            if res.success:
                self.get_logger().info(f"control mode request: success={res.success} msg={res.message}")
            else:
                self.get_logger().warn(f"skip control mode request: {res.message}")

        if not (enable_capture or enable_rosbag):
            return

        if start_on.strip():
            self.get_logger().info(
                f"wait start: {self._vehicle_state_topic} == {start_on} (timeout={finish_wait_s}s)"
            )
            self._wait_for_vehicle_state(start_on, finish_wait_s)

        if enable_capture:
            self._start_capture()
        if enable_rosbag:
            self._start_rosbag()

        if stop_on.strip():
            self.get_logger().info(f"wait stop: {self._vehicle_state_topic} == {stop_on} (timeout={finish_wait_s}s)")
            self._wait_for_vehicle_state(stop_on, finish_wait_s)

        if enable_rosbag:
            self._stop_rosbag()
        if enable_capture:
            self._stop_capture()

        if exit_on_finish and rclpy.ok():
            rclpy.shutdown()

    def destroy_node(self) -> bool:
        try:
            self._stop_rosbag()
            self._stop_capture()
        finally:
            return super().destroy_node()


def main() -> None:
    rclpy.init()
    node = AutostartOrchestrator()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if rclpy.ok():
            node.destroy_node()
            rclpy.shutdown()


if __name__ == "__main__":
    main()
