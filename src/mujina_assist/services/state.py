from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict
from dataclasses import fields as dataclass_fields
from pathlib import Path
from datetime import datetime

from mujina_assist.models import RuntimeState


def _now_for_filename() -> str:
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")


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


def _corrupt_backup_path(state_path: Path) -> Path:
    base = state_path.with_name(f"{state_path.name}.corrupt.{_now_for_filename()}")
    for idx in range(0, 100):
        candidate = base if idx == 0 else base.with_name(f"{base.name}.{idx}")
        if not candidate.exists():
            return candidate
    return base.with_name(f"{base.name}.{_now_for_filename()}")


def _quarantine_corrupt_state(state_path: Path, reason: Exception) -> None:
    backup_path = _corrupt_backup_path(state_path)
    try:
        shutil.move(str(state_path), backup_path)
    except OSError as exc:
        print(
            f"[state] failed to back up corrupt runtime state: {state_path} ({exc})",
            file=sys.stderr,
        )
        return
    print(
        f"[state] corrupted runtime state moved to {backup_path.name} for inspection: {reason}",
        file=sys.stderr,
    )


def load_runtime_state(path: Path) -> RuntimeState:
    if not path.exists():
        return RuntimeState()
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        _quarantine_corrupt_state(path, exc)
        return RuntimeState()
    if not isinstance(data, dict):
        _quarantine_corrupt_state(path, ValueError("runtime state is not a JSON object"))
        return RuntimeState()

    state = RuntimeState()
    expected_types = {field.name: type(getattr(state, field.name)) for field in dataclass_fields(RuntimeState)}
    try:
        for key, value in data.items():
            if key not in expected_types:
                continue
            expected = expected_types[key]
            if expected is bool and not isinstance(value, bool):
                raise TypeError(f"runtime state field {key} must be bool")
            if expected is str and not isinstance(value, str):
                raise TypeError(f"runtime state field {key} must be str")
            setattr(state, key, value)
    except TypeError as exc:
        _quarantine_corrupt_state(path, exc)
        return RuntimeState()
    return state


def save_runtime_state(path: Path, state: RuntimeState) -> None:
    _atomic_write_json(path, asdict(state))
