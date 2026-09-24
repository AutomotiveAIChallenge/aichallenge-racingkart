# multi_purpose_mpc_ros 高速化（挙動維持）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `multi_purpose_mpc_ros` の 40 Hz 制御ループの1サイクル計算コストを、制御挙動を変えずに大幅削減する（目標: ベースライン比 3 倍以上高速化、かつゴールデン出力との等価性を実証）。

**Architecture:** まずオフライン閉ループベンチマーク（実トラック final_ver3 データ、ROS 不要）でベースラインを実測し、ゴールデン制御系列を保存。その後、(1) 参照パス静的データのキャッシュ、(2) 毎サイクルの `set_v_ref` 抑止、(3) `_init_problem` のベクトル化と定数構造キャッシュ、(4) OSQP の永続化 + `update()` + ウォームスタート、を段階的に適用し、各段でゴールデン比較とベンチ数値を取る。

**Tech Stack:** Python 3.10, numpy, scipy.sparse, osqp, pytest。パッケージルート: `aichallenge/workspace/src/aichallenge_submit/multi_purpose_mpc_ros/`（以下、パスはこのルートからの相対）。

## Global Constraints

- **挙動維持が最優先**: Task 2〜4 は数値的に同一（bit-identical または |Δ| < 1e-9）であること。Task 5（ウォームスタート）のみソルバ許容誤差内の差を許すが、閉ループ軌跡 RMS 偏差 < 0.05 m を必須とする。
- コミットは Conventional Commits（`perf(mpc):` / `test(mpc):` 等)。**Claude co-author trailer・"Generated with Claude Code" footer は禁止。**
- 既存の公開 API（クラス名・メソッドシグネチャ・ROS トピック/パラメータ）を変えない。
- ベンチマークはホスト Python で実行可能（osqp/scipy/skimage はホストにインストール済み確認済）。作業ディレクトリ: `/home/taikitanaka/aic/.worktrees/mpc-speedup`。
- ゴールデン/ベンチ結果の一時ファイルは `/tmp/claude-1000/-home-taikitanaka-aic-aichallenge-racingkart/550cda0a-eaea-47ba-bb04-74bc35436579/scratchpad/` 配下（コミットしない）。ベンチ数値は各タスクの report に記載。

---

### Task 1: オフライン閉ループベンチマーク & ゴールデンハーネス

**Files:**
- Create: `test/perf/benchmark_mpc.py`（パッケージルート相対; 実体は `aichallenge/workspace/src/aichallenge_submit/multi_purpose_mpc_ros/test/perf/benchmark_mpc.py`）

**Interfaces:**
- Produces: CLI `python3 test/perf/benchmark_mpc.py --cycles 800 [--save-golden PATH] [--check-golden PATH] [--profile] [--rms-tol 1e-9 --traj-rms-tol 0.0]`
- 終了コード 0 = 等価性 OK。stdout に per-cycle 時間統計（mean/p50/p95/max ms）と `get_control` 内訳。

`mpc_controller.py:325-437` の `create_ref_path`/`create_car`/MPC 構築と同じパラメータで、ROS を介さずに閉ループを回す。構成は `config/config.yaml` の値をハードコードで再現（yaml 依存を避ける。N=20, R=[100000,0], a_min=-1.6, a_max=0.7, delta_max=32deg, steer_rate_max=0.35, control_rate=40, wp_id_offset=2, use_max_kappa_pred=True, width=2.30, length=1.087, resolution=0.6, smoothing_distance=2, max_width=6.0, circular=True）。Q/QN は `mpc_controller.py` の `_create_mpc` 相当箇所（`sparse.diags` で構築している値）を読んで同じ値を使うこと（実装時に `mpc_controller.py` の MPC 構築部を必ず確認して転記する）。

- [ ] **Step 1: `mpc_controller.py` の MPC/BicycleModel 構築部を読み、Q/QN/QN、SpeedProfileConstraints、InputConstraints/StateConstraints の実値を確認する**

