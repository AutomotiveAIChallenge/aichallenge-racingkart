"""BicycleModel.update_states must not re-localise onto another leg of the course.

``get_closest_waypoint`` took the argmin over the whole path every cycle.  On
the kashiwanoha preset the legs at s~146-167 m and s~277-298 m run
antiparallel ~3 m apart, so a 1.5 m lateral error made wp_id (and with it s,
the reference velocity and the MPC horizon) jump ~135 waypoints to the
opposite leg.  See ISSUE-06 / Zenn tamasy "MPC制御をやってみた" section 5.
"""
import math
import os

import numpy as np
import pytest

from multi_purpose_mpc_ros.core.map import Map
from multi_purpose_mpc_ros.core.reference_path import ReferencePath
from multi_purpose_mpc_ros.core.spatial_bicycle_models import BicycleModel
from multi_purpose_mpc_ros.core.utils import load_ref_path

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OFFSET = 1.5  # m, well inside GNSS/tracking error on a real kart


def _model(track):
    env = os.path.join(PKG_DIR, "env", track)
    track_map = Map(os.path.join(env, "occupancy_grid_map.yaml"))
    x, y, _, _ = load_ref_path(os.path.join(env, "traj_mincurv.csv"))
    ref = ReferencePath(track_map, x, y, 0.6, 2, 6.0, True)
    return BicycleModel(ref, 1.087, 2.30, 1.0 / 40.0)


def _xy(model):
    wps = model.reference_path.waypoints
    return np.array([w.x for w in wps]), np.array([w.y for w in wps])


def _global_argmin(model, x, y):
    X, Y = _xy(model)
    return int(np.argmin(np.hypot(X - x, Y - y)))


def _circ(a, b, n):
    d = abs(a - b) % n
    return min(d, n - d)


def _offset(wp, d):
    return wp.x - d * math.sin(wp.psi), wp.y + d * math.cos(wp.psi)


def _fold_back_case(model):
    """First waypoint whose OFFSET-m lateral pose is matched, by a whole-path
    argmin, to a waypoint more than 20 waypoints away."""
    wps = model.reference_path.waypoints
    n = len(wps)
    for k, wp in enumerate(wps):
        for side in (1.0, -1.0):
            px, py = _offset(wp, side * OFFSET)
            j = _global_argmin(model, px, py)
            if _circ(j, k, n) > 20:
                return k, wp, px, py, j
    pytest.fail(f"no fold-back jump at {OFFSET} m on kashiwanoha (geometry changed?)")


@pytest.fixture(scope="module")
def kashiwa():
    return _model("kashiwanoha")


def test_fold_back_geometry_exists(kashiwa):
    k, _, _, _, j = _fold_back_case(kashiwa)
    assert _circ(j, k, len(kashiwa.reference_path.waypoints)) > 100


def test_lateral_error_does_not_jump_to_the_other_leg():
    model = _model("kashiwanoha")
    k, wp, px, py, j = _fold_back_case(model)
    model.update_states(wp.x, wp.y, wp.psi)              # on the line
    model.update_states(px, py, wp.psi)                  # OFFSET m to the side
    n = len(model.reference_path.waypoints)
    assert _circ(model.wp_id, k, n) <= 5, f"jumped from wp {k} to wp {model.wp_id}"


def test_first_call_and_teleport_use_the_whole_path():
    model = _model("kashiwanoha")
    wps = model.reference_path.waypoints
    a, b = wps[40], wps[240]
    model.update_states(a.x, a.y, a.psi)
    assert model.wp_id == _global_argmin(model, a.x, a.y)
    model.update_states(b.x, b.y, b.psi)                 # far outside the window
    assert model.wp_id == _global_argmin(model, b.x, b.y)


def test_same_location_as_whole_path_on_the_default_track():
    """No behaviour change on citycircuit (final_ver3). The circular path
    repeats its closing point, so compare locations, not indices."""
    model = _model("final_ver3")
    wps = model.reference_path.waypoints
    for i in range(0, len(wps), 3):
        for off in (0.0, 1.0, -1.0):
            x, y = _offset(wps[i], off)
            model.update_states(x, y, wps[i].psi)
            g = wps[_global_argmin(model, x, y)]
            got = wps[model.wp_id]
            assert math.hypot(got.x - g.x, got.y - g.y) < 1e-9
