# Contributing

Thanks for helping. Mocap is a Mac app (Python + a web UI in a native window) that turns
webcam poses and Joy-Con weapon motions into key presses.

## Set up

```
./run.sh --sim --sim-camera --dry-run     # first run creates .venv and downloads the pose model
.venv/bin/python -m pytest                # the test suite (no hardware needed)
```

`--sim` gives you two simulated Joy-Cons, and `--sim-camera` a simulated webcam that sees
the simulated body holding them. Drive them with `POST /api/sim`, e.g.
`{"role": "melee", "gesture": "slash_left", "offset": [0, 0, 0.4]}`. Simulated sessions use a
throwaway data folder, so they never touch anyone's real recordings.

## Ground rules

- **One gesture = one key press.** No macros, sequences, repeats or reading game state. This
  keeps the project on the right side of game rules and is the whole design.
- **Never touch a player's data in tests.** Tests and labs write only to temporary folders.
- **Back claims with data.** Recognition changes should come with a lab run (see `lab/`) or a
  test, and `docs/verification.md` should say what changed.
- Match the surrounding code: small modules, plain functions, comments that say why.

## Where things are

| Path | What |
|---|---|
| `mocap/engine.py` | the hub: inputs in, one key out; capture; calibration wizard; accuracy test |
| `mocap/motion.py` | weapon motion recognition (DTW), calibration, fused camera + gyro decisions |
| `mocap/fusion.py` | camera track, hand paths, stillness, hand and camera-delay finding |
| `mocap/calibration.py` | gyro bias, mount alignment, camera-gated bias, auto mount correction |
| `mocap/pose.py`, `body.py` | poses, jog, hop |
| `mocap/metrics.py` | play log and accuracy test |
| `mocap/app.py`, `overlay.py` | the native Mac app window, menu bar item and HUD panel |
| `mocap/sim/` | stick physics, simulated body and webcam |
| `web/` | the UI (no build step) |
| `lab/` | experiments behind the docs (real datasets + simulation) |
| `packaging/` | `build_app.sh` makes `dist/Mocap.app` with PyInstaller |

## Labs and data

The labs download public datasets into `data/` (git-ignored); see `docs/verification.md`.
Don't commit datasets, photos or videos: link to them and let the scripts fetch them.
`.venv/bin/pip install -r requirements-lab.txt` adds what the labs need.

## Building the app

```
packaging/build_app.sh            # dist/Mocap.app
packaging/build_app.sh --install  # and copy it to /Applications
dist/Mocap.app/Contents/MacOS/Mocap --selftest
```
