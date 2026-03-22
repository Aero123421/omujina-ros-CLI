from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mujina_assist.models import AppPaths
from mujina_assist.services.processes import (
    build_real_imu_script,
    build_real_main_script,
    build_motor_probe_script,
    build_motor_read_script,
    build_zero_script,
)


class ProcessScriptTest(unittest.TestCase):
    def test_build_motor_probe_script_is_one_shot_and_sets_up_can(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            script = build_motor_probe_script(paths, [1, 2], "serial")

            self.assertIn("can_setup_serial.sh", script)
            self.assertIn("python3 - <<'PY'", script)
            self.assertIn("Motor probe completed.", script)
            self.assertIn("ids = [1, 2]", script)
            self.assertNotIn("while True", script)

    def test_build_motor_read_script_passes_device_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            script = build_motor_read_script(paths, [3], "net")

            self.assertIn("motor_test_read_only.py", script)
            self.assertIn("--device can0", script)
            self.assertIn("--ids 3", script)

    def test_build_zero_script_can_skip_can_setup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            script = build_zero_script(paths, [4], "serial", include_can_setup=False)

            self.assertIn("motor_set_zero_position.py", script)
            self.assertIn("--device can0", script)
            self.assertIn("--ids 4", script)
            self.assertNotIn("can_setup_serial.sh", script)

    def test_build_real_main_script_rejects_invalid_can_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            with self.assertRaises(ValueError):
                build_real_main_script(paths, "invalid")

    def test_build_real_imu_script_quotes_port_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            script = build_real_imu_script(paths, "/tmp/rt usb imu")

            self.assertIn("-p 'port_name:=/tmp/rt usb imu'", script)


if __name__ == "__main__":
    unittest.main()
