#!/usr/bin/env python3

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from typing import Optional

import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node
from std_msgs.msg import String


class AwsimStateManager(Node):
    """Manage evaluation shutdown from AWSIM admin/vehicle state transitions.

    The manager runs on ROS_DOMAIN_ID=0 and coordinates process cleanup in phases:
    1) stop autostart orchestrators first (graceful SIGINT/SIGTERM/SIGKILL)
    2) stop extra recorder-related processes
    3) stop remaining AWSIM/Autoware processes
    """

    def __init__(self) -> None:
        super().__init__("awsim_state_manager")

        defaults = [
            ("admin_state_topic", "/admin/awsim/state"),
            ("vehicle_state_topics", "/d1/awsim/state"),
            ("required_ready_count", 1),
            ("ready_states", "Ready"),
            ("finish_states", "FinishALL,Terminate"),
            ("ready_wait_timeout_sec", 60),
            ("finish_wait_timeout_sec", 600),
            ("shutdown_grace_sec", 2),
            ("kill_wait_sec", 10),
            ("orchestrator_shutdown_wait_sec", 2),
            (
                "orchestrator_kill_patterns",
                "autostart_orchestrator_node.py,autostart_orchestrator",
            ),
            (
                "kill_patterns",
                "AWSIM.x86_64,"
                "component_container,"
                "domain_bridge,"
                "rviz2,"
                "relay"
            ),
            ("shutdown_extra_patterns", "ros2 bag record"),
            ("exit_on_finish", True),
            ("fail_on_timeout", False),
            ("shutdown_on_exit", False),
        ]

        for key, default in defaults:
            self.declare_parameter(key, default)

        self._cbg = ReentrantCallbackGroup()

        self._admin_state_topic = str(self.get_parameter("admin_state_topic").value or "").strip()
        if not self._admin_state_topic:
            self._admin_state_topic = "/admin/awsim/state"

        self._vehicle_state_topics = self._split_csv(str(self.get_parameter("vehicle_state_topics").value))

        ros_domain_id = os.environ.get("ROS_DOMAIN_ID", "").strip()
        if ros_domain_id and ros_domain_id != "0":
            self.get_logger().warn(
                "awsim_state_manager is expected on ROS_DOMAIN_ID=0, "
                f"but got ROS_DOMAIN_ID={ros_domain_id}"
            )

        self._cond = threading.Condition()
        self._last_admin_state: Optional[str] = None
        self._vehicle_last_state: dict[str, str] = {topic: "" for topic in self._vehicle_state_topics}

        self._shutdown_started = False
        self._shutdown_reason: Optional[str] = None
        self._exit_code = 0

        self._admin_sub = self.create_subscription(
            String,
            self._admin_state_topic,
            self._on_admin_state,
            10,
            callback_group=self._cbg,
        )

        self._vehicle_subs = []
        for topic in self._vehicle_state_topics:
            sub = self.create_subscription(
                String,
                topic,
                lambda msg, t=topic: self._on_vehicle_state(t, msg),
                10,
                callback_group=self._cbg,
            )
            self._vehicle_subs.append(sub)

        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

        self.get_logger().info(f"admin state topic: {self._admin_state_topic}")
        if self._vehicle_state_topics:
            self.get_logger().info(f"vehicle state topics: {self._vehicle_state_topics}")
        else:
            self.get_logger().warn("vehicle_state_topics is empty; vehicle Ready gating is disabled")

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

    def _on_admin_state(self, msg: String) -> None:
        state = (msg.data or "").strip()
        if not state:
            return

        with self._cond:
            changed = state != self._last_admin_state
            self._last_admin_state = state
            self._cond.notify_all()

        if changed:
            self.get_logger().info(f"admin state: {state}")

    def _on_vehicle_state(self, topic: str, msg: String) -> None:
        state = (msg.data or "").strip()
        if not state:
            return

        with self._cond:
            before = self._vehicle_last_state.get(topic, "")
            changed = state != before
            self._vehicle_last_state[topic] = state
            self._cond.notify_all()

        if changed:
            self.get_logger().info(f"vehicle state: topic={topic} state={state}")

    def _wait_until(self, predicate, timeout_sec: int) -> bool:
        deadline = time.monotonic() + max(1, int(timeout_sec))
        with self._cond:
            while rclpy.ok():
                if predicate():
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(timeout=min(0.5, remaining))
        return False

    def _wait_for_admin_ready(self, timeout_sec: int) -> tuple[bool, Optional[str]]:
        ok = self._wait_until(lambda: self._last_admin_state is not None, timeout_sec)
        return ok, self._last_admin_state

    def _ready_vehicle_count(self, ready_states: set[str]) -> int:
        return sum(1 for state in self._vehicle_last_state.values() if state in ready_states)

    def _wait_for_vehicle_ready(
        self,
        ready_states: set[str],
        required_ready_count: int,
        timeout_sec: int,
    ) -> tuple[bool, int, dict[str, str]]:
        required = max(0, int(required_ready_count))
        ok = self._wait_until(
            lambda: self._ready_vehicle_count(ready_states) >= required,
            timeout_sec,
        )
        with self._cond:
            snapshot = dict(self._vehicle_last_state)
        return ok, self._ready_vehicle_count(ready_states), snapshot

    def _wait_for_finish_state(self, finish_states: set[str], timeout_sec: int) -> tuple[bool, Optional[str]]:
        ok = self._wait_until(lambda: self._last_admin_state in finish_states, timeout_sec)
        return ok, self._last_admin_state

    @staticmethod
    def _is_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except Exception:
            return False

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
            os.killpg(os.getpgid(pid), sig)
            return True
        except Exception:
            try:
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

    def _stop_process(self, pid: int) -> None:
        if pid == os.getpid() or not self._is_alive(pid):
            return

        grace = max(0, self._parse_int("shutdown_grace_sec", 2))
        kill_wait = max(1, self._parse_int("kill_wait_sec", 10))

        for sig in (signal.SIGINT, signal.SIGTERM):
            if not self._is_alive(pid):
                return
            self.get_logger().warning(f"stopping pid={pid} with {self._signal_name(sig)}")
            if not self._send_signal(pid, sig):
                return
            if self._wait_for_exit(pid, kill_wait):
                return
            if sig == signal.SIGINT and grace > 0:
                time.sleep(grace)

        if self._is_alive(pid):
            self.get_logger().warning(f"forcing pid={pid} with SIGKILL")
            self._send_signal(pid, signal.SIGKILL)
            self._wait_for_exit(pid, kill_wait)

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

    def _start_shutdown(self) -> None:
        with self._cond:
            if self._shutdown_started:
                return
            self._shutdown_started = True

        reason = self._shutdown_reason or "unknown"
        self.get_logger().warn(f"shutdown sequence started (reason={reason})")

        orchestrator_patterns = self._split_csv(str(self.get_parameter("orchestrator_kill_patterns").value))
        self._kill_by_patterns(orchestrator_patterns, "phase1-orchestrator")

        orchestrator_wait = max(0, self._parse_int("orchestrator_shutdown_wait_sec", 2))
        if orchestrator_wait > 0:
            time.sleep(orchestrator_wait)

        extra_patterns = self._split_csv(str(self.get_parameter("shutdown_extra_patterns").value))
        self._kill_by_patterns(extra_patterns, "phase2-extra")

        kill_patterns = self._split_csv(str(self.get_parameter("kill_patterns").value))
        self._kill_by_patterns(kill_patterns, "phase3-main")

        if bool(self.get_parameter("exit_on_finish").value):
            self.get_logger().info("exit_on_finish=true; shutting down ROS")
            if rclpy.ok():
                rclpy.shutdown()

        if bool(self.get_parameter("shutdown_on_exit").value):
            os._exit(0)

    def _run(self) -> None:
        ready_timeout = max(1, self._parse_int("ready_wait_timeout_sec", 60))
        finish_timeout = max(1, self._parse_int("finish_wait_timeout_sec", 600))
        fail_on_timeout = bool(self.get_parameter("fail_on_timeout").value)

        required_ready_count = max(0, self._parse_int("required_ready_count", 1))
        ready_states = set(self._split_csv(str(self.get_parameter("ready_states").value)))
        finish_states = set(self._split_csv(str(self.get_parameter("finish_states").value)))

        if required_ready_count > len(self._vehicle_state_topics):
            self.get_logger().warn(
                "required_ready_count exceeds configured topics; "
                f"clamping {required_ready_count} -> {len(self._vehicle_state_topics)}"
            )
            required_ready_count = len(self._vehicle_state_topics)

        self.get_logger().info(
            f"wait admin readiness: topic={self._admin_state_topic} timeout={ready_timeout}s"
        )
        ok, last_admin = self._wait_for_admin_ready(ready_timeout)
        if not ok:
            self.get_logger().warn(
                f"timeout waiting admin readiness: topic={self._admin_state_topic} last={last_admin or 'none'}"
            )
            if fail_on_timeout:
                self._set_exit_code(2)
                self._shutdown_reason = "admin_ready_timeout"
                self._start_shutdown()
            return

        if required_ready_count > 0:
            if not ready_states:
                self.get_logger().warn("ready_states is empty; skipping vehicle Ready gating")
            else:
                self.get_logger().info(
                    "wait vehicle readiness: "
                    f"states={sorted(ready_states)} required={required_ready_count}/{len(self._vehicle_state_topics)} "
                    f"timeout={ready_timeout}s"
                )
                ok, count, snapshot = self._wait_for_vehicle_ready(
                    ready_states,
                    required_ready_count,
                    ready_timeout,
                )
                if not ok:
                    self.get_logger().warn(
                        "timeout waiting vehicle readiness: "
                        f"ready_count={count}/{required_ready_count} states={snapshot}"
                    )
                    if fail_on_timeout:
                        self._set_exit_code(3)
                        self._shutdown_reason = "vehicle_ready_timeout"
                        self._start_shutdown()
                        return
                else:
                    self.get_logger().info(
                        f"vehicle readiness reached: ready_count={count}/{required_ready_count}"
                    )

        if not finish_states:
            self.get_logger().warn("finish_states is empty; manager will not auto-shutdown")
            return

        self.get_logger().info(
            f"wait finish: topic={self._admin_state_topic} states={sorted(finish_states)} timeout={finish_timeout}s"
        )
        ok, last_admin = self._wait_for_finish_state(finish_states, finish_timeout)
        if ok:
            self._shutdown_reason = f"admin_state={last_admin}"
            self._start_shutdown()
            return

        self.get_logger().error(
            f"timeout waiting finish state: expected={sorted(finish_states)} last={last_admin or 'none'}"
        )
        if fail_on_timeout:
            self._set_exit_code(4)
            self._shutdown_reason = "finish_wait_timeout"
            self._start_shutdown()

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
