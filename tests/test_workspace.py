from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mujina_assist.models import AppPaths
from mujina_assist.services.shell import CommandResult
from mujina_assist.services.workspace import build_initial_setup_script
from unittest.mock import patch


class WorkspaceTest(unittest.TestCase):
    def test_initial_setup_script_uses_release_redirect_and_validates_deb(self) -> None:
        script = build_initial_setup_script()
        self.assertIn("curl -fsSLI https://github.com/ros-infrastructure/ros-apt-source/releases/latest", script)
        self.assertIn("ROS_APT_SOURCE_VERSION=${ROS_APT_SOURCE_REDIRECT##*/}", script)
        self.assertIn("curl -fL -o /tmp/ros2-apt-source.deb", script)
        self.assertIn("dpkg-deb --info /tmp/ros2-apt-source.deb >/dev/null", script)
        self.assertNotIn("api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest", script)

    def test_initial_setup_script_can_skip_upgrade(self) -> None:
        script = build_initial_setup_script(skip_upgrade=True)
        self.assertNotIn("sudo apt upgrade -y", script)

    def test_workspace_dependency_script_uses_cpu_torch_wheels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()
            with patch(
                "mujina_assist.services.workspace.run_bash",
                return_value=CommandResult(command="ok", returncode=0),
            ) as mocked:
                from mujina_assist.services.workspace import run_workspace_dependency_setup

                run_workspace_dependency_setup(paths, Path(tmp) / "setup.log")

            script = mocked.call_args.args[0]
            self.assertIn("https://download.pytorch.org/whl/cpu", script)
            self.assertIn("export PIP_NO_CACHE_DIR=1", script)
            self.assertIn("python3 -m pip install --break-system-packages --no-cache-dir mujoco onnxruntime", script)


if __name__ == "__main__":
    unittest.main()
