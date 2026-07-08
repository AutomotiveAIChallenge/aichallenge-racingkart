"""Minimal lanelet2 .osm reader shared by the race_control nodes.

Parses the OSM once and exposes the pieces both nodes need: node coordinates
(from local_x/local_y tags), way node-refs, and lanelet relations with their
left/right bound way ids. No lanelet2 C++ dependency — the maps used here only
carry the tags below.
"""

import xml.etree.ElementTree as ET


class LaneletMap:
    def __init__(self, osm_path: str):
        root = ET.parse(osm_path).getroot()

        self.nodes = {}  # id -> (local_x, local_y)
        for n in root.iter("node"):
            tags = {t.get("k"): t.get("v") for t in n.iter("tag")}
            if "local_x" in tags and "local_y" in tags:
                self.nodes[n.get("id")] = (
                    float(tags["local_x"]),
                    float(tags["local_y"]),
                )

        self.ways = {  # id -> [node_ref, ...]
            w.get("id"): [nd.get("ref") for nd in w.iter("nd")]
            for w in root.iter("way")
        }

        self.lanelets = []  # list of (lanelet_id, left_way_id, right_way_id)
        for rel in root.iter("relation"):
            tags = {t.get("k"): t.get("v") for t in rel.iter("tag")}
            if tags.get("type") != "lanelet":
                continue
            bounds = {m.get("role"): m.get("ref") for m in rel.iter("member")}
            if "left" in bounds and "right" in bounds:
                self.lanelets.append(
                    (int(rel.get("id")), bounds["left"], bounds["right"])
                )

    def way_coords(self, way_id):
        """Coordinates of a way's nodes, in order."""
        return [self.nodes[ref] for ref in self.ways.get(way_id, []) if ref in self.nodes]

    def lanelet(self, lanelet_id):
        """Return (left_way_id, right_way_id) for a lanelet id, or None."""
        for lid, left, right in self.lanelets:
            if lid == lanelet_id:
                return left, right
        return None
