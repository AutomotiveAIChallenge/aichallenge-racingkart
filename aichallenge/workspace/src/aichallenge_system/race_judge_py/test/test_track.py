import numpy as np
import pytest

from race_judge_py.geometry.track import Track


def square_track():
    # 100m x 100m の正方形コース(反時計回り)、頂点のみ
    return Track(np.array([[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0]]))


def test_total_length():
    assert square_track().total_length == pytest.approx(400.0)


def test_project_on_first_edge():
    t = square_track()
    s, idx, dist = t.project([50.0, -2.0])
    assert s == pytest.approx(50.0)
    assert idx == 0
    assert dist == pytest.approx(2.0)


def test_progress_wraps_to_zero_at_start():
    t = square_track()
    p, _ = t.progress_at([0.0, 0.0])
    assert p == pytest.approx(0.0)


def test_progress_three_quarters():
    t = square_track()
    p, _ = t.progress_at([0.0, 50.0])  # 4辺目の中点 → 350/400
    assert p == pytest.approx(0.875)


def test_hint_window_finds_same_result():
    t = square_track()
    s_full, _, _ = t.project([100.0, 50.0])
    s_hint, _, _ = t.project([100.0, 50.0], hint=1, window=2)
    assert s_hint == pytest.approx(s_full)


def test_set_origin_shifts_progress():
    t = square_track()
    t.set_origin([100.0, 0.0])  # 進捗原点をs=100へ
    p, _ = t.progress_at([100.0, 100.0])  # s=200 → (200-100)/400
    assert p == pytest.approx(0.25)


def test_duplicate_points_dropped():
    t = Track(np.array([[0, 0], [0, 0], [100, 0], [100, 100], [0, 100]], dtype=float))
    assert t.total_length == pytest.approx(400.0)


def test_all_duplicate_points_raises():
    # All points identical — dedup leaves 0 points; must raise, not crash in project()
    with pytest.raises(ValueError):
        Track(np.array([[5.0, 5.0], [5.0, 5.0], [5.0, 5.0]], dtype=float))


def test_invalid_shape_raises():
    # (N,3) array is not a valid 2-D track
    with pytest.raises(ValueError):
        Track(np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]]))
    # 1-D array is not valid
    with pytest.raises(ValueError):
        Track(np.array([1.0, 2.0, 3.0]))
