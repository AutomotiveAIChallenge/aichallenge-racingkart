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

from gui_tools import DEFAULT_VEHICLE_ID, default_vehicle_id, read_env_vehicle_id  # noqa: E402


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

    def test_falls_back_when_missing(self):
        self.assertEqual(default_vehicle_id(Path(tempfile.mkdtemp()) / ".env"), DEFAULT_VEHICLE_ID)


if __name__ == "__main__":
    unittest.main()
