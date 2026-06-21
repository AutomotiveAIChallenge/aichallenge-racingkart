"""MPC-specific prerequisites for a safe OSQP Ax update cache.

OSQP update() is only valid when the sparse matrix structure is unchanged. The
current MPC builds its dynamic matrix through scipy's default sparse conversion,
which drops numerical zeros. This test documents why a cache must not update Ax
unless it owns a fixed explicit sparsity pattern.
"""

import numpy as np
import pytest

sparse = pytest.importorskip("scipy.sparse")


NX = 3
NU = 2
N = 20
WHEEL_BASE = 1.087


def _linearized_bicycle_matrices(n: int, shift: float):
    delta_s = 0.95 + 0.1 * np.sin(n + shift)
    kappa = 0.18 * np.sin(0.5 * n + shift)
    v_ref = 5.0 + 0.5 * np.cos(n + shift)

    a_lin = np.stack(
        (
            [1.0, delta_s, 0.0],
            [-kappa**2 * delta_s, 1.0, 0.0],
            [-kappa / v_ref * delta_s, 0.0, 1.0],
        ),
        axis=0,
    )
    b_lin = np.stack(
        (
            [0.0, 0.0],
            [0.0, delta_s],
            [-1.0 / (v_ref**2) * delta_s, 0.0],
        ),
        axis=0,
    )
    return a_lin, b_lin


def _original_mpc_a_matrix(shift: float):
    nx_n = NX * (N + 1)
    nu_n = NU * N
    a_dense = np.zeros((nx_n, nx_n))
    b_dense = np.zeros((nx_n, nu_n))

    for n in range(N):
        a_lin, b_lin = _linearized_bicycle_matrices(n, shift)
        a_dense[(n + 1) * NX : (n + 2) * NX, n * NX : (n + 1) * NX] = a_lin
        b_dense[(n + 1) * NX : (n + 2) * NX, n * NU : (n + 1) * NU] = b_lin

    ax = sparse.kron(sparse.eye(N + 1), -sparse.eye(NX)) + sparse.csc_matrix(a_dense)
    bu = sparse.csc_matrix(b_dense)
    aeq = sparse.hstack([ax, bu])
    return sparse.vstack([aeq, _inequality_matrix()], format="csc")


def _fixed_pattern_mpc_a_matrix(shift: float):
    nx_n = NX * (N + 1)
    nu_n = NU * N
    rows = []
    cols = []
    data = []

    for index in range(nx_n):
        rows.append(index)
        cols.append(index)
        data.append(-1.0)

    for n in range(N):
        a_lin, b_lin = _linearized_bicycle_matrices(n, shift)
        row_offset = (n + 1) * NX
        state_col_offset = n * NX
        input_col_offset = nx_n + n * NU

        # All coefficients that can be non-zero in BicycleModel.linearize().
        for row, col in ((0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 2)):
            rows.append(row_offset + row)
            cols.append(state_col_offset + col)
            data.append(a_lin[row, col])

        for row, col in ((1, 1), (2, 0)):
            rows.append(row_offset + row)
            cols.append(input_col_offset + col)
            data.append(b_lin[row, col])

    aeq = sparse.csc_matrix((data, (rows, cols)), shape=(nx_n, nx_n + nu_n))
    return sparse.vstack([aeq, _inequality_matrix()], format="csc")


def _inequality_matrix():
    nx_n = NX * (N + 1)
    nu_n = NU * N
    n_rate_constraints = N - 1
    steering_rate_matrix = np.zeros((n_rate_constraints, nx_n + nu_n))

    for i in range(n_rate_constraints):
        steering_rate_matrix[i, nx_n + NU * i + 1] = -1.0
        steering_rate_matrix[i, nx_n + NU * (i + 1) + 1] = 1.0

    return sparse.vstack(
        [
            sparse.eye(nx_n + nu_n),
            sparse.csc_matrix(steering_rate_matrix),
        ]
    )


def _same_csc_structure(left, right):
    return (
        left.shape == right.shape
        and np.array_equal(left.indices, right.indices)
        and np.array_equal(left.indptr, right.indptr)
    )


def test_original_mpc_sparse_structure_changes_between_cycles():
    a0 = _original_mpc_a_matrix(shift=0.0)
    a1 = _original_mpc_a_matrix(shift=0.1)

    assert not _same_csc_structure(a0, a1)


def test_fixed_pattern_sparse_structure_is_stable_between_cycles():
    a0 = _fixed_pattern_mpc_a_matrix(shift=0.0)
    a1 = _fixed_pattern_mpc_a_matrix(shift=0.1)

    assert _same_csc_structure(a0, a1)


def test_fixed_pattern_matrix_is_numerically_equivalent_to_original_matrix():
    for shift in (0.0, 0.1, 0.5, 1.0):
        original = _original_mpc_a_matrix(shift)
        fixed = _fixed_pattern_mpc_a_matrix(shift)

        np.testing.assert_allclose(
            fixed.toarray(),
            original.toarray(),
            rtol=1.0e-12,
            atol=1.0e-12,
        )
