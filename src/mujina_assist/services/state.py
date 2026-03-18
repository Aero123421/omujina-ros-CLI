from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from mujina_assist.models import RuntimeState


def load_runtime_state(path: Path) -> RuntimeState:
    if not path.exists():
        return RuntimeState()
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    state = RuntimeState()
    for key, value in data.items():
        if hasattr(state, key):
            setattr(state, key, value)
    return state


def save_runtime_state(path: Path, state: RuntimeState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(asdict(state), handle, indent=2, ensure_ascii=False)
