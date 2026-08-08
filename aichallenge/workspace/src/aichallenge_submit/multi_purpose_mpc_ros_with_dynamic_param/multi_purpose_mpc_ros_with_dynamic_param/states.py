#!/usr/bin/env python3
"""Driving state definitions for the State pattern.

Each concrete state defines:
  - MPC parameters (v_max, ay_max, Q, R, QN, etc.)
  - Transition conditions to other states
  - Optional control override (e.g., Recovery bypasses MPC)

Phase 1: FollowPath + Recovery
Phase 2: Follow + Overtake
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
import numpy as np

try:
    from autoware_auto_vehicle_msgs.msg import GearCommand
    GEAR_DRIVE = GearCommand.DRIVE
    GEAR_REVERSE = GearCommand.REVERSE
except ImportError:
    GEAR_DRIVE = 2
    GEAR_REVERSE = 20

# ---------------------------------------------------------------------------
# Stuck detection constants (shared by all states)
# ---------------------------------------------------------------------------
STUCK_VELOCITY_THRESHOLD = 0.3  # [m/s] — below this is considered "stopped"
STUCK_DURATION = 8.0            # [s] — stopped 8s triggers recovery (prevents startup false-alarm)


# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------

@dataclass
class MPCStateParams:
    """MPC parameters that each state provides."""
    v_max: float          # [km/h]
    ay_max: float         # [m/s^2]
    Q: List[float]        # [e_y, e_psi, t] — 3 elements
    R: List[float]        # [v, delta] — 2 elements
    QN: List[float]       # [e_y, e_psi, t] — 3 elements
    lateral_offset: float = 0.0  # [m] for overtake maneuver


@dataclass
class StateContext:
    """Snapshot of sensor / vehicle data consumed by state transition logic."""

    # --- Time ---------------------------------------------------------------
    current_time_sec: float   # ROS clock time [s]
    dt: float                 # time since last control loop [s]

    # --- Vehicle state ------------------------------------------------------
    pose_x: float             # [m]
    pose_y: float             # [m]
    pose_theta: float         # yaw [rad]
    velocity: float           # longitudinal speed [m/s]

    # --- Collision ----------------------------------------------------------
    is_colliding: bool
    time_since_collision: Optional[float] = None  # [s], None if no collision

    # --- Path ---------------------------------------------------------------
    path_deviation: float = 0.0  # lateral distance from reference path [m]
    path_psi: float = 0.0        # closest waypoint orientation [rad]
    path_e_y: float = 0.0        # signed lateral offset from path centerline [m]

    # --- V2X (Phase 2) ------------------------------------------------------
    forward_vehicle_distance: Optional[float] = None   # [m]
    forward_vehicle_speed: Optional[float] = None       # [m/s]

    # --- LiDAR (Phase 2) ----------------------------------------------------
    overtake_width_left: float = 0.0   # available width on left [m]
    overtake_width_right: float = 0.0  # available width on right [m]

    # --- Stuck detection ----------------------------------------------------
    time_stopped_sec: float = 0.0  # duration velocity ≈ 0 [s]
    is_in_recovery_cooldown: bool = False  # True during cooldown after recovery / startup


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class DrivingState(ABC):
    """Base class for all driving states (State pattern)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name identifying this state."""
        ...

    @property
    def gear(self) -> int:
        """Gear command for this state (GEAR_DRIVE or GEAR_REVERSE)."""
        return GEAR_DRIVE

    @abstractmethod
    def get_params(self) -> MPCStateParams:
        """Return the MPC parameters for this state."""
        ...

    @abstractmethod
    def check_transition(self, ctx: StateContext) -> Optional[str]:
        """Return the name of the next state, or ``None`` to stay."""
        ...

    def compute_control_override(
        self, ctx: StateContext
    ) -> Optional[Tuple[float, float, float]]:
        """Override MPC control output if this state needs direct actuation.

        Returns
        -------
        (speed [m/s], steer [rad], acceleration [m/s^2]) or ``None``
        to use normal MPC output.
        """
        return None

    def on_enter(self, ctx: StateContext) -> None:
        """Called once when entering this state."""
        pass

    def on_exit(self, ctx: StateContext) -> None:
        """Called once when leaving this state."""
        pass


# ---------------------------------------------------------------------------
# Phase 1 states
# ---------------------------------------------------------------------------

class FollowPathState(DrivingState):
    """Normal path-following — no obstacles or vehicles ahead.

    Target speed: 35 km/h (~9.7 m/s)
    """

    # ---- hardcoded parameters -----------------------------------------------
    V_MAX = 35.0              # [km/h]
    AY_MAX = 9.5              # [m/s^2]
    Q = [1_000_000.0, 100_000_000.0, 850_000.0]
    R = [100_000.0, 100.0]  # R[1]=1000.0 を追加してステアリング微振動を抑止
    QN = [1_000_000.0, 1_000.0, 10_000.0]

    # ---- forward-vehicle detection thresholds -------------------------------
    VEHICLE_DETECT_DISTANCE = 15.0   # [m]
    VEHICLE_WIDTH_WITH_MARGIN = 2.30 # vehicle width + safety margin [m]

    @property
    def name(self) -> str:
        return "follow_path"

    def get_params(self) -> MPCStateParams:
        return MPCStateParams(
            v_max=self.V_MAX,
            ay_max=self.AY_MAX,
            Q=list(self.Q),
            R=list(self.R),
            QN=list(self.QN),
        )

    def check_transition(self, ctx: StateContext) -> Optional[str]:
        # Collision → Recovery (always immediate)
        if ctx.is_colliding:
            return "recovery"

        # Stuck detection: velocity near zero for too long → Recovery (unless in cooldown)
        if (not ctx.is_in_recovery_cooldown
                and ctx.time_stopped_sec >= STUCK_DURATION
                and ctx.velocity < STUCK_VELOCITY_THRESHOLD):
            return "recovery"

        # Phase 2: forward-vehicle detection
        if ctx.forward_vehicle_distance is not None:
            if ctx.forward_vehicle_distance < self.VEHICLE_DETECT_DISTANCE:
                max_side = max(ctx.overtake_width_left, ctx.overtake_width_right)
                if max_side > self.VEHICLE_WIDTH_WITH_MARGIN:
                    return "overtake"
                else:
                    return "follow"

        return None


class RecoveryState(DrivingState):
    """Recovery after collision: wait → back up → rejoin path.

    Sequence
    --------
    1. **wait** (``WAIT_DURATION`` seconds): full stop.
    2. **back**: reverse at gentle speed (gear=REVERSE) until path deviation is small enough
       or ``MAX_BACK_DURATION`` elapses.
    3. Transition to ``follow_path``.
    """

    WAIT_DURATION = 2.0          # [s] (停止待機)
    BACK_SPEED = -2.5            # [m/s] (後退速度)
    BACK_ACCEL = 2.5             # [m/s^2] (正の絶対値でスロットルを要求)
    MIN_BACK_DURATION = 1.5      # [s] (最低1.5秒はバックを継続してチャタリング防止)
    MAX_BACK_DURATION = 3.5      # [s] (最大バック時間)
    PATH_DEVIATION_THRESHOLD = 2.0  # [m] — threshold to rejoin

    def __init__(self) -> None:
        self._enter_time: Optional[float] = None
        self._phase: str = "wait"          # "wait" | "back"

    @property
    def name(self) -> str:
        return "recovery"

    @property
    def gear(self) -> int:
        if self._phase == "back":
            return GEAR_REVERSE
        return GEAR_DRIVE

    def get_params(self) -> MPCStateParams:
        # Use a slow / conservative preset while recovering
        return MPCStateParams(
            v_max=18.0,  # 5 m/s = 18 km/h
            ay_max=3.0,
            Q=[5_000_000.0, 100_000_000.0, 200_000.0],
            R=[100_000.0, 0.0],
            QN=[1_000_000.0, 1_000.0, 10_000.0],
        )

    # -- lifecycle -----------------------------------------------------------

    def on_enter(self, ctx: StateContext) -> None:
        self._enter_time = ctx.current_time_sec
        self._phase = "wait"

    def on_exit(self, ctx: StateContext) -> None:
        self._enter_time = None
        self._phase = "wait"

    # -- transitions ---------------------------------------------------------

    def check_transition(self, ctx: StateContext) -> Optional[str]:
        if self._enter_time is None:
            return None

        elapsed = ctx.current_time_sec - self._enter_time

        if self._phase == "wait":
            if elapsed >= self.WAIT_DURATION:
                self._phase = "back"
            return None  # stay in recovery while waiting

        # phase == "back"
        back_elapsed = elapsed - self.WAIT_DURATION

        # Enforce minimum back duration to prevent instant exit / chattering
        if back_elapsed < self.MIN_BACK_DURATION:
            return None

        if ctx.path_deviation < self.PATH_DEVIATION_THRESHOLD:
            return "follow_path"
        if back_elapsed >= self.MAX_BACK_DURATION:
            return "follow_path"

        return None

    # -- control override ----------------------------------------------------

    def compute_control_override(
        self, ctx: StateContext
    ) -> Optional[Tuple[float, float, float]]:
        if self._phase == "wait":
            return (0.0, 0.0, 0.0)  # full stop

        # Steering while reversing: turn nose to face parallel + 10 deg towards the raceline
        TARGET_ANGLE_OFFSET = np.deg2rad(10.0)  # 10 degrees in radians
        if ctx.path_e_y >= 0:
            # Vehicle is to the left of the path -> point nose right (-10 deg relative to path)
            target_psi = ctx.path_psi - TARGET_ANGLE_OFFSET
        else:
            # Vehicle is to the right of the path -> point nose left (+10 deg relative to path)
            target_psi = ctx.path_psi + TARGET_ANGLE_OFFSET

        # Normalized yaw error [-pi, pi]
        psi_err = (ctx.pose_theta - target_psi + np.pi) % (2 * np.pi) - np.pi

        # P-control for reverse steering
        # psi_err > 0 (nose too far left) -> turn right (steer > 0) to rotate nose right
        K_P = 1.2
        steer_cmd = float(np.clip(K_P * psi_err, -0.55, 0.55))

        return (self.BACK_SPEED, steer_cmd, self.BACK_ACCEL)


# ---------------------------------------------------------------------------
# Phase 2 states
# ---------------------------------------------------------------------------

class FollowState(DrivingState):
    """Follow a leading vehicle — maintain safe distance (10m), match speed."""

    TARGET_FOLLOWING_DISTANCE = 10.0  # [m]
    FOLLOWING_KP = 0.5                # speed adjustment gain

    # ---- hardcoded parameters (35 km/h ceiling) ----------------------------
    _V_MAX_DEFAULT = 35.0   # [km/h] — ceiling, actual v_max is dynamic
    AY_MAX = 9.5
    Q = [5_000_000.0, 100_000_000.0, 200_000.0]
    R = [100_000.0, 1_000.0]
    QN = [1_000_000.0, 1_000.0, 10_000.0]

    VEHICLE_DETECT_DISTANCE = 15.0
    VEHICLE_WIDTH_WITH_MARGIN = 2.30

    @property
    def name(self) -> str:
        return "follow"

    def get_params(self) -> MPCStateParams:
        return MPCStateParams(
            v_max=self._V_MAX_DEFAULT,
            ay_max=self.AY_MAX,
            Q=list(self.Q),
            R=list(self.R),
            QN=list(self.QN),
        )

    def check_transition(self, ctx: StateContext) -> Optional[str]:
        if ctx.is_colliding:
            return "recovery"

        # Stuck detection
        if (not ctx.is_in_recovery_cooldown
                and ctx.time_stopped_sec >= STUCK_DURATION
                and ctx.velocity < STUCK_VELOCITY_THRESHOLD):
            return "recovery"

        # No forward vehicle any more → back to follow_path
        if ctx.forward_vehicle_distance is None:
            return "follow_path"
        if ctx.forward_vehicle_distance >= self.VEHICLE_DETECT_DISTANCE:
            return "follow_path"

        # Overtake became possible
        max_side = max(ctx.overtake_width_left, ctx.overtake_width_right)
        if max_side > self.VEHICLE_WIDTH_WITH_MARGIN:
            return "overtake"

        return None

    # -- dynamic v_max -------------------------------------------------------

    def get_adjusted_v_max_kmh(self, ctx: StateContext) -> float:
        """Compute dynamic v_max [km/h] to maintain following distance (10m)."""
        if ctx.forward_vehicle_distance is None or ctx.forward_vehicle_speed is None:
            return self._V_MAX_DEFAULT

        distance_error = ctx.forward_vehicle_distance - self.TARGET_FOLLOWING_DISTANCE
        target_mps = ctx.forward_vehicle_speed + self.FOLLOWING_KP * distance_error
        target_kmh = target_mps * 3.6
        # 最低速度を下限 20.0 km/h に設定し、極端な徐行・停滞を防止
        return float(np.clip(target_kmh, 20.0, self._V_MAX_DEFAULT))


class OvertakeState(DrivingState):
    """Overtake a slower vehicle by inducing a lateral offset."""

    LATERAL_OFFSET = 1.8  # [m] (壁に接触しない安全な横オフセット幅)
    MAX_OVERTAKE_DURATION = 2.5  # [s] (追い越し動作の最大存続時間)

    # ---- hardcoded parameters (35 km/h) ------------------------------------
    V_MAX = 35.0              # [km/h]
    AY_MAX = 9.5
    Q = [1_000_000.0, 100_000_000.0, 850_000.0]
    R = [100_000.0, 0.0]
    QN = [1_000_000.0, 1_000.0, 10_000.0]

    VEHICLE_DETECT_DISTANCE = 15.0

    def __init__(self) -> None:
        self._overtake_side: str = "left"  # "left" or "right"
        self._enter_time: Optional[float] = None
        self._calculated_offset: float = 1.8

    @property
    def name(self) -> str:
        return "overtake"

    def get_params(self) -> MPCStateParams:
        return MPCStateParams(
            v_max=self.V_MAX,
            ay_max=self.AY_MAX,
            Q=list(self.Q),
            R=list(self.R),
            QN=list(self.QN),
            lateral_offset=self._calculated_offset,
        )

    def on_enter(self, ctx: StateContext) -> None:
        self._enter_time = ctx.current_time_sec
        # 空きスペースの中央 (幅 / 2.0) を通る動的オフセット算出
        if ctx.overtake_width_left >= ctx.overtake_width_right:
            self._overtake_side = "left"
            half_w = ctx.overtake_width_left / 2.0
            self._calculated_offset = float(np.clip(half_w, 1.2, 2.2))
        else:
            self._overtake_side = "right"
            half_w = ctx.overtake_width_right / 2.0
            self._calculated_offset = -float(np.clip(half_w, 1.2, 2.2))

    def on_exit(self, ctx: StateContext) -> None:
        self._enter_time = None

    def check_transition(self, ctx: StateContext) -> Optional[str]:
        if ctx.is_colliding:
            return "recovery"

        # Stuck detection
        if (not ctx.is_in_recovery_cooldown
                and ctx.time_stopped_sec >= STUCK_DURATION
                and ctx.velocity < STUCK_VELOCITY_THRESHOLD):
            return "recovery"

        # Overtake timeout: return to follow_path after 2.5s for smooth raceline return
        if self._enter_time is not None:
            elapsed = ctx.current_time_sec - self._enter_time
            if elapsed >= self.MAX_OVERTAKE_DURATION:
                return "follow_path"

        # Vehicle cleared
        if ctx.forward_vehicle_distance is None:
            return "follow_path"
        if ctx.forward_vehicle_distance >= self.VEHICLE_DETECT_DISTANCE:
            return "follow_path"

        return None
