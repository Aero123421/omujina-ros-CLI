from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mujina_assist.models import AppPaths
from mujina_assist.services.jobs import create_job
from mujina_assist.services.terminals import _backend_command, launch_job, stop_job_launch, write_worker_script


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
                return_value=(True, ""),
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
                return_value=(SimpleNamespace(pid=4321), ""),
            ):
                result = launch_job(paths, job)

            self.assertTrue(result.ok)
            self.assertEqual(result.mode, "terminal")
            self.assertEqual(result.pid, 4321)

    def test_launch_job_includes_failure_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()
            job = create_job(paths, kind="build", name="workspace build")

            with patch("mujina_assist.services.terminals.has_graphical_session", return_value=True), patch(
                "mujina_assist.services.terminals.terminal_backends",
                return_value=["gnome-terminal", "konsole"],
            ), patch(
                "mujina_assist.services.terminals._launch_in_graphical_terminal",
                side_effect=[(None, "起動に失敗"), (None, "権限エラー")],
            ), patch(
                "mujina_assist.services.terminals.command_exists",
                return_value=False,
            ):
                result = launch_job(paths, job)

            self.assertFalse(result.ok)
            self.assertIn("gnome-terminal: 起動に失敗", result.message)
            self.assertIn("konsole: 権限エラー", result.message)
            self.assertGreaterEqual(len(result.failure_reasons), 2)

    def test_stop_job_launch_for_terminal_reports_unconfirmed_stop(self) -> None:
        with patch("mujina_assist.services.terminals.os.kill") as kill_mock:
            message = stop_job_launch(mode="terminal", label="gnome-terminal", pid=4321)

        kill_mock.assert_called_once()
        self.assertIn("SIGTERM", message or "")
        self.assertIn("停止確認", message or "")

    def test_stop_job_launch_for_tmux_reports_oserror(self) -> None:
        with patch(
            "mujina_assist.services.terminals.subprocess.run",
            side_effect=OSError("tmux missing"),
        ):
            message = stop_job_launch(mode="tmux", label="ma-demo")

        self.assertIn("tmux missing", message or "")

    def test_backend_command_wraps_xfce_script_with_bash_lc(self) -> None:
        command = _backend_command("xfce4-terminal", Path("/tmp/demo script.sh"), "demo")

        self.assertEqual(command[:3], ["xfce4-terminal", "--title", "demo"])
        self.assertIn("bash -lc", command[-1])
        self.assertIn("demo script.sh", command[-1])


if __name__ == "__main__":
    unittest.main()
