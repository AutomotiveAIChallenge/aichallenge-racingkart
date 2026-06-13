from pathlib import Path

import numpy as np
import pytest

from race_judge_py.geometry.osm_map import load_lanelet_map

FIXTURE = Path(__file__).parent / "fixtures" / "mini_course.osm"
REAL_MAP = (
    Path(__file__).resolve().parents[3]
    / "aichallenge_submit"
    / "aichallenge_submit_launch"
    / "map"
    / "lanelet2_map.osm"
)


def test_loads_two_polygons():
    m = load_lanelet_map(str(FIXTURE))
    assert len(m.polygons) == 2
    # lanelet A のポリゴンは左(1→2)+右逆順(4→3)の4点
    assert m.polygons[0].shape[1] == 2


def test_centerline_is_chained_loop():
    m = load_lanelet_map(str(FIXTURE))
    c = m.centerline
    assert len(c) >= 4
    # チェーン終端は始端の近く(レーン中心 y=8 と y=2 の2本が繋がる: 端点ギャップ<=10m)
    assert np.linalg.norm(c[-1] - c[0]) <= 10.0
    # centerline は両レーン中心(y=8, y=2)を通る
    ys = c[:, 1]
    assert ys.max() == pytest.approx(8.0, abs=0.5)
    assert ys.min() == pytest.approx(2.0, abs=0.5)


@pytest.mark.skipif(not REAL_MAP.exists(), reason="real map not found")
def test_real_map_loads_and_closes():
    m = load_lanelet_map(str(REAL_MAP))
    assert len(m.polygons) >= 5
    c = m.centerline
    assert len(c) > 100
    # 実コースは周回路: チェーンが閉じること
    assert np.linalg.norm(c[-1] - c[0]) < 15.0
