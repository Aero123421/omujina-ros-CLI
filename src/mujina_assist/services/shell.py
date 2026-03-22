from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class CommandResult:
    command: str
    returncode: int
    stdout: str = ""
    stderr: str = ""


def shell_quote(value: str | Path) -> str:
    return shlex.quote(str(value))


def run_plain(
    command: list[str],
    cwd: Path | None = None,
    capture: bool = True,
) -> CommandResult:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=capture,
            check=False,
        )
    except OSError as exc:
        return CommandResult(
            command=" ".join(command),
            returncode=1,
            stderr=f"{exc.__class__.__name__}: {exc}",
        )
    return CommandResult(
        command=" ".join(command),
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def run_bash(
    script: str,
    cwd: Path | None = None,
    log_path: Path | None = None,
    interactive: bool = False,
) -> CommandResult:
    normalized_log_path = None
    if log_path:
        normalized_log_path = Path(log_path)
        try:
            normalized_log_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return CommandResult(
                command=script,
                returncode=1,
                stderr=f"ログファイル先の準備に失敗しました: {exc}",
            )
    if interactive:
        piped = script
        if normalized_log_path:
            quoted = shlex.quote(str(normalized_log_path))
            piped = "\n".join(
                [
                    "set -o pipefail",
                    "(",
                    script,
                    f") 2>&1 | tee -a {quoted}",
                ]
            )
        try:
            completed = subprocess.run(
                ["bash", "-lc", piped],
                cwd=str(cwd) if cwd else None,
                text=True,
                check=False,
            )
        except OSError as exc:
            return CommandResult(
                command=script,
                returncode=1,
                stderr=f"{exc.__class__.__name__}: {exc}",
            )
        return CommandResult(command=script, returncode=completed.returncode)

    try:
        completed = subprocess.run(
            ["bash", "-lc", script],
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return CommandResult(
            command=script,
            returncode=1,
            stderr=f"{exc.__class__.__name__}: {exc}",
        )
    if normalized_log_path:
        try:
            with normalized_log_path.open("a", encoding="utf-8") as handle:
                if completed.stdout:
                    handle.write(completed.stdout)
                if completed.stderr:
                    handle.write(completed.stderr)
        except OSError as exc:
            completed_stderr = completed.stderr or ""
            completed_stderr += f"\nログファイルへの書き込みに失敗しました: {exc}"
            return CommandResult(
                command=script,
                returncode=max(completed.returncode, 1),
                stdout=completed.stdout or "",
                stderr=completed_stderr,
            )
    return CommandResult(
        command=script,
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )
