import pytest

from race_control.lap_tracker import LapTracker


def make_tracker(**kw):
    # start line: x=0 の縦線 (0,0)-(0,10)
    kw.setdefault("margin", 2.0)
    kw.setdefault("min_lap_time", 10.0)
    return LapTracker((0.0, 0.0), (0.0, 10.0), **kw)


def cross(tracker, t0, y=5.0):
    """線を横切る2点を食わせる(往路で戻る動きも交差になる点に注意)。

    どちらかの update が周回登録したら True。
    """
    r1 = tracker.update(1.0, y, t0)
    r2 = tracker.update(-1.0, y, t0 + 0.1)
    return r1 or r2


class TestCrossing:
    def test_first_crossing_arms_lap_zero(self):
        tr = make_tracker()
        assert cross(tr, 0.0) is True
        assert tr.lap_count == 0
        assert tr.lap_times == []

    def test_second_crossing_records_lap_time(self):
        tr = make_tracker()
        cross(tr, 0.0)  # lap 0 は 2点目 (t=0.1, x=-1.0) で登録
        # 60.0 の1点目 (x=1.0) で再交差 -> lap_time = 60.0 - 0.1 = 59.9
        assert cross(tr, 60.0) is True
        assert tr.lap_count == 1
        assert tr.lap_times == [pytest.approx(59.9)]

    def test_no_crossing_when_same_side(self):
        tr = make_tracker()
        assert tr.update(1.0, 5.0, 0.0) is False
        assert tr.update(2.0, 5.0, 0.1) is False
        assert tr.lap_count == -1

    def test_crossing_outside_segment_ignored(self):
        tr = make_tracker()
        # y=20 は線分端 y=10 + margin 2.0 の外
        tr.update(1.0, 20.0, 0.0)
        assert tr.update(-1.0, 20.0, 0.1) is False
        assert tr.lap_count == -1

    def test_crossing_within_margin_counts(self):
        tr = make_tracker()
        # y=11 は端 y=10 の外だが margin 2.0 の内
        assert cross(tr, 0.0, y=11.0) is True


class TestDebounce:
    def test_recross_within_min_lap_time_ignored(self):
        tr = make_tracker()
        cross(tr, 0.0)
        # 5秒後の再クロスは min_lap_time=10 未満 -> 無視
        tr.update(1.0, 5.0, 5.0)
        assert tr.update(-1.0, 5.0, 5.1) is False
        assert tr.lap_count == 0
        # lap_start は更新されない(既存挙動の維持)
        assert tr.lap_start == pytest.approx(0.1)

    def test_lap_time_measured_from_original_start(self):
        tr = make_tracker()
        cross(tr, 0.0)          # lap_start = 0.1
        cross(tr, 5.0)          # debounced (lap_start は 0.1 のまま)
        assert cross(tr, 30.0) is True
        assert tr.lap_times == [pytest.approx(29.9)]  # 30.0 - 0.1
