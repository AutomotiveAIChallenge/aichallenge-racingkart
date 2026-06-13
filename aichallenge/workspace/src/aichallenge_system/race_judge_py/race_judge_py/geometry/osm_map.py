from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import numpy as np

_log = logging.getLogger(__name__)


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


def _chain_from(start_idx, midlines, tol):
    """Greedily chain midlines starting from start_idx; return (chain, used_count)."""
    remaining = set(range(len(midlines)))
    remaining.discard(start_idx)
    chain = [midlines[start_idx]]
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
        remaining.discard(best)
        chain.append(midlines[best][::-1] if best_rev else midlines[best])
    return chain, len(midlines) - len(remaining)


def _chain_midlines(midlines: list, tol: float = 10.0) -> np.ndarray:
    """lanelet 毎の midline を端点最近傍で貪欲にチェーンして1本のループにする。

    最長連結成分を選択する: 全開始インデックスを試し、最も多くの midline を
    使うチェーンを採用する。閉ループを形成するチェーン（始端・終端間距離 <= tol）
    を非閉ループより優先し、同条件内では使用数・総点数で比較する。
    未使用の midline がある場合は警告を出力する。
    向きが逆の midline は反転して接続する。
    """
    best_chain, best_used, best_closes = None, 0, False
    for start in range(len(midlines)):
        chain, used = _chain_from(start, midlines, tol)
        pts_start = chain[0][0]
        pts_end = chain[-1][-1]
        closes = float(np.linalg.norm(pts_end - pts_start)) <= tol
        total_pts = sum(len(c) for c in chain)

        better = best_chain is None
        if not better:
            best_total_pts = sum(len(c) for c in best_chain)
            # Closed loop beats open chain; within same closure class: most used, then most pts
            if closes and not best_closes:
                better = True
            elif closes == best_closes:
                if used > best_used or (used == best_used and total_pts > best_total_pts):
                    better = True

        if better:
            best_chain, best_used, best_closes = chain, used, closes

    unused = len(midlines) - best_used
    if unused > 0:
        _log.warning(
            "_chain_midlines: dropped %d unconnected midline(s) "
            "(kept chain of %d midline(s))",
            unused,
            best_used,
        )

    pts = np.vstack(best_chain)
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
