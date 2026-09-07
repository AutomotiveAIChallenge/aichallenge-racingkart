#!/usr/bin/env python3
"""Unit tests for vehicle/v2x_virtual_objects_core.py.

No broker, no MQTT, no track: the raceline is built from CSV text and the
scenario from a dict. Run with python3 -m unittest (no third-party runner).
"""
import json
import math
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from v2x_virtual_objects_core import (  # noqa: E402
    DEFAULT_COVARIANCE,
    MODE_RACELINE,
    MODE_STATIC,
    ObjectState,
    ScenarioError,
    advance,
    build_payload,
    common_name_of,
    current_speed,
    initial_state,
    left_normals,
    parse_raceline,
    parse_scenario,
    position_topic,
    to_rfc3339_utc,
    with_vehicle_ids,
)

# 10 m 四方の閉ループ。周長 40 m。速度は 2 m/s。
SQUARE_CSV = """x,y,z,speed
0,0,0,2.0
10,0,0,2.0
10,10,0,2.0
0,10,0,2.0
0,0,0,2.0
"""

# 始点と終点が離れた開いた線。長さ 30 m。
OPEN_CSV = """x,y,speed
0,0,1.0
10,0,1.0
30,0,1.0
"""

# ガレージからの引き込み 2 点 + 10 m 四方の周回。raceline_*_from_garage.csv と
# 同じ形で、終点は引き込みの合流点 (0, 0) にちょうど戻る。
LEAD_IN_CSV = """x,y,speed
-5,-5,2.0
-2,-2,2.0
0,0,2.0
10,0,2.0
10,10,2.0
0,10,2.0
0,0,2.0
"""

# 終点が戻る先が先頭から 50 m と遠く、引き込みとは見なせない線。
FAR_RETURN_CSV = """x,y,speed
0,0,1.0
50,0,1.0
50,10,1.0
50,0,1.0
"""


def scenario_document(**overrides):
    """A minimal valid scenario, overridable key by key."""
    document = {
        "rate_hz": 20.0,
        "broker": {"host": "broker.example.com", "certs_dir": "certs"},
        "defaults": {"raceline": "square.csv"},
        "objects": [{"id": "d5", "mode": MODE_STATIC, "x": 1.0, "y": 2.0}],
    }
    document.update(overrides)
    return document


class PayloadTest(unittest.TestCase):
    def test_payload_carries_no_vehicle_id(self):
        """R4.2: the id travels in the topic name only."""
        document = json.loads(build_payload((1.0, 2.0, 3.0)))
        self.assertNotIn("vehicle_id", document)
        self.assertEqual({"stamp", "frame_id", "position", "covariance"}, set(document))

    def test_payload_values(self):
        stamp = datetime(2026, 9, 7, 1, 2, 3, 456789, tzinfo=timezone.utc)
        document = json.loads(
            build_payload((1.5, -2.5, 0.25), covariance=(0.1, 0.2, 0.3), stamp=stamp)
        )
        self.assertEqual("2026-09-07T01:02:03.456Z", document["stamp"])
        self.assertEqual("map", document["frame_id"])
        self.assertEqual({"x": 1.5, "y": -2.5, "z": 0.25}, document["position"])
        self.assertEqual({"x": 0.1, "y": 0.2, "z": 0.3}, document["covariance"])

    def test_payload_is_single_line(self):
        """mosquitto_pub -l splits on newlines, so a payload must not contain one."""
        self.assertNotIn("\n", build_payload((1.0, 2.0, 3.0)))

    def test_stamp_is_converted_to_utc(self):
        jst = timezone(timedelta(hours=9))
        stamp = datetime(2026, 9, 7, 9, 0, 0, tzinfo=jst)
        self.assertEqual("2026-09-07T00:00:00.000Z", to_rfc3339_utc(stamp))

    def test_topic(self):
        self.assertEqual("v2x/vehicles/d8/position", position_topic("d8"))


class RacelineTest(unittest.TestCase):
    def test_closed_loop_length(self):
        line = parse_raceline(SQUARE_CSV)
        self.assertTrue(line.closed)
        self.assertAlmostEqual(40.0, line.length, places=6)
        # 重複した終点は落とされる。
        self.assertEqual(4, len(line.points))

    def test_open_line_is_not_closed(self):
        line = parse_raceline(OPEN_CSV)
        self.assertFalse(line.closed)
        self.assertAlmostEqual(30.0, line.length, places=6)

    def test_sample_interpolates(self):
        line = parse_raceline(SQUARE_CSV)
        self.assertEqual((0.0, 0.0, 0.0), line.sample(0.0))
        self.assertEqual((5.0, 0.0, 0.0), line.sample(5.0))
        self.assertEqual((10.0, 5.0, 0.0), line.sample(15.0))
        # 閉ループの最終区間は終点から始点へ戻る。
        self.assertEqual((0.0, 5.0, 0.0), line.sample(35.0))

    def test_sample_wraps_on_a_loop(self):
        line = parse_raceline(SQUARE_CSV)
        self.assertEqual(line.sample(0.0), line.sample(40.0))
        self.assertEqual(line.sample(5.0), line.sample(45.0))
        self.assertEqual(line.sample(5.0), line.sample(-35.0))

    def test_normalize_clamps_an_open_line(self):
        line = parse_raceline(OPEN_CSV)
        self.assertEqual(0.0, line.normalize(-10.0))
        self.assertEqual(30.0, line.normalize(100.0))

    def test_speed_at(self):
        line = parse_raceline("x,y,speed\n0,0,3.0\n10,0,7.0\n")
        self.assertEqual(3.0, line.speed_at(0.0))
        self.assertEqual(3.0, line.speed_at(9.9))

    def test_garage_lead_in_is_dropped_so_the_lap_closes(self):
        """from_garage の CSV。引き込みを落とせば周回として閉じる。"""
        line = parse_raceline(LEAD_IN_CSV)
        self.assertTrue(line.closed)
        self.assertEqual(2, line.lead_in_points)
        self.assertEqual(4, len(line.points))
        self.assertAlmostEqual(40.0, line.length, places=6)
        # 弧長の原点は周回の入口へ移る。
        self.assertEqual((0.0, 0.0, 0.0), line.sample(0.0))
        self.assertEqual(line.sample(5.0), line.sample(45.0))

    def test_a_far_return_is_not_a_lead_in(self):
        """先頭から 30 m を超えて戻ってくる線は 1 周とみなさない。"""
        line = parse_raceline(FAR_RETURN_CSV)
        self.assertFalse(line.closed)
        self.assertEqual(0, line.lead_in_points)

    def test_loop_true_closes_a_line_whatever_the_gap(self):
        line = parse_raceline(OPEN_CSV, loop=True)
        self.assertTrue(line.closed)
        # 30 m の隙間がそのまま最終区間になる。
        self.assertAlmostEqual(60.0, line.length, places=6)
        self.assertEqual(line.sample(0.0), line.sample(60.0))

    def test_loop_false_keeps_a_closed_line_open(self):
        line = parse_raceline(SQUARE_CSV, loop=False)
        self.assertFalse(line.closed)
        self.assertEqual(0, line.lead_in_points)
        self.assertAlmostEqual(40.0, line.length, places=6)
        # 開いた線は終端で止まる。
        self.assertEqual(line.sample(40.0), line.sample(100.0))

    def test_loop_false_keeps_the_lead_in(self):
        line = parse_raceline(LEAD_IN_CSV, loop=False)
        self.assertFalse(line.closed)
        self.assertEqual(0, line.lead_in_points)
        self.assertEqual((-5.0, -5.0, 0.0), line.sample(0.0))

    def test_sample_with_a_lateral_offset(self):
        """+ は進行方向の左。CCW の四角形では内側になる。"""
        line = parse_raceline(SQUARE_CSV)
        self.assertEqual((5.0, 1.0, 0.0), line.sample(5.0, 1.0))
        self.assertEqual((5.0, -1.0, 0.0), line.sample(5.0, -1.0))
        # 2 辺目は +y 方向へ進むので、左は -x 側。
        self.assertEqual((9.0, 5.0, 0.0), line.sample(15.0, 1.0))

    def test_lateral_offset_keeps_its_distance_through_a_corner(self):
        """マイター法線。角を丸めて内側へ戻ってしまわないこと。"""
        line = parse_raceline(SQUARE_CSV)
        # 頂点 (10, 0) は 90 度の角。1 m 内側の線の角は (9, 1)。
        self.assertEqual((9.0, 1.0, 0.0), line.sample(10.0, 1.0))
        # 直線区間はどこでもレースラインから 1 m。
        for s_m in (0.5, 2.0, 5.0, 9.5):
            x, y, _ = line.sample(s_m, 1.0)
            self.assertAlmostEqual(1.0, y, places=9)

    def test_lateral_offset_is_continuous(self):
        """点をまたぐたびに横へ飛ばないこと（20 Hz で見えるジッタになる）。"""
        line = parse_raceline(SQUARE_CSV)
        previous = line.sample(0.0, 1.5)
        for step in range(1, 401):
            point = line.sample(step * 0.1, 1.5)
            self.assertLess(math.dist(point[:2], previous[:2]), 0.25)
            previous = point

    def test_lateral_offset_wraps_with_the_loop(self):
        line = parse_raceline(SQUARE_CSV)
        self.assertEqual(line.sample(5.0, 1.0), line.sample(45.0, 1.0))

    def test_normals_of_an_open_line_reuse_the_end_segments(self):
        line = parse_raceline(OPEN_CSV)
        for normal in line.normals:
            self.assertAlmostEqual(0.0, normal[0], places=9)
            self.assertAlmostEqual(1.0, normal[1], places=9)
        self.assertEqual((0.0, 2.0, 0.0), line.sample(0.0, 2.0))
        self.assertEqual((30.0, 2.0, 0.0), line.sample(30.0, 2.0))

    def test_a_single_point_line_has_no_direction(self):
        """方向が無いので横オフセットは効かない。落ちないことだけ確かめる。"""
        line = parse_raceline("x,y\n5,5\n")
        self.assertEqual(((0.0, 0.0),), line.normals)
        self.assertEqual((5.0, 5.0, 0.0), line.sample(0.0, 3.0))

    def test_miter_is_capped_at_a_hairpin(self):
        """折り返しでオフセットが無限に伸びないこと。"""
        for normal in left_normals(((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 0.0, 0.0)), False):
            self.assertLessEqual(math.hypot(*normal), 4.0 + 1e-9)

    def test_missing_columns_are_rejected(self):
        with self.assertRaises(ScenarioError):
            parse_raceline("a,b\n1,2\n")

    def test_non_numeric_row_is_rejected(self):
        with self.assertRaises(ScenarioError) as caught:
            parse_raceline("x,y\n0,0\nnope,1\n")
        self.assertIn("line 3", str(caught.exception))

    def test_optional_columns_default(self):
        line = parse_raceline("x,y\n0,0\n10,0\n")
        self.assertEqual((0.0, 0.0, 0.0), line.points[0])
        self.assertEqual(0.0, line.speeds[0])


