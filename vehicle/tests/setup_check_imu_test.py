"""Exercise host-side approval and Docker invocation without a running ROS session."""
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


class HostImuApprovalTest(unittest.TestCase):
    def run_check(self, answers, vehicle_id="A4", measure_rc=0):
        vehicle = Path(__file__).resolve().parents[1]
        script = (vehicle / "setup_check.sh").read_text()
        detection = script[script.index("read_env_value() {"):script.index("# ヘッダー表示")]
        check = script[script.index("check_imu_bias() {"):script.index("# past_log.md既知問題チェック")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = root / "calls"
            commands = f"""
source {shlex.quote(str(vehicle / 'vehicle_ports.sh'))}
SCRIPT_DIR={shlex.quote(directory)}
REPO_ROOT={shlex.quote(directory)}
VEHICLE_ID={shlex.quote(vehicle_id)}
IMU_BIAS_DURATION_SEC=0
IMU_BIAS_WARMUP_SEC=0
IMU_BIAS_VELOCITY_THRESHOLD=0.05
IMU_BIAS_STD_THRESHOLD=0.03
{detection}
{check}
print_section() {{ :; }}
log() {{ printf '%s\\n' "$1"; }}
record_result() {{ printf 'RESULT:%s\\n' "$1"; }}
is_compose_service_running() {{ return 0; }}
ros_setup_command_for_service() {{ echo true; }}
hostname() {{ echo unknown-host; }}
docker() {{
    printf '%s\\n' "$*" >> {shlex.quote(str(calls))}
    if [[ "$*" == *--proposal-output* ]]; then
        echo 'current measured difference: measurement complete, no settings changed'
        return {measure_rc}
    fi
    echo 'approved IMU bias applied'
}}
check_imu_bias
"""
            result = subprocess.run(["bash", "-c", commands], input=answers, text=True,
                                    capture_output=True, check=True, timeout=10)
            invocations = calls.read_text() if calls.exists() else ""
            self.assertEqual(list(root.glob(".imu-bias-*.json")), [])
            return result.stdout, invocations

    def test_participant_approval_applies_proposal_with_a4_storage(self):
        output, calls = self.run_check("y\ny\n")
        self.assertIn("RESULT:pass", output)
        self.assertIn("--proposal-output", calls)
        self.assertIn("--apply-proposal", calls)
        self.assertIn("--bias-output '/vehicle/.calibration/A4/imu_bias.yaml'", calls)

    def test_decline_or_eof_does_not_invoke_apply(self):
        for answers in ("y\nn\n", "y\n", "y\n\n"):
            with self.subTest(answers=answers):
                output, calls = self.run_check(answers)
                self.assertIn("RESULT:warn", output)
                self.assertIn("--proposal-output", calls)
                self.assertNotIn("--apply-proposal", calls)

    def test_noisy_failed_or_missing_target_does_not_invoke_apply(self):
        for rc, status in ((3, "fail"), (4, "warn"), (5, "warn")):
            with self.subTest(rc=rc):
                output, calls = self.run_check("y\nn\n", measure_rc=rc)
                self.assertIn(f"RESULT:{status}", output)
                self.assertNotIn("--apply-proposal", calls)

    def test_unknown_id_allows_measurement_and_approved_parameter_update_without_storage(self):
        output, calls = self.run_check("y\ny\n", vehicle_id="A9")
        self.assertIn("RESULT:pass", output)
        self.assertIn("--apply-proposal", calls)
        self.assertNotIn("--bias-output", calls)

    def test_unconfirmed_stationarity_does_not_measure(self):
        output, calls = self.run_check("n\n")
        self.assertIn("RESULT:warn", output)
        self.assertEqual(calls, "")


if __name__ == "__main__":
    unittest.main()
