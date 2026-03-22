from __future__ import annotations

import json
from dataclasses import asdict
from dataclasses import fields as dataclass_fields
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
    path.write_text(json.dumps(asdict(job), indent=2, ensure_ascii=False), encoding="utf-8")


def load_job(path: Path) -> JobRecord:
    data = json.loads(path.read_text(encoding="utf-8"))
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
    terminal_pid: int | None = None,
) -> JobRecord:
    current = job
    job_path = Path(job.job_file)
    if job_path.exists():
        try:
            current = load_job(job_path)
        except Exception:
            current = job

    if status is not None:
        current.status = status
    if terminal_mode is not None:
        current.terminal_mode = terminal_mode
    if started_at is not None:
        current.started_at = started_at
    if finished_at is not None:
        current.finished_at = finished_at
    if returncode is not None:
        current.returncode = returncode
    if message is not None:
        current.message = message
    if terminal_label is not None:
        current.terminal_label = terminal_label
    if terminal_pid is not None:
        current.terminal_pid = terminal_pid

    save_job(current)
    for field in dataclass_fields(JobRecord):
        setattr(job, field.name, getattr(current, field.name))
    return job


def mark_job_running(job: JobRecord, *, terminal_mode: str | None = None, terminal_label: str | None = None) -> JobRecord:
    return update_job(
        job,
        status="running",
        terminal_mode=terminal_mode if terminal_mode is not None else job.terminal_mode,
        terminal_label=terminal_label if terminal_label is not None else job.terminal_label,
        started_at=_timestamp(),
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
    if job.status == "queued":
        return f"{job.name}: 起動待ち"
    if job.status == "running":
        return f"{job.name}: 実行中"
    if job.status == "succeeded":
        return f"{job.name}: 成功"
    if job.status == "failed":
        return f"{job.name}: 失敗"
    if job.status == "stopped":
        return f"{job.name}: 停止"
    return f"{job.name}: {job.status}"