class ScenarioTest(unittest.TestCase):
    def test_static_object(self):
        scenario = parse_scenario(scenario_document())
        self.assertEqual(20.0, scenario.rate_hz)
        self.assertEqual(("d5",), scenario.vehicle_ids)
        spawned = scenario.objects[0]
        self.assertEqual((1.0, 2.0, 0.0), spawned.position)
        self.assertEqual(DEFAULT_COVARIANCE, spawned.covariance)
        self.assertEqual("v2x/vehicles/d5/position", spawned.topic)

    def test_tls_port_defaults(self):
        scenario = parse_scenario(scenario_document())
        self.assertTrue(scenario.broker.tls)
        self.assertEqual(8883, scenario.broker.port)

    def test_plain_port_defaults(self):
        scenario = parse_scenario(
            scenario_document(broker={"host": "127.0.0.1", "tls": False})
        )
        self.assertEqual(1883, scenario.broker.port)

    def test_defaults_reach_the_objects(self):
        scenario = parse_scenario(
            scenario_document(
                defaults={
                    "raceline": "square.csv",
                    "covariance": [0.5, 0.5, 1.0],
                    "frame_id": "map",
                    "z_offset": 0.3,
                    "speed_mps": 4.0,
                },
                objects=[{"id": "d8", "mode": MODE_RACELINE}],
            )
        )
        spawned = scenario.objects[0]
        self.assertEqual((0.5, 0.5, 1.0), spawned.covariance)
        self.assertEqual(0.3, spawned.z_offset)
        self.assertEqual(4.0, spawned.speed_mps)
        self.assertEqual("square.csv", spawned.raceline)

    def test_object_overrides_defaults(self):
        scenario = parse_scenario(
            scenario_document(
                defaults={"raceline": "square.csv", "speed_mps": 4.0},
                objects=[{"id": "d8", "mode": MODE_RACELINE, "speed_mps": 9.0}],
            )
        )
        self.assertEqual(9.0, scenario.objects[0].speed_mps)

    def test_raceline_paths_are_deduplicated_in_first_use_order(self):
        scenario = parse_scenario(
            scenario_document(
                defaults={},
                objects=[
                    {"id": "d5", "mode": MODE_RACELINE, "raceline": "b.csv", "speed_mps": 1.0},
                    {"id": "d8", "mode": MODE_RACELINE, "raceline": "a.csv", "speed_mps": 1.0},
                    {"id": "d10", "mode": MODE_RACELINE, "raceline": "b.csv", "speed_mps": 1.0},
                ],
            )
        )
        self.assertEqual(("b.csv", "a.csv"), scenario.raceline_paths)

    def test_duplicate_id_is_rejected(self):
        with self.assertRaises(ScenarioError):
            parse_scenario(
                scenario_document(
                    objects=[
                        {"id": "d5", "x": 0.0, "y": 0.0},
                        {"id": "d5", "x": 1.0, "y": 1.0},
                    ]
                )
            )

    def test_wildcard_in_id_is_rejected(self):
        """A + or # in the id would break the topic it publishes to."""
        for bad in ("d+", "d#", "a/b"):
            with self.assertRaises(ScenarioError):
                parse_scenario(scenario_document(objects=[{"id": bad, "x": 0.0, "y": 0.0}]))

    def test_static_needs_a_position(self):
        with self.assertRaises(ScenarioError):
            parse_scenario(scenario_document(objects=[{"id": "d5", "mode": MODE_STATIC}]))

    def test_loop_defaults_to_the_geometry(self):
        self.assertIsNone(parse_scenario(scenario_document()).loop)

    def test_loop_is_read_from_the_scenario(self):
        self.assertTrue(parse_scenario(scenario_document(loop=True)).loop)
        self.assertFalse(parse_scenario(scenario_document(loop=False)).loop)

    def test_non_boolean_loop_is_rejected(self):
        with self.assertRaises(ScenarioError):
            parse_scenario(scenario_document(loop="yes"))

    def test_lateral_offset_comes_from_the_defaults(self):
        scenario = parse_scenario(
            scenario_document(
                defaults={"raceline": "square.csv", "lateral_offset": -1.5},
                objects=[
                    {"id": "d8", "mode": MODE_RACELINE, "speed_mps": 1.0},
                    {"id": "d10", "mode": MODE_RACELINE, "speed_mps": 1.0, "lateral_offset": 2.0},
                ],
            )
        )
        self.assertEqual(-1.5, scenario.objects[0].lateral_offset)
        self.assertEqual(2.0, scenario.objects[1].lateral_offset)

    def test_lateral_offset_with_an_explicit_xy_is_rejected(self):
        """x/y にはレースラインが無いので、どちらが左かを決められない。"""
        with self.assertRaises(ScenarioError):
            parse_scenario(
                scenario_document(
                    objects=[
                        {"id": "d5", "mode": MODE_STATIC, "x": 0.0, "y": 0.0, "lateral_offset": 1.0}
                    ]
                )
            )

    def test_static_rejects_both_xy_and_s_m(self):
        with self.assertRaises(ScenarioError):
            parse_scenario(
                scenario_document(
                    objects=[{"id": "d5", "mode": MODE_STATIC, "x": 0.0, "y": 0.0, "s_m": 5.0}]
                )
            )

    def test_s_m_without_a_raceline_is_rejected(self):
        with self.assertRaises(ScenarioError):
            parse_scenario(
                scenario_document(
                    defaults={}, objects=[{"id": "d5", "mode": MODE_STATIC, "s_m": 5.0}]
                )
            )

    def test_raceline_mode_needs_a_raceline(self):
        with self.assertRaises(ScenarioError):
            parse_scenario(
                scenario_document(defaults={}, objects=[{"id": "d8", "mode": MODE_RACELINE}])
            )

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ScenarioError):
            parse_scenario(scenario_document(objects=[{"id": "d5", "mode": "teleport"}]))

    def test_empty_objects_is_rejected(self):
        with self.assertRaises(ScenarioError):
            parse_scenario(scenario_document(objects=[]))

    def test_bad_rate_is_rejected(self):
        with self.assertRaises(ScenarioError):
            parse_scenario(scenario_document(rate_hz=0.0))

    def test_bad_qos_is_rejected(self):
        with self.assertRaises(ScenarioError):
            parse_scenario(
                scenario_document(broker={"host": "h", "certs_dir": "c", "qos": 3})
            )

    def test_negative_speed_is_rejected(self):
        with self.assertRaises(ScenarioError):
            parse_scenario(
                scenario_document(
                    objects=[{"id": "d8", "mode": MODE_RACELINE, "speed_mps": -1.0}]
                )
            )

    def test_certificate_paths(self):
        scenario = parse_scenario(scenario_document())
        ca, crt, key = scenario.broker.certificate_paths("d5")
        self.assertEqual(os.path.join("certs", "d5", "ca.crt"), ca)
        self.assertEqual(os.path.join("certs", "d5", "kart.crt"), crt)
        self.assertEqual(os.path.join("certs", "d5", "kart.key"), key)

    def test_certificate_paths_without_certs_dir(self):
        scenario = parse_scenario(scenario_document(broker={"host": "h"}))
        with self.assertRaises(ScenarioError):
            scenario.broker.certificate_paths("d5")