`grep -n "sparse.diags\|SpeedProfileConstraints\|MPC(" multi_purpose_mpc_ros/mpc_controller.py` で該当行を特定し、ベンチに転記。

- [ ] **Step 2: ベンチスクリプトを書く**

```python
#!/usr/bin/env python3
"""Offline closed-loop benchmark & golden-equivalence harness for the MPC core.

Runs the vendored MPC (no ROS) on the real final_ver3 track in closed loop
(model.drive), measuring per-cycle wall time and optionally comparing the
control sequence / trajectory against a saved golden run.
"""
import argparse, cProfile, pstats, sys, time
from pathlib import Path

import numpy as np
from scipy import sparse

PKG = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PKG))

from multi_purpose_mpc_ros.core.map import Map
from multi_purpose_mpc_ros.core.reference_path import ReferencePath
from multi_purpose_mpc_ros.core.spatial_bicycle_models import BicycleModel
from multi_purpose_mpc_ros.core.MPC import MPC
from multi_purpose_mpc_ros.core.utils import load_ref_path


def build():
    map_ = Map(str(PKG / "env/final_ver3/occupancy_grid_map.yaml"))
    wp_x, wp_y, _, _ = load_ref_path(str(PKG / "env/final_ver3/traj_mincurv.csv"))
    ref_path = ReferencePath(map_, wp_x, wp_y, resolution=0.6,
                             smoothing_distance=2, max_width=6.0,
                             circular=True)
    car = BicycleModel(reference_path=ref_path, length=1.087, width=2.30,
                       Ts=1.0 / 40.0)
    # NOTE: Q/R/QN/constraints/speed profile: mpc_controller.py の実値を転記すること
    ...
    return car, mpc


def run(car, mpc, cycles):
    times, controls, traj = [], [], []
    for _ in range(cycles):
        t0 = time.perf_counter()
        u, _ = mpc.get_control()
        times.append(time.perf_counter() - t0)
        car.drive(u)
        car.update_states(car.temporal_state.x, car.temporal_state.y,
                          car.temporal_state.psi)
        controls.append(u.copy())
        traj.append([car.temporal_state.x, car.temporal_state.y])
    return np.array(times), np.array(controls), np.array(traj)
```

統計出力（mean/p50/p95/max ms）、`--profile` で cProfile cumtime 上位 25 行、`--save-golden` で `np.savez(path, controls=..., traj=...)`、`--check-golden` で `max|Δu|`, `RMS(traj偏差)` を出力し、`--rms-tol`/`--traj-rms-tol` 超過なら exit 1。

- [ ] **Step 3: 実行してベースライン計測（800 cycles = 20 秒走行相当）**

```bash
cd aichallenge/workspace/src/aichallenge_submit/multi_purpose_mpc_ros
python3 test/perf/benchmark_mpc.py --cycles 800 --profile \
  --save-golden $SCRATCH/golden_baseline.npz | tee $SCRATCH/bench_baseline.txt
```
Expected: 統計とプロファイル上位が出力され、golden が保存される。数値を report に記載。

- [ ] **Step 4: Commit**

```bash
git add aichallenge/workspace/src/aichallenge_submit/multi_purpose_mpc_ros/test/perf/benchmark_mpc.py
git commit -m "test(mpc): add offline closed-loop benchmark and golden harness"
```

---

### Task 2: 参照パス静的ジオメトリのキャッシュ（bit-identical）

**Files:**
- Modify: `multi_purpose_mpc_ros/core/reference_path.py`（`__init__` / waypoint 再構築箇所）
- Modify: `multi_purpose_mpc_ros/core/spatial_bicycle_models.py:261-322`（`get_current_waypoint` / `get_closest_waypoint` / `get_s_at_waypoint`）

**Interfaces:**
- Produces: `ReferencePath.waypoints_xy: np.ndarray (n,2)`、`ReferencePath.length_cum: np.ndarray (n,)`（`np.cumsum(segment_lengths)` と同一値）。`ReferencePath._rebuild_geometry_cache()` を waypoint 列確定後（`__init__` 末尾）に呼ぶ。

現状 40 Hz で毎サイクル: waypoint 全件の Python list comprehension ×2 + 全長 `np.cumsum` ×2（`spatial_bicycle_models.py:267,301-302,317`）。これらを構築時に 1 回だけ計算する。

- [ ] **Step 1: `reference_path.py` にキャッシュ構築を追加**

`__init__` で `self.length, self.segment_lengths = self._compute_length()`（`reference_path.py:205`）の直後に:

```python
self._rebuild_geometry_cache()
```

メソッド追加:

```python
def _rebuild_geometry_cache(self):
    """Precompute static per-waypoint geometry used every control cycle."""
    self.waypoints_xy = np.array([[wp.x, wp.y] for wp in self.waypoints])
    self.length_cum = np.cumsum(self.segment_lengths)
```

- [ ] **Step 2: `spatial_bicycle_models.py` の3メソッドをキャッシュ参照に書き換え**

- `get_closest_waypoint` (293-307): `xy = self.reference_path.waypoints_xy` を使い `np.argmin((xy[:,0]-x)**2 + (xy[:,1]-y)**2)`（`sqrt` は argmin に影響しないため省略可だが、**bit-identical のため sqrt を残した同一式で置換**: `distances = np.sqrt((xy[:,0]-x)**2 + (xy[:,1]-y)**2)`）。
- `get_current_waypoint` (267): `length_cum = self.reference_path.length_cum`
- `get_s_at_waypoint` (317): 同上。

`getattr` フォールバック禁止 — キャッシュは必ず存在する設計にする（`update_by_topic` 時は ReferencePath ごと作り直されるため `__init__` で張れば十分）。ただし `reference_path.py` 内で `self.waypoints` を再代入する箇所が `__init__` 以外にないか `grep -n "self.waypoints = " multi_purpose_mpc_ros/core/reference_path.py` で確認し、あれば直後に `_rebuild_geometry_cache()` を呼ぶ。

- [ ] **Step 3: 等価性 + ベンチ**

```bash
python3 test/perf/benchmark_mpc.py --cycles 800 --check-golden $SCRATCH/golden_baseline.npz \
  --rms-tol 0 --traj-rms-tol 0 | tee $SCRATCH/bench_task2.txt
```
Expected: `max|Δu| = 0.0`（bit-identical）、exit 0、時間統計がベースラインより改善。

- [ ] **Step 4: Commit**

```bash
git add -- aichallenge/workspace/src/aichallenge_submit/multi_purpose_mpc_ros/multi_purpose_mpc_ros/core/reference_path.py \
  aichallenge/workspace/src/aichallenge_submit/multi_purpose_mpc_ros/multi_purpose_mpc_ros/core/spatial_bicycle_models.py
git commit -m "perf(mpc): cache static path geometry for per-cycle waypoint lookups"
```

---

### Task 3: 毎サイクル `set_v_ref` の抑止（ROS ノード側）

**Files:**
- Modify: `multi_purpose_mpc_ros/mpc_controller.py:810-817` 付近（`_control` 内の `ReferenceVelocityConfigulator` 適用部）

**Interfaces:**
- Consumes: なし（自己完結）。オフラインベンチには現れないため、目視レビュー + 既存挙動維持で検証。

現状: `ref_vel_config_path` があると毎サイクル全 waypoint 長の list 生成 + `set_v_ref`（全 waypoint への属性代入ループ）。値が変わったときだけ適用する。

- [ ] **Step 1: 実装**

`_control` 内の該当ブロック（`mpc_controller.py:810-817`、実装時に実コードを確認）を、直前適用値のキャッシュ付きに変更:

```python
ref_vel_kmph = self._ref_vel_configulator.ref_vel(...)  # 既存の取得処理はそのまま
if ref_vel_kmph != self._last_applied_ref_vel_kmph:
    v_ref = [kmh_to_m_per_sec(ref_vel_kmph)] * len(self._reference_path.waypoints)
    self._reference_path.set_v_ref(v_ref)
    self._last_applied_ref_vel_kmph = ref_vel_kmph
```

`self._last_applied_ref_vel_kmph: Optional[float] = None` を `__init__` で初期化。注意: `_on_set_parameters`（`mpc_controller.py:268-269` 付近）など他経路で `set_v_ref` される場合はキャッシュを `None` に無効化すること（該当箇所を `grep -n set_v_ref multi_purpose_mpc_ros/mpc_controller.py` で全部確認する）。参照パス再構築時（`update_by_topic`）もキャッシュを `None` に戻す。

- [ ] **Step 2: 構文チェック + 既存テスト**

```bash
python3 -m py_compile multi_purpose_mpc_ros/mpc_controller.py
python3 -m pytest test/test_v2x_vehicle_tracker.py -q
```
Expected: PASS。

- [ ] **Step 3: Commit**

```bash
git add -- .../mpc_controller.py
git commit -m "perf(mpc): skip per-cycle set_v_ref when reference velocity unchanged"
```

---

### Task 4: `_init_problem` のベクトル化と定数構造キャッシュ（数値同一）

**Files:**
- Modify: `multi_purpose_mpc_ros/core/MPC.py:83-230`（`_init_problem`）
- Modify: `multi_purpose_mpc_ros/core/spatial_bicycle_models.py`（ベクトル化 `linearize_batch` を `BicycleModel` に追加）
- Modify: `multi_purpose_mpc_ros/core/reference_path.py`（`_rebuild_geometry_cache` に kappa / v_ref 配列を追加。v_ref は可変なので `set_v_ref` でも更新）

**Interfaces:**
- Produces: `BicycleModel.linearize_batch(v_ref: np.ndarray, kappa_ref: np.ndarray, delta_s: np.ndarray) -> (f: (N,3), A: (N,3,3), B: (N,3,2))` — `linearize()` と要素ごとに同値。
- Produces: `ReferencePath.kappas: np.ndarray (n,)`, `ReferencePath.v_refs: np.ndarray (n,)`（`set_v_ref` で同期更新）、`ReferencePath.segment_lengths_arr: np.ndarray`。
- Produces: `MPC` 内部キャッシュ: `self._cost_cache`（key=N → (P, q_Q_tile, q_R_tile)）、`self._rate_matrix_cache`（key=N → csc）、`self._eq_pattern_cache`（key=N → (rows, cols) 座標配列）。`update_Q/update_R/update_QN` はコストキャッシュを無効化する。

設計（現行 `MPC.py:83-230` と数値同一の出力を、Python ループなしで構築する）:

1. **horizon 参照値の一括取得**: `idx = self.model.wp_id + np.arange(N + 1)`; circular なら `idx %= n_wp`、非 circular なら `np.minimum(idx, n_wp - 1)`（`get_waypoint` の clamp と同一挙動）。`kappa = ref.kappas[idx[:-1]]`, `v_ref = np.clip(ref.v_refs[idx[:-1]], umin[0], umax[0])`, `delta_s = np.hypot(xy[idx[1:],0]-xy[idx[:-1],0], xy[idx[1:],1]-xy[idx[:-1],1])`。
   **注意**: 現行 `delta_s = next_waypoint - current_waypoint` は `Waypoint.__sub__`（`reference_path.py:138-146`, `((dx)**2+(dy)**2)**0.5`）。bit 同一のため `np.hypot` ではなく `(dx**2 + dy**2)**0.5` を使うこと。
2. **`linearize_batch`**: `linearize()`（`spatial_bicycle_models.py:458-489`）と同式のベクトル版。`v_ref == 0` の分岐は `np.where` ではなく mask 代入で（0 除算 warning を避けるため `np.errstate` + mask 上書き、値は同一）:

```python
def linearize_batch(self, v_ref, kappa_ref, delta_s):
    n = len(delta_s)
    A = np.zeros((n, 3, 3)); B = np.zeros((n, 3, 2)); f = np.zeros((n, 3))
    A[:, 0, 0] = 1.0; A[:, 0, 1] = delta_s
    A[:, 1, 0] = -kappa_ref**2 * delta_s; A[:, 1, 1] = 1.0
    A[:, 2, 2] = 1.0
    B[:, 1, 1] = delta_s
    nz = v_ref != 0
    with np.errstate(divide="ignore", invalid="ignore"):
        A[nz, 2, 0] = -kappa_ref[nz] / v_ref[nz] * delta_s[nz]
        B[nz, 2, 0] = -1.0 / v_ref[nz]**2 * delta_s[nz]
        f[nz, 2] = 1.0 / v_ref[nz] * delta_s[nz]
    return f, A, B
```

3. **ur/uq**: `ur = np.column_stack([v_ref, kappa_ref]).ravel()`; `uq = (np.einsum("nij,nj->ni", B, np.column_stack([v_ref, kappa_ref])) - f).ravel()`。einsum の演算順序差が出るなら `B[:,:,0]*v_ref[:,None] + B[:,:,1]*kappa_ref[:,None] - f` と書き、`benchmark --rms-tol 0` で bit 同一を確認（ダメなら 1e-12 許容し traj-rms-tol 0 のまま）。
4. **vmax_dyn**: `kappa_pred` の suffix max は `np.maximum.accumulate(np.abs(kappa_pred)[::-1])[::-1]`（use_max_kappa_pred=True 経路）。`umax_dyn[0::2] = np.minimum(np.sqrt(self.ay_max / (suffix_max[:N] + 1e-12)), umax_dyn[0::2])`。
5. **Aeq を座標形式で直接構築**（dense 63×63 の確保と `sparse.csc_matrix(dense)` を廃止）: ブロック位置は固定なので、`(rows, cols)` を N ごとに一度だけ計算してキャッシュし、毎サイクルは values のみ計算:
   - `-I` 対角 (nx_N 個)、A ブロック（各 n につき 3×3=9 エントリ、構造ゼロ含め全 9 個入れる）、B ブロック（各 n につき 3×2=6 エントリ全部）。
   - `Aeq = sparse.csc_matrix((vals, (rows, cols)), shape=(nx_N, nx_N + nu_N))`（coo→csc は構造ゼロを保持する。`eliminate_zeros()` を呼ばないこと — Task 5 のパターン固定に必要）。
6. **steering_rate 行列**: N ごとに一度だけ構築してキャッシュ（値は定数 ±1）。`A_inequality = sparse.vstack([sparse.eye(nx_N + nu_N, format="csc"), rate_csc])` の結合結果も N キーでキャッシュしてよい（毎サイクル不変）→ ただし `A_full = sparse.vstack([Aeq, A_ineq_cached], format="csc")` は Aeq が変わるため毎サイクル実施。
7. **P / q**: P は (Q,R,QN,N) にのみ依存 → キャッシュ。`q` は `q_Q_tile = -np.tile(np.diag(self.Q.toarray()), N)` / `q_R_tile` をキャッシュし、毎サイクル `q = np.hstack([q_Q_tile * xr[:-nx], -self.QN.dot(xr[-nx:]), q_R_tile * ur])`。`update_Q/update_R/update_QN`（`MPC.py:74-81`）でキャッシュ dict を `clear()`。
8. `xmin_dyn`/`xmax_dyn`/`umax_dyn` の `np.kron(np.ones(...), ...)` は N キーでテンプレートをキャッシュし毎サイクル `.copy()`。
9. **この Task では `self.optimizer = osqp.OSQP(); setup(...)` は現状のまま残す**（Task 5 で置換）。ub/lb 取得ブロック（`MPC.py:145-165`）、`xmin_dyn[0]=...` 以降（167-171）、境界構築（197-213）は現状ロジック維持（ただし `l`/`u` の組み立ては既存式のまま）。

- [ ] **Step 1: `reference_path.py` に kappa/v_ref/segment 長の配列キャッシュを追加**

`_rebuild_geometry_cache` に追記:

```python
self.kappas = np.array([wp.kappa for wp in self.waypoints])
self.v_refs = np.array([wp.v_ref for wp in self.waypoints])
```

`set_v_ref`（`reference_path.py:231-233`）の末尾に `self.v_refs = np.asarray(v_ref, dtype=float)` を追加。**`wp.v_ref` を個別代入している他の箇所**（`compute_speed_profile`、`_construct_path` 末尾の `waypoints[-1].v_ref = 0.0` 等）を `grep -n "\.v_ref" multi_purpose_mpc_ros/core/reference_path.py` で洗い出し、v_ref 変更が完了する各箇所の後で `self.v_refs = np.array([wp.v_ref for wp in self.waypoints])` を再構築すること（構築時のみの処理なのでコストは無視できる）。kappa も同様に `\.kappa =` 代入箇所を確認。

- [ ] **Step 2: `linearize_batch` を追加し、単体で `linearize` と一致することを確認するテストを書く**

Create: `test/test_mpc_vectorization.py`

```python
import numpy as np
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def test_linearize_batch_matches_scalar():
    from multi_purpose_mpc_ros.core.spatial_bicycle_models import BicycleModel
    rng = np.random.default_rng(0)
    v = np.concatenate([rng.uniform(0.1, 10, 50), [0.0]])
    k = rng.uniform(-0.5, 0.5, 51)
    ds = rng.uniform(0.3, 0.9, 51)
    f_b, A_b, B_b = BicycleModel.linearize_batch(None, v, k, ds)
    for i in range(51):
        f, A, B = BicycleModel.linearize(None, v[i], k[i], ds[i])
        assert np.array_equal(f, f_b[i]) and np.array_equal(A, A_b[i]) and np.array_equal(B, B_b[i])
```

（`linearize` が self を使わないことを確認の上、None 渡しで呼ぶ。使っていれば軽量ダミーで代替。）

- [ ] **Step 3: テスト実行（linearize_batch 未実装で FAIL → 実装 → PASS）**

```bash
python3 -m pytest test/test_mpc_vectorization.py -q
```

- [ ] **Step 4: `_init_problem` を上記設計どおり書き換え**

- [ ] **Step 5: 等価性 + ベンチ**

```bash
python3 -m pytest test/ -q
python3 test/perf/benchmark_mpc.py --cycles 800 --check-golden $SCRATCH/golden_baseline.npz \
  --rms-tol 0 --traj-rms-tol 0 | tee $SCRATCH/bench_task4.txt
```
Expected: bit-identical（`max|Δu| = 0`）。浮動小数の結合順差で 0 にならない場合のみ、`--rms-tol 1e-9 --traj-rms-tol 1e-6` まで緩和可（それ以上の差は実装ミスとして調査）。時間統計を記録。

- [ ] **Step 6: Commit**

```bash
git commit -m "perf(mpc): vectorize horizon assembly and cache constant QP structures" -- <changed files>
```

---

### Task 5: OSQP 永続化 + `update()` + ウォームスタート

**Files:**
- Modify: `multi_purpose_mpc_ros/core/MPC.py`（`_init_problem` 末尾と `get_control` の retry ループ）

**Interfaces:**
- Produces: `MPC._setup_or_update(P, q, A_full, l, u, N)` — 前回と N・A_full のスパースパターン（`indptr`/`indices` 一致）・P パターンが同一なら `self.optimizer.update(q=q, l=l, u=u, Ax=A_full.data)`、それ以外は `self.optimizer = osqp.OSQP(); setup(...)`。コスト行列変更時（`update_Q/R/QN`）は強制 re-setup。

設計:

```python
def _setup_or_update(self, P, q, A_full, l, u, N):
    c = self._osqp_cache
    if (c is not None and c["N"] == N
            and np.array_equal(c["A_indptr"], A_full.indptr)
            and np.array_equal(c["A_indices"], A_full.indices)):
        self.optimizer.update(q=q, l=l, u=u, Ax=A_full.data)
        return
    self.optimizer = osqp.OSQP()
    self.optimizer.setup(P=P, q=q, A=A_full, l=l, u=u, verbose=False)
    self._osqp_cache = {"N": N, "A_indptr": A_full.indptr.copy(),
                        "A_indices": A_full.indices.copy()}
```

