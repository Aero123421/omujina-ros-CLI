from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mujina_assist.models import AppPaths, PolicyCandidate, RuntimeState
from mujina_assist.services.policy import activate_policy, import_policy_to_cache
from mujina_assist.services.shell import CommandResult


class PolicyTest(unittest.TestCase):
    def test_import_policy_to_cache_copies_usb_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            paths = AppPaths.from_repo_root(repo_root)
            paths.ensure_directories()
            source = repo_root / "demo.onnx"
            source.write_bytes(b"onnx")
            candidate = PolicyCandidate(
                label="USB: demo.onnx",
                path=source,
                source_type="usb",
            )
            cached = import_policy_to_cache(paths, candidate)
            self.assertTrue(cached.exists())
            self.assertEqual(cached.read_bytes(), b"onnx")

    def test_activate_policy_restores_previous_file_when_build_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            paths = AppPaths.from_repo_root(repo_root)
            paths.ensure_directories()
            paths.upstream_dir.mkdir(parents=True, exist_ok=True)
            source_policy = paths.source_policy_path
            source_policy.parent.mkdir(parents=True, exist_ok=True)
            source_policy.write_bytes(b"default")
            paths.default_policy_cache.write_bytes(b"default")

            incoming = repo_root / "new.onnx"
            incoming.write_bytes(b"broken")
            candidate = PolicyCandidate(
                label="USB: new.onnx",
                path=incoming,
                source_type="usb",
            )
            state = RuntimeState(
                active_policy_label="公式デフォルト",
                active_policy_source=str(paths.default_policy_cache),
                active_policy_hash="abc",
            )
            with patch(
                "mujina_assist.services.policy.run_workspace_build_with_options",
                return_value=CommandResult(command="build", returncode=1),
            ), patch(
                "mujina_assist.services.policy.run_onnx_self_test",
                return_value=CommandResult(command="onnx", returncode=0),
            ):
                ok, _message = activate_policy(paths, state, candidate, repo_root / "policy.log")

            self.assertFalse(ok)
            self.assertEqual(source_policy.read_bytes(), b"default")
            self.assertEqual(state.active_policy_label, "公式デフォルト")


if __name__ == "__main__":
    unittest.main()