class VehicleIdOverrideTest(unittest.TestCase):
    """--ids: publish the same scenario under other ids."""

    def two_object_scenario(self):
        return parse_scenario(
            scenario_document(
                objects=[
                    {"id": "d5", "x": 0.0, "y": 0.0},
                    {"id": "d8", "mode": MODE_RACELINE, "speed_mps": 1.0},
                ]
            )
        )

    def test_ids_are_replaced_in_order(self):
        renamed = with_vehicle_ids(self.two_object_scenario(), ("d14", "d15"))
        self.assertEqual(("d14", "d15"), renamed.vehicle_ids)
        self.assertEqual("v2x/vehicles/d14/position", renamed.objects[0].topic)

    def test_everything_but_the_id_is_kept(self):
        original = self.two_object_scenario()
        renamed = with_vehicle_ids(original, ("d14", "d15"))
        self.assertEqual(original.broker, renamed.broker)
        self.assertEqual(original.rate_hz, renamed.rate_hz)
        for before, after in zip(original.objects, renamed.objects):
            self.assertEqual(
                {key: value for key, value in vars(before).items() if key != "vehicle_id"},
                {key: value for key, value in vars(after).items() if key != "vehicle_id"},
            )

    def test_count_must_match(self):
        for ids in ((), ("d14",), ("d14", "d15", "d16")):
            with self.assertRaises(ScenarioError):
                with_vehicle_ids(self.two_object_scenario(), ids)

    def test_duplicate_id_is_rejected(self):
        with self.assertRaises(ScenarioError):
            with_vehicle_ids(self.two_object_scenario(), ("d14", "d14"))

    def test_wildcard_in_id_is_rejected(self):
        for bad in ("d+", "d#", "a/b", ""):
            with self.assertRaises(ScenarioError):
                with_vehicle_ids(self.two_object_scenario(), ("d14", bad))


