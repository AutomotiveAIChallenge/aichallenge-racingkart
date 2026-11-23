"""Exercise the real phase routing with fake Docker/CAN, without a running vehicle."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = (Path(__file__).resolve().parents[1] / "setup_check.sh").read_text()
# Keep argument parsing and every check; omit path discovery/log directory creation and main call.
DEFINITIONS = SCRIPT[SCRIPT.index("# ログ関数"):SCRIPT.index("# スクリプト実行")]
FAKES = r'''
MODE=vehicle
ENABLE_LOG=false
CAN_IFACE=can0
CAN_SAMPLE_SEC=1
CAN_MIN_FRAMES=1
GNSS_NAVPVT_TIMEOUT_SEC=1
ROS_TOPIC_TIMEOUT_SEC=1
ROS_TOPIC_RETRY=${ROS_TOPIC_RETRY:-1}
REPO_ROOT=/fake-repo
TOTAL_CHECKS=0
PASSED_CHECKS=0
FAILED_CHECKS=0
WARNING_CHECKS=0
SECTION_INDEX=0
OK=OK
FAIL=FAIL
WARN=WARN
INFO=INFO
log() { printf '%s\n' "$1"; }
print_header() { :; }
# Preflight checks are irrelevant to topic routing; record their invocation instead.
check_hardware() { echo preflight:hardware >>"$TRACE_FILE"; }
check_network() { echo preflight:network >>"$TRACE_FILE"; }
check_docker() { echo preflight:docker >>"$TRACE_FILE"; }
check_known_issues() { echo preflight:known >>"$TRACE_FILE"; }
check_execution_readiness() { echo preflight:readiness >>"$TRACE_FILE"; }
check_imu_bias() { echo check_imu_bias >>"$TRACE_FILE"; record_result fail; }
apply_calibration() { echo apply_calibration >>"$TRACE_FILE"; record_result fail; }
ip() {
    echo ip >>"$TRACE_FILE"
    echo 'can0: UP state ERROR-ACTIVE'
}
candump() {
    echo candump >>"$TRACE_FILE"
    echo '(0.0) can0 123#00'
}
timeout() { shift; "$@"; }
docker() {
    printf 'docker %s\n' "$*" >>"$TRACE_FILE"
    if [ "$1" = ps ]; then
        if [ "${2-}" = --no-trunc ]; then
            [ "$HOST_PS_FAIL" = 0 ] || return 1
            printf '%s\n' "$HOST_CONTAINERS"
        fi
        return 0
    fi
    if [ "$4" = ps ]; then
        if [[ "$*" == *'{{.ID}} {{.Service}}'* ]]; then
            [ "$COMPOSE_PS_FAIL" = 0 ] || return 1
            printf '%s\n' "$EXPECTED_CONTAINERS"
        else
            printf '%s\n' $SERVICES
        fi
        return 0
    fi
    if [ "$4" = exec ]; then
        case "${9}" in
        *'/sensing/gnss/navpvt'*) echo "$GNSS_FLAGS" ;;
        *"'$FAILED_TOPIC'"*) return 1 ;;
        *'--field actuation'*)
            local attempt=0
            if [ -f "$ACTUATION_DIR/attempt" ]; then
                read -r attempt <"$ACTUATION_DIR/attempt"
            fi
            attempt=$((attempt + 1))
            echo "$attempt" >"$ACTUATION_DIR/attempt"
            cat "$ACTUATION_DIR/$attempt" 2>/dev/null
            return $?
            ;;
        esac
        return 0
    fi
    return 99
}
main
'''


class PhaseRoutingTest(unittest.TestCase):
    def run_phase(self, phase, services, *, flags="131", failed_topic="/not-a-topic",
                  host_containers=None, expected_containers=None,
                  host_ps_fail=False, compose_ps_fail=False,
                  actuation_outputs=("accel_cmd: 0.2\nbrake_cmd: 0.0\nsteer_cmd: 0.0\n---",)):
        containers = [(f"{index:064x}", service) for index, service in enumerate(services.split(), 1)]
        if expected_containers is None:
            expected_containers = "\n".join(
                f"{identifier} {service}" for identifier, service in containers
                if service in ("autoware", "driver", "zenoh")
            )
        if host_containers is None:
            host_containers = "\n".join(
                f"{identifier} aichallenge-{service}-{index}"
                for index, (identifier, service) in enumerate(containers, 1)
            )
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp) / "trace"
            trace.touch()
            for attempt, output in enumerate(actuation_outputs, 1):
                if output is not None:
                    (Path(tmp) / str(attempt)).write_text(output)
            env = dict(os.environ, PHASE=phase, SERVICES=services, GNSS_FLAGS=flags,
                       FAILED_TOPIC=failed_topic, TRACE_FILE=str(trace),
                       HOST_CONTAINERS=host_containers, EXPECTED_CONTAINERS=expected_containers,
                       HOST_PS_FAIL=str(int(host_ps_fail)), COMPOSE_PS_FAIL=str(int(compose_ps_fail)),
                       ACTUATION_DIR=tmp, ROS_TOPIC_RETRY=str(len(actuation_outputs)))
            result = subprocess.run(["bash", "-c", DEFINITIONS + FAKES], env=env,
                                    text=True, capture_output=True, timeout=10)
            return result, trace.read_text()

    def test_driver_succeeds_without_autoware_or_rosbag(self):
        result, trace = self.run_phase("driver", "driver zenoh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("candump", trace)
        self.assertIn("/sensing/gnss/navpvt", trace)
        self.assertIn("/sensing/imu/imu_raw", trace)
        self.assertIn("exec -T driver", trace)
        self.assertNotIn("exec -T autoware", trace)
        self.assertNotIn("/control/command/", trace)
        self.assertNotIn("preflight:", trace)
        self.assertNotIn("docker ps --no-trunc", trace)
        for name in ("velocity_status", "steering_status", "gear_status", "actuation_status"):
            self.assertIn("/vehicle/status/" + name, trace)
        for name in ("vcu", "steer", "brake"):
            self.assertIn(f"/racing_kart/{name}/status", trace)
            self.assertIn(f"/racing_kart/{name}/command", trace)
        self.assertIn("/racing_kart/sd/joy", trace)
        self.assertIn("driver / zenoh チェック完了", result.stdout)

    def test_autoware_checks_host_containers_and_its_control_topics(self):
        result, trace = self.run_phase("autoware", "autoware driver zenoh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("exec -T autoware", trace)
        self.assertIn("/control/command/control_cmd", trace)
        self.assertIn("/control/command/actuation_cmd", trace)
        for unrelated in ("exec -T driver", "candump", "/sensing/", "/vehicle/status/", "preflight:"):
            self.assertNotIn(unrelated, trace)
        self.assertIn("Autoware チェック完了", result.stdout)
        self.assertIn("docker ps --no-trunc", trace)
        self.assertIn("--status running --no-trunc --format {{.ID}} {{.Service}} autoware driver zenoh", trace)
        self.assertIn("0 warn, 0 fail", result.stdout)

    def test_accelerator_check_is_last_and_shows_both_command_values(self):
        result, trace = self.run_phase("autoware", "autoware driver zenoh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("OK Autoware accelerator command: accel_cmd=0.2, brake_cmd=0.0", result.stdout)
        self.assertIn("6 checks: 6 ok, 0 warn, 0 fail", result.stdout)
        self.assertGreater(result.stdout.index("OK Autoware accelerator command:"),
                           result.stdout.index("OK Actuation command:"))
        self.assertIn("--once --field actuation", trace)
        self.assertNotIn("ros2 topic pub", trace)

    def test_zero_negative_accelerator_and_nonzero_brake_fail_with_values(self):
        for accel, brake in (("0.0", "0.0"), ("-0.1", "0.0"), ("0.2", "0.1"),
                             ("0.2", "-0.1"), ("0.0", "1.0")):
            with self.subTest(accel=accel, brake=brake):
                result, _ = self.run_phase(
                    "autoware", "autoware driver zenoh",
                    actuation_outputs=(f"accel_cmd: {accel}\nbrake_cmd: {brake}",),
                )
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(f"FAIL Autoware accelerator command: accel_cmd={accel}, brake_cmd={brake}",
                              result.stdout)
                self.assertIn("6 checks: 5 ok, 0 warn, 1 fail", result.stdout)

    def test_scientific_notation_is_compared_numerically(self):
        result, _ = self.run_phase(
            "autoware", "autoware driver zenoh",
            actuation_outputs=("accel_cmd: 1.0e-05\nbrake_cmd: -0.0",),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("accel_cmd=1.0e-05, brake_cmd=-0.0", result.stdout)

    def test_missing_invalid_and_nonfinite_values_fail(self):
        for output in ("", "accel_cmd: 0.2", "brake_cmd: 0.0",
                       "accel_cmd: 0.2\nbrake_cmd: invalid",
                       "accel_cmd: .nan\nbrake_cmd: 0.0",
                       "accel_cmd: .inf\nbrake_cmd: 0.0",
                       "accel_cmd: 0.2\nbrake_cmd: .nan",
                       "accel_cmd: 0.2\nbrake_cmd: 0.0\naccel_cmd: 0.3"):
            with self.subTest(output=output):
                result, _ = self.run_phase("autoware", "autoware driver zenoh",
                                           actuation_outputs=(output,))
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("FAIL Autoware accelerator command: invalid or missing accel_cmd/brake_cmd",
                              result.stdout)

    def test_accelerator_receive_timeout_fails_even_if_topic_presence_passed(self):
        result, trace = self.run_phase("autoware", "autoware driver zenoh",
                                       actuation_outputs=(None, None))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("OK Actuation command:", result.stdout)
        self.assertIn("FAIL Autoware accelerator command: no message on /control/command/actuation_cmd",
                      result.stdout)
        self.assertEqual(trace.count("--field actuation"), 2)

    def test_accelerator_retries_until_a_valid_command_arrives(self):
        for first in (None, "accel_cmd: 0.0\nbrake_cmd: 0.0", "accel_cmd: 0.2"):
            with self.subTest(first=first):
                result, trace = self.run_phase(
                    "autoware", "autoware driver zenoh",
                    actuation_outputs=(first, "accel_cmd: 0.3\nbrake_cmd: 0.0"),
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("OK Autoware accelerator command: accel_cmd=0.3, brake_cmd=0.0", result.stdout)
                self.assertIn("6 checks: 6 ok, 0 warn, 0 fail", result.stdout)
                self.assertEqual(trace.count("--field actuation"), 2)

    def test_accelerator_stops_after_the_first_passing_command(self):
        result, trace = self.run_phase(
            "autoware", "autoware driver zenoh",
            actuation_outputs=("accel_cmd: 0.2\nbrake_cmd: 0.0", None),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(trace.count("--field actuation"), 1)

    def test_autoware_requires_driver_and_zenoh_as_well(self):
        for services, missing in (("autoware zenoh", "driver"), ("autoware driver", "zenoh")):
            with self.subTest(missing=missing):
                result, _ = self.run_phase("autoware", services)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("Required compose services not running: " + missing, result.stdout)

    def test_additional_containers_warn_without_failing_or_skipping_topics(self):
        for extra in ("rosbag", "autoware-command", "simulator"):
            with self.subTest(extra=extra):
                result, trace = self.run_phase("autoware", "autoware driver zenoh " + extra)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertIn("WARN Additional running container: aichallenge-" + extra, result.stdout)
                self.assertIn("1 warn, 0 fail", result.stdout)
                self.assertIn("Autoware チェック完了（警告あり）", result.stdout)
                self.assertIn("/control/command/actuation_cmd", trace)

    def test_foreign_project_and_unmanaged_containers_warn(self):
        expected = "a autoware\nb driver\nc zenoh"
        for extra in ("old-team-autoware-1", "standalone-container"):
            with self.subTest(extra=extra):
                result, _ = self.run_phase(
                    "autoware", "autoware driver zenoh", expected_containers=expected,
                    host_containers=f"a current-autoware\nb current-driver\nc current-zenoh\nx {extra}",
                )
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertIn("WARN Additional running container: " + extra, result.stdout)

    def test_duplicate_required_service_warns(self):
        result, _ = self.run_phase("autoware", "autoware driver zenoh autoware")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("WARN Multiple running containers for autoware:", result.stdout)
        self.assertIn("1 warn, 0 fail", result.stdout)

    def test_foreign_autoware_does_not_satisfy_missing_current_service(self):
        result, _ = self.run_phase(
            "autoware", "driver zenoh", expected_containers="b driver\nc zenoh",
            host_containers="x old-team-autoware-1\nb current-driver\nc current-zenoh",
        )
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("WARN Additional running container: old-team-autoware-1", result.stdout)
        self.assertIn("Required compose services not running: autoware", result.stdout)

    def test_missing_host_container_is_not_satisfied_by_stale_compose_snapshot(self):
        result, _ = self.run_phase(
            "autoware", "autoware driver zenoh", expected_containers="a autoware\nb driver\nc zenoh",
            host_containers="a current-autoware\nb current-driver",
        )
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("Required compose services not running: zenoh", result.stdout)

    def test_empty_host_and_failed_inventory_queries_fail(self):
        for options in ({"host_containers": ""}, {"host_ps_fail": True}, {"compose_ps_fail": True}):
            with self.subTest(options=options):
                result, _ = self.run_phase("autoware", "autoware driver zenoh", **options)
                self.assertEqual(result.returncode, 1, result.stdout)

    def test_missing_service_fails_its_phase(self):
        for phase, services, missing in (("driver", "driver", "zenoh"),
                                          ("driver", "zenoh", "driver"),
                                          ("autoware", "driver zenoh", "autoware")):
            with self.subTest(phase=phase, missing=missing):
                result, _ = self.run_phase(phase, services)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("Required compose services not running: " + missing, result.stdout)
                if phase == "autoware":
                    self.assertIn("FAIL Autoware accelerator command: autoware service is not running",
                                  result.stdout)

    def test_missing_topic_fails_without_stopping_remaining_checks(self):
        for phase, topic in (("driver", "/sensing/imu/imu_raw"),
                             ("autoware", "/control/command/control_cmd")):
            with self.subTest(phase=phase):
                result, trace = self.run_phase(phase, "driver zenoh autoware", failed_topic=topic)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("no message on " + topic, result.stdout)
                last_topic = "/vehicle/status/actuation_status" if phase == "driver" else "/control/command/actuation_cmd"
                self.assertIn(last_topic, trace)
                if phase == "autoware":
                    self.assertIn("OK Autoware accelerator command:", result.stdout)

    def test_runtime_and_all_preserve_both_checks(self):
        for phase in ("runtime", "all"):
            with self.subTest(phase=phase):
                result, trace = self.run_phase(phase, "driver zenoh autoware")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("candump", trace)
                self.assertIn("exec -T driver", trace)
                self.assertIn("exec -T autoware", trace)
                self.assertEqual("preflight:" in trace, phase == "all")
                self.assertEqual(trace.count("/sensing/imu/imu_raw"), 1)
                self.assertEqual(trace.count("/control/command/control_cmd"), 1)
                self.assertEqual(trace.count("--field actuation"), 1)

    def test_runtime_and_all_fail_when_the_accelerator_is_zero(self):
        for phase in ("runtime", "all"):
            with self.subTest(phase=phase):
                result, _ = self.run_phase(
                    phase, "driver zenoh autoware",
                    actuation_outputs=("accel_cmd: 0.0\nbrake_cmd: 0.0",),
                )
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("FAIL Autoware accelerator command: accel_cmd=0.0, brake_cmd=0.0", result.stdout)

    def test_preflight_does_not_require_running_services(self):
        result, trace = self.run_phase("preflight", "")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(trace.splitlines()), 5)
        self.assertTrue(all(line.startswith("preflight:") for line in trace.splitlines()))

    def test_warning_is_success_but_not_a_ready_to_drive_claim(self):
        result, _ = self.run_phase("driver", "driver zenoh", flags="67")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("driver / zenoh チェック完了（警告あり）", result.stdout)
        self.assertNotIn("走行準備 OK", result.stdout)

    def test_no_phase_measures_or_updates_imu_bias(self):
        for phase in ("preflight", "driver", "autoware", "runtime", "all"):
            with self.subTest(phase=phase):
                result, trace = self.run_phase(phase, "driver zenoh autoware")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn("check_imu_bias", trace)
                self.assertNotIn("apply_calibration", trace)
                self.assertNotIn("Proceed?", result.stdout)

    def test_phase_argument_validation(self):
        for phase in ("preflight", "driver", "autoware", "runtime", "all", "invalid"):
            with self.subTest(phase=phase):
                result = subprocess.run(["bash", "-c", DEFINITIONS + '\nprintf "%s" "$PHASE"',
                                         "setup_check", "--phase", phase],
                                        text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 1 if phase == "invalid" else 0)
                if phase != "invalid":
                    self.assertEqual(result.stdout, phase)


if __name__ == "__main__":
    unittest.main()
