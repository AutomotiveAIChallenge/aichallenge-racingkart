# race_control ブラッシュアップ Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `race_control` パッケージ(lap_counter / route_safety_monitor / visualizer)のコード品質を正規化し、点内判定と trail 描画を numpy でベクトル化して高速化する。機能追加はしない。

**Architecture:** 純粋な幾何ロジック(`LapTracker`, `RouteArea`)を ROS 非依存モジュールに抽出し、`ament_python_install_package` で正規インストールする。ノードは薄いラッパになる。純粋モジュールは plain pytest でテストする。

**Tech Stack:** ROS 2 Humble (ament_cmake + ament_cmake_python), Python 3.10, numpy, OpenCV, pytest

## Global Constraints

- パッケージルート: `aichallenge/workspace/src/aichallenge_system/race_control/`(以下、相対パスはここ基準)
- 挙動変更禁止: ラップ判定・デバウンス・逸脱判定・トピック名・パラメータ名は既存と同一に保つ(debounce時に `lap_start` を更新しない挙動も維持)
- Conventional Commits。Claude co-author trailer 禁止
- コミットは必ず `git commit -- <paths>` でパス限定する
- テスト実行: パッケージルートで `python3 -m pytest test/ -q`(ホストに numpy/pytest あり。cwd がパッケージルートなら `race_control/` が `__init__.py` 経由で import 可能)
- 最終ビルド検証はリポジトリルートで `make autoware-build`

## File Structure

```
race_control/
├── CMakeLists.txt                 # ament_python_install_package 追加、テスト wiring
├── package.xml                    # test_depend 追加
├── README.md                      # 見出し階層修正
├── config/
│   ├── lap_counter.param.yaml            # 既存(変更なし)
│   └── route_safety_monitor.param.yaml   # 新規
├── launch/
│   ├── race_control.launch.xml           # 両ノード統合
│   └── route_safety_monitor.launch.xml   # param file 読み込み追加
├── race_control/
│   ├── __init__.py                # 新規(空)
│   ├── lanelet_map.py             # 既存(変更なし)
│   ├── lap_tracker.py             # 新規: 純粋ラップ判定ロジック
│   ├── route_area.py              # 新規: numpy ポリゴン containment
│   ├── lap_counter_node.py        # 薄く: LapTracker を使う
│   ├── route_safety_monitor.py    # 薄く: RouteArea を使う
│   └── route_safety_visualizer.py # trail 描画ベクトル化
└── test/
    ├── test_lap_tracker.py        # 新規
    └── test_route_area.py         # 新規
```

---

### Task 1: Python パッケージ正規化(sys.path ハック除去)

**Files:**
- Create: `race_control/__init__.py`(空ファイル)
- Modify: `CMakeLists.txt`
- Modify: `race_control/lap_counter_node.py`(import 部のみ)
- Modify: `race_control/route_safety_monitor.py`(import 部のみ)
- Modify: `race_control/route_safety_visualizer.py`(import 部のみ)

**Interfaces:**
- Produces: `race_control.lanelet_map` が site-packages モジュールとして import 可能。後続タスクは `from race_control.lap_tracker import LapTracker` 形式を前提とする。

- [ ] **Step 1: `race_control/__init__.py` を空で作成**

- [ ] **Step 2: CMakeLists.txt に module install を追加**

`find_package(ament_cmake_python REQUIRED)` の直後に:

```cmake
ament_python_install_package(${PROJECT_NAME})
```

`install(PROGRAMS ...)` はそのまま残すが、`race_control/lanelet_map.py` の行は削除する(モジュールとして入るため実行体は不要)。

- [ ] **Step 3: 3ファイルの import ハックを置換**

`lap_counter_node.py`:

```python
# 削除:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lanelet_map import LaneletMap  # noqa: E402
# 置換:
from race_control.lanelet_map import LaneletMap
```

不要になった `import os` / `import sys` も削除。`route_safety_monitor.py` も同様(こちらは `os` を `get_package_share_directory` の join で使うので `os` は残す)。`route_safety_visualizer.py` は:

```python
# 削除:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from route_safety_monitor import RouteDeviationSafetyMonitor
# 置換:
from race_control.route_safety_monitor import RouteDeviationSafetyMonitor
```

- [ ] **Step 4: ビルドして launch 解決を確認**

リポジトリルートで:

```bash
make autoware-build
```

Expected: `race_control` のビルド成功(Failed 0)。

- [ ] **Step 5: コミット**

```bash
git add aichallenge/workspace/src/aichallenge_system/race_control
git commit -m "refactor(race_control): install python module properly, drop sys.path hacks" -- aichallenge/workspace/src/aichallenge_system/race_control
```

---

### Task 2: LapTracker 抽出(TDD)

**Files:**
- Create: `race_control/lap_tracker.py`
- Create: `test/test_lap_tracker.py`
- Modify: `race_control/lap_counter_node.py`

**Interfaces:**
- Produces: `LapTracker(line_a, line_b, margin, min_lap_time)` — `update(x, y, stamp) -> bool`(周回登録時 True)、属性 `lap_count: int`(初期 -1)、`lap_start: float | None`、`lap_times: list[float]`。

- [ ] **Step 1: failing test を書く**

`test/test_lap_tracker.py`:

```python
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
```

- [ ] **Step 2: 失敗を確認**

```bash
cd aichallenge/workspace/src/aichallenge_system/race_control && python3 -m pytest test/test_lap_tracker.py -q
```

Expected: `ModuleNotFoundError: No module named 'race_control.lap_tracker'` で collection error。

- [ ] **Step 3: `race_control/lap_tracker.py` を実装**

```python
"""Pure start-line crossing / lap timing logic (no ROS dependency)."""

import math


class LapTracker:
    """Counts start-line crossings with segment-extent and debounce checks.

    Behavior contract (must match the original lap_counter node exactly):
    - first crossing arms lap 0; each later valid crossing increments lap_count
    - a crossing closer than min_lap_time to lap_start is ignored entirely
      (lap_start is NOT reset by a debounced crossing)
    """

    def __init__(self, line_a, line_b, margin=2.0, min_lap_time=10.0):
        self._a = line_a
        self._ab = (line_b[0] - line_a[0], line_b[1] - line_a[1])
        ab_len = math.hypot(*self._ab)
        self._ab_len2 = ab_len * ab_len
        self._margin_t = margin / ab_len
        self._min_lap_time = min_lap_time
        self._prev_side = None
        self.lap_start = None
        self.lap_count = -1
        self.lap_times = []

    def update(self, x, y, stamp):
        """Feed one position sample; returns True when a crossing registers."""
        apx, apy = x - self._a[0], y - self._a[1]
        side = self._ab[0] * apy - self._ab[1] * apx > 0.0
        t = (self._ab[0] * apx + self._ab[1] * apy) / self._ab_len2
        on_segment = -self._margin_t <= t <= 1.0 + self._margin_t

        crossed = False
        if self._prev_side is not None and side != self._prev_side and on_segment:
            crossed = self._register(stamp)
        self._prev_side = side
        return crossed

    def _register(self, stamp):
        if self.lap_start is not None:
            lap_time = stamp - self.lap_start
            if lap_time < self._min_lap_time:
                return False
            self.lap_times.append(lap_time)
        self.lap_count += 1
        self.lap_start = stamp
        return True
```

- [ ] **Step 4: テスト通過を確認**

```bash
python3 -m pytest test/test_lap_tracker.py -q
```

Expected: 7 passed。

- [ ] **Step 5: `lap_counter_node.py` を LapTracker に載せ替え**

`_on_odom` / `_on_cross` の幾何・状態管理を LapTracker に委譲する。ノード全体像:

