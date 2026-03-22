from __future__ import annotations

import json
import os
import shutil
import socket
from dataclasses import asdict
from dataclasses import fields as dataclass_fields
from datetime import datetime
from pathlib import Path
import sys
from uuid import uuid4

from mujina_assist.models import AppPaths, JobRecord


def _now_for_filename() -> str:
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _job_claim_path(job_path: Path) -> Path:
    return job_path.with_suffix(f"{job_path.suffix}.claim")


def _corrupt_job_backup_path(job_path: Path) -> Path:
    base = job_path.with_name(f"{job_path.name}.corrupt.{_now_for_filename()}")
    for idx in range(0, 100):
        candidate = base if idx == 0 else base.with_name(f"{base.name}.{idx}")
        if not candidate.exists():
            return candidate
    return base.with_name(f"{base.name}.{_now_for_filename()}")


def _quarantine_corrupt_job(job_path: Path, reason: Exception) -> None:
    backup_path = _corrupt_job_backup_path(job_path)
    try:
        shutil.move(str(job_path), backup_path)
    except OSError as exc:
        print(f"[jobs] failed to back up corrupt job file: {job_path} ({exc})", file=sys.stderr)
        return
    print(
        f"[jobs] corrupted job file moved to {backup_path.name} for inspection: {reason}",
        file=sys.stderr,
    )


def _parse_job_timestamp(raw: str) -> datetime:
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(raw)
    except Exception:
        return datetime.min
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed


def _job_sort_key(job: JobRecord) -> tuple[datetime, str]:
    return (_parse_job_timestamp(job.created_at), job.job_id)


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
    _atomic_write_json(path, asdict(job))


def load_job(path: Path) -> JobRecord:
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        _quarantine_corrupt_job(path, exc)
        raise
    try:
        return _job_from_data(data)
    except Exception as exc:
        _quarantine_corrupt_job(path, exc)
        raise


def _job_from_data(data: object) -> JobRecord:
    if not isinstance(data, dict):
        raise TypeError("job payload must be a JSON object")

    required_str_fields = (
        "job_id",
        "kind",
        "name",
        "status",
        "log_path",
        "created_at",
        "job_file",
        "script_path",
    )
    optional_str_fields = (
        "terminal_mode",
        "group_id",
        "started_at",
        "finished_at",
        "message",
        "terminal_label",
    )

    for field_name in required_str_fields:
        if not isinstance(data.get(field_name), str):
            raise TypeError(f"job field {field_name} must be str")
    for field_name in optional_str_fields:
        value = data.get(field_name, "")
        if value is not None and not isinstance(value, str):
            raise TypeError(f"job field {field_name} must be str")

    payload = data.get("payload", {})
    if not isinstance(payload, dict):
        raise TypeError("job field payload must be dict")

    returncode = data.get("returncode")
    if returncode is not None and not isinstance(returncode, int):
        raise TypeError("job field returncode must be int or null")

    terminal_pid = data.get("terminal_pid")
    if terminal_pid is not None and not isinstance(terminal_pid, int):
        raise TypeError("job field terminal_pid must be int or null")

    return JobRecord(
        job_id=str(data["job_id"]),
        kind=str(data["kind"]),
        name=str(data["name"]),
        status=str(data["status"]),
        log_path=str(data["log_path"]),
        created_at=str(data["created_at"]),
        job_file=str(data["job_file"]),
        script_path=str(data["script_path"]),
        payload=payload,
        terminal_mode=str(data.get("terminal_mode", "")),
        group_id=str(data.get("group_id", "")),
        started_at=str(data.get("started_at", "")),
        finished_at=str(data.get("finished_at", "")),
        returncode=returncode,
        message=str(data.get("message", "")),
        terminal_label=str(data.get("terminal_label", "")),
        terminal_pid=terminal_pid,
    )


def job_log_path(job: JobRecord) -> Path:
    return Path(job.log_path)


def job_script_path(job: JobRecord) -> Path:
    return Path(job.script_path)


def list_jobs(paths: AppPaths) -> list[JobRecord]:
    jobs: list[JobRecord] = []
    for job_file in sorted(paths.jobs_dir.glob("*.json")):
        try:
            jobs.append(load_job(job_file))
        except Exception as exc:
            print(f"[jobs] invalid job file skipped: {job_file.name}: {exc}", file=sys.stderr)
            continue
    jobs.sort(key=_job_sort_key, reverse=True)
    return jobs


def recent_jobs(paths: AppPaths, limit: int = 5) -> list[JobRecord]:
    return list_jobs(paths)[:limit]


def active_jobs(paths: AppPaths) -> list[JobRecord]:
    return [job for job in list_jobs(paths) if job.status == "running"]


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


def acquire_job_claim(job: JobRecord, *, worker_id: str | None = None, ttl_seconds: int = 3600) -> str | None:
    claim_path = _job_claim_path(Path(job.job_file))
    claim_token = uuid4().hex
    now = _timestamp()
    owner = worker_id or f"{socket.gethostname()}:{os.getpid()}"

    for _ in range(5):
        existing_claim = _read_job_claim(claim_path)
        if existing_claim is not None and not _is_claim_stale(existing_claim, ttl_seconds):
            return None
        if existing_claim is not None:
            _release_job_claim(claim_path, expected_token=existing_claim.get("token", ""))

        claim_body = json.dumps(
            {
                "token": claim_token,
                "worker_id": owner,
                "pid": os.getpid(),
                "claimed_at": now,
            },
            ensure_ascii=False,
        )
        try:
            fd = os.open(claim_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(claim_body + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return claim_token
        except OSError as exc:
            _ = exc
            _release_job_claim(claim_path, expected_token=claim_token)
            raise

    return None


def release_job_claim(job: JobRecord, claim_token: str) -> bool:
    claim_path = _job_claim_path(Path(job.job_file))
    return _release_job_claim(claim_path, expected_token=claim_token)


def _read_job_claim(claim_path: Path) -> dict | None:
    if not claim_path.exists():
        return None
    try:
        with claim_path.open(encoding="utf-8") as handle:
            data = json.load(handle)
            if isinstance(data, dict):
                return data
    except Exception as exc:
        _quarantine_corrupt_job(claim_path, ValueError("invalid claim json"))
        print(f"[jobs] invalid claim metadata skipped: {claim_path.name}: {exc}", file=sys.stderr)
    return None


def _is_claim_stale(claim: dict, ttl_seconds: int) -> bool:
    if ttl_seconds < 0:
        return False
    claimed_at = claim.get("claimed_at", "")
    if not isinstance(claimed_at, str):
        return True
    try:
        claimed = datetime.fromisoformat(claimed_at)
    except Exception:
        return True
    if claimed.tzinfo is None:
        claimed = claimed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    elapsed = datetime.now().astimezone() - claimed
    return elapsed.total_seconds() > ttl_seconds


def _release_job_claim(claim_path: Path, expected_token: str) -> bool:
    if not claim_path.exists():
        return False
    if not expected_token:
        try:
            claim_path.unlink()
            return True
        except OSError:
            return False

    claim = _read_job_claim(claim_path)
    if claim is None or claim.get("token") != expected_token:
        return False
    try:
        claim_path.unlink()
    except OSError:
        return False
    return True


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
