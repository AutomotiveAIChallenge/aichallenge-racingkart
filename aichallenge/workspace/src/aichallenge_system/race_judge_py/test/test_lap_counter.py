import pytest

from race_judge_py.logic.lap_counter import LapCounter


def drive(counter, samples):
    """samples: (progress, t) 列を順に流す。完了ラップ数を返す。"""
    laps = 0
    for p, t in samples:
        if counter.update(p, t):
            laps += 1
    return laps


def test_simple_lap():
    c = LapCounter()
    c.start(0.0)
    laps = drive(c, [(0.05, 1.0), (0.5, 30.0), (0.9, 55.0), (0.1, 62.0)])
    assert laps == 1
    assert c.lap_count == 1
    assert len(c.lap_times) == 1
    # 横断時刻の線形補間: prev=(0.9,55) cur=(0.1,62) gap=0.2, frac=0.5 → t_cross=58.5
    assert c.lap_times[0] == pytest.approx(58.5)


def test_lap_time_measured_from_race_start():
    c = LapCounter()
    c.start(10.0)
    drive(c, [(0.5, 30.0), (0.95, 50.0), (0.05, 52.0)])
    # t_cross = 50 + (0.05/0.1)*2 = 51.0 → lap time = 41.0
    assert c.lap_times[0] == pytest.approx(41.0)


def test_no_lap_without_hysteresis_band():
    c = LapCounter()
    c.start(0.0)
    # 0.6 → 0.4 のような中間帯の逆行ではラップしない
    laps = drive(c, [(0.6, 1.0), (0.4, 2.0), (0.6, 3.0)])
    assert laps == 0


def test_reverse_crossing_creates_debt():
    c = LapCounter()
    c.start(0.0)
    # ライン後ろへバック(0.1→0.9) → 前進で戻る(0.9→0.1): ラップ加算なし
    laps = drive(c, [(0.1, 1.0), (0.9, 2.0), (0.1, 3.0)])
    assert laps == 0
    # その後の正規の周回はカウントされる
    laps = drive(c, [(0.5, 10.0), (0.9, 20.0), (0.05, 22.0)])
    assert laps == 1


def test_not_started_does_not_record_times():
    c = LapCounter()
    laps = drive(c, [(0.9, 1.0), (0.1, 2.0)])
    assert laps == 0
    assert c.lap_times == []


def test_current_lap_elapsed():
    c = LapCounter()
    c.start(5.0)
    assert c.current_lap_elapsed(15.0) == pytest.approx(10.0)
