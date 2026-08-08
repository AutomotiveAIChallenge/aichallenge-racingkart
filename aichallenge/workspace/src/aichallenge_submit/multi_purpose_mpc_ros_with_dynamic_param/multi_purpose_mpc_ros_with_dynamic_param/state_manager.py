#!/usr/bin/env python3
"""State-machine manager for driving mode transitions.

Responsibilities
----------------
* Holds the registry of all ``DrivingState`` instances.
* Evaluates transition conditions every control tick.
* Enforces a **minimum dwell-time** to prevent chattering (except for
  ``Recovery``, which is always allowed immediately).
* Publishes the current state name on a ROS 2 topic for debug / RViz.
* Logs every transition at WARN level for post-mortem analysis.
"""

from typing import Dict, Optional

from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSDurabilityPolicy, QoSReliabilityPolicy
from std_msgs.msg import String

from multi_purpose_mpc_ros_with_dynamic_param.states import (
    DrivingState,
    FollowPathState,
    RecoveryState,
    FollowState,
    OvertakeState,
    StateContext,
    MPCStateParams,
)


class StateManager:
    """Drives the state machine each control tick."""

    # seconds — minimum time in any state before a non-Recovery transition
    MIN_DWELL_TIME = 1.0

    def __init__(self, node: Node) -> None:
        self._node = node
        self._logger = node.get_logger()

        # --- state registry ---------------------------------------------------
        self._states: Dict[str, DrivingState] = {
            "follow_path": FollowPathState(),
            "recovery": RecoveryState(),
            "follow": FollowState(),
            "overtake": OvertakeState(),
        }

        self._current: DrivingState = self._states["follow_path"]
        self._last_transition_time: Optional[float] = None

        # --- ROS 2 publisher for current state name (Transient Local for topic echo) ---
        state_qos = QoSProfile(
            depth=1,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
            reliability=QoSReliabilityPolicy.RELIABLE,
        )
        self._state_pub = node.create_publisher(String, "/mpc/driving_state", state_qos)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    @property
    def current_state(self) -> DrivingState:
        return self._current

    @property
    def current_state_name(self) -> str:
        return self._current.name

    @property
    def current_gear(self) -> int:
        return self._current.gear

    def update(self, ctx: StateContext) -> Optional[MPCStateParams]:
        """Evaluate transitions and return *new* params if state changed.

        Returns
        -------
        ``MPCStateParams`` when a transition occurred (caller must apply
        them to the MPC), or ``None`` when the state is unchanged.
        """
        next_name = self._current.check_transition(ctx)

        if next_name is None or next_name == self._current.name:
            self._publish_state()
            return None

        # Enforce minimum dwell-time (Recovery is exempt)
        if next_name != "recovery" and self._last_transition_time is not None:
            dwell = ctx.current_time_sec - self._last_transition_time
            if dwell < self.MIN_DWELL_TIME:
                self._publish_state()
                return None

        # Validate target state exists
        next_state = self._states.get(next_name)
        if next_state is None:
            self._logger.error(
                f"StateManager: unknown target state '{next_name}', staying in '{self._current.name}'"
            )
            return None

        # --- perform transition ------------------------------------------------
        prev_name = self._current.name
        self._current.on_exit(ctx)
        self._current = next_state
        self._current.on_enter(ctx)
        self._last_transition_time = ctx.current_time_sec

        self._logger.info(
            f"[StateManager] {prev_name} → {self._current.name}"
        )
        self._publish_state()

        return self._current.get_params()

    def get_control_override(self, ctx: StateContext):
        """Delegate to the current state's control override."""
        return self._current.compute_control_override(ctx)

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _publish_state(self) -> None:
        msg = String()
        msg.data = self._current.name
        self._state_pub.publish(msg)
