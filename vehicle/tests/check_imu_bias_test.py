"""Exercise measurement persistence using mocked ROS inputs."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from calibration import parse_offsets, save_bias  # noqa: E402
from calibration_test import OFFSETS, PARAM  # noqa: E402


def load_checker():
    modules = {name: ModuleType(name) for name in (
        "rclpy", "rclpy.node", "rclpy.qos", "sensor_msgs", "sensor_msgs.msg",
        "autoware_vehicle_msgs", "autoware_vehicle_msgs.msg",
    )}
    modules["rclpy.node"].Node = object
    modules["rclpy.qos"].qos_profile_sensor_data = object()
    modules["sensor_msgs.msg"].Imu = object
    modules["autoware_vehicle_msgs.msg"].VelocityReport = object
    spec = importlib.util.spec_from_file_location(
        "mocked_imu_checker", Path(__file__).resolve().parents[1] / "check_imu_bias.py"
    )
    checker = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(checker)
    return checker


class MeasurementPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.checker = load_checker()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.param = self.root / "param.yaml"
        self.param.write_text(PARAM)
        self.bias = self.root / "A2/imu_bias.yaml"
        save_bias(self.bias, {"x": 0, "y": 0, "z": 0})
        self.node = MagicMock()
        self.node.sample_count = 10
        self.node.velocity_seen = False
        self.node.stats.return_value = {axis: (value, 0.001) for axis, value in OFFSETS.items()}

    def run_measurement(self, duration="0", answer="y", proposal_output=None):
        argv = ["check_imu_bias.py", "--duration", duration, "--warmup", "0",
                "--param-yaml", str(self.param), "--bias-output", str(self.bias)]
        if proposal_output is not None:
            argv += ["--proposal-output", str(proposal_output)]
        input_patch = patch("builtins.input", side_effect=answer) if isinstance(answer, BaseException) else patch("builtins.input", return_value=answer)
        with input_patch, patch.object(self.checker, "rclpy", MagicMock()), \
                patch.object(self.checker, "ImuBiasChecker", return_value=self.node), \
                patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
            return self.checker.main()

    def test_approval_saves_bias_and_preserves_other_parameter_settings(self):
        self.assertEqual(self.run_measurement(), 0)
        self.assertEqual(parse_offsets(self.param.read_text()), OFFSETS)
        self.assertEqual(parse_offsets(self.bias.read_text(), flat=True), OFFSETS)

    def test_noisy_or_insufficient_samples_do_not_update_either_file(self):
        for noisy in (True, False):
            with self.subTest(noisy=noisy):
                before = (self.param.read_bytes(), self.bias.read_bytes())
                self.node.sample_count = 10 if noisy else 1
                self.node.stats.return_value = {axis: (value, 1) for axis, value in OFFSETS.items()}
                self.assertEqual(self.run_measurement(), 4 if noisy else 3)
                self.assertEqual((self.param.read_bytes(), self.bias.read_bytes()), before)

    def test_invalid_target_does_not_save_measurement(self):
        self.param.write_text("invalid params")
        before = self.bias.read_bytes()
        self.assertEqual(self.run_measurement(), 5)
        self.assertEqual(self.bias.read_bytes(), before)

    def test_movement_during_sampling_does_not_update_either_file(self):
        before = (self.param.read_bytes(), self.bias.read_bytes())
        self.node.velocity_seen = True
        self.node.max_abs_velocity = 1.0
        self.assertEqual(self.run_measurement(duration="1"), 3)
        self.assertEqual((self.param.read_bytes(), self.bias.read_bytes()), before)

    def test_save_failure_is_reported_and_preserves_previous_saved_bias(self):
        before = self.bias.read_bytes()
        with patch.object(self.checker, "save_bias", side_effect=OSError("read only")):
            self.assertEqual(self.run_measurement(), 3)
        self.assertEqual(self.bias.read_bytes(), before)
        self.assertEqual(self.param.read_text(), PARAM)

    def test_decline_empty_input_or_eof_preserves_both_files(self):
        for answer in ("n", "", "unexpected", EOFError()):
            with self.subTest(answer=answer):
                before = (self.param.read_bytes(), self.bias.read_bytes())
                self.assertEqual(self.run_measurement(answer=answer), 5)
                self.assertEqual((self.param.read_bytes(), self.bias.read_bytes()), before)

    def test_measurement_displays_current_measured_values_and_signed_difference(self):
        output = io.StringIO()
        with patch("builtins.input", return_value="n"), \
                patch.object(self.checker, "rclpy", MagicMock()), \
                patch.object(self.checker, "ImuBiasChecker", return_value=self.node), \
                patch.object(sys, "argv", ["check_imu_bias.py", "--duration", "0", "--warmup", "0",
                                           "--param-yaml", str(self.param)]), \
                contextlib.redirect_stdout(output):
            self.assertEqual(self.checker.main(), 5)
        text = output.getvalue()
        self.assertIn("現在値", text)
        self.assertIn("実測値", text)
        self.assertIn("差分", text)
        self.assertIn("z     +0.001000  +0.003000  +0.002000", text)

    def test_failed_or_noisy_measurement_does_not_request_update_approval(self):
        for count, std, rc in ((1, 0.001, 3), (10, 1.0, 4)):
            with self.subTest(count=count):
                self.node.sample_count = count
                self.node.stats.return_value = {axis: (value, std) for axis, value in OFFSETS.items()}
                proposal = self.root / "proposal.json"
                self.assertEqual(self.run_measurement(answer=AssertionError("must not ask"),
                                                      proposal_output=proposal), rc)
                self.assertFalse(proposal.exists())

    def test_missing_target_is_skipped_without_measurement_or_update(self):
        self.param.unlink()
        with patch.object(self.checker, "ImuBiasChecker", side_effect=AssertionError("must not measure")):
            self.assertEqual(self.run_measurement(), 5)
        self.assertEqual(parse_offsets(self.bias.read_text(), flat=True), {"x": 0, "y": 0, "z": 0})

    def test_proposal_does_not_modify_files_and_approved_apply_uses_same_measurement(self):
        proposal = self.root / "proposal.json"
        before = (self.param.read_bytes(), self.bias.read_bytes())
        self.assertEqual(self.run_measurement(proposal_output=proposal), 0)
        self.assertEqual((self.param.read_bytes(), self.bias.read_bytes()), before)
        argv = ["check_imu_bias.py", "--apply-proposal", str(proposal), "--bias-output", str(self.bias)]
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.checker.main(), 0)
        self.assertEqual(parse_offsets(self.param.read_text()), OFFSETS)
        self.assertEqual(parse_offsets(self.bias.read_text(), flat=True), OFFSETS)

    def test_changed_participant_settings_reject_stale_proposal(self):
        proposal = self.root / "proposal.json"
        self.assertEqual(self.run_measurement(proposal_output=proposal), 0)
        self.param.write_text(PARAM + "# participant edited after measurement\n")
        before = (self.param.read_bytes(), self.bias.read_bytes())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.checker.apply_proposal(json.loads(proposal.read_text()), self.bias), 3)
        self.assertEqual((self.param.read_bytes(), self.bias.read_bytes()), before)

    def test_readonly_parameter_can_be_updated_after_approval(self):
        self.param.chmod(0o444)
        self.assertEqual(self.run_measurement(), 0)
        self.assertEqual(parse_offsets(self.param.read_text()), OFFSETS)
        self.assertEqual(self.param.stat().st_mode & 0o777, 0o444)

    def test_parameter_write_failure_does_not_update_saved_bias(self):
        before = (self.param.read_bytes(), self.bias.read_bytes())
        with patch.object(self.checker, "atomic_write", side_effect=OSError("read only directory")):
            self.assertEqual(self.run_measurement(), 3)
        self.assertEqual((self.param.read_bytes(), self.bias.read_bytes()), before)


if __name__ == "__main__":
    unittest.main()
