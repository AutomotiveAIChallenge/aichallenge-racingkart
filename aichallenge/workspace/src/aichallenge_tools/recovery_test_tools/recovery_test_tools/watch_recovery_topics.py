#!/usr/bin/env python3

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node

from std_msgs.msg import String


class RecoveryStateWatch(Node):
    """Print recovery supervisor state transitions for manual smoke checks."""

    def __init__(self) -> None:
        super().__init__("recovery_state_watch")
        self._last_state = ""
        self.create_subscription(String, "/recovery_supervisor/state", self._on_state, 1)

    def _on_state(self, msg: String) -> None:
        state = msg.data.strip()
        if not state or state == self._last_state:
            return
        self._last_state = state
        print(state, flush=True)


def main() -> None:
    rclpy.init()
    node = RecoveryStateWatch()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
