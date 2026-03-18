from __future__ import annotations

from pathlib import Path
import sys

from mujina_assist.app import run_app


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    return run_app(repo_root, sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
