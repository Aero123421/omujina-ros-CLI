from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mujina_assist.models import AppPaths
from mujina_assist.services.jobs import create_job
from mujina_assist.services.terminals import launch_job, write_worker_script


class TerminalsTest(unittest.TestCase):
    def test_write_worker_script_points_to_worker_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()
            job = create_job(paths, kind="build", name="workspace build")

            script_path = write_worker_script(paths, job)
            content = script_path.read_text(encoding="utf-8")

            self.assertIn("bash ./start.sh worker --job-file", content)
            self.assertIn(job.job_file, content)

    def test_launch_job_falls_back_to_tmux_when_gui_terminal_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()
            job = create_job(paths, kind="build", name="workspace build")

            with patch("mujina_assist.services.terminals.has_graphical_session", return_value=False), patch(
                "mujina_assist.services.terminals.command_exists",
                side_effect=lambda name: name == "tmux",
            ), patch(
                "mujina_assist.services.terminals._launch_in_tmux",
                return_value=True,
            ):
                result = launch_job(paths, job)

            self.assertTrue(result.ok)
            self.assertEqual(result.mode, "tmux")

    def test_launch_job_returns_terminal_pid_for_graphical_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()
            job = create_job(paths, kind="build", name="workspace build")

            with patch("mujina_assist.services.terminals.has_graphical_session", return_value=True), patch(
                "mujina_assist.services.terminals.terminal_backends",
                return_value=["gnome-terminal"],
            ), patch(
                "mujina_assist.services.terminals._launch_in_graphical_terminal",
                return_value=SimpleNamespace(pid=4321),
            ):
                result = launch_job(paths, job)

            self.assertTrue(result.ok)
            self.assertEqual(result.mode, "terminal")
            self.assertEqual(result.pid, 4321)


if __name__ == "__main__":
    unittest.main()