```python
#!/usr/bin/env python3
"""Lap counter node.

Derives a start line from the lanelet2 map (the entry edge of a configured
lanelet: first left-bound node to first right-bound node, using local_x/local_y
tags) and counts laps / lap times each time the vehicle crosses it.
Geometry/state lives in race_control.lap_tracker.LapTracker (pure, tested).
"""

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from std_msgs.msg import Float64, Int32, String

from race_control.lanelet_map import LaneletMap
from race_control.lap_tracker import LapTracker


def load_start_line(map_path: str, lanelet_id: int):
    """Return ((ax, ay), (bx, by)): entry edge of the given lanelet."""
    lmap = LaneletMap(map_path)
    bound = lmap.lanelet(lanelet_id)
    if bound is None:
        raise ValueError(f"lanelet id {lanelet_id} not found in {map_path}")
    left_way, right_way = bound
    return lmap.way_coords(left_way)[0], lmap.way_coords(right_way)[0]


class LapCounterNode(Node):
    def __init__(self):
        super().__init__("lap_counter")
        map_path = self.declare_parameter("map_path", "").value
        lanelet_id = self.declare_parameter("start_lanelet_id", 14).value
        min_lap_time = self.declare_parameter("min_lap_time", 10.0).value
        margin = self.declare_parameter("line_margin", 2.0).value
        odom_topic = self.declare_parameter(
            "odom_topic", "/localization/kinematic_state"
        ).value

        line_a, line_b = load_start_line(map_path, lanelet_id)
        self._tracker = LapTracker(
            line_a, line_b, margin=margin, min_lap_time=min_lap_time
        )
        self.get_logger().info(
            f"start line: ({line_a[0]:.2f}, {line_a[1]:.2f}) -> "
            f"({line_b[0]:.2f}, {line_b[1]:.2f}) (lanelet {lanelet_id})"
        )

        self._pub_count = self.create_publisher(Int32, "~/lap_count", 1)
        self._pub_last = self.create_publisher(Float64, "~/last_lap_time", 1)
        self._pub_current = self.create_publisher(Float64, "~/current_lap_time", 1)
        self._pub_summary = self.create_publisher(String, "~/summary", 1)
        self.create_subscription(Odometry, odom_topic, self._on_odom, 10)

    def _on_odom(self, msg: Odometry):
        p = msg.pose.pose.position
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        if self._tracker.update(p.x, p.y, stamp):
            self._on_lap()
        if self._tracker.lap_start is not None:
            self._pub_current.publish(Float64(data=stamp - self._tracker.lap_start))

    def _on_lap(self):
        tracker = self._tracker
        if tracker.lap_times:
            self._pub_last.publish(Float64(data=tracker.lap_times[-1]))
        self._pub_count.publish(Int32(data=tracker.lap_count))
        times = ", ".join(f"{t:.2f}" for t in tracker.lap_times)
        summary = f"lap={tracker.lap_count} lap_times=[{times}]"
        self._pub_summary.publish(String(data=summary))
        self.get_logger().info(summary)


def main():
    rclpy.init()
    node = None
    try:
        node = LapCounterNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
```

注意: 元コードは「最初のクロス」では `last_lap_time` を publish しない(`lap_times` 空)— `if tracker.lap_times:` ガードで同じ挙動になる。

- [ ] **Step 6: 全テスト再実行**

```bash
python3 -m pytest test/ -q
```

Expected: passed。

- [ ] **Step 7: コミット**

```bash
git add aichallenge/workspace/src/aichallenge_system/race_control
git commit -m "refactor(race_control): extract pure LapTracker with pytest coverage" -- aichallenge/workspace/src/aichallenge_system/race_control
```

---

### Task 3: RouteArea numpy 化 + bbox 事前判定(TDD + 実測)

**Files:**
- Create: `race_control/route_area.py`
- Create: `test/test_route_area.py`
- Modify: `race_control/route_safety_monitor.py`
- Modify: `race_control/route_safety_visualizer.py`(polygon 参照の追随)

**Interfaces:**
- Produces: `RouteArea(polygons: list[list[tuple[float, float]]])` — `contains(x, y) -> bool`、`polygons: list[tuple[tuple[float,...], tuple[float,...]]]`(xs, ys のタプル。visualizer が描画に使う)、classmethod `RouteArea.from_osm(osm_path) -> RouteArea`、`__len__`。
- Consumes: `race_control.lanelet_map.LaneletMap`(Task 1 で正規 import 化済み)。

