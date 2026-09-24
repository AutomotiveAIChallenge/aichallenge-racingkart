"""Synthetic, ROS-free fixture for MPC regression tests.

Builds a ring-shaped track (inner radius 26 m, outer 34 m) on an in-memory
occupancy grid and wires it to the real ``ReferencePath``, ``BicycleModel`` and
``MPC`` classes with the parameters of ``config/config.yaml``.  No files, no
rclpy -- only numpy / scipy / osqp / scikit-image, which the package already
requires.
"""
import math

import numpy as np
from scipy import sparse

from multi_purpose_mpc_ros.core.map import Map
from multi_purpose_mpc_ros.core.reference_path import ReferencePath
from multi_purpose_mpc_ros.core.spatial_bicycle_models import BicycleModel
from multi_purpose_mpc_ros.core.MPC import MPC

# Values mirrored from config/config.yaml
MAP_RES = 0.1
PATH_RES = 0.6
SMOOTHING = 2
MAX_WIDTH = 6.0
CAR_LENGTH = 1.087
CAR_WIDTH = 2.30
N = 20
Q = [3000000.0, 90000000.0, 100000.0]
R = [100000.0, 0.0]
QN = [1000000.0, 1000.0, 10000.0]
V_MAX = 20.0 / 3.6
A_MIN, A_MAX = -1.6, 0.7
AY_MAX = 6.5
DELTA_MAX = math.radians(32.0)
STEER_RATE = 0.35 / 1.639
CONTROL_RATE = 40.0
WP_ID_OFFSET = 2


def ring_map(r_in=26.0, r_out=34.0, size_m=80.0):
    """Occupancy grid with a free annulus; 1 = free, 0 = occupied."""
    m = Map.__new__(Map)  # bypass the yaml/pgm loader
    n = int(size_m / MAP_RES)
    m.resolution = MAP_RES
    m.origin = [-size_m / 2.0, -size_m / 2.0, 0.0]
    m.height = n
    m.width = n
    rows, cols = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
    x = cols * MAP_RES + m.origin[0]
    y = (n - 1 - rows) * MAP_RES + m.origin[1]
    r = np.hypot(x, y)
    m.data = ((r > r_in) & (r < r_out)).astype(np.int8)
    m.data_backup = m.data.copy()
    m.obstacles = []
    m.boundaries = []
    m.threshold_occupied = 0.65
    return m


def ring_path(track_map, radius=30.0, n_pts=96):
    t = np.linspace(0.0, 2.0 * math.pi, n_pts, endpoint=False)
    wp_x = list(radius * np.cos(t))
    wp_y = list(radius * np.sin(t))
    return ReferencePath(track_map, wp_x, wp_y, PATH_RES, SMOOTHING, MAX_WIDTH, True)


def make_mpc(**mpc_kwargs):
    track_map = ring_map()
    ref = ring_path(track_map)
    car = BicycleModel(ref, CAR_LENGTH, CAR_WIDTH, 1.0 / CONTROL_RATE)
    state_constraints = {
        "xmin": np.array([-np.inf, -np.inf, -np.inf]),
        "xmax": np.array([np.inf, np.inf, np.inf])}
    input_constraints = {
        "umin": np.array([0.0, -np.tan(DELTA_MAX) / CAR_LENGTH]),
        "umax": np.array([V_MAX, np.tan(DELTA_MAX) / CAR_LENGTH])}
    mpc = MPC(car, N, sparse.diags(Q), sparse.diags(R), sparse.diags(QN),
              state_constraints, input_constraints, AY_MAX, STEER_RATE,
              WP_ID_OFFSET, False, False, True, **mpc_kwargs)
    ref.compute_speed_profile({"a_min": A_MIN, "a_max": A_MAX, "v_min": 0.0,
                               "v_max": V_MAX, "ay_max": AY_MAX})
    return mpc


def place_car(mpc, wp_index, e_y, e_psi):
    """Put the car at lateral offset e_y [m] (left +) and heading error e_psi
    [rad] relative to waypoint ``wp_index``."""
    wp = mpc.model.reference_path.waypoints[wp_index]
    x = wp.x - e_y * math.sin(wp.psi)
    y = wp.y + e_y * math.cos(wp.psi)
    mpc.model.update_states(x, y, wp.psi + e_psi)


def corridor_rows(mpc):
    """Copy of the stored per-waypoint corridor tables (ub, lb)."""
    ub, lb = mpc.model.reference_path.path_constraints
    return ub.copy(), lb.copy()
