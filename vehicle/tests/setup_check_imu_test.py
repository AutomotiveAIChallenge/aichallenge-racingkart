"""Check runtime routing without hardware, Docker or ROS."""
from pathlib import Path
import subprocess
import unittest


SCRIPT = (Path(__file__).resolve().parents[1] / "setup_check.sh").read_text()


class PhaseRoutingTest(unittest.TestCase):
    def test_no_phase_measures_or_updates_imu_bias(self):
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
                self.assertNotIn("apply_imu_bias", called)
                if phase in ("runtime", "all"):
                    self.assertIn("check_runtime_ros_topics", called)

    def test_runtime_still_checks_raw_imu_reception(self):
        topics = SCRIPT[SCRIPT.index("check_runtime_ros_topics() {"):SCRIPT.index("# past_log.md")]
        commands = topics + """
print_section() { :; }
log() { :; }
check_ros_topic_once() { printf '%s %s\\n' "$1" "$2"; }
check_runtime_ros_topics
"""
        result = subprocess.run(["bash", "-c", commands], text=True,
                                capture_output=True, check=True, timeout=10)
        self.assertIn("driver /sensing/imu/imu_raw", result.stdout.splitlines())


if __name__ == "__main__":
    unittest.main()