- [ ] **Step 1: failing test を書く**

`test/test_route_area.py`:

```python
import numpy as np
import pytest

from race_control.route_area import RouteArea

SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
TRIANGLE = [(20.0, 0.0), (30.0, 0.0), (25.0, 10.0)]


@pytest.fixture
def area():
    return RouteArea([SQUARE, TRIANGLE])


class TestContains:
    def test_inside_square(self, area):
        assert area.contains(5.0, 5.0) is True

    def test_inside_triangle(self, area):
        assert area.contains(25.0, 2.0) is True

    def test_outside_all(self, area):
        assert area.contains(15.0, 5.0) is False
        assert area.contains(-1.0, 5.0) is False

    def test_outside_bbox_short_circuits(self, area):
        assert area.contains(1000.0, 1000.0) is False

    def test_degenerate_polygon_dropped(self):
        area = RouteArea([[(0.0, 0.0), (1.0, 1.0)], SQUARE])
        assert len(area) == 1

    def test_matches_reference_raycast(self, area):
        """既存実装(純Python ray-cast)と結果が一致すること。"""

        def reference(x, y, poly):
            n = len(poly)
            j = n - 1
            inside = False
            for i in range(n):
                xi, yi = poly[i]
                xj, yj = poly[j]
                if ((yi > y) != (yj > y)) and (
                    x < (xj - xi) * (y - yi) / (yj - yi) + xi
                ):
                    inside = not inside
                j = i
            return inside

        rng = np.random.default_rng(0)
        pts = rng.uniform(-5.0, 35.0, size=(500, 2))
        for x, y in pts:
            expected = reference(x, y, SQUARE) or reference(x, y, TRIANGLE)
            assert area.contains(float(x), float(y)) == expected, (x, y)
```

- [ ] **Step 2: 失敗を確認**

```bash
python3 -m pytest test/test_route_area.py -q
```

Expected: `ModuleNotFoundError: No module named 'race_control.route_area'`。

- [ ] **Step 3: `race_control/route_area.py` を実装**

```python
"""Drivable route area as numpy lanelet polygons with fast containment tests.

Pure geometry (no ROS dependency). Each polygon is pre-converted to numpy
edge arrays and a bounding box, so a containment query is a bbox reject per
polygon plus one vectorized ray-cast for the few candidates that remain.
"""

import numpy as np

from race_control.lanelet_map import LaneletMap


class RouteArea:
    def __init__(self, polygons):
        """polygons: iterable of coordinate lists [(x, y), ...] (>= 3 points)."""
        self.polygons = []  # (xs_tuple, ys_tuple) — kept for visualization
        self._edges = []  # (xi, yi, xj, yj) numpy arrays per polygon
        self._bboxes = []  # (x_min, x_max, y_min, y_max) per polygon
        for coords in polygons:
            if len(coords) < 3:
                continue
            xs = np.asarray([c[0] for c in coords], dtype=np.float64)
            ys = np.asarray([c[1] for c in coords], dtype=np.float64)
            self.polygons.append((tuple(xs), tuple(ys)))
            # edge i runs from vertex j = i-1 to vertex i (same as classic ray-cast)
            self._edges.append((xs, ys, np.roll(xs, 1), np.roll(ys, 1)))
            self._bboxes.append((xs.min(), xs.max(), ys.min(), ys.max()))

    @classmethod
    def from_osm(cls, osm_path):
        """Build from a lanelet2 .osm map: left bound + reversed right bound."""
        lmap = LaneletMap(osm_path)
        polygons = [
            lmap.way_coords(left) + list(reversed(lmap.way_coords(right)))
            for _lid, left, right in lmap.lanelets
        ]
        return cls(polygons)

    def __len__(self):
        return len(self._edges)

    def contains(self, x, y):
        """True if (x, y) is inside any lanelet polygon."""
        for (xi, yi, xj, yj), (x0, x1, y0, y1) in zip(self._edges, self._bboxes):
            if not (x0 <= x <= x1 and y0 <= y <= y1):
                continue
            crossing = (yi > y) != (yj > y)
            if not crossing.any():
                continue
            with np.errstate(divide="ignore", invalid="ignore"):
                x_int = (xj - xi) * (y - yi) / (yj - yi) + xi
            if np.count_nonzero(crossing & (x < x_int)) % 2:
                return True
        return False
```

