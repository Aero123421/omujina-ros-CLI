from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mujina_assist.app import MujinaAssistApp
from mujina_assist.models import PolicyCandidate
from mujina_assist.services.jobs import list_jobs


class AppTest(unittest.TestCase):
    def test_missing_devices_for_serial_mode_only_requires_serial_can(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            with patch(
                "mujina_assist.app.detect_real_devices",
                return_value={
                    "/dev/rt_usb_imu": True,
                    "/dev/usb_can": True,
                    "/dev/input/js0": False,
                    "can0": False,
                },
            ):
                missing = app._missing_devices_for_can_mode(
                    "serial",
                    include_imu=True,
                    include_joy=False,
                )

            self.assertEqual(missing, [])

    def test_select_can_mode_prefers_available_requested_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            with patch(
                "mujina_assist.app.detect_real_devices",
                return_value={
                    "/dev/rt_usb_imu": False,
                    "/dev/usb_can": True,
                    "/dev/input/js0": False,
                    "can0": True,
                },
            ):
                self.assertEqual(app._select_can_mode("serial"), "serial")
                self.assertEqual(app._select_can_mode("net"), "net")

    def test_ask_ids_rejects_invalid_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            with patch("mujina_assist.app.ask_text", return_value="1 a 2"):
                self.assertEqual(app._ask_ids(), [])

    def test_ask_ids_accepts_commas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            with patch("mujina_assist.app.ask_text", return_value="1,2,3"), patch(
                "mujina_assist.app.ask_yes_no",
                return_value=True,
            ):
                self.assertEqual(app._ask_ids(), [1, 2, 3])

    def test_confirm_no_conflicting_jobs_returns_false_when_user_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            from mujina_assist.services.jobs import create_job, update_job

            job = create_job(app.paths, kind="build", name="workspace build")
            update_job(job, status="running")

            with patch("mujina_assist.app.ask_yes_no", return_value=False):
                self.assertFalse(app._confirm_no_conflicting_jobs({"build"}))

    def test_handle_setup_creates_setup_job_with_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            with patch("mujina_assist.app.ask_yes_no", side_effect=[True, True]), patch.object(
                app,
                "_launch_job",
                return_value=0,
            ):
                result = app.handle_setup(skip_upgrade=True)

            self.assertEqual(result, 0)
            jobs = list_jobs(app.paths)
            self.assertEqual(jobs[0].kind, "setup")
            self.assertTrue(jobs[0].payload["skip_upgrade"])
            self.assertTrue(jobs[0].payload["setup_real_devices"])

    def test_handle_sim_creates_two_jobs_and_updates_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            app.state.active_policy_hash = "abc"
            app.paths.upstream_dir.mkdir(parents=True, exist_ok=True)
            (app.paths.upstream_dir / ".git").mkdir(parents=True, exist_ok=True)
            (app.paths.upstream_dir / "mujina_control").mkdir(parents=True, exist_ok=True)
            (app.paths.upstream_dir / "mujina_description").mkdir(parents=True, exist_ok=True)
            install_dir = app.paths.workspace_dir / "install" / "mujina_control"
            install_dir.mkdir(parents=True, exist_ok=True)
            (app.paths.workspace_dir / "install" / "setup.bash").write_text("", encoding="utf-8")

            with patch("mujina_assist.app.ask_yes_no", return_value=True), patch.object(
                app,
                "_launch_job_group",
                return_value=0,
            ):
                result = app.handle_sim()

            self.assertEqual(result, 0)
            jobs = list_jobs(app.paths)
            self.assertEqual(len(jobs), 2)
            self.assertEqual({job.kind for job in jobs}, {"sim_main", "sim_joy"})
            self.assertEqual(app.state.last_action, "sim_launch")
            self.assertFalse(app.state.last_sim_success)
            self.assertEqual(app.state.last_sim_policy_hash, "")

    def test_prepare_candidate_for_job_caches_usb_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            app = MujinaAssistApp(repo_root)
            source = repo_root / "sample.onnx"
            source.write_bytes(b"onnx")
            candidate = PolicyCandidate(
                label="USB: sample.onnx",
                path=source,
                source_type="usb",
                description="demo",
            )

            prepared = app._prepare_candidate_for_job(candidate)

            self.assertEqual(prepared.source_type, "cache")
            self.assertTrue(prepared.path.exists())
            self.assertEqual(prepared.path.read_bytes(), b"onnx")


if __name__ == "__main__":
    unittest.main()
