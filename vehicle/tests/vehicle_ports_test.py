#!/usr/bin/env python3
"""Regression tests for the shared vehicle-to-Zenoh endpoint mappings."""
import subprocess
import unittest
from pathlib import Path


VEHICLE_PORTS = Path(__file__).resolve().parents[1] / "vehicle_ports.sh"


def call_mapping(function: str, vehicle_id: str) -> str:
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; "$2" "$3"',
            "bash",
            str(VEHICLE_PORTS),
            function,
            vehicle_id,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


class VehiclePortsTest(unittest.TestCase):
    def test_a4_uses_port_7455(self):
        self.assertEqual(call_mapping("zenoh_port_for_vehicle_id", "A4"), "7455")

    def test_a4_uses_the_tournament_server_endpoint(self):
        self.assertEqual(
            call_mapping("zenoh_endpoint_for_vehicle_id", "A4"),
            "tls/zenoh.dev.aichallenge-board.jsae.or.jp:7455",
        )


if __name__ == "__main__":
    unittest.main()