- [ ] **Step 4: テスト通過を確認**

```bash
python3 -m pytest test/test_route_area.py -q
```

Expected: 6 passed。

- [ ] **Step 5: `route_safety_monitor.py` を RouteArea に載せ替え**

`_point_in_polygon` 関数と `RouteDeviationSafetyMonitor` クラスの polygon 構築部を削除し、RouteArea を使う。互換のため `RouteDeviationSafetyMonitor` クラス名と `is_in_any_lane` メソッドは残す(visualizer が使用):

```python
class RouteDeviationSafetyMonitor:
    """Thin wrapper: builds a RouteArea from an .osm map and tests containment."""

    def __init__(self, osm_file_path, logger=None):
        self.area = RouteArea.from_osm(osm_file_path)
        if logger:
            logger.info(
                f"Loaded {len(self.area)} lanelet polygons from {osm_file_path}"
            )

    def is_in_any_lane(self, x, y):
        return self.area.contains(x, y)
```

import は `from race_control.route_area import RouteArea` を追加。ノードクラス(`RouteDeviationSafetyMonitorNode`)は変更しない。

- [ ] **Step 6: visualizer の polygon 参照を追随**

`route_safety_visualizer.py` の `MapRenderer.__init__` 冒頭:

```python
# 変更前:
polys = monitor._lane_polygons
# 変更後:
polys = monitor.area.polygons
```

- [ ] **Step 7: マイクロベンチマークで実測**

scratchpad にベンチスクリプトを書いて実行(コミットしない)。旧実装は `git show HEAD~1:...route_safety_monitor.py` から取り出す。実マップ `map/route_area.osm` に対しランダム点(トラック bbox 内外混合)10,000 点で新旧の `is_in_any_lane` 総時間を計測し、数値を記録する。

Expected: 新実装が同等以上(bbox 棄却が効く分速い)。結果の数値を最終報告に含める。

- [ ] **Step 8: 全テスト + コミット**

```bash
python3 -m pytest test/ -q
git add aichallenge/workspace/src/aichallenge_system/race_control
git commit -m "perf(race_control): numpy point-in-polygon with bbox prefilter" -- aichallenge/workspace/src/aichallenge_system/race_control
```

---

### Task 4: visualizer trail 描画のベクトル化(実測付き)

**Files:**
- Modify: `race_control/route_safety_visualizer.py`

**Interfaces:**
- Consumes: `MapRenderer.to_px(x, y)` の変換パラメータ(`x_min`, `y_min`, `scale`, `ox`, `oy`, `h`, `w`)。

- [ ] **Step 1: `_draw_trail` を numpy 一括描画に置換**

最大 600 点それぞれへの Python ループ + `cv2.circle` 呼び出しを、numpy での一括座標変換・色計算・fancy-indexing でのピクセル書き込みに置き換える。見た目は維持(fade 0.12→1.0、古い点は小、新しい点は大):

