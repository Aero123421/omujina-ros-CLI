from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mujina_assist.models import AppPaths
from mujina_assist.services.jobs import create_job, list_jobs, load_job, mark_job_running, mark_job_stopped, summarize_job, update_job


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


if __name__ == "__main__":
    unittest.main()
