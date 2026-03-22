#!/usr/bin/env bash
set -euo pipefail

# Container entrypoint for Dockerfile.test. Local direct execution is not supported.
ROOT_DIR="/app"
export PYTHONPATH="$ROOT_DIR/src"

cd "$ROOT_DIR"

python3 -m unittest discover -s tests -t . -q
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

set +e
python3 -m mujina_assist.main worker --job-file "$JOB_FILE"
WORKER_EXIT=$?
set -e

case "$WORKER_EXIT" in
  0|1|130)
    ;;
  *)
    echo "unexpected worker exit code: $WORKER_EXIT" >&2
    exit "$WORKER_EXIT"
    ;;
esac

JOB_FILE_ENV="$JOB_FILE" WORKER_EXIT_ENV="$WORKER_EXIT" python3 - <<'PY'
import json
import os

job_file = os.environ["JOB_FILE_ENV"]
with open(job_file, encoding="utf-8") as handle:
    data = json.load(handle)

assert data["status"] != "queued", data
assert data["status"] in {"failed", "succeeded", "stopped"}, data
expected_exit = {
    "succeeded": 0,
    "failed": 1,
    "stopped": 130,
}[data["status"]]
assert int(os.environ["WORKER_EXIT_ENV"]) == expected_exit, (os.environ["WORKER_EXIT_ENV"], data)
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
