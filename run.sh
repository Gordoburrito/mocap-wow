#!/bin/zsh
# Sets up the Python environment on first run, downloads the pose model, then starts the controller.
set -e
cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
  PY=""
  for candidate in ~/.pyenv/versions/3.12*/bin/python3 python3.12 python3.11; do
    if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
  done
  if [[ -z "$PY" ]]; then
    echo "Needs Python 3.11 or 3.12 (MediaPipe doesn't support newer versions yet)." >&2
    exit 1
  fi
  "$PY" -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
fi

MODEL=models/pose_landmarker_full.task
if [[ ! -f $MODEL ]]; then
  mkdir -p models
  echo "Downloading pose model…"
  curl -sSfL -o $MODEL https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task
fi

exec .venv/bin/python -m mocap "$@"
