"""Opt-in dropout hold for V2XVehicleTracker (``hold_time_s``).

``_active`` is the latest message's vehicle list verbatim, so one missing
entry removes that opponent from the occupancy map for the cycle and the MPC
solves as if the track were clear.  ``hold_time_s`` keeps a vehicle for that
long after its last sample, extrapolated from the sample's age (fail-closed).
``hold_time_s=0.0`` (the default) is the historical behaviour exactly.

Prior art: fis-teria ``opponent_stale_time_sec = 0.50`` (fail-closed hold),
topse ADR-008 (a single lost frame flipped their follow state machine).
"""
import pytest

from multi_purpose_mpc_ros.v2x_vehicle_tracker import V2XVehicleTracker


class _Stamp:
    def __init__(self, t):
        self.sec = int(t)
        self.nanosec = int(round((t - int(t)) * 1e9))


class _V:
    def __init__(self, vid, t, x, y):
        self.vehicle_id = vid
        self.header = type("H", (), {"stamp": _Stamp(t)})()
        self.position = type("P", (), {"x": x, "y": y, "z": 0.0})()


def _msg(*vs):
    return type("M", (), {"vehicles": list(vs)})()


def _tracker(hold):
    return V2XVehicleTracker(v_max_safety=30.0, position_jump_threshold=5.0,
                             hold_time_s=hold)


def test_default_is_the_old_behaviour():
    tr = V2XVehicleTracker(v_max_safety=30.0, position_jump_threshold=5.0)
    tr.update(_msg(_V("d2", 0.0, 0.0, 0.0), _V("d3", 0.0, 10.0, 0.0)))
    tr.update(_msg(_V("d2", 0.1, 1.0, 0.0)))
    assert tr.active_vehicle_ids() == ["d2"]
    assert tr.held_vehicle_ids() == []
    assert "d3" not in tr.predict_all([0.0])


def test_a_dropped_entry_no_longer_empties_the_map():
    tr = _tracker(0.5)
    tr.update(_msg(_V("d2", 0.0, 0.0, 0.0), _V("d3", 0.0, 10.0, 0.0)))
    tr.update(_msg(_V("d2", 0.1, 1.0, 0.0)))          # d3 missing this frame
    assert sorted(tr.active_vehicle_ids()) == ["d2", "d3"]
    assert tr.held_vehicle_ids() == ["d3"]
    assert "d3" in tr.predict_all([0.0])


def test_the_hold_expires():
    tr = _tracker(0.5)
    tr.update(_msg(_V("d2", 0.0, 0.0, 0.0), _V("d3", 0.0, 10.0, 0.0)))
    for t in (0.2, 0.4, 0.51, 0.7):
        tr.update(_msg(_V("d2", t, t, 0.0)))
    assert tr.active_vehicle_ids() == ["d2"]


def test_the_boundary_is_inclusive():
    tr = _tracker(0.5)
    tr.update(_msg(_V("d3", 0.0, 10.0, 0.0)))
    tr.update(_msg(_V("d2", 0.5, 0.0, 0.0)))
    assert "d3" in tr.active_vehicle_ids()
    tr.update(_msg(_V("d2", 0.51, 0.0, 0.0)))
    assert "d3" not in tr.active_vehicle_ids()


def test_a_held_vehicle_is_extrapolated_from_its_age():
    """d3 moves at 4 m/s, is last seen at t=0.2 and then missed for 0.3 s.
    Fail-closed means predicting where it IS (2.0 m), not where it was (0.8 m)."""
    tr = _tracker(0.5)
    for t in (0.0, 0.1, 0.2):
        tr.update(_msg(_V("d3", t, 4.0 * t, 0.0), _V("d2", t, -50.0, 0.0)))
    assert tr.velocity("d3")[0] == pytest.approx(4.0)
    tr.update(_msg(_V("d2", 0.5, -50.0, 0.0)))        # d3 missing, age 0.3 s
    got = tr.predict_all([0.0, 0.2])["d3"]
    assert got[0][0] == pytest.approx(0.8 + 4.0 * 0.3)
    assert got[1][0] == pytest.approx(0.8 + 4.0 * 0.5)


def test_a_present_vehicle_is_never_aged():
    tr = _tracker(0.5)
    for t in (0.0, 0.1, 0.2):
        tr.update(_msg(_V("d3", t, 4.0 * t, 0.0)))
    assert tr.predict_all([0.0])["d3"][0][0] == pytest.approx(0.8)


def test_a_returning_vehicle_resumes_normally():
    tr = _tracker(0.5)
    tr.update(_msg(_V("d3", 0.0, 0.0, 0.0), _V("d2", 0.0, -50.0, 0.0)))
    tr.update(_msg(_V("d2", 0.2, -50.0, 0.0)))
    assert tr.held_vehicle_ids() == ["d3"]
    tr.update(_msg(_V("d3", 0.4, 1.6, 0.0), _V("d2", 0.4, -50.0, 0.0)))
    assert tr.held_vehicle_ids() == []
    assert sorted(tr.active_vehicle_ids()) == ["d2", "d3"]


def test_a_negative_hold_is_clamped_to_off():
    tr = _tracker(-1.0)
    tr.update(_msg(_V("d3", 0.0, 0.0, 0.0)))
    tr.update(_msg(_V("d2", 0.1, 0.0, 0.0)))
    assert tr.active_vehicle_ids() == ["d2"]
