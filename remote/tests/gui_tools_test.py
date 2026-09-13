#!/usr/bin/env python3
"""Unit tests for remote/gui_tools.py (.env parsing only, no Tk).

Run with python3 -m unittest (no third-party runner).
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from gui_tools import (  # noqa: E402
    VALID_VEHICLE_IDS,
    default_vehicle_id,
    read_env_vehicle_id,
)


def _write_env(text: str) -> Path:
    path = Path(tempfile.mkdtemp()) / ".env"
    path.write_text(text)
    return path


class TestReadEnvVehicleId(unittest.TestCase):
    def test_reads_plain_value(self):
        self.assertEqual(read_env_vehicle_id(_write_env("VEHICLE_ID=A3\n")), "A3")

    def test_strips_quotes_and_spaces(self):
        self.assertEqual(read_env_vehicle_id(_write_env('VEHICLE_ID = "A6" \n')), "A6")

    def test_ignores_comments_and_other_keys(self):
        text = "# VEHICLE_ID=A1\nV2X_VEHICLE_ID=d1\nROS_DOMAIN_ID=1\n"
        self.assertIsNone(read_env_vehicle_id(_write_env(text)))

    def test_blank_value_is_none(self):
        self.assertIsNone(read_env_vehicle_id(_write_env("VEHICLE_ID=\n")))

    def test_missing_file_is_none(self):
        self.assertIsNone(read_env_vehicle_id(Path(tempfile.mkdtemp()) / ".env"))

    def test_last_assignment_wins(self):
        self.assertEqual(read_env_vehicle_id(_write_env("VEHICLE_ID=A1\nVEHICLE_ID=A2\n")), "A2")


class TestDefaultVehicleId(unittest.TestCase):
    def test_uses_env_value(self):
        self.assertEqual(default_vehicle_id(_write_env("VEHICLE_ID=A7\n")), "A7")

    def test_missing_env_leaves_the_field_empty(self):
        # 繋ぎ先は .env が決める。書かれていないなら初期値も無い。
        self.assertEqual(default_vehicle_id(Path(tempfile.mkdtemp()) / ".env"), "")
        self.assertEqual(default_vehicle_id(_write_env("VEHICLE_ID=\n")), "")

    def test_id_this_gui_cannot_run_is_shown_as_is(self):
        # .env.example は VEHICLE_ID=A0。GUI に case は無いが、実車 ID に差し替えると
        # 設定ミスが見えなくなるので、そのまま出して _run_spec の検証に名指しさせる。
        self.assertEqual(default_vehicle_id(_write_env("VEHICLE_ID=A0\n")), "A0")

    def test_vehicle_side_test_id_maps_to_test_remote(self):
        # 車両側 vehicle_ports.sh の "test" はローカル zenohd 向け。GUI 側の対向は
        # test-remote。読み替えないと毎回手で直すことになる。
        self.assertEqual(default_vehicle_id(_write_env("VEHICLE_ID=test\n")), "test-remote")

    def test_never_substitutes_a_reachable_vehicle(self):
        # 初期値に出てよい実車 ID は .env がそう書いたときだけ。
        for value in ("A0", "test-nope", ""):
            with self.subTest(value=value):
                got = default_vehicle_id(_write_env(f"VEHICLE_ID={value}\n"))
                self.assertNotIn(got, VALID_VEHICLE_IDS)


if __name__ == "__main__":
    unittest.main()
