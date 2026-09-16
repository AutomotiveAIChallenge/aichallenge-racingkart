"""Check runtime routing without hardware, Docker or ROS."""
from pathlib import Path
import subprocess
import unittest


SCRIPT = (Path(__file__).resolve().parents[1] / "setup_check.sh").read_text()


class PhaseRoutingTest(unittest.TestCase):
    def test_only_runtime_and_all_apply_saved_bias_without_measurement(self):
        main = SCRIPT[SCRIPT.index("main() {"):SCRIPT.index("# スクリプト実行")]
        checks = (
            "check_hardware", "check_network", "check_docker", "check_known_issues",
            "check_execution_readiness", "check_runtime_hardware", "check_runtime_docker_services",
            "check_gnss_rtk_status", "check_runtime_ros_topics", "check_imu_bias", "apply_imu_bias",
        )
        stubs = "\n".join(f"{name}() {{ echo {name}; }}" for name in checks)
        for phase in ("preflight", "runtime", "all"):
            with self.subTest(phase=phase):
                commands = f"""
{main}
{stubs}
print_header() {{ :; }}
print_summary() {{ :; }}
ENABLE_LOG=false
PHASE={phase}
main
"""
                result = subprocess.run(["bash", "-c", commands], text=True,
                                        capture_output=True, check=True, timeout=10)
                called = result.stdout.splitlines()
                self.assertNotIn("check_imu_bias", called)
                self.assertEqual("apply_imu_bias" in called, phase in ("runtime", "all"))
                if phase in ("runtime", "all"):
                    self.assertIn("check_runtime_ros_topics", called)

    def test_runtime_still_checks_raw_imu_reception(self):
        topics = SCRIPT[SCRIPT.index("check_runtime_ros_topics() {"):SCRIPT.index("# 保存済みの車両別 IMU")]
        commands = topics + """
print_section() { :; }
log() { :; }
check_ros_topic_once() { printf '%s %s\\n' "$1" "$2"; }
check_runtime_ros_topics
"""
        result = subprocess.run(["bash", "-c", commands], text=True,
                                capture_output=True, check=True, timeout=10)
        self.assertIn("driver /sensing/imu/imu_raw", result.stdout.splitlines())


    def test_runtime_reports_apply_skip_and_failure_without_docker(self):
        function = SCRIPT[SCRIPT.index("apply_imu_bias() {"):SCRIPT.index("# past_log.md")]
        for code, status in ((0, "pass"), (5, "warn"), (3, "fail")):
            with self.subTest(code=code):
                commands = function + f"""
SCRIPT_DIR=/unused/vehicle
print_section() {{ :; }}
log() {{ echo "$1"; }}
record_result() {{ echo "RESULT:$1"; }}
python3() {{ echo "PYTHON:$*"; return {code}; }}
docker() {{ echo UNEXPECTED_DOCKER; return 99; }}
apply_imu_bias
"""
                result = subprocess.run(["bash", "-c", commands], text=True,
                                        capture_output=True, check=True, timeout=10)
                self.assertIn(f"RESULT:{status}", result.stdout)
                self.assertIn("PYTHON:/unused/vehicle/apply_imu_bias.py", result.stdout)
                self.assertNotIn("UNEXPECTED_DOCKER", result.stdout)
                if code == 0:
                    self.assertIn("restart autoware", result.stdout)


if __name__ == "__main__":
    unittest.main()
