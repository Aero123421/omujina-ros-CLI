from __future__ import annotations

import unittest
from pathlib import Path


class StartShTest(unittest.TestCase):
    def test_start_sh_routes_no_args_to_menu_and_passes_worker_args_through(self) -> None:
        script = Path("start.sh").read_text(encoding="utf-8")
        self.assertIn('if [[ "$#" -eq 0 ]]; then', script)
        self.assertIn('if "$VENV_PYTHON" -m pip --version', script)
        self.assertIn('exec "${RUNNER[@]}" -m mujina_assist.main menu', script)
        self.assertIn('exec "${RUNNER[@]}" -m mujina_assist.main "$@"', script)

    def test_start_sh_uses_lf_line_endings(self) -> None:
        script_bytes = Path("start.sh").read_bytes()
        self.assertNotIn(b"\r\n", script_bytes)

    def test_container_smoke_test_checks_worker_exit_code_contract(self) -> None:
        script = Path("scripts/run-container-tests.sh").read_text(encoding="utf-8")
        self.assertIn('python3 -m unittest discover -s tests -t . -q', script)
        self.assertIn("WORKER_EXIT=$?", script)
        self.assertNotIn('worker --job-file "$JOB_FILE" || true', script)
        self.assertIn('WORKER_EXIT_ENV="$WORKER_EXIT"', script)


if __name__ == "__main__":
    unittest.main()
