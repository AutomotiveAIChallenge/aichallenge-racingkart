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


def test_update_v_max_skips_write_when_unchanged():
    from multi_purpose_mpc_ros.core.MPC import MPC

    mpc = MPC.__new__(MPC)
    mpc.input_constraints = {'umax': np.array([5.0, 1.0])}
    mpc._bounds_template_cache = {'sentinel': object()}

    # Same value -> update_v_max is a no-op; it no longer clears the cache
    # itself (staleness is detected fetch-side in _get_bounds_template()).
    mpc.update_v_max(5.0)
    assert 'sentinel' in mpc._bounds_template_cache
    assert mpc.input_constraints['umax'][0] == 5.0

    # Different value -> the input constraint is updated but the cache dict
    # is untouched by update_v_max itself.
    mpc.update_v_max(7.5)
    assert 'sentinel' in mpc._bounds_template_cache
    assert mpc.input_constraints['umax'][0] == 7.5


def test_get_bounds_template_rebuilds_on_value_change_and_reuses_when_unchanged():
    from multi_purpose_mpc_ros.core.MPC import MPC

    mpc = MPC.__new__(MPC)
    mpc.state_constraints = {'xmin': np.array([-1.0, -2.0, -3.0]),
                              'xmax': np.array([1.0, 2.0, 3.0])}
    mpc.input_constraints = {'umin': np.array([0.0, -0.5]),
                              'umax': np.array([5.0, 0.5])}
    mpc._bounds_template_cache = {}

    N = 4
    first = mpc._get_bounds_template(N)
    assert np.allclose(first[3][0::2], 5.0)

    # Fetching again with unchanged input_constraints reuses the cached
    # arrays (identity check — no rebuild happened).
    second = mpc._get_bounds_template(N)
    assert first[0] is second[0]
    assert first[3] is second[3]

    # update_v_max() changes the live value; the next fetch must rebuild
    # rather than silently return a stale template.
    mpc.update_v_max(7.5)
    third = mpc._get_bounds_template(N)
    assert third[3] is not first[3]
    assert np.allclose(third[3][0::2], 7.5)
