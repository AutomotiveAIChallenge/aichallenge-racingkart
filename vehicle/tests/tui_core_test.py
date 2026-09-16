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
    STEP_AUTOWARE_DOWN,
    PARTICIPANT_STEPS,
    ROLE_PARTICIPANT,
    ROLE_STAFF,
    STAFF_STEPS,
    STEP_DRIVER,
    STEP_DRIVER_DOWN,
    STEP_ZENOH,
    STEP_ZENOH_DOWN,
    STEP_ROSBAG,
    STEP_ROSBAG_DOWN,
    STEP_PREFLIGHT,
    STEP_CALIBRATION,
    STEP_CHECK_AUTOWARE,
    STEP_CHECK_DRIVER,
    STEP_TEARDOWN,
    STEP_UP,
    Workspace,
    is_runnable,
    step_by_id,
    step_status,
    steps_for_role,
    has_unmet_requirement,
)

ALL_UP = frozenset({"driver", "autoware", "zenoh", "rosbag"})


class TestSteps(unittest.TestCase):
    def test_participant_steps_in_execution_order(self):
        self.assertEqual(
            [s.step_id for s in PARTICIPANT_STEPS],
            [
                STEP_CALIBRATION,
                STEP_UP,
                STEP_CHECK_AUTOWARE,
                STEP_AUTOWARE_DOWN,
            ],
        )

    def test_staff_checks_and_service_controls_in_execution_order(self):
        staff = [s.step_id for s in steps_for_role(ROLE_STAFF)]
        self.assertEqual(
            staff,
            [
                STEP_PREFLIGHT,
                STEP_DRIVER,
                STEP_ZENOH,
                STEP_CHECK_DRIVER,
                STEP_DRIVER_DOWN,
                STEP_ZENOH_DOWN,
                # rosbag は記録の開始・終了なので down all の直前にまとめる
                STEP_ROSBAG,
                STEP_ROSBAG_DOWN,
                STEP_TEARDOWN,
            ],
        )

    def test_preflight_belongs_to_staff_only(self):
        staff = {s.step_id for s in steps_for_role(ROLE_STAFF)}
        participant_ids = {s.step_id for s in PARTICIPANT_STEPS}
        self.assertFalse(staff & participant_ids)
        self.assertIn(STEP_PREFLIGHT, staff)
        self.assertNotIn(STEP_PREFLIGHT, participant_ids)

    def test_participant_never_touches_the_infra_or_the_whole_stack(self):
        # driver / zenoh / rosbag の起動・停止、make down は運営の仕事。
        participant = {s.step_id for s in steps_for_role(ROLE_PARTICIPANT)}
        self.assertFalse(
            participant
            & {
                STEP_DRIVER,
                STEP_ZENOH,
                STEP_ROSBAG,
                STEP_DRIVER_DOWN,
                STEP_ZENOH_DOWN,
                STEP_ROSBAG_DOWN,
                STEP_TEARDOWN,
            }
        )

    def test_unknown_role_is_rejected(self):
        with self.assertRaises(ValueError):
            steps_for_role("admin")

    def test_autoware_steps_touch_only_the_autoware_container(self):
        # 参加者の autoware / autoware down は driver / zenoh / rosbag を動かしたまま
        # autoware だけを上げ下げする。土台を触るのは運営の driver / zenoh / rosbag。
        self.assertEqual(step_by_id(STEP_UP).command, ("make", "autoware-vehicle"))
        self.assertEqual(
            step_by_id(STEP_AUTOWARE_DOWN).command,
            ("docker", "compose", "down", "autoware"),
        )
        self.assertEqual(step_by_id(STEP_DRIVER).command, ("make", "driver"))
        self.assertEqual(step_by_id(STEP_ZENOH).command, ("make", "zenoh"))
        self.assertEqual(step_by_id(STEP_ROSBAG).command, ("make", "rosbag"))
        self.assertEqual(
            step_by_id(STEP_DRIVER_DOWN).command, ("docker", "compose", "down", "driver")
        )
        self.assertEqual(
            step_by_id(STEP_ZENOH_DOWN).command, ("docker", "compose", "down", "zenoh")
        )
        self.assertEqual(
            step_by_id(STEP_ROSBAG_DOWN).command, ("docker", "compose", "down", "rosbag")
        )
        self.assertEqual(step_by_id(STEP_TEARDOWN).command, ("make", "down"))

    def test_preflight_step_is_not_interactive(self):
        self.assertFalse(step_by_id(STEP_PREFLIGHT).interactive)

    def test_calibration_releases_the_terminal_and_runtime_streams_output(self):
        step = step_by_id(STEP_CALIBRATION)
        self.assertTrue(step.interactive)
        self.assertEqual(step.title, "Update the accel/brake maps and IMU bias")
        self.assertEqual(step.command, ("python3", "apply_calibration.py"))
        self.assertEqual(step.cwd, "vehicle")
        self.assertFalse(step_by_id(STEP_CHECK_AUTOWARE).interactive)

    def test_participant_does_not_require_staff_session_results(self):
        self.assertEqual(step_by_id(STEP_CALIBRATION).requires, ())
        participant_ids = {step.step_id for step in PARTICIPANT_STEPS}
        for step in PARTICIPANT_STEPS:
            self.assertTrue(set(step.requires) <= participant_ids)
        self.assertEqual(step_by_id(STEP_UP).requires, (STEP_CALIBRATION,))

    def test_step_by_id_rejects_unknown(self):
        with self.assertRaises(KeyError):
            step_by_id("no-such-step")


