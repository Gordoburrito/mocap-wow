# Body moves (webcam only)

A **pose** is a shape you hold. A **body move** is something you *do* with the weapons still: a
kick, a stomp, a duck, a two-handed shove, a wave, a lunge. It's seen by the webcam alone, no
gyro, so it frees you from needing a weapon for every key.

Capture one like anything else: **Capture** → **It's a body move** (or just do it with the
weapons still; the engine works out that it wasn't a weapon motion or a held pose) → do it 5
times, settling for a second between takes → press the key.

## Judged on one part of you (scope)

A body move can be judged on just the part of you that does it, so the rest of your body doesn't
matter: raise your arm whether you're standing, crouching or walking; kick whatever your arms are
doing. On the move's page, **Judged on**:

- **Auto** (the default): the parts that clearly moved in your takes. A kick becomes "right leg",
  a two-handed shove "both arms", a duck "whole body".
- **Whole body**, **Left arm**, **Right arm**, **Both arms**, **Left leg**, **Right leg**,
  **Both legs**, or **Whole-body position** (a duck, a hop, a step).

Each scope watches only its own body parts, both to notice that a move started and to match it.
Legs are judged against the floor, not your hips, so a duck doesn't look like a kick to a leg
move. A leg or whole-body-position move isn't held back by a weapon swing, so you can kick while
you slash; an arm move is, if that hand's weapon motion fired.

Poses have the same choice (whole body by default): set a pose to "Left arm" and it fires on
your left arm's position whatever the rest of you does. Weapon motions can be set to **Weapon
only (gyro)** to ignore the camera entirely.

On real video, Auto came out level with Whole body overall (all 27 actions: 90.7% vs 90.6%
right). It trades a little safety on arm-only actions (1.0% vs 0.1% wrong keys), because the
other arm no longer helps tell them apart. Pick Whole body for a move that keeps getting mixed up.

## What you recorded

Each take keeps up to 12 frames of your actual skeleton as the webcam saw it. The move's tile
and page draw your recording: faint where you started, solid at the furthest point of the move,
with dotted trails for the joints it's judged on, plus a small view of every take.

## How it works

`mocap/camera_moves.py`:

- **Cutting moves out of the video.** A move starts when your two fastest joints (of wrists,
  elbows, ankles, knees, and your body as a whole) move faster than 1.4 shoulder widths a second,
  measured over 0.1 s so the frame rate doesn't matter, and ends once you've settled for 0.25 s.
  Record Neutral and the trigger is set from how much you sway standing.
- **Matching.** The move is resampled to 16 steps of where those 9 points went, measured from
  your hips in shoulder widths (so distance from the camera doesn't matter), and compared with
  your takes by DTW. Thresholds are calibrated per move exactly as for weapon motions: how much
  your own takes vary, and how close your other moves are. Slow or tiny versions don't count.
- **Pauses.** About 1 in 5 real moves has a pause in it (a wind-up, then the throw). A piece
  that doesn't match anything is kept for 1.5 s and tried together with the next piece.
- **Weapons first.** A body move made while a weapon motion fires is left to the weapon (your
  arm moves with the pipe, so the camera always sees a swing too).
- The usual safety: lockout after a fire, the way back from a move doesn't fire its mirror,
  near misses ask "loosen it?", and "Wasn't me" teaches it to ignore a move.

It's slower than a weapon motion: the camera runs ~30 times a second and lags, and a move only
ends once you've settled, so expect roughly 0.3 s from the end of the move to the key.

## How well it works

### Real people (UTD-MHAD video, MediaPipe, no gyro)

8 people × 27 actions × 4 trials, the app's segmenter and recognizer on the real video. Per
person: 3 trials recorded, the 4th tested, rotated. Rejection on.

| Actions | Right | Wrong key |
|---|---|---|
| 21 arm actions (swipes, throws, swings, clap, wave, push, catch…) | **94.0%** | 0.1% |
| 6 leg and body moves (jog, walk, sit to stand, stand to sit, lunge, squat) | 80.0% | 0.5% |
| All 27 | 90.6% | 0.2% |

- **27 actions with the live segmenter** (pieces joined as in the app): 89.7% right, 0.3% wrong.
- **Biggest reliable set:** 23 of the 27 actions together still score 95%+ right with at most
  1% wrong keys. The four left out are arm curl, catch, walk in place, and stand to sit.
- **Moves never recorded** fired something 4.9% of the time (the gyro alone: 10.4% on 6DMG).
- **Walking in place and sitting down are the weak ones**, and they get missed rather than
  misfired: slow, continuous, or the hips disappear. Jog has its own detector anyway.

### Compared with the gyro, on the same people and arm actions

| 21 arm actions, same people and trials | Right | Wrong key |
|---|---|---|
| Gyro only (wrist sensor) | 87.8% | 0.9% |
| Camera only, just the sensor wrist's path | 84.8% | 1.6% |
| **Camera only, whole body (body moves)** | **94.0%** | **0.1%** |
| Camera + gyro (wrist path), what weapon motions use | 90.3% | 0.3% |
| Camera + gyro with whole-body features (experiment, not in the app yet) | 93.4% | 0.1% |

For everyday, whole-arm actions the camera watching your whole body beats a wrist gyro: it sees
both arms and your legs, not one wrist.

### Compared on the weapon (simulated)

Weapon moves are another story. In the simulator, the camera alone recognized only part of the
weapon moves (see `docs/verification.md`, section 6): it can't see a twist or a cant (the hand
barely moves), nor a thrust straight at the screen, and the bow's moves are mostly rotations in
place. Keep weapon moves on the Joy-Cons; use body moves for everything you do without them.

## What to use

Good body moves are **big, clearly different, and done the same way each time**:

- a kick (front or side), a stomp, a knee raise
- a duck or a squat, a lunge
- a two-handed shove, both arms up, arms crossed and back out
- a wave or a circle with the hand that isn't holding much

Avoid: slow continuous ones (walking), ones that end with you out of frame (sitting down), and
two moves that only differ in speed. Keep the whole body in frame, about 2–3 m from the camera.
Run Metrics → Accuracy test after recording: it includes body moves.

Re-run: `.venv/bin/python -m lab.utd_landmarks` (once), then
`.venv/bin/python -m lab.camera_moves_lab`.
