# Architecture

## Overview

Mujina Assist is a Python based interactive CLI with a thin `start.sh` launcher.

The repository does not vendor `mujina_ros`. Instead, the CLI clones upstream into:

- `workspace/src/mujina_ros`

The app keeps its own small runtime state in:

- `.state/`
- `cache/`
- `logs/`

## Why fixed workspace

`mujina_ros` currently has a relative path dependency for MuJoCo simulation, so the CLI enforces a fixed workspace and runs simulation from the workspace root.

## Runtime model

- `start.sh`: Linux-only launcher
- `src/mujina_assist/main.py`: entrypoint
- `src/mujina_assist/app.py`: interactive menu and command dispatch
- `services/`: shell, state, workspace, policy, process helpers

## Safety model

- `sim` and `real` are separated
- `real`, `motor zero`, and similar actions use explicit confirmation
- `policy` changes run rebuild + ONNX validation before being marked active
