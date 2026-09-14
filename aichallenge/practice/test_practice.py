#!/usr/bin/env python3
"""Tests for make practice-4car. No Docker, no AWSIM needed.

    python3 -m unittest discover -s aichallenge/practice -p 'test_*.py' -v
"""

import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SCRIPTS = REPO / "aichallenge" / "simulator_scripts"
sys.path.insert(0, str(HERE))

import practice_summary as ps  # noqa: E402


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def vehicle(number, position, laps, finished=True):
    return {
        "vehicle_number": number, "vehicle_name": f"GoKart{number}", "final_position": position,
        "finished": finished, "lap_count": len(laps), "laps": laps,
        "min_lap_time": min(laps) if laps else 0.0, "total_lap_time": sum(laps),
    }


def details(events):
    by_kind = {}
    for e in events:
        k = by_kind.setdefault(e["kind"], {"count": 0, "total_seconds": 0.0})
        k["count"] += 1
        k["total_seconds"] += e["duration"]
    return {
        "schema_version": "v3", "penalty_count": len(events),
        "penalty_total_seconds": sum(e["duration"] for e in events),
        "penalty_events": events, "penalty_by_kind": by_kind,
    }


class PositionSwapTest(unittest.TestCase):
    def test_pass_between_lap_lines_is_counted_once_for_the_car_that_moved_ahead(self):
        # line 1: car 2 ahead (55 < 60); line 2: car 1 ahead (110 < 115)
        swaps = ps.position_swaps({1: ps.cumulative([60, 50, 50]), 2: ps.cumulative([55, 60, 50])})
        self.assertEqual(swaps, [{"lap": 2, "car": 1, "passed": 2}])

    def test_lapping_a_slow_car_is_not_a_swap(self):
        swaps = ps.position_swaps({1: ps.cumulative([50] * 6), 2: ps.cumulative([90] * 4)})
        self.assertEqual(swaps, [])

    def test_passing_a_car_that_stopped_is_a_swap(self):
        # car 2 leads at line 1, then never reaches line 2
        swaps = ps.position_swaps({1: ps.cumulative([60, 50]), 2: ps.cumulative([55])})
        self.assertEqual(swaps, [{"lap": 2, "car": 1, "passed": 2}])


class SummaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name) / "20260912-120000"
        write_json(self.run / "result-summary.json", {
            "schema_version": "v2", "session": {"required_laps": 6, "timeout": 420.0, "total_vehicles": 3},
            "vehicles": [vehicle(2, 1, [55, 60, 50]), vehicle(1, 2, [60, 50, 51]), vehicle(3, 3, [], False)],
        })
        write_json(self.run / "d1-result-details.json",
                   details([{"kind": "crash", "lap": 2, "race_time": 80.0, "duration": 10.0}]))
        write_json(self.run / "d2-result-details.json", details([]))
        # AWSIM's CWD decides where details land; the summariser must also look in d<N>/.
        write_json(self.run / "d3" / "d3-result-details.json",
                   details([{"kind": "block", "lap": 1, "race_time": 30.0, "duration": 20.0}]))
        write_json(self.run / "practice-manifest.json", {
            "class": "s2r", "handicap": "on", "npcs": 0, "grid": "fixed", "round": 0, "pin": False,
            "slots": [{"slot": n, "label": f"team{n}.tar.gz"} for n in (1, 2, 3, 4)],
        })

    def tearDown(self):
        self.tmp.cleanup()

    def test_cars_are_ordered_by_final_position_and_labelled_from_the_manifest(self):
        result = ps.summarise(self.run)
        self.assertEqual([c["slot"] for c in result["cars"]], [2, 1, 3])
        self.assertEqual(result["cars"][0]["label"], "team2.tar.gz")

    def test_penalties_come_from_result_details_including_the_nested_d3_file_and_block(self):
        cars = {c["slot"]: c for c in ps.summarise(self.run)["cars"]}
        self.assertEqual(cars[1]["penalty_kinds"]["crash"], 1)
        self.assertEqual(cars[3]["penalty_kinds"]["block"], 1)
        self.assertAlmostEqual(cars[3]["penalty_s"], 20.0)

    def test_a_slot_that_never_joined_is_reported(self):
        warnings = ps.summarise(self.run)["warnings"]
        self.assertTrue(any("4 slots were started but AWSIM reports 3 cars" in w for w in warnings))

    def test_render_has_one_table_row_per_car_and_marks_dnf(self):
        text = ps.render(ps.summarise(self.run))
        self.assertEqual(sum(1 for line in text.splitlines() if re.match(r"\| \d+ \| \d \|", line)), 3)
        self.assertIn("0 DNF", text)
        self.assertIn("lap 2: slot 1 moved ahead of slot 2", text)

    def test_cli_defaults_to_the_newest_practice_run_and_writes_json(self):
        out = Path(self.tmp.name) / "s.json"
        cwd = os.getcwd()
        os.chdir(self.tmp.name)
        try:
            (Path("output")).mkdir()
            os.rename(self.run, Path("output") / self.run.name)
            self.assertEqual(ps.main(["--json", str(out)]), 0)
        finally:
            os.chdir(cwd)
        self.assertEqual(len(json.loads(out.read_text())["cars"]), 3)


