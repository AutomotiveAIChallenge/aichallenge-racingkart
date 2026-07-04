"""Offline equivalence tests for OSQP setup() vs update().

These tests intentionally avoid ROS. They validate the prerequisite for an MPC
optimization cache: for the same sparse structure, updating q/l/u/Ax must produce
the same solution as creating a fresh OSQP workspace with setup().
"""

import numpy as np
import pytest

osqp = pytest.importorskip("osqp")
sparse = pytest.importorskip("scipy.sparse")


OSQP_SETTINGS = {
    "verbose": False,
    "eps_abs": 1.0e-9,
    "eps_rel": 1.0e-9,
    "max_iter": 100000,
    "polishing": True,
}


def _build_problem(a_values=None, q=None, l=None, u=None):
    """Build a small deterministic QP with a fixed explicit CSC pattern."""
    p = sparse.diags([4.0, 2.5, 3.0, 1.5], format="csc")

    if q is None:
        q = np.array([0.3, -1.0, 0.2, -0.5], dtype=np.float64)
    if l is None:
        l = np.array([-0.2, -0.8, -0.1, -1.0, -1.0], dtype=np.float64)
    if u is None:
        u = np.array([0.4, 0.7, 0.6, 1.0, 1.0], dtype=np.float64)

    # Column-compressed representation. Some entries may be updated to zero but
    # remain structurally present, which is the condition required by OSQP Ax updates.
    indices = np.array([0, 3, 0, 1, 4, 1, 2, 4, 2, 3], dtype=np.int32)
    indptr = np.array([0, 2, 5, 8, 10], dtype=np.int32)
    if a_values is None:
        a_values = np.array(
            [1.0, 0.2, -0.1, 1.0, 0.3, 0.4, 1.0, -0.2, 0.8, 1.0],
            dtype=np.float64,
        )
    a = sparse.csc_matrix((a_values, indices, indptr), shape=(5, 4))
    return p, q, a, l, u


def _solve_fresh(p, q, a, l, u):
    solver = osqp.OSQP()
    solver.setup(P=p, q=q, A=a, l=l, u=u, **OSQP_SETTINGS)
    result = solver.solve()
    assert result.info.status == "solved"
    assert result.x is not None
    return result.x.copy(), result.info.obj_val


def _assert_same_csc_structure(a, reference):
    assert a.shape == reference.shape
    assert np.array_equal(a.indices, reference.indices)
    assert np.array_equal(a.indptr, reference.indptr)


def test_update_matches_fresh_setup_for_identical_qp():
    p, q0, a0, l0, u0 = _build_problem()
    fresh0_x, fresh0_obj = _solve_fresh(p, q0, a0, l0, u0)

    solver = osqp.OSQP()
    solver.setup(P=p, q=q0, A=a0, l=l0, u=u0, **OSQP_SETTINGS)
    cached0 = solver.solve()

    assert cached0.info.status == "solved"
    np.testing.assert_allclose(cached0.x, fresh0_x, rtol=1.0e-8, atol=1.0e-8)
    assert cached0.info.obj_val == pytest.approx(fresh0_obj, rel=1.0e-8, abs=1.0e-8)


def test_update_matches_fresh_setup_after_q_l_u_and_ax_change():
    p, q0, a0, l0, u0 = _build_problem()
    solver = osqp.OSQP()
    solver.setup(P=p, q=q0, A=a0, l=l0, u=u0, **OSQP_SETTINGS)
    assert solver.solve().info.status == "solved"

    q1 = np.array([0.1, -0.7, 0.4, -0.2], dtype=np.float64)
    l1 = np.array([-0.1, -0.6, -0.2, -1.0, -0.9], dtype=np.float64)
    u1 = np.array([0.6, 0.8, 0.5, 1.0, 0.8], dtype=np.float64)
    a1_values = np.array(
        [1.0, 0.0, -0.2, 1.0, 0.35, 0.5, 1.0, -0.1, 0.75, 1.0],
        dtype=np.float64,
    )
    _, _, a1, _, _ = _build_problem(a_values=a1_values, q=q1, l=l1, u=u1)
    _assert_same_csc_structure(a1, a0)

    fresh_x, fresh_obj = _solve_fresh(p, q1, a1, l1, u1)
    solver.update(q=q1, l=l1, u=u1, Ax=a1.data)
    updated = solver.solve()

    assert updated.info.status == "solved"
    np.testing.assert_allclose(updated.x, fresh_x, rtol=1.0e-7, atol=1.0e-7)
    assert updated.info.obj_val == pytest.approx(fresh_obj, rel=1.0e-7, abs=1.0e-7)


def test_sequential_updates_match_fresh_setup_each_cycle():
    p, q, a, l, u = _build_problem()
    solver = osqp.OSQP()
    solver.setup(P=p, q=q, A=a, l=l, u=u, **OSQP_SETTINGS)
    assert solver.solve().info.status == "solved"

    for i in range(1, 8):
        scale = float(i)
        q_i = q + np.array([0.02, -0.01, 0.015, -0.005]) * scale
        l_i = l + np.array([0.005, 0.0, -0.004, 0.0, 0.0]) * scale
        u_i = u + np.array([0.0, 0.006, 0.0, 0.0, -0.003]) * scale
        a_i_values = a.data + np.array(
            [0.0, -0.01, 0.005, 0.0, 0.003, -0.004, 0.0, 0.002, -0.003, 0.0],
            dtype=np.float64,
        ) * scale
        _, _, a_i, _, _ = _build_problem(a_values=a_i_values, q=q_i, l=l_i, u=u_i)
        _assert_same_csc_structure(a_i, a)

        fresh_x, fresh_obj = _solve_fresh(p, q_i, a_i, l_i, u_i)
        solver.update(q=q_i, l=l_i, u=u_i, Ax=a_i.data)
        updated = solver.solve()

        assert updated.info.status == "solved"
        np.testing.assert_allclose(updated.x, fresh_x, rtol=1.0e-7, atol=1.0e-7)
        assert updated.info.obj_val == pytest.approx(fresh_obj, rel=1.0e-7, abs=1.0e-7)


def test_structure_change_must_not_be_treated_as_ax_update():
    _, _, a0, _, _ = _build_problem()
    a_changed = sparse.eye(5, 4, format="csc")

    with pytest.raises(AssertionError):
        _assert_same_csc_structure(a_changed, a0)