- `self._osqp_cache = None` を `__init__` で初期化。`update_Q/update_R/update_QN/update_v_max/update_ay_max` では `self._osqp_cache = None` にして次サイクルで re-setup（v_max/ay_max は l/u/q 経由だが安全側で可。ただし**毎サイクル呼ばれる経路がないこと**を `grep -n "update_v_max\|update_ay_max" multi_purpose_mpc_ros/` で確認。呼ばれるのはパラメータコールバックのみのはず）。
- Task 4 で Aeq を座標形式（構造ゼロ保持）にしたので、A_full のパターンは毎サイクル一致し update 経路に乗る。パターン検証は `array_equal`（数千要素、数 µs〜数十 µs）で毎サイクル行い、不一致なら黙って re-setup（安全フォールバック）。
- **infeasible retry ループ**（`get_control` 内 `MPC.py:255-266`）は `_init_problem(N, relaxed_safety_margin)` を呼び直す現行構造のまま — `_setup_or_update` が update 経路に乗るため retry も安価になる。
- OSQP はデフォルトで前回解からウォームスタートする（`update` はそれを保持）。追加設定不要。

- [ ] **Step 1: 実装**

`_init_problem` 末尾の `self.optimizer = osqp.OSQP(); self.optimizer.setup(...)`（Task 4 適用後の該当行）を `self._setup_or_update(P, q, A_full, l, u, N)` に置換し、上記メソッドとキャッシュ無効化を追加。

- [ ] **Step 2: 閉ループ等価性 + ベンチ（ソルバ許容誤差レベルの差は許容）**

```bash
python3 -m pytest test/ -q
python3 test/perf/benchmark_mpc.py --cycles 800 --check-golden $SCRATCH/golden_baseline.npz \
  --rms-tol 0.05 --traj-rms-tol 0.05 | tee $SCRATCH/bench_task5.txt
```
Expected: 閉ループ軌跡 RMS 偏差 < 0.05 m、exit 0。時間統計が大幅改善していること。**もし軌跡偏差が 0.05 m を超える場合**: `optimizer.update` 後に `self.optimizer.warm_start(x=None, y=None)` でコールドスタート化して差の原因を切り分け、それでも超えるなら本タスクを revert し Task 4 までで PR する（判断は親セッションにエスカレーション）。

- [ ] **Step 3: Commit**

```bash
git commit -m "perf(mpc): reuse OSQP workspace via update() with warm start" -- <changed files>
```

---

### Task 6: 総仕上げ — 最終計測・ビルド確認・PR

**Files:**
- Modify: `aichallenge/workspace/src/aichallenge_submit/multi_purpose_mpc_ros/README.md`（ベンチ結果と実行方法を追記）

- [ ] **Step 1: 最終ベンチ（before/after 表を作る）**

baseline / task2 / task4 / task5 の mean/p50/p95 を表にまとめ README に「Performance」節として追記（ベンチの実行コマンド付き）。

- [ ] **Step 2: コンテナで colcon ビルド確認**

worktree に `.env` がなければ main checkout からコピー（`cp /home/taikitanaka/aic/aichallenge-racingkart/.env aichallenge/../../.env` 相当、実際のパスを確認）。その後:

```bash
cd /home/taikitanaka/aic/.worktrees/mpc-speedup
make autoware-build 2>&1 | tail -20
```
Expected: `multi_purpose_mpc_ros` を含めビルド成功。失敗したらログを確認して修正。

- [ ] **Step 3: pre-commit**

```bash
pre-commit run -a
```
Expected: PASS（YAML/XML/shell に触っていなければ素通り）。

- [ ] **Step 4: README コミット + push + draft PR（base=dev、説明は日本語）**

```bash
git add README.md && git commit -m "docs(mpc): document MPC core benchmark and speedup results"
git push -u origin feat/mpc-speedup
gh pr create --draft --base dev --title "perf(mpc): multi_purpose_mpc_ros 制御ループ高速化" --body "<日本語の summary + before/after ベンチ表 + 検証方法>"
```
