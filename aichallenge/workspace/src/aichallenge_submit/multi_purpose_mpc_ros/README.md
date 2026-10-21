# multi_purpose_mpc_ros

このパッケージはリポジトリ内（`aichallenge/workspace/src/aichallenge_submit/multi_purpose_mpc_ros/`）に直接収録されています。別途 `git clone` は不要です。

## build

autoware コンテナ内で実行します（`make autoware-bash` または `make autoware-build`）：

```bash
cd /aichallenge/workspace
colcon build --symlink-install --allow-overriding gyro_odometer \
  --cmake-args -DCMAKE_BUILD_TYPE=Release
```

- ビルド時に仮想環境が `${ROS_WS}/install/multi_purpose_mpc_ros/.venv` に作成されます。

## run

### MPC コントローラー
```bash
ros2 run multi_purpose_mpc_ros run_mpc_controller.bash
```

### MPC シミュレーション
```bash
ros2 run multi_purpose_mpc_ros run_mpc_simulation.bash
```

### まとめて起動（コントローラー + シミュレーション）
```bash
ros2 launch multi_purpose_mpc_ros test.launch.xml
```

## Performance

制御ループ `MPC.get_control()` の高速化に取り組んだ結果です。計測は以下のコマンドで再現できます（パッケージディレクトリで実行、800 サイクル）。

```bash
python3 test/perf/benchmark_mpc.py --cycles 800 \
  --check-golden /path/to/golden_baseline.npz --rms-tol 0.05 --traj-rms-tol 0.05
```

| 段階 | mean [ms] | p50 [ms] | p95 [ms] |
|---|---|---|---|
| baseline | 7.77 | 9.35 | 9.87 |
| + パスジオメトリキャッシュ／`set_v_ref` スキップ | 6.02 | 7.70 | 7.90 |
| + `_init_problem` のベクトル化（linearize_batch, 固定スパースパターン, N 別テンプレートキャッシュ） | 5.06 | 7.02 | 7.24 |
| + OSQP `update()` 再利用（毎サイクル zero warm_start） | 4.60 | 6.48 | 6.75 |
| **最終（HEAD）** | **4.87** | **6.96** | **7.08** |

主な最適化:
- 静的パスジオメトリ（`waypoints_xy` / `length_cum` / `kappas` / `v_refs`）を事前計算し、毎サイクルのリスト内包表記・`cumsum` を排除
- 参照速度が変化していないサイクルでは `set_v_ref` の再計算をスキップ
- `_init_problem` のホライズン組み立てをベクトル化（`linearize_batch`、固定スパースパターンの座標形式 `Aeq`、N ごとにキャッシュした P/q タイル・レート行列・境界テンプレート）
- OSQP を毎サイクル `setup()` で再factorizeするのではなく、`update()` による永続インスタンス再利用に変更（毎サイクル zero warm_start を維持し、旧来の cold-start と等価な挙動を保持）
- `update_v_max` のキャッシュ無効化ガード追加

**等価性の検証方法:** baseline のクローズドループ実行を `golden_baseline.npz` として保存し、各最適化後の実行結果と比較（`max|Δu|` と軌道 RMS）。許容誤差は `--rms-tol 0.05` / `--traj-rms-tol 0.05`。Task 2–3 はビット完全一致、Task 4 は `max|Δu| = 1.43e-11`（`Aeq` の格納ゼロパターンによる OSQP 分解順序の違いのみ）、Task 5（最終）は軌道 RMS `1.10e-2 m`（閾値 `0.05 m` に対し十分小さい）。

### Attribution
This repository includes code derived from:

Multi-Purpose-MPC  
Author: Mats Steinweg  
Original repository: https://github.com/matssteinweg/Multi-Purpose-MPC

Used with permission from the author.
