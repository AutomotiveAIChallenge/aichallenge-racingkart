"""The safety-margin relaxation retry in MPC.get_control must not modify the stored corridor table.

State used throughout: the car is 2.3 m left of the reference line with a
0.45 rad heading error.  Row 0 of the spatial model has no input authority
(b_1 == [0, 0]), so e_y_1 = e_y_0 + ds * e_psi_0 = 2.57 m, which is outside the
2.36 m corridor: the first QP is primal infeasible by construction and
get_control enters the relaxation loop.
"""
import numpy as np

import mpc_fixture as F

WP = 10
E_Y, E_PSI = 2.3, 0.45


def test_state_really_is_infeasible_for_the_hard_qp():
    mpc = F.make_mpc()
    F.place_car(mpc, WP, E_Y, E_PSI)
    mpc.model.get_current_waypoint()
    mpc.model.spatial_state = mpc.model.t2s(
        reference_state=mpc.model.temporal_state,
        reference_waypoint=mpc.model.current_waypoint)
    mpc._init_problem(F.N, mpc.model.safety_margin)
    assert mpc.optimizer.solve().info.status == "primal infeasible"


def test_relaxation_does_not_rewrite_the_stored_corridor(capsys):
    mpc = F.make_mpc()
    F.place_car(mpc, WP, E_Y, E_PSI)
    ub_before, lb_before = F.corridor_rows(mpc)

    mpc.get_control()

    assert "Relaxed safety margin" in capsys.readouterr().out  # the loop ran
    ub_after, lb_after = F.corridor_rows(mpc)
    assert np.array_equal(ub_after, ub_before), \
        f"corridor widened by {np.max(ub_after - ub_before):.3f} m"
    assert np.array_equal(lb_after, lb_before)


def test_next_tick_sees_the_original_corridor():
    """After one infeasible tick, a car back on the line must get the same
    plan as a car that never had one."""
    fresh = F.make_mpc()
    F.place_car(fresh, WP, 0.0, 0.0)
    u_fresh, _ = fresh.get_control()

    used = F.make_mpc()
    F.place_car(used, WP, E_Y, E_PSI)
    used.get_control()
    used.previous_steering = 0.0
    used.current_control = np.zeros_like(used.current_control)
    F.place_car(used, WP, 0.0, 0.0)
    u_used, _ = used.get_control()

    assert np.allclose(u_used, u_fresh, atol=1e-9)


def test_feasible_tick_is_unchanged():
    mpc = F.make_mpc()
    F.place_car(mpc, WP, 0.0, 0.0)
    mpc.model.get_current_waypoint()
    base = mpc.model.wp_id
    ub_before, _ = F.corridor_rows(mpc)

    u, _ = mpc.get_control()

    assert mpc.model.wp_id == base + F.WP_ID_OFFSET
    assert np.array_equal(F.corridor_rows(mpc)[0], ub_before)
    assert np.all(np.isfinite(u))
