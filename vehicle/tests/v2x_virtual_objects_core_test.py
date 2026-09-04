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
    parse_raceline,
    parse_scenario,
    position_topic,
    to_rfc3339_utc,
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
