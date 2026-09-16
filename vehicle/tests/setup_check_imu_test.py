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
ROS_TOPIC_RETRY=1
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
    if [ "$1" = ps ]; then return 0; fi
    if [ "$4" = ps ]; then printf '%s\n' $SERVICES; return 0; fi
    if [ "$4" = exec ]; then
        case "${9}" in
        *'/sensing/gnss/navpvt'*) echo "$GNSS_FLAGS" ;;
        *"'$FAILED_TOPIC'"*) return 1 ;;
        esac
        return 0
    fi
    return 99
}
main
'''


class PhaseRoutingTest(unittest.TestCase):
    def run_phase(self, phase, services, *, flags="131", failed_topic="/not-a-topic"):
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp) / "trace"
            trace.touch()
            env = dict(os.environ, PHASE=phase, SERVICES=services, GNSS_FLAGS=flags,
                       FAILED_TOPIC=failed_topic, TRACE_FILE=str(trace))
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
        for name in ("velocity_status", "steering_status", "gear_status", "actuation_status"):
            self.assertIn("/vehicle/status/" + name, trace)
        for name in ("vcu", "steer", "brake"):
            self.assertIn(f"/racing_kart/{name}/status", trace)
            self.assertIn(f"/racing_kart/{name}/command", trace)
        self.assertIn("/racing_kart/sd/joy", trace)
        self.assertIn("driver / zenoh チェック完了", result.stdout)

    def test_autoware_only_checks_its_service_and_control_topics(self):
        result, trace = self.run_phase("autoware", "autoware")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("exec -T autoware", trace)
        self.assertIn("/control/command/control_cmd", trace)
        self.assertIn("/control/command/actuation_cmd", trace)
        for unrelated in ("exec -T driver", "candump", "/sensing/", "/vehicle/status/", "preflight:"):
            self.assertNotIn(unrelated, trace)
        self.assertIn("Autoware チェック完了", result.stdout)

    def test_missing_service_fails_its_phase(self):
        for phase, services, missing in (("driver", "driver", "zenoh"),
                                          ("driver", "zenoh", "driver"),
                                          ("autoware", "driver zenoh", "autoware")):
            with self.subTest(phase=phase, missing=missing):
                result, _ = self.run_phase(phase, services)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("Required compose services not running: " + missing, result.stdout)

    def test_missing_topic_fails_without_stopping_remaining_checks(self):
        for phase, topic in (("driver", "/sensing/imu/imu_raw"),
                             ("autoware", "/control/command/control_cmd")):
            with self.subTest(phase=phase):
                result, trace = self.run_phase(phase, "driver zenoh autoware", failed_topic=topic)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("no message on " + topic, result.stdout)
                last_topic = "/vehicle/status/actuation_status" if phase == "driver" else "/control/command/actuation_cmd"
                self.assertIn(last_topic, trace)

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