class TestStepStatus(unittest.TestCase):
    def test_preflight_starts_pending(self):
        self.assertEqual(step_status(STEP_PREFLIGHT, Workspace(), {}), PENDING)

    def test_preflight_reflects_session_result(self):
        ws = Workspace()
        self.assertEqual(step_status(STEP_PREFLIGHT, ws, {STEP_PREFLIGHT: DONE}), DONE)
        self.assertEqual(
            step_status(STEP_PREFLIGHT, ws, {STEP_PREFLIGHT: FAILED}), FAILED
        )

    def test_up_done_when_autoware_runs_even_without_the_infra(self):
        # autoware-vehicle が上げるのは autoware だけ。土台の有無はバッジで見せる。
        ws = Workspace(services_running=frozenset({"autoware"}))
        self.assertEqual(step_status(STEP_UP, ws, {}), DONE)

    def test_up_pending_when_autoware_is_missing(self):
        ws = Workspace(services_running=frozenset({"driver", "zenoh", "rosbag"}))
        self.assertEqual(step_status(STEP_UP, ws, {}), PENDING)

    def test_per_service_up_done_only_when_that_service_runs(self):
        # 各サービスの up は自分だけを見る。他が動いていても関係ない。
        ws = Workspace(services_running=frozenset({"driver"}))
        self.assertEqual(step_status(STEP_DRIVER, ws, {}), DONE)
        self.assertEqual(step_status(STEP_ZENOH, ws, {}), PENDING)
        self.assertEqual(step_status(STEP_ROSBAG, ws, {}), PENDING)

    def test_per_service_down_pending_while_that_service_runs(self):
        ws = Workspace(services_running=frozenset({"driver"}))
        self.assertEqual(step_status(STEP_DRIVER_DOWN, ws, {}), PENDING)
        self.assertEqual(step_status(STEP_ZENOH_DOWN, ws, {}), DONE)
        self.assertEqual(step_status(STEP_ROSBAG_DOWN, ws, {}), DONE)

    def test_per_service_down_done_when_nothing_runs(self):
        ws = Workspace()
        self.assertEqual(step_status(STEP_DRIVER_DOWN, ws, {}), DONE)
        self.assertEqual(step_status(STEP_ZENOH_DOWN, ws, {}), DONE)
        self.assertEqual(step_status(STEP_ROSBAG_DOWN, ws, {}), DONE)

    def test_teardown_done_when_nothing_runs(self):
        self.assertEqual(step_status(STEP_TEARDOWN, Workspace(), {}), DONE)

    def test_teardown_pending_while_services_run(self):
        ws = Workspace(services_running=ALL_UP)
        self.assertEqual(step_status(STEP_TEARDOWN, ws, {}), PENDING)

    def test_teardown_pending_while_another_project_still_runs(self):
        # default プロジェクトの 4 サービスが落ちていても、simulator や -p 2 の
        # autoware が残っていれば make down はまだ済んでいない。
        ws = Workspace(services_running=frozenset(), stack_containers=1)
        self.assertEqual(step_status(STEP_TEARDOWN, ws, {}), PENDING)

    def test_autoware_down_done_when_autoware_is_not_running(self):
        # driver だけ生きていても autoware が落ちていれば済んでいる。
        ws = Workspace(services_running=frozenset({"driver", "zenoh"}))
        self.assertEqual(step_status(STEP_AUTOWARE_DOWN, ws, {}), DONE)

    def test_autoware_down_pending_while_autoware_runs(self):
        ws = Workspace(services_running=frozenset({"autoware"}))
        self.assertEqual(step_status(STEP_AUTOWARE_DOWN, ws, {}), PENDING)

    def test_measured_step_ignores_a_stale_session_entry(self):
        # An external `make down` must show through even though this session
        # recorded the stack as up.
        ws = Workspace(services_running=frozenset())
        self.assertEqual(step_status(STEP_UP, ws, {STEP_UP: DONE}), PENDING)

    def test_running_wins_over_everything(self):
        ws = Workspace(services_running=ALL_UP)
        self.assertEqual(step_status(STEP_UP, ws, {STEP_UP: RUNNING}), RUNNING)


