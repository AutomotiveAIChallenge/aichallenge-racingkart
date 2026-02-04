#!/usr/bin/env python3

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional
import threading
import time

import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from std_msgs.msg import String
from std_srvs.srv import Trigger

try:
    from awsim_state_manager_py.srv import WaitForState
except ImportError:  # pragma: no cover
    WaitForState = None


@dataclass(frozen=True)
class StateEvent:
    stamp_ns: int
    state: str


class AwsimStateManager(Node):
    def __init__(self) -> None:
        super().__init__("awsim_state_manager")

        self.declare_parameter("topic", "/admin/awsim/state")
        self.declare_parameter("history_size", 1000)
        self.declare_parameter("log_transitions", True)

        topic = self.get_parameter("topic").get_parameter_value().string_value
        history_size = int(self.get_parameter("history_size").get_parameter_value().integer_value)
        self._log_transitions = bool(self.get_parameter("log_transitions").get_parameter_value().bool_value)

        self._events: Deque[StateEvent] = deque(maxlen=max(1, history_size))
        self._last_state: Optional[str] = None
        self._cond = threading.Condition()

        qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )

        cbg = ReentrantCallbackGroup()
        self._sub = self.create_subscription(String, topic, self._on_state, qos, callback_group=cbg)
        self._srv_get = self.create_service(Trigger, "~/get", self._on_get, callback_group=cbg)
        self._srv_wait = None
        if WaitForState is not None:
            self._srv_wait = self.create_service(WaitForState, "~/wait", self._on_wait, callback_group=cbg)
            self.get_logger().info("Service: ~wait (awsim_state_manager_py/srv/WaitForState) blocks until expected state")
        else:
            self.get_logger().warn("WaitForState srv is not available (did you build/install the workspace?)")

        self.get_logger().info(f"Subscribing: {topic} (transient_local/reliable)")
        self.get_logger().info("Service: ~get (std_srvs/Trigger) returns last known /admin/awsim/state")

    def _on_state(self, msg: String) -> None:
        state = (msg.data or "").strip()
        if not state:
            return

        is_transition = state != self._last_state
        self._last_state = state
        self._events.append(StateEvent(self.get_clock().now().nanoseconds, state))

        with self._cond:
            self._cond.notify_all()

        if self._log_transitions and is_transition:
            self.get_logger().info(f"admin_state={state}")

    def _on_get(self, request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        _ = request
        if self._last_state is None:
            response.success = False
            response.message = "no_state_received_yet"
            return response

        response.success = True
        response.message = self._last_state
        return response

    def _on_wait(self, request: "WaitForState.Request", response: "WaitForState.Response") -> "WaitForState.Response":
        expected = [s.strip() for s in request.expected_states if (s or "").strip()]
        expected_lower = {s.lower() for s in expected}

        timeout_sec = int(request.timeout_sec)
        if timeout_sec <= 0:
            timeout_sec = 60

        deadline = time.monotonic() + timeout_sec
        with self._cond:
            while True:
                state = self._last_state
                if state is not None and (not expected_lower or state.lower() in expected_lower):
                    response.success = True
                    response.state = state
                    response.message = "ok"
                    return response

                left = deadline - time.monotonic()
                if left <= 0:
                    response.success = False
                    response.state = state or ""
                    response.message = "timeout"
                    return response

                self._cond.wait(timeout=min(0.5, left))


def main() -> None:
    rclpy.init()
    node = AwsimStateManager()
    try:
        executor = MultiThreadedExecutor(num_threads=2)
        executor.add_node(node)
        executor.spin()
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
