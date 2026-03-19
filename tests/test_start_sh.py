from __future__ import annotations

import unittest
from pathlib import Path


class StartShTest(unittest.TestCase):
    def test_start_sh_routes_no_args_to_menu_and_passes_worker_args_through(self) -> None:
        script = Path("start.sh").read_text(encoding="utf-8")
        self.assertIn('if [[ "$#" -eq 0 ]]; then', script)
        self.assertIn('exec "${RUNNER[@]}" -m mujina_assist.main menu', script)
        self.assertIn('exec "${RUNNER[@]}" -m mujina_assist.main "$@"', script)


if __name__ == "__main__":
    unittest.main()
