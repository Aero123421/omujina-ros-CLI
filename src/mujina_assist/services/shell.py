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
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=capture,
        check=False,
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
    if interactive:
        piped = script
        if log_path:
            quoted = shlex.quote(str(log_path))
            piped = "\n".join(
                [
                    "set -o pipefail",
                    "(",
                    script,
                    f") 2>&1 | tee -a {quoted}",
                ]
            )
        completed = subprocess.run(
            ["bash", "-lc", piped],
            cwd=str(cwd) if cwd else None,
            text=True,
            check=False,
        )
        return CommandResult(command=script, returncode=completed.returncode)

    completed = subprocess.run(
        ["bash", "-lc", script],
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=False,
    )
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            if completed.stdout:
                handle.write(completed.stdout)
            if completed.stderr:
                handle.write(completed.stderr)
    return CommandResult(
        command=script,
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )
