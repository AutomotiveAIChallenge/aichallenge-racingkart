#!/usr/bin/env python3
"""Unit tests for vehicle/tui_core.py.

No curses, no subprocess, no filesystem: the Workspace snapshot is built by
hand. Run with python3 -m unittest (no third-party runner).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tui_core import (  # noqa: E402
    DONE,
    FAILED,
    PENDING,
    RUNNING,
    STEP_BUILD,
    STEP_PREFLIGHT,
    STEP_RUNTIME,
    STEP_SUBMISSION,
    STEP_TEARDOWN,
    STEP_UP,
    STEPS,
    Workspace,
    build_done,
    is_runnable,
    step_by_id,
    step_status,
    has_unmet_requirement,
)

ALL_UP = frozenset({"driver", "autoware", "zenoh", "rosbag"})


def built_ws(**kwargs):
    """A workspace with a submission present and a fresh install/."""
    base = dict(
        submit_mtime=100.0,
        install_mtime=200.0,
    )
    base.update(kwargs)
    return Workspace(**base)


class TestSteps(unittest.TestCase):
    def test_six_steps_in_execution_order(self):
        self.assertEqual(
            [s.step_id for s in STEPS],
            [
                STEP_PREFLIGHT,
                STEP_SUBMISSION,
                STEP_BUILD,
                STEP_UP,
                STEP_RUNTIME,
                STEP_TEARDOWN,
            ],
        )

    def test_download_step_is_interactive(self):
        # download_submission.sh reads a hidden password; the console has to
        # release the terminal for it.
        self.assertTrue(step_by_id(STEP_SUBMISSION).interactive)

    def test_preflight_step_is_not_interactive(self):
        self.assertFalse(step_by_id(STEP_PREFLIGHT).interactive)

    def test_up_step_only_brings_the_stack_up(self):
        # The target carries no checks: the console runs preflight and doctor
        # as their own steps, and `make setup-vehicle` covers the CLI case.
        self.assertEqual(
            step_by_id(STEP_UP).command,
            ("make", "autoware-driver-zenoh-rosbag"),
        )

    def test_step_by_id_rejects_unknown(self):
        with self.assertRaises(KeyError):
            step_by_id("no-such-step")


class TestBuildDone(unittest.TestCase):
    def test_install_newer_than_src_is_built(self):
        self.assertTrue(build_done(built_ws()))

    def test_equal_mtime_counts_as_built(self):
        self.assertTrue(build_done(built_ws(install_mtime=100.0)))

    def test_install_older_than_src_is_stale(self):
        self.assertFalse(build_done(built_ws(install_mtime=50.0)))

    def test_no_install_is_not_built(self):
        self.assertFalse(build_done(Workspace(submit_mtime=100.0)))

    def test_missing_mtime_is_not_built(self):
        ws = Workspace(install_mtime=1.0)
        self.assertFalse(build_done(ws))

    def test_empty_workspace_is_not_built(self):
        self.assertFalse(build_done(Workspace()))


class TestStepStatus(unittest.TestCase):
    def test_preflight_starts_pending(self):
        self.assertEqual(step_status(STEP_PREFLIGHT, Workspace(), {}), PENDING)

    def test_preflight_reflects_session_result(self):
        ws = Workspace()
        self.assertEqual(step_status(STEP_PREFLIGHT, ws, {STEP_PREFLIGHT: DONE}), DONE)
        self.assertEqual(
            step_status(STEP_PREFLIGHT, ws, {STEP_PREFLIGHT: FAILED}), FAILED
        )

    def test_submission_pending_with_empty_session_even_if_dir_has_content(self):
        # aichallenge_submit/ ships tracked packages, so it always has
        # content on a checkout -- that must not read as "downloaded".
        ws = Workspace(submit_mtime=100.0)
        self.assertEqual(step_status(STEP_SUBMISSION, ws, {}), PENDING)

    def test_submission_done_when_session_records_done(self):
        ws = Workspace(submit_mtime=100.0)
        self.assertEqual(
            step_status(STEP_SUBMISSION, ws, {STEP_SUBMISSION: DONE}), DONE
        )

    def test_submission_failed_when_session_records_failed(self):
        ws = Workspace(submit_mtime=100.0)
        self.assertEqual(
            step_status(STEP_SUBMISSION, ws, {STEP_SUBMISSION: FAILED}), FAILED
        )

    def test_build_measured_from_the_workspace(self):
        self.assertEqual(step_status(STEP_BUILD, built_ws(), {}), DONE)
        self.assertEqual(
            step_status(STEP_BUILD, built_ws(install_mtime=50.0), {}), PENDING
        )

    def test_up_done_when_all_required_services_run(self):
        self.assertEqual(step_status(STEP_UP, built_ws(services_running=ALL_UP), {}), DONE)

    def test_up_pending_when_a_service_is_missing(self):
        ws = built_ws(services_running=frozenset({"driver", "autoware", "zenoh"}))
        self.assertEqual(step_status(STEP_UP, ws, {}), PENDING)

    def test_teardown_done_when_nothing_runs(self):
        self.assertEqual(step_status(STEP_TEARDOWN, built_ws(), {}), DONE)

    def test_teardown_pending_while_services_run(self):
        ws = built_ws(services_running=ALL_UP)
        self.assertEqual(step_status(STEP_TEARDOWN, ws, {}), PENDING)

    def test_measured_step_ignores_a_stale_session_entry(self):
        # An external `make down` must show through even though this session
        # recorded the stack as up.
        ws = built_ws(services_running=frozenset())
        self.assertEqual(step_status(STEP_UP, ws, {STEP_UP: DONE}), PENDING)

    def test_running_wins_over_everything(self):
        ws = built_ws(services_running=ALL_UP)
        self.assertEqual(step_status(STEP_UP, ws, {STEP_UP: RUNNING}), RUNNING)


class TestRunnable(unittest.TestCase):
    """Prerequisites are advisory: only a step's own RUNNING status blocks it."""

    def test_preflight_always_runnable(self):
        self.assertTrue(is_runnable(STEP_PREFLIGHT, Workspace(), {}))

    def test_teardown_always_runnable(self):
        self.assertTrue(is_runnable(STEP_TEARDOWN, Workspace(), {}))

    def test_submission_runnable_even_with_preflight_unmet(self):
        self.assertTrue(is_runnable(STEP_SUBMISSION, Workspace(), {}))

    def test_submission_runnable_even_after_preflight_failed(self):
        session = {STEP_PREFLIGHT: FAILED}
        self.assertTrue(is_runnable(STEP_SUBMISSION, Workspace(), session))

    def test_submission_runnable_after_preflight_passes(self):
        session = {STEP_PREFLIGHT: DONE}
        self.assertTrue(is_runnable(STEP_SUBMISSION, Workspace(), session))

    def test_build_runnable_without_a_submission(self):
        session = {STEP_PREFLIGHT: DONE}
        self.assertTrue(is_runnable(STEP_BUILD, Workspace(), session))

    def test_up_runnable_even_when_not_built(self):
        session = {STEP_PREFLIGHT: DONE, STEP_SUBMISSION: DONE}
        ws = Workspace(submit_mtime=100.0)
        self.assertTrue(is_runnable(STEP_UP, ws, session))

    def test_up_runnable_once_built(self):
        session = {STEP_PREFLIGHT: DONE}
        self.assertTrue(is_runnable(STEP_UP, built_ws(), session))

    def test_restart_with_empty_session_still_allows_up_once_built(self):
        # Console restarted: session is empty, so STEP_SUBMISSION reads
        # PENDING again even though a submission was downloaded and built in
        # an earlier session. STEP_BUILD and STEP_UP are measured from disk,
        # not from the session, so a fresh install/ still reports built and
        # the stack can still be brought up without re-running download.
        ws = built_ws()
        self.assertEqual(step_status(STEP_SUBMISSION, ws, {}), PENDING)
        self.assertEqual(step_status(STEP_BUILD, ws, {}), DONE)
        self.assertTrue(is_runnable(STEP_UP, ws, {}))

    def test_runtime_runnable_even_when_stack_is_not_up(self):
        session = {STEP_PREFLIGHT: DONE}
        self.assertTrue(is_runnable(STEP_RUNTIME, built_ws(), session))

    def test_runtime_runnable_once_the_stack_is_up(self):
        session = {STEP_PREFLIGHT: DONE}
        ws = built_ws(services_running=ALL_UP)
        self.assertTrue(is_runnable(STEP_RUNTIME, ws, session))

    def test_a_failed_step_stays_runnable(self):
        # That is how retry works.
        session = {STEP_PREFLIGHT: DONE, STEP_SUBMISSION: FAILED}
        self.assertTrue(is_runnable(STEP_SUBMISSION, Workspace(), session))

    def test_a_running_step_is_not_runnable(self):
        # No launching a second overlapping run of the same step. This is
        # the one case prerequisites cannot override.
        session = {STEP_PREFLIGHT: DONE, STEP_SUBMISSION: RUNNING}
        self.assertFalse(is_runnable(STEP_SUBMISSION, Workspace(), session))


class TestHasUnmetRequirement(unittest.TestCase):
    def test_false_when_there_are_no_requirements(self):
        self.assertFalse(has_unmet_requirement(STEP_PREFLIGHT, Workspace(), {}))
        self.assertFalse(has_unmet_requirement(STEP_TEARDOWN, Workspace(), {}))

    def test_false_when_the_prerequisite_is_done(self):
        session = {STEP_PREFLIGHT: DONE}
        self.assertFalse(has_unmet_requirement(STEP_SUBMISSION, Workspace(), session))

    def test_true_when_the_prerequisite_is_pending(self):
        self.assertTrue(has_unmet_requirement(STEP_SUBMISSION, Workspace(), {}))

    def test_a_failed_prerequisite_counts_as_unmet(self):
        session = {STEP_PREFLIGHT: FAILED}
        self.assertTrue(has_unmet_requirement(STEP_SUBMISSION, Workspace(), session))

    def test_build_reports_submission_as_unmet_without_a_session_entry(self):
        self.assertTrue(has_unmet_requirement(STEP_BUILD, Workspace(), {}))

if __name__ == "__main__":
    unittest.main()
