from __future__ import annotations

import os
import shlex
import signal
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
    failure_reasons: list[str]
    pid: int | None = None


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
    content = f"""#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR={shlex.quote(str(paths.repo_root))}
JOB_FILE={shlex.quote(str(job.job_file))}
LOG_FILE={shlex.quote(str(job.log_path))}
JOB_NAME={shlex.quote(job.name)}

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
  printf '%s\\n' "[Mujina Assist] $JOB_NAME は終了コード $EXIT_CODE で停止しました。"
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
    failure_reasons: list[str] = []
    if has_graphical_session():
        for backend in terminal_backends():
            launched, reason = _launch_in_graphical_terminal(backend, script_path, job.name, paths.repo_root)
            if reason:
                failure_reasons.append(f"{backend}: {reason}")
            if launched is not None:
                return TerminalLaunch(
                    ok=True,
                    mode="terminal",
                    label=backend,
                    message=f"{backend} で {job.name} を起動しました。",
                    failure_reasons=[],
                    pid=launched.pid,
                )
    if command_exists("tmux"):
        session_name = _tmux_session_name(job)
        launched, reason = _launch_in_tmux(session_name, script_path, paths.repo_root)
        if reason:
            failure_reasons.append(f"tmux ({session_name}): {reason}")
        if launched:
            return TerminalLaunch(
                ok=True,
                mode="tmux",
                label=session_name,
                message=f"tmux セッション {session_name} で {job.name} を起動しました。",
                failure_reasons=[],
            )
    else:
        failure_reasons.append("tmux が見つかりません")
    if not has_graphical_session():
        failure_reasons.append("GUIセッションがありません（DISPLAY/WAYLAND_DISPLAY が未設定）")
    if not terminal_backends():
        failure_reasons.append("GUIターミナルが見つかりません")
    if not failure_reasons:
        failure_reasons.append("GUIターミナル/ tmux で起動できませんでした")
    return TerminalLaunch(
        ok=False,
        mode="",
        label="",
        message="ジョブを起動できませんでした。失敗原因: " + "; ".join(failure_reasons),
        failure_reasons=failure_reasons,
    )


def stop_job_launch(*, mode: str, label: str, pid: int | None = None) -> str | None:
    if mode == "tmux":
        if not label:
            return "tmux セッション名がありません"
        try:
            completed = subprocess.run(
                ["tmux", "kill-session", "-t", label],
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            return f"{exc.__class__.__name__}: {exc}"
        if completed.returncode == 0:
            return None
        detail = "\n".join(part for part in (completed.stderr.strip(), completed.stdout.strip()) if part)
        return detail or f"tmux kill-session が終了コード {completed.returncode} を返しました"
    if mode == "terminal":
        if pid is None:
            return "端末 PID がありません"
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            return f"{exc.__class__.__name__}: {exc}"
        return "端末プロセスへ SIGTERM は送信しましたが、停止確認はできていません"
    return f"未知の terminal mode です: {mode}"


def _launch_in_graphical_terminal(
    backend: str,
    script_path: Path,
    title: str,
    cwd: Path,
) -> tuple[subprocess.Popen[str] | None, str]:
    command = _backend_command(backend, script_path, title)
    if not command:
        return None, f"対応していないバックエンドです: {backend}"
    try:
        return subprocess.Popen(command, cwd=str(cwd), text=True), ""
    except OSError as exc:
        return None, f"{exc.__class__.__name__}: {exc}"
    except subprocess.SubprocessError as exc:
        return None, f"{exc.__class__.__name__}: {exc}"


def _backend_command(backend: str, script_path: Path, title: str) -> list[str]:
    script = str(script_path)
    if backend == "gnome-terminal":
        return [backend, "--title", title, "--", "bash", script]
    if backend == "mate-terminal":
        return [backend, "--title", title, "--", "bash", script]
    if backend == "konsole":
        return [backend, "--hold", "-p", f"tabtitle={title}", "-e", "bash", script]
    if backend == "xfce4-terminal":
        return [backend, "--title", title, "--command", f"bash -lc {shlex.quote(script)}"]
    if backend == "x-terminal-emulator":
        return [backend, "-e", "bash", script]
    return []


def _tmux_session_name(job: JobRecord) -> str:
    suffix = job.job_id[-24:]
    return f"ma-{suffix}"


def _launch_in_tmux(session_name: str, script_path: Path, cwd: Path) -> tuple[bool, str]:
    command = ["tmux", "new-session", "-d", "-s", session_name, "bash", str(script_path)]
    try:
        completed = subprocess.run(command, cwd=str(cwd), text=True, capture_output=True, check=False)
    except OSError as exc:
        return False, f"{exc.__class__.__name__}: {exc}"
    except subprocess.SubprocessError as exc:
        return False, f"{exc.__class__.__name__}: {exc}"
    if completed.returncode == 0:
        return True, ""
    detail = "\n".join(part for part in (completed.stderr.strip(), completed.stdout.strip()) if part)
    reason = detail if detail else f"終了コード {completed.returncode}"
    return False, reason
