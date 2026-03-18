from __future__ import annotations

import unittest

from mujina_assist.services.workspace import build_initial_setup_script


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


if __name__ == "__main__":
    unittest.main()
