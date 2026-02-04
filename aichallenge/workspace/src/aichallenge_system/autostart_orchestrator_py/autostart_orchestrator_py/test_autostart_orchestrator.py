#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import Bool
from std_msgs.msg import String
from std_srvs.srv import Trigger


@dataclass
class _Counts:
    initial_pose: int = 0
    capture: int = 0
    control_mode_msgs: int = 0


class _Harness(Node):
    def __init__(self, vehicle_state_topic: str) -> None:
        super().__init__("autostart_orchestrator_test_harness")

        self.counts = _Counts()
        self._counts_lock = threading.Lock()
        self._vehicle_state_topic = vehicle_state_topic

        self._pub_state = self.create_publisher(String, vehicle_state_topic, 10)
        self._sub_control_mode = self.create_subscription(
            Bool, "/awsim/control_mode_request_topic", self._on_control_mode, 10
        )

        self._initial_pose_svc = self.create_service(Trigger, "/set_initial_pose", self._on_initial_pose)
        self._capture_svc = self.create_service(Trigger, "/debug/service/capture_screen", self._on_capture)

        self._evt_initial_pose_called = threading.Event()
        self._evt_capture_called_once = threading.Event()
        self._evt_capture_called_twice = threading.Event()
        self._evt_control_mode_received = threading.Event()

    def _on_initial_pose(self, _req: Trigger.Request, resp: Trigger.Response) -> Trigger.Response:
        with self._counts_lock:
            self.counts.initial_pose += 1
        self._evt_initial_pose_called.set()
        resp.success = True
        resp.message = "ok"
        return resp

    def _on_capture(self, _req: Trigger.Request, resp: Trigger.Response) -> Trigger.Response:
        with self._counts_lock:
            self.counts.capture += 1
            capture_calls = self.counts.capture
        if capture_calls >= 1:
            self._evt_capture_called_once.set()
        if capture_calls >= 2:
            self._evt_capture_called_twice.set()
        resp.success = True
        resp.message = "ok"
        return resp

    def _on_control_mode(self, _msg: Bool) -> None:
        with self._counts_lock:
            self.counts.control_mode_msgs += 1
        self._evt_control_mode_received.set()

    def publish_state(self, state: str) -> None:
        msg = String()
        msg.data = state
        self._pub_state.publish(msg)


def _require_ros2() -> None:
    if shutil.which("ros2") is None:
        raise RuntimeError("ros2 command not found. Run this inside the container or source ROS 2 environment.")


