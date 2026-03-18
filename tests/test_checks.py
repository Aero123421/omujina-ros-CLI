from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mujina_assist.models import AppPaths, RuntimeState
from mujina_assist.services.checks import current_policy_label


class ChecksTest(unittest.TestCase):
    def test_policy_label_unknown_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()
            label = current_policy_label(paths, RuntimeState())
            self.assertEqual(label, "未設定")


if __name__ == "__main__":
    unittest.main()
