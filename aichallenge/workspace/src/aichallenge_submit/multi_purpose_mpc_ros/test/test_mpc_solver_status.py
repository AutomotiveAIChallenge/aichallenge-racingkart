"""MPC.get_control must not use the primal iterate of a failed OSQP solve.

osqp==0.6.7 (the pinned version) returns ``x = [None, ...]`` for an infeasible
problem, which happens to fall into the ``except TypeError`` fallback.  osqp
>= 1.0 returns a *finite* iterate instead (measured: 2.14e9 for a trivially
infeasible QP) and ``except TypeError or ValueError`` only catches TypeError,
so that iterate would be published as the speed / steering command.  The
solver status is the contract; these tests pin it.
"""
import numpy as np
import pytest

import mpc_fixture as F
from multi_purpose_mpc_ros.core import MPC as mpc_module

GARBAGE = 2.14328934e9  # what osqp 1.1.3 returned in x for a primal infeasible QP


class _Info:
    def __init__(self, status):
        self.status = status
        self.status_val = 3


class _Result:
    def __init__(self, n, status):
        self.x = np.full(n, GARBAGE)
        self.info = _Info(status)


def _fake_osqp(status):
    class FakeOSQP:
        def setup(self, P=None, q=None, A=None, l=None, u=None, **kwargs):
            self._n = A.shape[1]

        def solve(self):
            return _Result(self._n, status)

    return FakeOSQP


@pytest.mark.parametrize(
    "status",
    [
        "primal infeasible",
        "dual infeasible",
        # Neither status guarantees a converged, feasible solution: an
        # unconverged / inaccurate primal iterate must not be published.
        "maximum iterations reached",
        "solved inaccurate",
    ],
)
def test_failed_solve_falls_back_to_previous_plan(monkeypatch, status):
    mpc = F.make_mpc()
    F.place_car(mpc, 10, 0.0, 0.0)
    mpc.get_control()                       # real solve -> a plan to fall back on
    previous_plan = mpc.current_control.copy()

    monkeypatch.setattr(mpc_module.osqp, "OSQP", _fake_osqp(status))
    F.place_car(mpc, 11, 0.0, 0.0)
    u, _ = mpc.get_control()

    assert np.all(np.isfinite(u))
    assert u[0] <= F.V_MAX + 1e-9, f"published speed {u[0]:.3g} m/s"
    assert np.allclose(u, previous_plan[2:4])
    assert mpc.infeasibility_counter == 1


def test_successful_solve_is_used_as_before():
    mpc = F.make_mpc()
    F.place_car(mpc, 10, 0.0, 0.0)
    u, _ = mpc.get_control()
    assert mpc.infeasibility_counter == 0
    assert 0.0 <= u[0] <= F.V_MAX + 1e-9


def test_unconvertible_x_does_not_abort_the_retry_loop(monkeypatch):
    """A "usable" status whose ``x`` fails float conversion (osqp 0.6's
    ``x = [None, ...]`` for an infeasible problem is one real example) must
    be treated as unusable *without raising*, so the safety-margin retry loop
    keeps running and a later, valid retry is still used. If the conversion
    error escapes ``_is_usable_solution`` it is caught by the outer
    ``except`` in ``get_control`` instead, which exits before the retry loop
    ever runs a second ``solve()``.
    """
    mpc = F.make_mpc()
    F.place_car(mpc, 10, 0.0, 0.0)

    calls = {"n": 0}

    class _BadXInfo:
        def __init__(self):
            self.status = "solved"
            self.status_val = 1

    class _Unconvertible:
        """Raises TypeError from float(), like the real osqp 0.6 x=[None]."""

    class _BadXResult:
        def __init__(self, n):
            self.x = np.array([_Unconvertible()] * n, dtype=object)
            self.info = _BadXInfo()

    class _GoodInfo:
        def __init__(self):
            self.status = "solved"
            self.status_val = 1

    class _GoodResult:
        def __init__(self, n):
            self.x = np.zeros(n)
            self.info = _GoodInfo()

    class FakeOSQP:
        def setup(self, P=None, q=None, A=None, l=None, u=None, **kwargs):
            self._n = A.shape[1]

        def solve(self):
            calls["n"] += 1
            if calls["n"] == 1:
                return _BadXResult(self._n)
            return _GoodResult(self._n)

    monkeypatch.setattr(mpc_module.osqp, "OSQP", FakeOSQP)
    u, _ = mpc.get_control()

    # The retry loop must actually have run a second solve() and used its
    # (finite, usable) result instead of jumping straight to the
    # previous-plan fallback.
    assert calls["n"] >= 2
    assert mpc.infeasibility_counter == 0
    assert np.all(np.isfinite(u))
