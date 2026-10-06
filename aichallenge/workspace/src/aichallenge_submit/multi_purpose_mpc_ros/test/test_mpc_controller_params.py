"""The ``v_max`` runtime parameter is in km/h; ``MPCConfig.v_max`` is m/s.

At start-up ``MPCConfig.v_max = kmh_to_m_per_sec(cfg.mpc.v_max)``, but the
parameter callback stored the raw km/h value.  ``_control()`` then clamps every
tick with ``min(kmh_to_m_per_sec(ref_vel), self._mpc_cfg.v_max)``
(mpc_controller.py:812), so after ``ros2 param set /mpc_controller v_max 10.0``
the cap became 10 m/s (36 km/h) and the kart ran at the 30 km/h ref_vel
instead of 10 km/h: lowering v_max made it faster.
"""
import os

import pytest
import yaml
from scipy import sparse

import ros_stubs

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ros_stubs.install(PKG_DIR)

from multi_purpose_mpc_ros import mpc_controller as mc  # noqa: E402
from multi_purpose_mpc_ros.common import convert_to_namedtuple  # noqa: E402
from multi_purpose_mpc_ros.core.utils import kmh_to_m_per_sec  # noqa: E402


class _FakeMPC:
    v_max = None

    def update_v_max(self, v):
        self.v_max = v


class _FakeRefPath:
    def __init__(self, n=5):
        self.waypoints = [object()] * n
        self.v_ref = None

    def set_v_ref(self, v_ref):
        self.v_ref = list(v_ref)


def _controller():
    with open(os.path.join(PKG_DIR, "config", "config.yaml")) as f:
        cfg = convert_to_namedtuple(yaml.safe_load(f))
    ctrl = mc.MPCController.__new__(mc.MPCController)
    ros_stubs.Node.__init__(ctrl, "mpc_controller")
    m = cfg.mpc
    ctrl._cfg = cfg
    ctrl._mpc_cfg = mc.MPCConfig(
        m.N, sparse.diags(m.Q), sparse.diags(m.R), sparse.diags(m.QN),
        kmh_to_m_per_sec(m.v_max), m.a_min, m.a_max, m.ay_max, 0.5,
        m.steer_rate_max, m.control_rate, m.steering_tire_angle_gain_var,
        m.accel_low_pass_gain, m.steer_low_pass_gain, m.wp_id_offset,
        m.use_max_kappa_pred)
    ctrl._mpc = _FakeMPC()
    ctrl._reference_path = _FakeRefPath()
    ctrl._setup_parameters_callback()
    return ctrl, ctrl._stub_param_callbacks[-1]


def _set(cb, name, value):
    P = ros_stubs.Parameter
    return cb([P(name, P.Type.DOUBLE, value)])


def test_startup_value_is_m_per_s():
    ctrl, _ = _controller()
    assert ctrl._mpc_cfg.v_max == pytest.approx(20.0 / 3.6)


def test_runtime_v_max_is_stored_in_the_same_unit_as_at_startup():
    ctrl, cb = _controller()
    _set(cb, "v_max", 10.0)
    assert ctrl._mpc_cfg.v_max == pytest.approx(10.0 / 3.6)
    assert ctrl._mpc.v_max == pytest.approx(10.0 / 3.6)
    assert ctrl._reference_path.v_ref == [pytest.approx(10.0 / 3.6)] * 5


def test_lowering_v_max_lowers_the_per_tick_ref_vel_cap():
    ctrl, cb = _controller()
    _set(cb, "v_max", 10.0)
    ref_vel_kmph_section = 30.0              # config/ref_vel.yaml, most sections
    # the per-tick clamp in MPCController._control (mpc_controller.py:812)
    cap = min(kmh_to_m_per_sec(ref_vel_kmph_section), ctrl._mpc_cfg.v_max)
    assert cap == pytest.approx(10.0 / 3.6)   # 10 km/h, not 30 km/h
