#!/bin/zsh
# Builds dist/Mocap.app: a self-contained Mac app (Python, MediaPipe, SDL and the pose model are
# all inside). Add --install to copy it to /Applications (or ~/Applications if that's not writable),
# or --zip to make a release download (dist/Mocap-<version>-macos-<arch>.zip).
set -e
cd "$(dirname "$0")/.."

if [[ ! -x .venv/bin/python ]]; then
  ./run.sh --help > /dev/null          # first run: sets up .venv and downloads the pose model
fi
MODEL=models/pose_landmarker_full.task
if [[ ! -f $MODEL ]]; then
  mkdir -p models
  curl -sSfL -o $MODEL https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task
fi
.venv/bin/pip install -q pyinstaller pillow
.venv/bin/python packaging/make_icon.py
.venv/bin/pyinstaller --noconfirm --clean --log-level WARN --distpath dist --workpath build/pyinstaller packaging/Mocap.spec
# Ad-hoc signature: Apple Silicon needs one, and it lets macOS remember the Camera and
# Accessibility permissions you grant this build.
codesign --force --deep --sign - dist/Mocap.app
echo "Built dist/Mocap.app ($(du -sh dist/Mocap.app | cut -f1))"

if [[ "$1" == "--zip" ]]; then
  VERSION=$(.venv/bin/python -c "import mocap; print(mocap.__version__)")
  ZIP="dist/Mocap-$VERSION-macos-$(uname -m).zip"
  rm -f "$ZIP"
  ditto -c -k --keepParent dist/Mocap.app "$ZIP"
  echo "Release zip: $ZIP ($(du -sh "$ZIP" | cut -f1))"
fi

if [[ "$1" == "--install" ]]; then
  DEST=/Applications
  [[ -w $DEST ]] || DEST=~/Applications
  mkdir -p $DEST
  rm -rf "$DEST/Mocap.app"
  ditto dist/Mocap.app "$DEST/Mocap.app"
  echo "Installed $DEST/Mocap.app"
fi
