#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/app"
export PYTHONPATH="$ROOT_DIR/src"

cd "$ROOT_DIR"

python3 -m unittest discover -s tests -q
bash ./start.sh doctor >/tmp/mujina-start-doctor.txt

JOB_FILE="$(
python3 - <<'PY'
from pathlib import Path
from mujina_assist.app import MujinaAssistApp
from mujina_assist.services.jobs import create_job

app = MujinaAssistApp(Path("/app"))
job = create_job(app.paths, kind="policy_test", name="docker worker smoke")
print(job.job_file)
PY
)"

python3 -m mujina_assist.main worker --job-file "$JOB_FILE" || true

JOB_FILE_ENV="$JOB_FILE" python3 - <<'PY'
import json
import os

job_file = os.environ["JOB_FILE_ENV"]
with open(job_file, encoding="utf-8") as handle:
    data = json.load(handle)

assert data["status"] != "queued", data
assert data["status"] in {"failed", "succeeded", "stopped"}, data
PY

python3 - <<'PY'
from pathlib import Path

from mujina_assist.app import MujinaAssistApp
from mujina_assist.services.jobs import create_job
from mujina_assist.services.terminals import launch_job

app = MujinaAssistApp(Path("/app"))
job = create_job(app.paths, kind="policy_test", name="docker tmux smoke")
launch = launch_job(app.paths, job)
assert launch.ok, launch
assert launch.mode == "tmux", launch
PY

tmux kill-server >/dev/null 2>&1 || true
