from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mujina_assist.models import AppPaths, RuntimeState
from mujina_assist.services.state import _corrupt_backup_path, load_runtime_state, save_runtime_state


class StateTest(unittest.TestCase):
    def test_save_runtime_state_uses_atomic_path_and_loads_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".state" / "runtime.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            state = RuntimeState(active_policy_label="policy", last_sim_success=True)

            save_runtime_state(path, state)
            loaded = load_runtime_state(path)
            self.assertEqual(loaded.active_policy_label, "policy")
            self.assertTrue(loaded.last_sim_success)
            self.assertFalse((path.with_suffix(f"{path.suffix}.tmp")).exists())

    def test_load_runtime_state_moves_corrupt_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()
            state_path = paths.runtime_state_file
            state_path.write_text("{", encoding="utf-8")

            loaded = load_runtime_state(state_path)
            backups = list(paths.state_dir.glob("runtime.json.corrupt.*"))

            self.assertEqual(loaded.active_policy_label, "未設定")
            self.assertEqual(len(backups), 1)
            self.assertFalse(state_path.exists())

    def test_load_runtime_state_moves_non_utf8_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()
            state_path = paths.runtime_state_file
            state_path.write_bytes(b"\xff\xfe")

            loaded = load_runtime_state(state_path)
            backups = list(paths.state_dir.glob("runtime.json.corrupt.*"))

            self.assertEqual(loaded.active_policy_label, "未設定")
            self.assertEqual(len(backups), 1)
            self.assertFalse(state_path.exists())

    def test_load_runtime_state_moves_type_corrupted_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()
            state_path = paths.runtime_state_file
            state_path.write_text('{"last_sim_success":"false"}', encoding="utf-8")

            loaded = load_runtime_state(state_path)
            backups = list(paths.state_dir.glob("runtime.json.corrupt.*"))

            self.assertFalse(loaded.last_sim_success)
            self.assertEqual(len(backups), 1)
            self.assertFalse(state_path.exists())

    def test_corrupt_backup_path_avoids_existing_name_collision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "runtime.json"
            first = _corrupt_backup_path(state_path)
            first.write_text("old", encoding="utf-8")

            second = _corrupt_backup_path(state_path)

            self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