class MotionTest(unittest.TestCase):
    def setUp(self):
        self.line = parse_raceline(SQUARE_CSV)

    def spawn(self, **overrides):
        document = scenario_document(
            objects=[dict({"id": "d8", "mode": MODE_RACELINE}, **overrides)]
        )
        return parse_scenario(document).objects[0]

    def test_static_never_moves(self):
        spawned = parse_scenario(scenario_document()).objects[0]
        state = initial_state(spawned, self.line)
        self.assertEqual(state, advance(spawned, self.line, state, 1.0))

    def test_static_s_m_lands_on_the_raceline(self):
        spawned = parse_scenario(
            scenario_document(objects=[{"id": "d5", "mode": MODE_STATIC, "s_m": 15.0}])
        ).objects[0]
        state = initial_state(spawned, self.line)
        self.assertEqual((10.0, 5.0, 0.0), state.position)
        self.assertEqual(0.0, state.speed_mps)

    def test_z_offset_is_applied(self):
        spawned = parse_scenario(
            scenario_document(
                objects=[{"id": "d5", "mode": MODE_STATIC, "s_m": 0.0, "z_offset": 1.25}]
            )
        ).objects[0]
        self.assertEqual((0.0, 0.0, 1.25), initial_state(spawned, self.line).position)

    def test_lateral_offset_moves_a_parked_object_off_the_line(self):
        """路肩に停めた 1 台。s_m は弧長のまま、位置だけ横へ寄る。"""
        spawned = parse_scenario(
            scenario_document(
                objects=[
                    {"id": "d5", "mode": MODE_STATIC, "s_m": 15.0, "lateral_offset": -2.0}
                ]
            )
        ).objects[0]
        state = initial_state(spawned, self.line)
        self.assertEqual(15.0, state.s_m)
        self.assertEqual((12.0, 5.0, 0.0), state.position)

    def test_lateral_offset_holds_while_the_object_walks(self):
        spawned = self.spawn(speed_mps=4.0, start_s_m=0.0, lateral_offset=1.0)
        state = initial_state(spawned, self.line)
        # 始点は 90 度の角なので、1 m 内側の線の角 (1, 1) に乗る。
        self.assertEqual((1.0, 1.0, 0.0), state.position)
        state = advance(spawned, self.line, state, 0.5)
        # 弧長はレースライン上のまま、位置は 1 m 内側の平行線の上を進む。この
        # 四角形は角が 90 度なので進行方向へ 0.6 m ずれるが、点間の角度が小さい
        # 実際のレースラインでは cm 単位でしかずれない。
        self.assertAlmostEqual(2.0, state.s_m, places=6)
        self.assertEqual((2.6, 1.0, 0.0), state.position)

    def test_lateral_offset_and_z_offset_combine(self):
        spawned = self.spawn(speed_mps=1.0, start_s_m=5.0, lateral_offset=1.0, z_offset=0.5)
        self.assertEqual((5.0, 1.0, 0.5), initial_state(spawned, self.line).position)

    def test_fixed_speed_walk(self):
        spawned = self.spawn(speed_mps=4.0, start_s_m=0.0)
        state = initial_state(spawned, self.line)
        self.assertEqual(0.0, state.s_m)
        state = advance(spawned, self.line, state, 0.5)
        self.assertAlmostEqual(2.0, state.s_m, places=6)
        self.assertEqual((2.0, 0.0, 0.0), state.position)

    def test_speed_scale_uses_the_csv_speed(self):
        spawned = self.spawn(speed_scale=0.5)
        self.assertEqual(1.0, current_speed(spawned, self.line, 0.0))
        state = advance(spawned, self.line, initial_state(spawned, self.line), 2.0)
        self.assertAlmostEqual(2.0, state.s_m, places=6)

    def test_speed_mps_wins_over_speed_scale(self):
        spawned = self.spawn(speed_mps=8.0, speed_scale=0.1)
        self.assertEqual(8.0, current_speed(spawned, self.line, 0.0))

    def test_start_s_m_offsets_the_object(self):
        spawned = self.spawn(speed_mps=1.0, start_s_m=10.0)
        self.assertEqual((10.0, 0.0, 0.0), initial_state(spawned, self.line).position)

    def test_walk_wraps_around_the_loop(self):
        spawned = self.spawn(speed_mps=10.0, start_s_m=35.0)
        state = advance(spawned, self.line, initial_state(spawned, self.line), 1.0)
        self.assertAlmostEqual(5.0, state.s_m, places=6)

    def test_a_lap_returns_to_the_start(self):
        spawned = self.spawn(speed_mps=4.0, start_s_m=0.0)
        state = initial_state(spawned, self.line)
        # 20 Hz で 1 周 (40 m / 4 m/s = 10 s)。
        for _ in range(200):
            state = advance(spawned, self.line, state, 0.05)
        self.assertAlmostEqual(0.0, state.s_m, places=6)
        self.assertLess(math.dist(state.position[:2], (0.0, 0.0)), 1e-6)

    def test_zero_speed_stalls_without_error(self):
        spawned = self.spawn(speed_mps=0.0, start_s_m=7.0)
        state = initial_state(spawned, self.line)
        self.assertEqual(state.s_m, advance(spawned, self.line, state, 1.0).s_m)

    def test_raceline_mode_without_a_loaded_raceline_is_reported(self):
        spawned = self.spawn(speed_mps=1.0)
        with self.assertRaises(ScenarioError):
            initial_state(spawned, None)

    def test_advance_without_a_raceline_holds_position(self):
        """A missing raceline must not move the object to a wrong place."""
        spawned = self.spawn(speed_mps=1.0)
        state = ObjectState(s_m=3.0, position=(3.0, 0.0, 0.0), speed_mps=1.0)
        self.assertEqual(state, advance(spawned, None, state, 1.0))


class CommonNameTest(unittest.TestCase):
    def test_openssl_1_1_format(self):
        self.assertEqual("d5", common_name_of("subject=CN = d5"))

    def test_openssl_legacy_format(self):
        self.assertEqual("d5", common_name_of("subject= /CN=d5"))

    def test_multi_field_subject(self):
        self.assertEqual(
            "d10", common_name_of("subject=C = JP, O = AI Challenge, CN = d10")
        )

    def test_no_common_name(self):
        self.assertIsNone(common_name_of("subject=C = JP"))


if __name__ == "__main__":
    unittest.main()