def _run_orchestrator(
    *,
    params_file: Path,
) -> subprocess.Popen:
    cmd = [
        "ros2",
        "run",
        "autostart_orchestrator_py",
        "autostart_orchestrator_node.py",
        "--ros-args",
        "--params-file",
        str(params_file),
    ]

    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    env["ROS_LOG_DIR"] = os.environ.get("ROS_LOG_DIR", "/tmp")

    return subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test for autostart_orchestrator_py.")
    parser.add_argument("--vehicle-state-topic", default="/test/awsim/state")
    parser.add_argument("--start-on", default="TimingStart")
    parser.add_argument("--stop-on", default="Finish")
    parser.add_argument("--timeout-sec", type=int, default=20)
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()

    timeout_sec = max(5, int(args.timeout_sec))
    output_dir = (args.output_dir or "").strip()
    if output_dir:
        out_dir = Path(output_dir)
    else:
        out_dir = Path("/tmp") / f"autostart_orchestrator_smoketest_{os.getpid()}"

    _require_ros2()

    os.environ.setdefault("ROS_LOG_DIR", f"/tmp/ros_log_autostart_orchestrator_test_{os.getpid()}")
    Path(os.environ["ROS_LOG_DIR"]).mkdir(parents=True, exist_ok=True)

    rclpy.init()
    harness = _Harness(args.vehicle_state_topic)
    executor = MultiThreadedExecutor()
    executor.add_node(harness)

    spin_thread = threading.Thread(target=executor.spin, daemon=True)
    spin_thread.start()

    proc: subprocess.Popen | None = None
    output_buf: list[str] = []
    output_lock = threading.Lock()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        fake_rosbag_code = "\n".join(
            [
                "import signal",
                "import time",
                "import sys",
                "",
                "def _handler(_sig, _frm):",
                "    print('FAKE_ROSBAG: exiting', flush=True)",
                "    raise SystemExit(0)",
                "",
                "signal.signal(signal.SIGINT, _handler)",
                "signal.signal(signal.SIGTERM, _handler)",
                "print('FAKE_ROSBAG: started', flush=True)",
                "while True:",
                "    time.sleep(1.0)",
            ]
        )

        params_file = out_dir / "autostart_orchestrator_test_params.yaml"
        wait_s = min(5, max(1, timeout_sec))
        params_file.write_text(
            "\n".join(
                [
                    "/**:",
                    "  ros__parameters:",
                    f"    vehicle_state_topic: \"{args.vehicle_state_topic}\"",
                    f"    start_on_vehicle_state: \"{args.start_on}\"",
                    f"    stop_on_vehicle_state: \"{args.stop_on}\"",
                    "    enable_capture: true",
                    "    enable_rosbag: true",
                    "    call_initial_pose: true",
                    "    request_control_mode: true",
                    f"    wait_service_timeout_sec: {wait_s}",
                    "    call_timeout_sec: 3",
                    f"    finish_wait_timeout_sec: {timeout_sec}",
                    f"    output_dir: \"{str(out_dir)}\"",
                    "    rosbag_log_file: \"rosbag_test.log\"",
                    "    exit_on_finish: true",
                    "    rosbag_argv_override:",
                    "      - python3",
                    "      - -c",
                    "      - |",
                    *[f"          {line}" for line in fake_rosbag_code.splitlines()],
                    "",
                ]
            ),
            encoding="utf-8",
        )

        proc = _run_orchestrator(params_file=params_file)

        t0 = time.monotonic()

        def timed_out() -> bool:
            return time.monotonic() - t0 > timeout_sec

        def read_output() -> None:
            if proc is None or proc.stdout is None:
                return
            for line in proc.stdout:
                with output_lock:
                    output_buf.append(line)

        reader_thread = threading.Thread(target=read_output, daemon=True)
        reader_thread.start()

        if not harness._evt_initial_pose_called.wait(timeout=5.0):
            raise RuntimeError("initial pose service was not called")

        if not harness._evt_control_mode_received.wait(timeout=5.0):
            raise RuntimeError("control mode request topic was not observed")

        while not harness._evt_capture_called_once.is_set() and proc.poll() is None and not timed_out():
            harness.publish_state(args.start_on)
            time.sleep(0.2)

        if not harness._evt_capture_called_once.is_set():
            raise RuntimeError("capture service was not called (start phase)")

        while not harness._evt_capture_called_twice.is_set() and proc.poll() is None and not timed_out():
            harness.publish_state(args.stop_on)
            time.sleep(0.2)

        if not harness._evt_capture_called_twice.is_set():
            raise RuntimeError("capture service was not called (stop phase)")

        if proc.poll() is None:
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                raise RuntimeError("orchestrator did not exit after stop phase") from None

        rc = int(proc.returncode or 0)
        if rc != 0:
            raise RuntimeError(f"orchestrator exited with non-zero code: {rc}")

        log_path = out_dir / "rosbag_test.log"
        if not log_path.exists():
            raise RuntimeError(f"rosbag log was not created: {log_path}")
        if "FAKE_ROSBAG: started" not in log_path.read_text(errors="replace"):
            raise RuntimeError("rosbag did not appear to start (missing marker in log)")

        with harness._counts_lock:
            counts = harness.counts
        print("PASS")
        print(f"  initial_pose_calls={counts.initial_pose}")
        print(f"  capture_calls={counts.capture}")
        print(f"  control_mode_msgs={counts.control_mode_msgs}")
        print(f"  rosbag_log={log_path}")
        return 0
    except Exception as e:
        print("FAIL")
        print(f"  error={e}")
        with output_lock:
            tail = output_buf[-50:]
        if tail:
            print("  orchestrator_output_tail:")
            for line in tail:
                sys.stdout.write(f"    {line}")
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        return 1
    finally:
        try:
            executor.shutdown()
        finally:
            harness.destroy_node()
            rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