```python
# r=1 / r=2 の cv2.circle 塗りつぶしと同形状のオフセット
_OFFSETS_SMALL = np.array(
    [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx * dx + dy * dy <= 1],
    dtype=np.int32,
)
_OFFSETS_BIG = np.array(
    [(dx, dy) for dx in range(-2, 3) for dy in range(-2, 3) if dx * dx + dy * dy <= 4],
    dtype=np.int32,
)


def _draw_trail(frame, renderer, trail):
    n = len(trail)
    if n == 0:
        return
    arr = np.asarray(trail, dtype=np.float64)  # columns: x, y, deviated
    px = ((arr[:, 0] - renderer.x_min) * renderer.scale + renderer.ox).astype(np.int32)
    py = (
        renderer.h - ((arr[:, 1] - renderer.y_min) * renderer.scale + renderer.oy)
    ).astype(np.int32)
    dev = arr[:, 2] >= 0.5

    t = np.arange(n, dtype=np.float32) / n  # 0 = oldest, 1 = newest
    alpha = 0.12 + 0.88 * t
    colors = np.where(dev[:, None], _TRAIL_NG_BASE, _TRAIL_OK_BASE)
    colors = (colors * alpha[:, None]).astype(np.uint8)

    h, w = frame.shape[:2]
    big = t >= 0.5
    for mask, offsets in ((~big, _OFFSETS_SMALL), (big, _OFFSETS_BIG)):
        if not mask.any():
            continue
        # (points, offsets) 全組み合わせのピクセル座標
        qx = (px[mask, None] + offsets[None, :, 0]).ravel()
        qy = (py[mask, None] + offsets[None, :, 1]).ravel()
        qc = np.repeat(colors[mask], len(offsets), axis=0)
        ok = (qx >= 0) & (qx < w) & (qy >= 0) & (qy < h)
        frame[qy[ok], qx[ok]] = qc[ok]
```

呼び出し側は `_draw_trail(frame, renderer, list(node._trail))` に変更。deque の中身 `(x, y, deviated)` はそのまま(bool は float に落ちる)。

- [ ] **Step 2: 動作確認(ヘッドレスでフレーム生成)**

GUI なし(`cv2.imshow` を呼ばない)で描画パスを1回通す最小スクリプトを scratchpad に書いて実行する。内容:

1. `RouteDeviationSafetyMonitor("map/route_area.osm")` を構築(logger なし)
2. `MapRenderer(monitor)` を構築
3. トラック bbox 内のダミー trail 600 点 `[(x, y, False), ...]` を生成
4. `frame = renderer.new_frame()` → `_draw_trail(frame, renderer, trail)` を実行
5. `frame` と `renderer.new_frame()` の差分ピクセル数が 0 より大きいことを assert

注意: `route_safety_visualizer.py` はトップレベルで rclpy を import するが、import だけなら ROS デーモン不要でホストでも通る。通らなければ同スクリプトをコンテナ(`docker compose run --rm --no-deps autoware-build`)で実行する。

Expected: assert 通過(エラーなくピクセルが描かれる)。

- [ ] **Step 3: ベンチマーク**

同じ scratchpad スクリプト内で旧 `_draw_trail`(git show HEAD~1 から取得)と新実装を 600 点 trail で各 200 回実行して比較計測し、数値を記録する。

Expected: 新実装が大幅に速い(目安 5〜20 倍)。数値を最終報告に含める。

- [ ] **Step 4: コミット**

```bash
git add aichallenge/workspace/src/aichallenge_system/race_control
git commit -m "perf(race_control): vectorize visualizer trail drawing" -- aichallenge/workspace/src/aichallenge_system/race_control
```

---

### Task 5: param/launch/README 整備 + colcon test wiring

**Files:**
- Create: `config/route_safety_monitor.param.yaml`
- Modify: `launch/race_control.launch.xml`
- Modify: `launch/route_safety_monitor.launch.xml`
- Modify: `race_control/route_safety_monitor.py`(osm_path 空文字フォールバック)
- Modify: `CMakeLists.txt`, `package.xml`(pytest wiring)
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1〜4 の成果一式。

- [ ] **Step 1: `config/route_safety_monitor.param.yaml` を作成**

```yaml
/**:
  ros__parameters:
    # Absolute path to the route-area .osm map. Empty -> package map/route_area.osm.
    osm_path: ""
    odom_topic: /localization/kinematic_state
    deviation_topic: /vehicle/emergency/is_route_deviation
    # Containment check period (seconds).
    monitor_period: 0.5
```

- [ ] **Step 2: ノードの osm_path フォールバックを空文字対応に**

`route_safety_monitor.py` のノード `__init__`:

```python
osm_path = self.declare_parameter("osm_path", "").value
if not osm_path:
    osm_path = os.path.join(
        get_package_share_directory("race_control"), "map", "route_area.osm"
    )
```

