from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mujina_assist.models import AppPaths, JobRecord
from mujina_assist.services.jobs import (
    active_jobs,
    acquire_job_claim,
    create_job,
    list_jobs,
    load_job,
    mark_job_running,
    mark_job_stopped,
    recent_jobs,
    release_job_claim,
    save_job,
    summarize_job,
    update_job,
)


class JobsTest(unittest.TestCase):
    def test_create_job_persists_job_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()

            job = create_job(paths, kind="build", name="workspace build", payload={"demo": True})

            self.assertTrue(Path(job.job_file).exists())
            self.assertEqual(list_jobs(paths)[0].job_id, job.job_id)
            self.assertIn("build-", job.job_id)

    def test_stopped_job_summary_is_human_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()

            job = create_job(paths, kind="viz", name="RViz 可視化")
            mark_job_stopped(job, message="Ctrl+C")

            self.assertEqual(summarize_job(job), "RViz 可視化: 停止")

    def test_mark_job_running_preserves_existing_terminal_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()

            job = create_job(paths, kind="build", name="workspace build")
            job.terminal_mode = "terminal"
            job.terminal_label = "gnome-terminal"
            mark_job_running(job)

            self.assertEqual(job.terminal_mode, "terminal")
            self.assertEqual(job.terminal_label, "gnome-terminal")

    def test_update_job_merges_with_latest_saved_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()

            job = create_job(paths, kind="build", name="workspace build")
            running = update_job(job, status="running", message="worker is running")

            stale = load_job(Path(running.job_file))
            stale.status = "queued"

            update_job(stale, terminal_mode="terminal", terminal_label="gnome-terminal")
            merged = list_jobs(paths)[0]

            self.assertEqual(merged.status, "running")
            self.assertEqual(merged.message, "worker is running")
            self.assertEqual(merged.terminal_mode, "terminal")
            self.assertEqual(merged.terminal_label, "gnome-terminal")

    def test_list_jobs_orders_by_created_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()

            older = JobRecord(
                job_id="zzzz-20260101-aaaa",
                kind="b",
                name="older",
                status="queued",
                log_path=str(paths.logs_dir / "zzzz-20260101-aaaa.log"),
                created_at="2026-01-01T12:00:00+09:00",
                job_file=str(paths.jobs_dir / "zzzz-20260101-aaaa.json"),
                script_path=str(paths.job_scripts_dir / "zzzz-20260101-aaaa.sh"),
                payload={"batch": "older"},
            )
            newer = JobRecord(
                job_id="aaaa-20260102-bbbb",
                kind="a",
                name="newer",
                status="queued",
                log_path=str(paths.logs_dir / "aaaa-20260102-bbbb.log"),
                created_at="2026-01-02T12:00:00+09:00",
                job_file=str(paths.jobs_dir / "aaaa-20260102-bbbb.json"),
                script_path=str(paths.job_scripts_dir / "aaaa-20260102-bbbb.sh"),
                payload={"batch": "newer"},
            )
            save_job(older)
            save_job(newer)

            jobs = recent_jobs(paths, limit=2)
            self.assertEqual(jobs[0].job_id, newer.job_id)
            self.assertEqual(jobs[1].job_id, older.job_id)

    def test_load_job_moves_corrupted_file_to_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()
            bad = paths.jobs_dir / "corrupt.json"
            bad.write_text("{", encoding="utf-8")

            with self.assertRaises(json.JSONDecodeError):
                load_job(bad)

            self.assertFalse(bad.exists())
            backups = list(paths.jobs_dir.glob("corrupt.json.corrupt.*"))
            self.assertEqual(len(backups), 1)

    def test_atomic_save_does_not_leave_tmp_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()

            job = create_job(paths, kind="build", name="workspace build")
            self.assertFalse((Path(job.job_file).with_suffix(f"{Path(job.job_file).suffix}.tmp")).exists())

    def test_job_claim_prevents_parallel_worker_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()

            job = create_job(paths, kind="build", name="workspace build")
            claim_1 = acquire_job_claim(job, ttl_seconds=300)
            claim_2 = acquire_job_claim(job, ttl_seconds=300)

            self.assertIsNotNone(claim_1)
            self.assertIsNone(claim_2)

            self.assertTrue(release_job_claim(job, claim_1 or ""))
            self.assertIsNotNone(acquire_job_claim(job, ttl_seconds=300))

    def test_job_claim_reclaims_stale_naive_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()

            job = create_job(paths, kind="build", name="workspace build")
            claim_path = Path(job.job_file).with_suffix(f"{Path(job.job_file).suffix}.claim")
            claim_path.write_text(
                json.dumps(
                    {
                        "token": "legacy-token",
                        "worker_id": "legacy-worker",
                        "host": "test-host",
                        "claimed_at": "2000-01-01T00:00:00",
                    }
                ),
                encoding="utf-8",
            )

            claim = acquire_job_claim(job, ttl_seconds=300)

            self.assertIsNotNone(claim)
            saved = json.loads(claim_path.read_text(encoding="utf-8"))
            self.assertNotEqual(saved["token"], "legacy-token")

    def test_load_job_moves_schema_corrupted_file_to_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()
            bad = paths.jobs_dir / "corrupt-schema.json"
            bad.write_text('{"job_id":"x"}', encoding="utf-8")

            with self.assertRaises(TypeError):
                load_job(bad)

            self.assertFalse(bad.exists())
            backups = list(paths.jobs_dir.glob("corrupt-schema.json.corrupt.*"))
            self.assertEqual(len(backups), 1)

    def test_active_jobs_only_returns_running_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.from_repo_root(Path(tmp))
            paths.ensure_directories()

            queued = create_job(paths, kind="build", name="queued build")
            running = create_job(paths, kind="sim_main", name="running sim")
            update_job(running, status="running", started_at="2026-03-22T00:00:00+09:00")

            jobs = active_jobs(paths)

            self.assertEqual([job.job_id for job in jobs], [running.job_id])


if __name__ == "__main__":
    unittest.main()
