from race_judge_py.logic.rank_registry import RankTracker, compute_ranks


def test_higher_progress_wins():
    ranks = compute_ranks({1: 2.5, 2: 3.1, 3: 0.2})
    assert ranks == {2: 1, 1: 2, 3: 3}


def test_tie_breaks_to_smaller_vehicle_number():
    ranks = compute_ranks({3: 1.0, 1: 1.0, 2: 1.0})
    assert ranks == {1: 1, 2: 2, 3: 3}


def test_rank_tracker_confirms_after_persistence():
    rt = RankTracker(persistence_sec=1.0)
    r0 = {1: 1, 2: 2}
    assert rt.update(r0, 0.0) == r0          # 初回は即確定
    r1 = {1: 2, 2: 1}
    assert rt.update(r1, 0.1) == r0          # 変動直後は未確定
    assert rt.update(r1, 0.5) == r0          # 1.0s 未満は保留
    assert rt.update(r1, 1.2) == r1          # 1.0s 持続で確定


def test_rank_tracker_flicker_rejected():
    rt = RankTracker(persistence_sec=1.0)
    r0 = {1: 1, 2: 2}
    r1 = {1: 2, 2: 1}
    rt.update(r0, 0.0)
    rt.update(r1, 0.1)       # 変動開始
    rt.update(r0, 0.5)       # 元に戻った → 保留破棄
    assert rt.update(r1, 0.8) == r0   # 再変動はタイマー再スタート
    assert rt.update(r1, 1.7) == r0   # 0.9s しか経っていない
    assert rt.update(r1, 1.9) == r1   # 0.8からの1.1s持続で確定


def test_returned_dict_is_isolated():
    rt = RankTracker(persistence_sec=1.0)
    r0 = {1: 1, 2: 2}
    result = rt.update(r0, 0.0)   # 初回確定
    result[99] = 99               # 返り値を破壊的に変更
    # 内部状態が汚染されていなければ同一ranksはconfirmedと一致し続ける
    result2 = rt.update(r0, 0.1)
    assert result2 == r0          # 確定済みのまま — pendingに落ちていないはず
