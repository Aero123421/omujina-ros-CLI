from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mujina_assist.models import AppPaths, RuntimeState
from mujina_assist.services.checks import build_doctor_report, current_policy_label, resolve_imu_port, sim_policy_verified


class ChecksTest(unittest.TestCase):
    def test_policy_label_unknown_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()
            label = current_policy_label(paths, RuntimeState())
            self.assertEqual(label, "未設定")

    def test_sim_policy_verified_when_hash_matches(self) -> None:
        state = RuntimeState(active_policy_hash="abc", last_sim_success=True, last_sim_policy_hash="abc")
        self.assertTrue(sim_policy_verified(state))

    def test_resolve_imu_port_falls_back_to_single_generic_serial(self) -> None:
        with patch.object(
            Path,
            "exists",
            autospec=True,
            side_effect=lambda path: not str(path).endswith("rt_usb_imu"),
        ), patch(
            "mujina_assist.services.checks.list_serial_device_candidates",
            return_value=["/dev/ttyACM0"],
        ):
            port, fallback, candidates = resolve_imu_port()

        self.assertEqual(port, "/dev/ttyACM0")
        self.assertTrue(fallback)
        self.assertEqual(candidates, ["/dev/ttyACM0"])

    def test_doctor_report_notes_serial_alias_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()
            with patch(
                "mujina_assist.services.checks.read_os_release",
                return_value={"VERSION_ID": "24.04", "PRETTY_NAME": "Ubuntu 24.04"},
            ), patch(
                "mujina_assist.services.checks.detect_real_devices",
                return_value={
                    "/dev/rt_usb_imu": False,
                    "/dev/usb_can": False,
                    "can0": False,
                    "/dev/input/js0": False,
                },
            ), patch(
                "mujina_assist.services.checks.real_setup_status",
                return_value={"dialout": True, "udev_rule": True},
            ), patch(
                "mujina_assist.services.checks.list_serial_device_candidates",
                return_value=["/dev/ttyUSB0"],
            ), patch(
                "mujina_assist.services.checks.count_usb_policies",
                return_value=0,
            ), patch(
                "mujina_assist.services.checks.command_exists",
                side_effect=lambda name: name in {"git", "bash", "tmux", "colcon", "rosdep"},
            ), patch(
                "mujina_assist.services.checks.graphical_terminal_available",
                return_value=True,
            ), patch(
                "mujina_assist.services.checks.inspect_can_status",
                return_value={"present": False, "operstate": "missing", "controller_state": "", "txqueuelen": None, "raw": "", "ok": False, "warn": False},
            ):
                report = build_doctor_report(paths, RuntimeState())

            joined = "\n".join(report.notes)
            self.assertIn("固定名デバイス", joined)
            self.assertIn("代替ポート", joined)
            self.assertNotIn("slcand", joined)

    def test_doctor_report_marks_fallback_imu_as_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()
            with patch(
                "mujina_assist.services.checks.read_os_release",
                return_value={"VERSION_ID": "24.04", "PRETTY_NAME": "Ubuntu 24.04"},
            ), patch(
                "mujina_assist.services.checks.detect_real_devices",
                return_value={
                    "/dev/rt_usb_imu": False,
                    "/dev/usb_can": False,
                    "can0": True,
                    "/dev/input/js0": True,
                },
            ), patch(
                "mujina_assist.services.checks.real_setup_status",
                return_value={"dialout": True, "udev_rule": True},
            ), patch(
                "mujina_assist.services.checks.resolve_imu_port",
                return_value=("/dev/ttyACM0", True, ["/dev/ttyACM0"]),
            ), patch(
                "mujina_assist.services.checks.command_exists",
                return_value=True,
            ), patch(
                "mujina_assist.services.checks.graphical_terminal_available",
                return_value=True,
            ), patch(
                "mujina_assist.services.checks.inspect_can_status",
                return_value={"present": True, "operstate": "up", "controller_state": "error-active", "txqueuelen": 10, "raw": "", "ok": True, "warn": False},
            ):
                report = build_doctor_report(paths, RuntimeState())

            imu_check = next(check for check in report.checks if check.key == "imu")
            self.assertEqual(imu_check.status, "warn")
            self.assertIn("代替ポート", "\n".join(imu_check.details))

    def test_doctor_report_notes_relogin_and_gamepad_mode_reminder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()
            state = RuntimeState(real_setup_requires_relogin=True)
            with patch(
                "mujina_assist.services.checks.read_os_release",
                return_value={"VERSION_ID": "24.04", "PRETTY_NAME": "Ubuntu 24.04"},
            ), patch(
                "mujina_assist.services.checks.detect_real_devices",
                return_value={
                    "/dev/rt_usb_imu": True,
                    "/dev/usb_can": True,
                    "can0": False,
                    "/dev/input/js0": True,
                },
            ), patch(
                "mujina_assist.services.checks.real_setup_status",
                return_value={"dialout": True, "udev_rule": True},
            ), patch(
                "mujina_assist.services.checks.resolve_imu_port",
                return_value=("/dev/rt_usb_imu", False, []),
            ), patch(
                "mujina_assist.services.checks.command_exists",
                return_value=True,
            ), patch(
                "mujina_assist.services.checks.graphical_terminal_available",
                return_value=True,
            ), patch(
                "mujina_assist.services.checks.inspect_can_status",
                return_value={"present": True, "operstate": "up", "controller_state": "error-active", "txqueuelen": 10, "raw": "", "ok": True, "warn": False},
            ):
                report = build_doctor_report(paths, state)

            joined = "\n".join(report.notes)
            self.assertIn("ログアウト / ログイン", joined)
            self.assertIn("MODE LED OFF", joined)
            self.assertIn("再ログイン", report.recommendation)


if __name__ == "__main__":
    unittest.main()
