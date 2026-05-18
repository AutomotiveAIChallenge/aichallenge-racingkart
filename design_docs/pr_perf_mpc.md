# perf(mpc): MPC を軽くする

PR3/3 (base: `fix/ros-domain-id`)

## やったこと

1 制御 tick の計算コストを削るためのキャッシュ層を入れる。定式化は変えていないので結果は等価。

- **OSQP の setup を使い回す**: `safety_margin` を緩めて再求解するリトライ (最大 5 回) で毎回 `setup()` し直していたのを、`update()` で `l/u/q` だけ差し替える形に変更。因数分解が走らなくなる
- **N 依存の固定行列をキャッシュ**: `kron(I, -I)` とかステアレート差分行列とか、`xmin/xmax/umin` の tile とか。`_ensure_static_cache(N)` で 1 度だけ作る
- **コスト行列 P もキャッシュ**: `Q/R/QN` が変わるまで作り直さない (`update_Q/R/QN` で invalidate)
- **曲率制限のベクトル化**: `use_max_kappa_pred` 経路で `np.max(np.abs(kappa_pred[n:]))` を毎ループ呼んで O(N²) だったのを、逆向き累積最大で O(N) に
- **Path 制約に 2 段キャッシュ** (`reference_path.py`):
  - フルキャッシュ: `(length, width)` ごとに全 waypoint 分の raw 上下限を `safety_margin=0` で 1 度計算
  - tick キャッシュ: `(wp_id, N, length, width)` でスライス結果を保持。`safety_margin` だけ変わるリトライではスライス + マージン適用のみで済む
  - 障害物更新時の `reset_dynamic_constraints` で両方クリア
- **累積長キャッシュ**: `np.cumsum(segment_lengths)` を `ReferencePath` 構築時に 1 度だけ。`get_current_waypoint` / `get_s_at_waypoint` から都度 `cumsum` するのをやめる

## コミット

- a9f9eca chore: lighter mpc
- (pending) perf(mpc): cache OSQP static matrices and add `_update_safety_margin` retry path

## 触ったファイル

`multi_purpose_mpc_ros/multi_purpose_mpc_ros/core/{MPC,reference_path,spatial_bicycle_models}.py`

## 注意点

- 数学的には等価なので軌道は変わらない想定。実機/SIM で念のため目視確認したい
- キャッシュの invalidate 漏れがないか (`update_Q/R/QN`、`reset_dynamic_constraints`、`set_path_constraints`) はレビューで見てほしい

## テスト

- [ ] SIM で 1 周走らせて、軌道と速度プロファイルが従来と一致すること (`use_obstacle_avoidance=true` / `use_path_constraints_topic` の両経路)
- [ ] 障害物近傍で `safety_margin` リトライが発火するシーンを通過 → `_update_safety_margin` 経路を踏み、`setup()` が呼ばれないこと
- [ ] 障害物トピックを更新した直後の tick でキャッシュがクリアされ、新しい制約で再計算されること
- [ ] 制御周期の中央値・最悪値が悪化していないこと (本 PR の目的)
- [ ] `pre-commit run -a` がパス