def awsim_args(script, env=None):
    """Run a simulator_scripts/*.sh with AWSIM stubbed out; return its AWSIM argv as {flag: value}."""
    with tempfile.TemporaryDirectory() as tmp:
        stub = Path(tmp) / "AWSIM.x86_64"
        stub.write_text('#!/bin/sh\nfor a in "$@"; do echo "$a"; done\n')
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        text = re.sub(r"^AWSIM_DIRECTORY=.*$", f"AWSIM_DIRECTORY={tmp}", script.read_text(), flags=re.M)
        run_env = {"PATH": os.environ["PATH"], "LOG_DIR": tmp, **(env or {})}
        proc = subprocess.run(["bash", "-c", text], capture_output=True, text=True, env=run_env)
    if proc.returncode != 0:
        return proc.returncode, proc.stderr
    tokens = proc.stdout.split()
    flags, i = {}, 0
    while i < len(tokens):
        has_value = i + 1 < len(tokens) and not tokens[i + 1].startswith("-")
        flags[tokens[i]] = tokens[i + 1] if has_value else None
        i += 2 if has_value else 1
    return 0, flags


class PracticeFinalMatchesFinalsTest(unittest.TestCase):
    """practice-final.sh must stay the SIM-final AWSIM command, except for --sound."""

    def assert_only_sound_differs(self, final_script, practice_env):
        _, final = awsim_args(SCRIPTS / final_script)
        _, practice = awsim_args(SCRIPTS / "practice-final.sh", practice_env)
        diff = {k for k in set(final) | set(practice) if final.get(k) != practice.get(k)}
        self.assertEqual(diff, {"--sound"}, f"{final_script} vs practice-final.sh")
        self.assertEqual(practice["--sound"], "off")

    def test_s2r_default_is_s2r_final(self):
        self.assert_only_sound_differs("s2r-final.sh", {})

    def test_class_e2e_is_e2e_final(self):
        self.assert_only_sound_differs("e2e-final.sh", {"PRACTICE_CLASS": "e2e"})

    def test_handicap_npc_and_vehicle_overrides_reach_awsim(self):
        _, practice = awsim_args(SCRIPTS / "practice-final.sh",
                                 {"PRACTICE_HANDICAP": "off", "PRACTICE_NPCS": "1", "PRACTICE_VEHICLES": "3"})
        self.assertEqual((practice["--handicap"], practice["--npcs"], practice["--vehicles"]), ("off", "1", "3"))

    def test_invalid_values_fail_before_awsim_starts(self):
        for env in ({"PRACTICE_CLASS": "rl"}, {"PRACTICE_HANDICAP": "yes"}, {"PRACTICE_NPCS": "4"},
                    {"PRACTICE_VEHICLES": "5"}):
            code, _ = awsim_args(SCRIPTS / "practice-final.sh", env)
            self.assertEqual(code, 1, env)


class PracticeRaceInputTest(unittest.TestCase):
    """practice_race.bash rejects bad input before touching Docker."""

    def run_race(self, **env):
        return subprocess.run(["bash", str(HERE / "practice_race.bash")], capture_output=True, text=True,
                              env={"PATH": os.environ["PATH"], **env})

    def make_tar(self, tmp, member):
        src = Path(tmp) / member
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("x")
        path = Path(tmp) / "s.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            tar.add(src, arcname=member)
        return path

    def test_needs_one_to_four_tarballs(self):
        for subs in ("", "a b c d e"):
            proc = self.run_race(SUBMISSIONS=subs)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("SUBMISSIONS must list 1 to 4", proc.stderr)

    def test_rejects_a_tarball_that_is_not_an_aichallenge_submit_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self.run_race(SUBMISSIONS=str(self.make_tar(tmp, "other/pkg.xml")))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("every entry must be under aichallenge_submit/", proc.stderr)

    def test_rejects_bad_options(self):
        for key, value in (("CLASS", "x"), ("HANDICAP", "1"), ("NPC", "9"), ("GRID", "random")):
            proc = self.run_race(SUBMISSIONS="a.tar.gz", **{key: value})
            self.assertEqual(proc.returncode, 1, key)


if __name__ == "__main__":
    unittest.main()