- [ ] **Step 3: launch を整備**

`launch/route_safety_monitor.launch.xml`(param file 読み込み追加):

```xml
<launch>
  <arg name="visualize" default="false"/>
  <arg name="param_file" default="$(find-pkg-share race_control)/config/route_safety_monitor.param.yaml"/>

  <node pkg="race_control" exec="route_safety_monitor.py" name="route_deviation_safety_monitor" output="screen">
    <param from="$(var param_file)"/>
  </node>

  <node pkg="race_control" exec="route_safety_visualizer.py" name="route_safety_visualizer" output="screen" if="$(var visualize)"/>
</launch>
```

`launch/race_control.launch.xml`(両ノード統合、個別 on/off):

```xml
<launch>
  <arg name="map_path" default="$(find-pkg-share aichallenge_submit_launch)/map/lanelet2_map.osm"/>
  <arg name="lap_counter" default="true"/>
  <arg name="route_safety_monitor" default="true"/>
  <arg name="visualize" default="false"/>
  <arg name="lap_counter_param_file" default="$(find-pkg-share race_control)/config/lap_counter.param.yaml"/>

  <node pkg="race_control" exec="lap_counter_node.py" name="lap_counter" output="screen" if="$(var lap_counter)">
    <param from="$(var lap_counter_param_file)"/>
    <param name="map_path" value="$(var map_path)"/>
  </node>

  <include file="$(find-pkg-share race_control)/launch/route_safety_monitor.launch.xml" if="$(var route_safety_monitor)">
    <arg name="visualize" value="$(var visualize)"/>
  </include>
</launch>
```

- [ ] **Step 4: colcon test wiring**

`CMakeLists.txt` の `ament_package()` 前に:

```cmake
if(BUILD_TESTING)
  find_package(ament_cmake_pytest REQUIRED)
  ament_add_pytest_test(race_control_pytest test
    WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
  )
endif()
```

`package.xml` に:

```xml
  <test_depend>ament_cmake_pytest</test_depend>
```

- [ ] **Step 5: README を修正**

見出し階層を直す(`## lap_counter` の下は `###`)。route_safety_monitor 節に param file と `race_control.launch.xml` 統合の記述を追加。トピック表・使い方は現状に合わせて更新。全体構成:

```markdown
# race_control

Race judging tools.

- **lap_counter** — counts laps and lap times by detecting start-line crossings.
- **route_safety_monitor** — flags when the vehicle leaves the drivable route
  area, with an optional real-time OpenCV visualizer.

Run both together:

    ros2 launch race_control race_control.launch.xml
    # options: lap_counter:=false / route_safety_monitor:=false / visualize:=true

Tests (pure geometry, no ROS needed): `python3 -m pytest test/ -q`
(also wired into `colcon test`).

## lap_counter

### How it works
...(既存本文)

### Topics
...(既存表)

### Usage / Parameters
...(既存本文、config 言及)

## route_safety_monitor

### How it works
...(既存本文。0.5 s は `monitor_period` パラメータになった旨、numpy + bbox prefilter の一文を追加)

### Topics
...(既存表)

### Usage
...(既存本文 + `config/route_safety_monitor.param.yaml` 言及)
```

- [ ] **Step 6: フルビルド + colcon test + pre-commit**

```bash
make autoware-build
```

Expected: 成功。続いてコンテナ内 colcon test(autoware-build サービス経由):

```bash
docker compose run --rm --no-deps autoware-build bash -c "source /autoware/install/setup.bash && cd /aichallenge/workspace && colcon test --packages-select race_control && colcon test-result --verbose"
```

Expected: race_control_pytest 通過。最後に:

```bash
pre-commit run -a
```

Expected: passed(XML/YAML チェック含む)。

- [ ] **Step 7: コミット**

```bash
git add aichallenge/workspace/src/aichallenge_system/race_control
git commit -m "chore(race_control): param file, unified launch, README, colcon test wiring" -- aichallenge/workspace/src/aichallenge_system/race_control
```
