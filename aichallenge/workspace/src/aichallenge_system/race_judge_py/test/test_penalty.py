import pytest

from race_judge_py.logic.penalty import PenaltyKind, PenaltyTracker


def test_single_trigger_creates_event_after_cooldown():
    pt = PenaltyTracker(cooldown_sec=2.0)
    pt.trigger(PenaltyKind.WALL, lap=1, race_time=10.0)
    assert pt.is_active(10.5)
    assert pt.update(11.0) == []          # まだクールダウン中
    events = pt.update(12.5)              # 12.0 で期限切れ
    assert len(events) == 1
    e = events[0]
    assert e.kind == PenaltyKind.WALL
    assert e.lap == 1
    assert e.race_time == pytest.approx(10.0)
    assert e.duration == pytest.approx(2.0)
    assert not pt.is_active(12.5)


def test_stay_refresh_extends_single_event():
    pt = PenaltyTracker(cooldown_sec=2.0)
    pt.trigger(PenaltyKind.WALL, lap=1, race_time=10.0)
    pt.trigger(PenaltyKind.WALL, lap=1, race_time=11.0)   # 接触継続(Stay)
    pt.trigger(PenaltyKind.WALL, lap=1, race_time=12.0)
    events = pt.update(15.0)
    assert len(events) == 1               # 1イベントに統合
    assert events[0].duration == pytest.approx(4.0)  # 10.0→14.0


def test_separate_triggers_after_expiry_are_two_events():
    pt = PenaltyTracker(cooldown_sec=2.0)
    pt.trigger(PenaltyKind.CRASH, lap=1, race_time=10.0)
    pt.update(13.0)
    pt.trigger(PenaltyKind.CRASH, lap=2, race_time=20.0)
    pt.finalize_all()
    assert len(pt.events) == 2
    assert pt.by_kind()["crash"]["count"] == 2


def test_union_total_counts_overlap_once():
    pt = PenaltyTracker(cooldown_sec=2.0)
    pt.trigger(PenaltyKind.WALL, lap=1, race_time=10.0)   # 10-12
    pt.trigger(PenaltyKind.CRASH, lap=1, race_time=11.0)  # 11-13
    pt.finalize_all()
    assert pt.union_total_seconds() == pytest.approx(3.0)  # 10-13


def test_by_kind_totals():
    pt = PenaltyTracker(cooldown_sec=2.0)
    pt.trigger(PenaltyKind.WALL, lap=1, race_time=0.0)
    pt.finalize_all()
    bk = pt.by_kind()
    assert bk["wall"] == {"count": 1, "total_seconds": pytest.approx(2.0)}
    assert bk["crash"]["count"] == 0
    assert bk["over"]["count"] == 0


def test_event_to_dict_schema():
    pt = PenaltyTracker(cooldown_sec=2.0)
    pt.trigger(PenaltyKind.WALL, lap=3, race_time=5.0)
    pt.finalize_all()
    d = pt.events[0].to_dict()
    assert set(d.keys()) == {"kind", "lap", "race_time", "duration"}
    assert d["kind"] == "wall"


def test_stay_refresh_never_shrinks_window():
    pt = PenaltyTracker(cooldown_sec=2.0)
    # First trigger opens window [10.0, 12.0]
    pt.trigger(PenaltyKind.WALL, lap=1, race_time=10.0)
    # Stay at 10.5 → window end should be max(12.0, 12.5) = 12.5
    pt.trigger(PenaltyKind.WALL, lap=1, race_time=10.5)
    # Stay at 9.9 (out-of-order / stale) → window end must NOT shrink below 12.5
    pt.trigger(PenaltyKind.WALL, lap=1, race_time=9.9)
    # Window end should be 12.5; time just before it must still be active
    assert pt.is_active(12.4)
    # Time at or after window end must be inactive
    assert not pt.is_active(12.5)
