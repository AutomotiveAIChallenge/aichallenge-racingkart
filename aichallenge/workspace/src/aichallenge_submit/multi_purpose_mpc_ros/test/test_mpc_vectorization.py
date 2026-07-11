import numpy as np
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_linearize_batch_matches_scalar():
    from multi_purpose_mpc_ros.core.spatial_bicycle_models import BicycleModel
    rng = np.random.default_rng(0)
    v = np.concatenate([rng.uniform(0.1, 10, 50), [0.0]])
    k = rng.uniform(-0.5, 0.5, 51)
    ds = rng.uniform(0.3, 0.9, 51)
    f_b, A_b, B_b = BicycleModel.linearize_batch(None, v, k, ds)
    for i in range(51):
        f, A, B = BicycleModel.linearize(None, v[i], k[i], ds[i])
        assert np.array_equal(f, f_b[i]) and np.array_equal(A, A_b[i]) and np.array_equal(B, B_b[i])


def test_update_v_max_skips_cache_clear_when_unchanged():
    from multi_purpose_mpc_ros.core.MPC import MPC

    mpc = MPC.__new__(MPC)
    mpc.input_constraints = {'umax': np.array([5.0, 1.0])}
    mpc._bounds_template_cache = {'sentinel': object()}

    # Same value -> cache must be left untouched.
    mpc.update_v_max(5.0)
    assert 'sentinel' in mpc._bounds_template_cache
    assert mpc.input_constraints['umax'][0] == 5.0

    # Different value -> cache must be cleared and value updated.
    mpc.update_v_max(7.5)
    assert mpc._bounds_template_cache == {}
    assert mpc.input_constraints['umax'][0] == 7.5
