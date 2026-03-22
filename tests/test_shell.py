from __future__ import annotations

import shlex
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mujina_assist.services.shell import run_bash


class ShellTest(unittest.TestCase):
    def test_run_bash_returns_failure_when_bash_cannot_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "mujina_assist.services.shell.subprocess.run",
                side_effect=OSError("bash missing"),
            ):
                result = run_bash("echo hello", cwd=Path(tmp), interactive=True)

        self.assertEqual(result.returncode, 1)
        self.assertIn("bash missing", result.stderr)

    def test_run_bash_creates_log_parent_directory_for_interactive_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing_parent = Path(tmp) / "deep" / "nested"
            log_path = missing_parent / "sample.log"
            script = "echo ok"

            with patch(
                "mujina_assist.services.shell.subprocess.run",
                return_value=SimpleNamespace(returncode=0),
            ) as run_mock:
                result = run_bash(script, cwd=Path(tmp), log_path=log_path, interactive=True)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(missing_parent, log_path.parent)
            self.assertTrue(missing_parent.exists())
            wrapped = run_mock.call_args.args[0][2]
            expected_log_path = shlex.quote(str(missing_parent / "sample.log"))
            self.assertIn(f") 2>&1 | tee -a {expected_log_path}", wrapped)

    def test_run_bash_interactive_log_wraps_multiline_script_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "sample.log"
            script = "python3 - <<'PY'\nprint('ok')\nPY"

            with patch(
                "mujina_assist.services.shell.subprocess.run",
                return_value=SimpleNamespace(returncode=0),
            ) as run_mock:
                result = run_bash(script, cwd=Path(tmp), log_path=log_path, interactive=True)

            self.assertEqual(result.returncode, 0)
            wrapped = run_mock.call_args.args[0][2]
            self.assertIn("set -o pipefail\n(\n", wrapped)
            self.assertIn("\nPY\n) 2>&1 | tee -a ", wrapped)

    def test_run_bash_interactive_without_log_uses_original_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = "echo hello"

            with patch(
                "mujina_assist.services.shell.subprocess.run",
                return_value=SimpleNamespace(returncode=0),
            ) as run_mock:
                result = run_bash(script, cwd=Path(tmp), interactive=True)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(run_mock.call_args.args[0], ["bash", "-lc", script])


if __name__ == "__main__":
    unittest.main()
