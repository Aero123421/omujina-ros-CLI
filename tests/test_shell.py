from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mujina_assist.services.shell import run_bash


class ShellTest(unittest.TestCase):
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
