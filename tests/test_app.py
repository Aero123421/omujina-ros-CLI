from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mujina_assist.app import MujinaAssistApp
from mujina_assist.models import DoctorReport, PolicyCandidate
from mujina_assist.services.jobs import create_job, list_jobs, update_job


class AppTest(unittest.TestCase):
    def _prepare_built_workspace(self, app: MujinaAssistApp) -> None:
        app.paths.upstream_dir.mkdir(parents=True, exist_ok=True)
        (app.paths.upstream_dir / ".git").mkdir(parents=True, exist_ok=True)
        (app.paths.upstream_dir / "mujina_control" / "models").mkdir(parents=True, exist_ok=True)
        (app.paths.upstream_dir / "mujina_description").mkdir(parents=True, exist_ok=True)
        (app.paths.upstream_dir / "mujina_control" / "models" / "policy.onnx").write_bytes(b"policy")
        install_dir = app.paths.workspace_dir / "install" / "mujina_control"
        install_dir.mkdir(parents=True, exist_ok=True)
        (app.paths.workspace_dir / "install" / "setup.bash").write_text("", encoding="utf-8")

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
                missing = app._missing_devices_for_can_mode("serial", include_imu=True, include_joy=False)

            self.assertEqual(missing, [])

    def test_missing_devices_accepts_generic_imu_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            generic_imu = app.paths.repo_root / "ttyACM0"
            generic_imu.write_text("", encoding="utf-8")
            with patch(
                "mujina_assist.app.detect_real_devices",
                return_value={
                    "/dev/rt_usb_imu": False,
                    "/dev/usb_can": True,
                    "/dev/input/js0": True,
                    "can0": False,
                },
            ), patch(
                "mujina_assist.app.resolve_imu_port",
                return_value=(str(generic_imu), True, [str(generic_imu)]),
            ):
                missing = app._missing_devices_for_can_mode("serial", include_imu=True, include_joy=True)

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

    def test_confirm_no_conflicting_jobs_asks_but_defaults_to_continue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            job = create_job(app.paths, kind="build", name="workspace build")
            update_job(job, status="running")
            with patch("mujina_assist.app.ask_yes_no", return_value=True):
                self.assertTrue(app._confirm_no_conflicting_jobs({"build"}))

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
            self._prepare_built_workspace(app)

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

    def test_execute_zero_job_stops_when_probe_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            self._prepare_built_workspace(app)
            job = create_job(app.paths, kind="zero", name="zero (1 2)", payload={"ids": [1, 2], "can_mode": "net"})

            with patch(
                "mujina_assist.app.run_bash",
                return_value=SimpleNamespace(returncode=2),
            ) as run_bash_mock, patch.object(app, "_execute_shell_job") as execute_shell_job_mock:
                result = app._execute_zero_job(job)

            self.assertEqual(result[0], 2)
            self.assertIn("前提確認", result[1])
            self.assertFalse(result[2])
            execute_shell_job_mock.assert_not_called()
            self.assertIn("Motor probe completed.", run_bash_mock.call_args.args[0])
            self.assertEqual(run_bash_mock.call_args.kwargs["log_path"], Path(job.log_path).with_suffix(".preflight.log"))

    def test_execute_zero_job_runs_probe_then_zero_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            self._prepare_built_workspace(app)
            job = create_job(app.paths, kind="zero", name="zero (3)", payload={"ids": [3], "can_mode": "serial"})

            with patch(
                "mujina_assist.app.run_bash",
                return_value=SimpleNamespace(returncode=0),
            ) as run_bash_mock, patch.object(
                app,
                "_execute_shell_job",
                return_value=(0, "ok", False),
            ) as execute_shell_job_mock:
                result = app._execute_zero_job(job)

            self.assertEqual(result, (0, "ok", False))
            self.assertIn("Motor probe completed.", run_bash_mock.call_args.args[0])
            self.assertIn("can_setup_serial.sh", run_bash_mock.call_args.args[0])
            self.assertEqual(run_bash_mock.call_args.kwargs["log_path"], Path(job.log_path).with_suffix(".preflight.log"))
            self.assertIn("motor_set_zero_position.py", execute_shell_job_mock.call_args.args[1])
            self.assertIn("--device can0", execute_shell_job_mock.call_args.args[1])
            self.assertNotIn("can_setup_serial.sh", execute_shell_job_mock.call_args.args[1])

    def test_execute_zero_job_treats_preflight_interrupt_as_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            self._prepare_built_workspace(app)
            job = create_job(app.paths, kind="zero", name="zero (5)", payload={"ids": [5], "can_mode": "net"})

            with patch(
                "mujina_assist.app.run_bash",
                return_value=SimpleNamespace(returncode=130),
            ) as run_bash_mock, patch.object(app, "_execute_shell_job") as execute_shell_job_mock:
                result = app._execute_zero_job(job)

            self.assertEqual(result, (130, "原点位置設定の前提確認を中断しました。", True))
            execute_shell_job_mock.assert_not_called()
            self.assertIn("Motor probe completed.", run_bash_mock.call_args.args[0])

    def test_mark_sim_verified_records_current_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            self._prepare_built_workspace(app)

            with patch("mujina_assist.app.ask_yes_no", return_value=True):
                result = app.handle_mark_sim_verified()

            self.assertEqual(result, 0)
            self.assertTrue(app.state.last_sim_success)
            self.assertEqual(app.state.last_sim_policy_hash, app.state.active_policy_hash)

    def test_mark_sim_verified_preserves_custom_policy_when_default_cache_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            self._prepare_built_workspace(app)
            app.paths.default_policy_cache.write_bytes(b"default-policy")
            app.paths.source_policy_path.write_bytes(b"custom-policy")
            app.state.active_policy_label = "USB: custom.onnx"
            app.state.active_policy_source = str(app.paths.imported_policy_dir / "custom.onnx")

            with patch("mujina_assist.app.ask_yes_no", return_value=True):
                result = app.handle_mark_sim_verified()

            self.assertEqual(result, 0)
            self.assertEqual(app.state.active_policy_label, "USB: custom.onnx")
            self.assertEqual(app.state.active_policy_source, str(app.paths.imported_policy_dir / "custom.onnx"))
            self.assertTrue(app.state.last_sim_success)

    def test_diagnostic_can_mode_prompts_when_only_generic_serial_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            with patch(
                "mujina_assist.app.detect_real_devices",
                return_value={
                    "/dev/rt_usb_imu": False,
                    "/dev/usb_can": False,
                    "/dev/input/js0": False,
                    "can0": False,
                },
            ), patch(
                "mujina_assist.app.list_serial_device_candidates",
                return_value=["/dev/ttyUSB0"],
            ), patch("mujina_assist.app.select_from_list", return_value=1):
                self.assertEqual(app._diagnostic_can_mode("auto"), "serial")

    def test_handle_real_robot_requires_slcand_for_serial_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            self._prepare_built_workspace(app)
            app.state.active_policy_hash = "abc"
            app.state.last_sim_success = True
            app.state.last_sim_policy_hash = "abc"

            report = DoctorReport(
                os_label="Ubuntu 24.04",
                ubuntu_24_04=True,
                ros_installed=True,
                workspace_cloned=True,
                workspace_built=True,
                active_policy_label="公式デフォルト",
                usb_policy_count=0,
                tool_status={"slcand": False},
            )
            with patch.object(app, "_select_can_mode", return_value="serial"), patch(
                "mujina_assist.app.build_doctor_report",
                return_value=report,
            ):
                result = app.handle_real_robot()

            self.assertEqual(result, 1)

    def test_handle_real_robot_uses_generic_imu_fallback_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            self._prepare_built_workspace(app)
            app.state.active_policy_hash = "abc"
            app.state.last_sim_success = True
            app.state.last_sim_policy_hash = "abc"
            generic_imu = app.paths.repo_root / "ttyACM0"
            generic_imu.write_text("", encoding="utf-8")

            report = DoctorReport(
                os_label="Ubuntu 24.04",
                ubuntu_24_04=True,
                ros_installed=True,
                workspace_cloned=True,
                workspace_built=True,
                active_policy_label="公式デフォルト",
                usb_policy_count=0,
                tool_status={"slcand": True},
            )
            launched_jobs = []
            with patch.object(app, "_select_can_mode", return_value="serial"), patch(
                "mujina_assist.app.build_doctor_report",
                return_value=report,
            ), patch(
                "mujina_assist.app.ask_yes_no",
                side_effect=[True, True],
            ), patch(
                "mujina_assist.app.ask_text",
                return_value="REAL",
            ), patch(
                "mujina_assist.app.detect_real_devices",
                return_value={
                    "/dev/rt_usb_imu": False,
                    "/dev/usb_can": True,
                    "/dev/input/js0": True,
                    "can0": False,
                },
            ), patch(
                "mujina_assist.app.resolve_imu_port",
                return_value=(str(generic_imu), True, [str(generic_imu)]),
            ), patch.object(
                app,
                "_launch_job_group",
                side_effect=lambda jobs, heading: launched_jobs.extend(jobs) or 0,
            ):
                result = app.handle_real_robot()

            self.assertEqual(result, 0)
            imu_jobs = [job for job in launched_jobs if job.kind == "real_imu"]
            self.assertEqual(len(imu_jobs), 1)
            self.assertEqual(imu_jobs[0].payload["imu_port"], str(generic_imu))

    def test_prepare_candidate_for_job_caches_usb_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            app = MujinaAssistApp(repo_root)
            source = repo_root / "sample.onnx"
            source.write_bytes(b"onnx")
            candidate = PolicyCandidate(label="USB: sample.onnx", path=source, source_type="usb", description="demo")

            prepared = app._prepare_candidate_for_job(candidate)

            self.assertEqual(prepared.source_type, "cache")
            self.assertTrue(prepared.path.exists())
            self.assertEqual(prepared.path.read_bytes(), b"onnx")

    def test_prepare_candidate_for_job_runs_cache_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            app = MujinaAssistApp(repo_root)
            source = repo_root / "sample.onnx"
            source.write_bytes(b"onnx")
            candidate = PolicyCandidate(label="USB: sample.onnx", path=source, source_type="usb", description="demo")

            with patch("mujina_assist.app.cleanup_policy_cache") as cleanup_mock:
                prepared = app._prepare_candidate_for_job(candidate)

            self.assertEqual(prepared.source_type, "cache")
            cleanup_mock.assert_called_once()

    def test_handle_zero_position_requires_targeted_confirmation_phrase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            self._prepare_built_workspace(app)

            with patch.object(app, "_select_can_mode", return_value="net"), patch(
                "mujina_assist.app.detect_real_devices",
                return_value={
                    "/dev/rt_usb_imu": False,
                    "/dev/usb_can": False,
                    "/dev/input/js0": False,
                    "can0": True,
                },
            ), patch("mujina_assist.app.ask_text", return_value="ZERO"), patch.object(
                app,
                "_launch_job",
                return_value=0,
            ) as launch_job:
                result = app.handle_zero_position(ids=[1], can_mode="net")

            self.assertEqual(result, 1)
            launch_job.assert_not_called()
            self.assertEqual(list_jobs(app.paths), [])

    def test_handle_zero_position_bails_out_before_confirmation_when_can_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            self._prepare_built_workspace(app)

            with patch.object(app, "_select_can_mode", return_value="net"), patch(
                "mujina_assist.app.detect_real_devices",
                return_value={
                    "/dev/rt_usb_imu": False,
                    "/dev/usb_can": False,
                    "/dev/input/js0": False,
                    "can0": False,
                },
            ), patch("mujina_assist.app.ask_text") as ask_text_mock, patch.object(
                app,
                "_launch_job",
            ) as launch_job_mock:
                result = app.handle_zero_position(ids=[1], can_mode="net")

            self.assertEqual(result, 1)
            ask_text_mock.assert_not_called()
            launch_job_mock.assert_not_called()

    def test_run_worker_skips_already_running_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            job = create_job(app.paths, kind="build", name="workspace build")
            update_job(job, status="running")

            with patch.object(app, "_execute_build_job") as execute_mock:
                result = app.run_worker(Path(job.job_file))

            self.assertEqual(result, 0)
            execute_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
