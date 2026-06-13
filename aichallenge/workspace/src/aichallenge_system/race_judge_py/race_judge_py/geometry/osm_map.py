from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

import numpy as np


@dataclass
class LaneletMap:
    polygons: list      # lanelet 毎の閉ポリゴン (K,2) ndarray のリスト
    centerline: np.ndarray  # 全lanelet midline をチェーンした閉ループ (M,2)


def _parse_nodes(root):
    nodes = {}
    for node in root.findall("node"):
        local_x = local_y = None
        for tag in node.findall("tag"):
            if tag.attrib["k"] == "local_x":
                local_x = float(tag.attrib["v"])
            elif tag.attrib["k"] == "local_y":
                local_y = float(tag.attrib["v"])
        if local_x is not None and local_y is not None:
            nodes[node.attrib["id"]] = (local_x, local_y)
    return nodes


def _way_coords(ways, nodes, way_id):
    way = ways.get(way_id)
    if way is None:
        return []
    return [nodes[ref] for ref in way if ref in nodes]


def _resample(line: np.ndarray, count: int) -> np.ndarray:
    seg_len = np.linalg.norm(np.diff(line, axis=0), axis=1)
    cum = np.concatenate(([0.0], np.cumsum(seg_len)))
    if cum[-1] <= 0.0:
        return np.repeat(line[:1], count, axis=0)
    s = np.linspace(0.0, cum[-1], count)
    return np.stack([np.interp(s, cum, line[:, 0]), np.interp(s, cum, line[:, 1])], axis=1)


def _chain_midlines(midlines: list, tol: float = 10.0) -> np.ndarray:
    """lanelet 毎の midline を端点最近傍で貪欲にチェーンして1本のループにする。
    向きが逆の midline は反転して接続する。"""
    remaining = list(range(1, len(midlines)))
    chain = [midlines[0]]
    while remaining:
        end = chain[-1][-1]
        best, best_d, best_rev = None, None, False
        for j in remaining:
            m = midlines[j]
            d0 = float(np.linalg.norm(m[0] - end))
            d1 = float(np.linalg.norm(m[-1] - end))
            d, rev = (d0, False) if d0 <= d1 else (d1, True)
            if best_d is None or d < best_d:
                best, best_d, best_rev = j, d, rev
        if best is None or best_d > tol:
            break
        remaining.remove(best)
        m = midlines[best]
        chain.append(m[::-1] if best_rev else m)
    pts = np.vstack(chain)
    keep = np.ones(len(pts), dtype=bool)
    keep[1:] = np.linalg.norm(np.diff(pts, axis=0), axis=1) > 1e-6
    return pts[keep]


def load_lanelet_map(osm_path: str, midline_points_per_lanelet: int = 20) -> LaneletMap:
    root = ET.parse(osm_path).getroot()
    nodes = _parse_nodes(root)
    ways = {w.attrib["id"]: [nd.attrib["ref"] for nd in w.findall("nd")] for w in root.findall("way")}

    polygons, midlines = [], []
    for relation in root.findall("relation"):
        if relation.find("tag[@k='type'][@v='lanelet']") is None:
            continue
        left = right = None
        for member in relation.findall("member"):
            if member.attrib.get("role") == "left":
                left = member.attrib.get("ref")
            elif member.attrib.get("role") == "right":
                right = member.attrib.get("ref")
        if not (left and right):
            continue
        lc = _way_coords(ways, nodes, left)
        rc = _way_coords(ways, nodes, right)
        if len(lc) < 2 or len(rc) < 2:
            continue
        la, ra = np.asarray(lc), np.asarray(rc)
        # 右境界が逆向きにデジタイズされている場合は揃える
        if np.linalg.norm(la[0] - ra[0]) > np.linalg.norm(la[0] - ra[-1]):
            ra = ra[::-1]
        polygons.append(np.vstack([la, ra[::-1]]))
        k = max(len(la), len(ra), midline_points_per_lanelet)
        midlines.append((_resample(la, k) + _resample(ra, k)) / 2.0)

    if not midlines:
        raise ValueError(f"no lanelet found in {osm_path}")
    return LaneletMap(polygons=polygons, centerline=_chain_midlines(midlines))
