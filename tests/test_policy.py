from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mujina_assist.models import AppPaths, PolicyCandidate, RuntimeState
from mujina_assist.services.policy import activate_policy, cleanup_policy_cache, import_policy_to_cache
from mujina_assist.services.shell import CommandResult


class PolicyTest(unittest.TestCase):
    def test_import_policy_to_cache_copies_usb_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            paths = AppPaths.from_repo_root(repo_root)
            paths.ensure_directories()
            source = repo_root / "demo.onnx"
            source.write_bytes(b"onnx")
            candidate = PolicyCandidate(label="USB: demo.onnx", path=source, source_type="usb")

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
            candidate = PolicyCandidate(label="USB: new.onnx", path=incoming, source_type="usb")
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

    def test_activate_policy_clears_stale_manual_recovery_when_rollback_succeeds(self) -> None:
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
            candidate = PolicyCandidate(label="USB: new.onnx", path=incoming, source_type="usb")
            state = RuntimeState(
                active_policy_label="公式デフォルト",
                active_policy_source=str(paths.default_policy_cache),
                active_policy_hash="abc",
                manual_recovery_required=True,
                manual_recovery_summary="old",
            )
            with patch(
                "mujina_assist.services.policy.run_workspace_build_with_options",
                side_effect=[
                    CommandResult(command="build", returncode=1),
                    CommandResult(command="rollback", returncode=0),
                ],
            ), patch(
                "mujina_assist.services.policy.run_onnx_self_test",
                return_value=CommandResult(command="onnx", returncode=0),
            ):
                ok, _message = activate_policy(paths, state, candidate, repo_root / "policy.log")

            self.assertFalse(ok)
            self.assertFalse(state.manual_recovery_required)
            self.assertEqual(state.manual_recovery_kind, "")
            self.assertEqual(state.manual_recovery_summary, "")

    def test_activate_policy_marks_manual_recovery_when_rollback_also_fails(self) -> None:
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
            candidate = PolicyCandidate(label="USB: new.onnx", path=incoming, source_type="usb")
            state = RuntimeState(
                active_policy_label="公式デフォルト",
                active_policy_source=str(paths.default_policy_cache),
                active_policy_hash="abc",
            )
            with patch(
                "mujina_assist.services.policy.run_workspace_build_with_options",
                side_effect=[
                    CommandResult(command="build", returncode=1),
                    CommandResult(command="rollback", returncode=1),
                ],
            ), patch(
                "mujina_assist.services.policy.run_onnx_self_test",
                return_value=CommandResult(command="onnx", returncode=0),
            ):
                ok, message = activate_policy(paths, state, candidate, repo_root / "policy.log")

            self.assertFalse(ok)
            self.assertIn("復旧", message)
            self.assertTrue(state.manual_recovery_required)
            self.assertEqual(state.manual_recovery_kind, "policy")
            self.assertIn("手動で確認", state.manual_recovery_summary)
            self.assertEqual(source_policy.read_bytes(), b"default")
            self.assertEqual(state.active_policy_label, "公式デフォルト")

    def test_cleanup_policy_cache_preserves_active_and_sim_verified_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            paths = AppPaths.from_repo_root(repo_root)
            paths.ensure_directories()

            protected = paths.imported_policy_dir / "protected.onnx"
            protected.write_bytes(b"a")
            old = paths.imported_policy_dir / "old.onnx"
            old.write_bytes(b"b")
            paths.policy_index_file.write_text(
                '{"entries": ['
                '{"policy_hash": "active", "blob_path": "%s", "label": "active", "source_kind": "usb", "original_path": "", "size_bytes": 1, "first_seen_at": "2026-01-01T00:00:00+09:00", "last_used_at": "2026-01-02T00:00:00+09:00", "use_count": 1, "pinned": false, "manifest_path": ""},'
                '{"policy_hash": "old", "blob_path": "%s", "label": "old", "source_kind": "usb", "original_path": "", "size_bytes": 1, "first_seen_at": "2026-01-01T00:00:00+09:00", "last_used_at": "2026-01-01T00:00:00+09:00", "use_count": 1, "pinned": false, "manifest_path": ""}'
                "]}"
                % (str(protected).replace("\\", "\\\\"), str(old).replace("\\", "\\\\")),
                encoding="utf-8",
            )
            state = RuntimeState(active_policy_hash="active", active_policy_source=str(protected), last_sim_policy_hash="active")

            with patch("mujina_assist.services.policy.MAX_CACHED_POLICIES", 1):
                result = cleanup_policy_cache(paths, state, dry_run=False)

            self.assertEqual(result["deleted_entries"], 1)
            self.assertTrue(protected.exists())
            self.assertFalse(old.exists())


if __name__ == "__main__":
    unittest.main()
