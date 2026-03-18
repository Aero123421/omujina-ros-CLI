#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOOTSTRAP_LOG="$ROOT_DIR/logs/bootstrap.log"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "このツールは Ubuntu 24.04 上での利用を想定しています。"
  echo "VirtualBox 内の Ubuntu から ./start.sh を実行してください。"
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 が見つかりません。Ubuntu 側で python3 を導入してください。"
  exit 1
fi

mkdir -p "$ROOT_DIR/.state" "$ROOT_DIR/cache" "$ROOT_DIR/logs" "$ROOT_DIR/workspace"
export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
VENV_DIR="$ROOT_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
RUNNER=(python3)

if [[ -x "$VENV_PYTHON" ]]; then
  RUNNER=("$VENV_PYTHON")
else
  echo "初回起動の準備をしています。"
  if python3 -m venv "$VENV_DIR" >>"$BOOTSTRAP_LOG" 2>&1; then
    if ! "$VENV_PYTHON" -m pip install --upgrade pip >>"$BOOTSTRAP_LOG" 2>&1; then
      echo "pip の更新に失敗しました。"
      echo "詳細ログ: $BOOTSTRAP_LOG"
      exit 1
    fi
    if ! "$VENV_PYTHON" -m pip install -e "$ROOT_DIR" >>"$BOOTSTRAP_LOG" 2>&1; then
      echo "CLI 本体のインストールに失敗しました。"
      echo "詳細ログ: $BOOTSTRAP_LOG"
      exit 1
    fi
    RUNNER=("$VENV_PYTHON")
  else
    echo "注意: python3-venv が無いため system Python で起動します。"
    echo "Ubuntu では後で 'sudo apt install -y python3-venv' を入れるのがおすすめです。"
    echo "詳細ログ: $BOOTSTRAP_LOG"
  fi
fi

if [[ -f /etc/os-release ]]; then
  # shellcheck disable=SC1091
  source /etc/os-release
  if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "24.04" ]]; then
    echo "警告: このツールは Ubuntu 24.04 を前提に設計しています。"
    echo "現在の環境: ${PRETTY_NAME:-unknown}"
    echo "動作確認は Ubuntu 24.04 で行ってください。"
  fi
fi

exec "${RUNNER[@]}" -m mujina_assist.main menu "$@"