class TestRunnable(unittest.TestCase):
    """Prerequisites are advisory: only a step's own RUNNING status blocks it."""

    def test_preflight_always_runnable(self):
        self.assertTrue(is_runnable(STEP_PREFLIGHT, Workspace(), {}))

    def test_teardown_always_runnable(self):
        self.assertTrue(is_runnable(STEP_TEARDOWN, Workspace(), {}))

    def test_up_runnable_even_with_preflight_unmet(self):
        self.assertTrue(is_runnable(STEP_UP, Workspace(), {}))

    def test_up_runnable_even_after_preflight_failed(self):
        session = {STEP_PREFLIGHT: FAILED}
        self.assertTrue(is_runnable(STEP_UP, Workspace(), session))

    def test_up_runnable_after_preflight_passes(self):
        session = {STEP_PREFLIGHT: DONE}
        self.assertTrue(is_runnable(STEP_UP, Workspace(), session))

    def test_runtime_runnable_even_when_stack_is_not_up(self):
        session = {STEP_PREFLIGHT: DONE}
        self.assertTrue(is_runnable(STEP_CHECK_AUTOWARE, Workspace(), session))

    def test_runtime_runnable_once_the_stack_is_up(self):
        session = {STEP_PREFLIGHT: DONE}
        ws = Workspace(services_running=ALL_UP)
        self.assertTrue(is_runnable(STEP_CHECK_AUTOWARE, ws, session))

    def test_a_failed_step_stays_runnable(self):
        # That is how retry works.
        session = {STEP_CHECK_AUTOWARE: FAILED}
        self.assertTrue(is_runnable(STEP_CHECK_AUTOWARE, Workspace(), session))

    def test_a_running_step_is_not_runnable(self):
        # No launching a second overlapping run of the same step. This is
        # the one case prerequisites cannot override.
        session = {STEP_PREFLIGHT: DONE, STEP_UP: RUNNING}
        self.assertFalse(is_runnable(STEP_UP, Workspace(), session))


class TestHasUnmetRequirement(unittest.TestCase):
    def test_false_when_there_are_no_requirements(self):
        self.assertFalse(has_unmet_requirement(STEP_PREFLIGHT, Workspace(), {}))
        self.assertFalse(has_unmet_requirement(STEP_TEARDOWN, Workspace(), {}))

    def test_false_when_the_prerequisite_is_done(self):
        session = {STEP_CALIBRATION: DONE}
        self.assertFalse(has_unmet_requirement(STEP_UP, Workspace(), session))

    def test_true_when_the_prerequisite_is_pending(self):
        self.assertTrue(has_unmet_requirement(STEP_UP, Workspace(), {}))

    def test_a_failed_prerequisite_counts_as_unmet(self):
        session = {STEP_CALIBRATION: FAILED}
        self.assertTrue(has_unmet_requirement(STEP_UP, Workspace(), session))


if __name__ == "__main__":
    unittest.main()
