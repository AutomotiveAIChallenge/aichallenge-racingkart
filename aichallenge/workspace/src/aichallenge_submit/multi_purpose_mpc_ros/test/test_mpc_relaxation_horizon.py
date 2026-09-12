"""The safety-margin relaxation retry in MPC.get_control must keep the horizon anchored to the car.

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


def test_relaxation_keeps_the_horizon_anchored_to_the_car():
    mpc = F.make_mpc()
    F.place_car(mpc, WP, E_Y, E_PSI)
    mpc.model.get_current_waypoint()
    base = mpc.model.wp_id

    mpc.get_control()

    # One control-delay offset, however many relaxation retries ran.
    assert mpc.model.wp_id == base + F.WP_ID_OFFSET


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
