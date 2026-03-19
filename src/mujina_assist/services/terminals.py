from __future__ import annotations

import os
import shlex
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from mujina_assist.models import AppPaths, JobRecord
from mujina_assist.services.checks import command_exists
from mujina_assist.services.jobs import job_script_path


@dataclass(slots=True)
class TerminalLaunch:
    ok: bool
    mode: str
    label: str
    message: str


def has_graphical_session() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def terminal_backends() -> list[str]:
    backends: list[str] = []
    for name in ("gnome-terminal", "mate-terminal", "konsole", "xfce4-terminal", "x-terminal-emulator"):
        if command_exists(name):
            backends.append(name)
    return backends


def write_worker_script(paths: AppPaths, job: JobRecord) -> Path:
    script_path = job_script_path(job)
    job_file = Path(job.job_file)
    log_path = Path(job.log_path)
    job_name = shlex.quote(job.name)
    content = f"""#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR={shlex.quote(str(paths.repo_root))}
JOB_FILE={shlex.quote(str(job_file))}
LOG_FILE={shlex.quote(str(log_path))}
JOB_NAME={job_name}

cd "$ROOT_DIR"
printf '%s\\n' "[Mujina Assist] $JOB_NAME を開始します。"
printf '%s\\n' "ログ: $LOG_FILE"
printf '\\n'

bash ./start.sh worker --job-file "$JOB_FILE"
EXIT_CODE=$?

printf '\\n'
if [[ "$EXIT_CODE" -eq 0 ]]; then
  printf '%s\\n' "[Mujina Assist] $JOB_NAME は完了しました。"
else
  printf '%s\\n' "[Mujina Assist] $JOB_NAME は失敗しました。"
fi
printf '%s\\n' "ログ: $LOG_FILE"
printf '%s\\n' "このターミナルは確認用に開いたままです。"
exec bash
"""
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(content, encoding="utf-8")
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script_path


def launch_job(paths: AppPaths, job: JobRecord) -> TerminalLaunch:
    script_path = write_worker_script(paths, job)
    if has_graphical_session():
        for backend in terminal_backends():
            if _launch_in_graphical_terminal(backend, script_path, job.name, paths.repo_root):
                return TerminalLaunch(
                    ok=True,
                    mode="terminal",
                    label=backend,
                    message=f"{backend} で {job.name} を起動しました。",
                )
    if command_exists("tmux"):
        session_name = _tmux_session_name(job)
        if _launch_in_tmux(session_name, script_path, paths.repo_root):
            return TerminalLaunch(
                ok=True,
                mode="tmux",
                label=session_name,
                message=f"GUI ターミナルが無いため tmux セッション {session_name} で {job.name} を起動しました。",
            )
    return TerminalLaunch(
        ok=False,
        mode="",
        label="",
        message="利用可能な GUI ターミナルも tmux も見つかりませんでした。",
    )


def _launch_in_graphical_terminal(backend: str, script_path: Path, title: str, cwd: Path) -> bool:
    command = _backend_command(backend, script_path, title)
    if not command:
        return False
    try:
        subprocess.Popen(command, cwd=str(cwd))
    except Exception:
        return False
    return True


def _backend_command(backend: str, script_path: Path, title: str) -> list[str]:
    script = str(script_path)
    if backend == "gnome-terminal":
        return [backend, "--title", title, "--", "bash", script]
    if backend == "mate-terminal":
        return [backend, "--title", title, "--", "bash", script]
    if backend == "konsole":
        return [backend, "--hold", "-p", f'tabtitle={title}', "-e", "bash", script]
    if backend == "xfce4-terminal":
        return [backend, "--title", title, "--command", f"bash {shlex.quote(script)}"]
    if backend == "x-terminal-emulator":
        return [backend, "-e", "bash", script]
    return []


def _tmux_session_name(job: JobRecord) -> str:
    suffix = job.job_id[-24:]
    return f"ma-{suffix}"


def _launch_in_tmux(session_name: str, script_path: Path, cwd: Path) -> bool:
    command = [
        "tmux",
        "new-session",
        "-d",
        "-s",
        session_name,
        "bash",
        str(script_path),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            check=False,
        )
    except Exception:
        return False
    return completed.returncode == 0
