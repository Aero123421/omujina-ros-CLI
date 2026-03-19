from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from mujina_assist.models import AppPaths, JobRecord


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def create_job(
    paths: AppPaths,
    *,
    kind: str,
    name: str,
    payload: dict | None = None,
    group_id: str = "",
) -> JobRecord:
    slug = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
    job_id = f"{kind}-{slug}"
    log_path = paths.logs_dir / f"{job_id}.log"
    job = JobRecord(
        job_id=job_id,
        kind=kind,
        name=name,
        status="queued",
        log_path=str(log_path),
        created_at=_timestamp(),
        job_file=str(paths.jobs_dir / f"{job_id}.json"),
        script_path=str(paths.job_scripts_dir / f"{job_id}.sh"),
        payload=payload or {},
        group_id=group_id,
    )
    save_job(job)
    return job


def save_job(job: JobRecord) -> None:
    path = Path(job.job_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(asdict(job), handle, indent=2, ensure_ascii=False)


def load_job(path: Path) -> JobRecord:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return JobRecord(**data)


def job_log_path(job: JobRecord) -> Path:
    return Path(job.log_path)


def job_script_path(job: JobRecord) -> Path:
    return Path(job.script_path)


def list_jobs(paths: AppPaths) -> list[JobRecord]:
    jobs: list[JobRecord] = []
    for job_file in sorted(paths.jobs_dir.glob("*.json"), reverse=True):
        try:
            jobs.append(load_job(job_file))
        except Exception:
            continue
    return jobs


def recent_jobs(paths: AppPaths, limit: int = 5) -> list[JobRecord]:
    return list_jobs(paths)[:limit]


def active_jobs(paths: AppPaths) -> list[JobRecord]:
    return [job for job in list_jobs(paths) if job.status in {"queued", "running"}]


def update_job(
    job: JobRecord,
    *,
    status: str | None = None,
    terminal_mode: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    returncode: int | None = None,
    message: str | None = None,
    terminal_label: str | None = None,
) -> JobRecord:
    if status is not None:
        job.status = status
    if terminal_mode is not None:
        job.terminal_mode = terminal_mode
    if started_at is not None:
        job.started_at = started_at
    if finished_at is not None:
        job.finished_at = finished_at
    if returncode is not None:
        job.returncode = returncode
    if message is not None:
        job.message = message
    if terminal_label is not None:
        job.terminal_label = terminal_label
    save_job(job)
    return job


def mark_job_running(job: JobRecord, *, terminal_mode: str | None = None, terminal_label: str | None = None) -> JobRecord:
    return update_job(
        job,
        status="running",
        terminal_mode=terminal_mode,
        started_at=_timestamp(),
        terminal_label=terminal_label,
    )


def mark_job_finished(job: JobRecord, *, returncode: int, message: str = "") -> JobRecord:
    status = "succeeded" if returncode == 0 else "failed"
    return _finish_job(job, status=status, returncode=returncode, message=message)


def mark_job_stopped(job: JobRecord, *, returncode: int = 130, message: str = "") -> JobRecord:
    return _finish_job(job, status="stopped", returncode=returncode, message=message)


def _finish_job(job: JobRecord, *, status: str, returncode: int, message: str) -> JobRecord:
    return update_job(
        job,
        status=status,
        finished_at=_timestamp(),
        returncode=returncode,
        message=message,
    )


def summarize_job(job: JobRecord) -> str:
    label = job.name
    if job.status == "queued":
        return f"{label}: 起動待ち"
    if job.status == "running":
        return f"{label}: 実行中"
    if job.status == "succeeded":
        return f"{label}: 完了"
    if job.status == "failed":
        return f"{label}: 失敗"
    if job.status == "stopped":
        return f"{label}: 停止"
    return f"{label}: {job.status}"
