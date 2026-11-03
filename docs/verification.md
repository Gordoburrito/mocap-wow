# How this was verified

Everything here runs without a Joy-Con or WoW. What it proves: the recognizers, calibration and
engine logic work on realistic and real data. What it can't prove yet: how your own Joy-Cons,
camera and WoW behave. That's the on-hardware checklist at the end, and the app's own accuracy
test (Metrics page) is how you measure it on your setup.

Re-run any of it:

```
.venv/bin/python -m pytest              # 72 tests
.venv/bin/python -m lab.stick_lab       # simulated Joy-Con-on-a-pipe physics
.venv/bin/python -m lab.pick_sets       # which gesture sets survive real fights
.venv/bin/python -m lab.real_imu_lab    # REAL handheld gyro gestures (6DMG)
.venv/bin/python lab/fetch_poses.py && .venv/bin/python -m lab.pose_lab   # REAL photos
.venv/bin/python -m lab.body_lab        # REAL video: jog in place, sit-to-stand, squats, jumping jacks
.venv/bin/python -m lab.utd_landmarks   # once: MediaPipe on the UTD-MHAD videos
.venv/bin/python -m lab.fusion_real_lab # REAL wrist gyro + video together: camera + gyro
.venv/bin/python -m lab.fusion_sim_lab  # simulated Joy-Con on a pipe + webcam
.venv/bin/python -m lab.camera_moves_lab # REAL video alone: body moves, no gyro
.venv/bin/python -m lab.summarize       # numbers for the app's Metrics page
```
(`pip install -r requirements-lab.txt` first: the labs also need SciPy.)

Data downloads go to `data/` (not in git). Sources and licences: `research_notes/gyro stick prior art/notes.md`.

## 1. Real handheld gyro gestures (6DMG)

28 people × 20 gestures × 10 trials, Wii Remote + MotionPlus (gyro + accelerometer), CC BY 4.0.
Run through the app's own recognizer with thresholds and rejection **on**, so a rejected move
counts as a miss. Per person: record 5 takes, test the other 5 (and the reverse).

| Set | Correct | Wrong key | Rejected |
|---|---|---|---|
| 4 direction swipes | 98.3% | 0.0% | 1.7% |
| 4 swipes + 2 twists ("stick set") | 98.4% | 0.0% | 1.6% |
| 4 pokes (thrust and back) | 99.6% | 0.0% | 0.4% |
| 4 circles | 95.4% | 0.0% | 4.6% |
| 8 swipes incl. diagonals | 89.0% | 0.4% | 10.6% |
| All 20 gestures | 92.5% | 0.2% | 7.3% |
| Stick set, 1 / 3 / 5 takes | 90.5% / 97.5% / 98.4% | 0% | rest |

Misses are almost always safe rejections, not wrong keys. Gestures nobody recorded still fire
something about 10% of the time when they resemble a recorded one, which is what "moves to
ignore" and the near-miss prompt are for.

Caveat: 6DMG's sensor is in the hand, not at the tip of a pipe, so its accelerometer sees far
less than yours will. The gyro part transfers directly.

## 2. Simulated Joy-Con at the tip of a PVC pipe

`mocap/sim/stick.py` models a rigid pipe (melee 0.7 m held at the end, bow 1.2 m held in the
middle), the Joy-Con at the tip, minimum-jerk human movement with take-to-take variation, sensor
noise, slow hand sway, gyro bias, and the Joy-Con's ±2000 °/s and ±8 g limits. Fast swings clip
the accelerometer at the tip (lob, spin, circle: 14–26% of samples), so recognition leans on the gyro.

**Fights**: each move is followed by its natural swing back to guard; 3 sessions × 60 moves.

| Set | Correct | Extra fires |
|---|---|---|
| Melee: slash L, slash R, chop, thrust, circle | 180/180 | 0 |
| Melee: slash L, chop, thrust, circle, twist | 176/180 | 0 |
| Melee: slash L, slash R, chop, rising slash, thrust (two mirror pairs) | 178/180 | 0 |
| Same, before mirror suppression existed | 106/180 | 75 |
| Bow: point, punch, cant, twist | 177/180 | 0 |
| Bow: point, punch, yank, cant, twist | 176/180 | 0 |

**What each safeguard is worth** (60-move fights, melee and bow):

| Without | With |
|---|---|
| No lockout after a hit: 24–32 / 60 right, 34–38 extra | 300 ms lockout: 59–60 / 60, 0 extra |
| No mirror suppression (left + right slashes): 75 extra in 180 | 1 s mirror window: 0 extra |
| Joy-Con remounted 35°, not re-aligned: 14–17 / 60 | after the 2-pose re-align: 58–60 / 60 |
| Gyro bias 0.1 rad/s left in | no change (58–60 / 60): shape matching absorbs it, and calibration removes it anyway |
| A much sloppier player (1.6× the variation) | still 53–56 / 60, 0 extra |

The simulation also caught three real bugs, all fixed:
1. The gravity estimate swallowed straight punches.
2. Gravity wasn't rotated with the weapon, so a chop merged with its return.
3. Swing-backs were being saved as takes during capture. This also happens in real use; the
   capture now ignores them and prunes odd takes.

## 3. Real photos of people (Wikimedia Commons)

About 250 freely licensed photos (arms raised, arms crossed, hands on hips, archers, people
standing), run through the same MediaPipe model and pose classifier.

- **Same person, natural variation** (closest to real use, where you record yourself):
  **88% correct, 0.7% false triggers from just standing**. Calibrated thresholds raised this from
  82% with the old fixed threshold. Some misses are mislabelled photos (only 14 of 29 "raised
  both hands" photos have both wrists above the shoulders).
- **Different people, different cameras** (harsh stress test, not how you'll use it): low recall
  (1–25%) with 1% false triggers. Poses are personal; record your own.

## 4. Real video: jog and hop

- **Jogging in place** (UTD-MHAD, 6 people): detected in 5 of 6 clips. Each clip holds about
  1.5–2 s of jogging, and detection starts after 3 steps. Real jogging in place is mostly heel kicks, so the
  detector follows the ankles, not the knees (the knee-only version found 0 of 6).
- **Sit-to-stand and squats** (12 clips): 0 false jogs, 0 false hops.
- **Jumping jacks + burpees** (45 s, CC BY-SA): 17 hops counted where an independent count of
  both ankles leaving the floor found 18. An earlier version counted 40: it divided the hips'
  height by that frame's shoulder width, and MediaPipe's ~2% wobble in shoulder width, times the
  ~5 shoulder widths the hips sit down the frame, read as jumps. It now uses a steady median
  shoulder width, and the rise needed is 0.15 shoulder widths (0.2 caught only 13 of 18).
- False hops: none in the 12 sit-to-stand and squat clips, none in 100 simulated kicks, ducks,
  stomps and shoves (the old version fired on some), 3 during jogging in place (hops don't fire
  while you're jogging). Hop stays beta and off by default.

## 5. Camera + gyro on real people (UTD-MHAD)

8 people × 21 arm actions × 4 trials, a gyro/accelerometer on the right wrist recorded together
with RGB video. MediaPipe (the app's model) ran on the video; the app's recognizer, camera track,
hand finder and delay finder did the rest. Per person: 3 trials recorded, the 4th tested,
rotated. Rejection on.

| 21 arm actions | Right | Wrong key | Safe miss |
|---|---|---|---|
| Gyro only (recorded and played without a camera) | 87.8% | 0.9% | 11.3% |
| Camera only (hand path alone) | 84.8% | 1.6% | 13.6% |
| **Camera + gyro** | **90.3%** | **0.3%** | 9.4% |
| Camera blocked (recorded with it, played without) | 84.6% | 0.7% | 14.6% |

| 10 swing-like actions (swipes, throws, swings, punches, push, catch) | Right | Wrong key |
|---|---|---|
| Gyro only | 86.2% | 1.6% |
| Camera only | 88.1% | 1.9% |
| **Camera + gyro** | **90.0%** | **0.0%** |
| Camera blocked | 80.6% | 1.2% |

- **More moves at the same accuracy.** Random sets of 4 to 21 actions: camera + gyro was ahead
  of the gyro alone at every size, by 1 to 3 points, with 3 to 8 times fewer wrong keys
  (21 actions: 90.3% vs 87.8% right, 0.3% vs 0.9% wrong keys). The biggest set that stays at
  95%+ right with at most 1% wrong keys: **10 actions with the gyro alone, 15 with camera + gyro**.
- **"Camera blocked"** (recorded with the camera, played without) stays safe because moves the
  gyro can't tell apart are held back. The twin cut was tuned on this data: at 1.25× the
  threshold, 46% right while blocked; at 0.7×, 83%, with simulated high/low twins still held
  back and 0 wrong keys.
- **Which hand:** the hand finder picked the sensor wrist from a single move 86.6% of the time and from three moves 97.2%. The dataset's video is mirrored, so MediaPipe calls the sensor wrist
  "left"; the hand finder followed the gyro, not the label, which is the point.
- **Camera delay:** the dataset's video runs 260 ms behind its sensor (the same for all 8
  people). The delay finder found it, and found added delays of 60 ms (all 8 people within 20 ms) and 120 ms (7 of 8 within 20 ms).
- **Stillness:** in half-second windows where the wrist sensor was still, the camera agreed
  52.6% of the time (MediaPipe's still wrist wanders a median 0.065 shoulder widths); in moving
  windows it wrongly said still 8.6% of the time, which the gyro steadiness check then rejects.
  Drift on this data: a 32°/s offset added to the real recordings came down to 17.7°/s from
  camera stillness and not at all from the gyro-only tracker. People here hardly ever stand still
  (2 usable still moments per person across all their clips), so the simulation below tests it
  properly.
- **Caveats:** the sensor is on the wrist, not at the tip of a pipe; the video is 15 fps; these
  are everyday arm actions, not weapon moves. The simulation below covers the weapon.

## 6. Camera + gyro on the weapon (simulated)

The stick physics from section 2, now with the arm swinging the weapon, plus a body holding both
weapons and a webcam with MediaPipe-like errors (jitter sized from the real video above, blur
and lost frames on fast swings, tracking lag, 45 ms delay). 3 fights × 60 moves per set, each
move followed by its swing back, through the app's pipeline sample by sample.

| Set (180 moves) | Gyro only | Camera only | Camera + gyro | Camera blocked |
|---|---|---|---|---|
| Melee: slash L, slash R, chop, thrust, circle | 176, 0 wrong keys | 120, 9 | **180, 0** | 178, 0 |
| Melee 9: + high/low slash, high/low thrust | 87, 32 wrong | 106, 2 | **179, 0** | 55, 0 |
| Melee 12: + cross chop, overhand lob, quick jab | 74, 41 wrong | 88, 11 | 149, 6 | 29, 0 |
| Bow: point, punch, yank, cant, twist | 180, 0 | 17, 0 | **180, 0** | 178, 0 |
| Bow 8: + high/low punch, wide yank | 100, 26 wrong | 49, 0 | **179, 0** | 61, 0 |

"Camera only" is the body-move recognizer on the webcam alone (section 6b): enough for big
position changes, not for twists, cants or thrusts at the screen.

The webcam's quality barely matters for the 9-move melee set (178–179/180 at 30 or 15 fps, with
twice the blur and lost frames, or with the delay misjudged by 80 ms, all with 0 wrong keys).
Turning 30° away drops it to 140/180 (0 wrong); a much sloppier player gets 166/180 (1 wrong).

**Keeping the gyro calibrated from the body:**

- A Joy-Con slipped 35° on the pipe: uncorrected, 58.3% of the last 40 of 120 moves right. With
  the auto mount correction learning from moves the camera agreed on: 99.2% from the first 40
  moves on, mount left 3.9° off. Learning from the gyro alone: 74.2% by the end, 26.5° off. With
  no slip it stayed within 3.1°.
- A never-calibrated gyro, ~20°/s offset drifting up 40% as it warms: the gyro-only tracker never
  sees "still" and ends 33.8°/s off (79.3% of moves right); with camera stillness, 11.3°/s off and
  97.5% right. (Camera stillness waits for a swing to settle: without that, 18 wrong re-zeroings
  in 25 s of simulated sword swings, up to 5.9°/s; with it, none in three runs.)

The simulation caught four real bugs, all fixed:
1. A held-back move didn't lock out its own swing back, so the return fired the mirror move
   (36 wrong keys with the camera blocked → 0).
2. The Joy-Con clock mapping couldn't follow a clock that jumped (as after a reconnect), which
   put hand paths in the wrong place in time.
3. The "fire without waiting for the camera" rule trusted the twin list alone; it now also checks
   that no other move is near its threshold on that swing.
4. Gyro drift corrections were logged at noise level (1000 lines a minute); only real
   corrections (≥ 1°/s) count now.

Building body moves caught three more:
1. Starting a capture as a body move still let the pose recorder add frames as takes (and the
   weapon recorder could too). Each recorder now ignores captures meant for another kind.
2. The play log's camera-outcome label overwrote the record's kind, so weapon decisions were
   logged as "agreed" instead of "motion".
3. The simulated webcam's jitter jumped independently every frame, so a still body looked like
   it was moving (real trackers drift); it now drifts, and the numbers above use it.

## 6b. Body moves from the webcam alone (UTD-MHAD)

The same 8 people, all 27 actions (incl. jog, walk, sit to stand, stand to sit, lunge, squat),
the RGB video only, through the app's body-move segmenter and recognizer
(`mocap/camera_moves.py`). Per person: 3 trials recorded, the 4th tested, rotated. Rejection on.

| | Right | Wrong key | Missed |
|---|---|---|---|
| 21 arm actions | **94.0%** | 0.1% | 5.8% |
| 6 leg and body moves | 80.0% | 0.5% | 19.5% |
| All 27 | 90.6% | 0.2% | 9.2% |
| All 27, live segmenter (pieces joined as in the app) | 89.7% | 0.3% | 10.0% |

- Against the gyro on the same 21 arm actions: gyro alone 87.8% (0.9% wrong), camera + gyro with
  the wrist path 90.3% (0.3%), camera alone watching the whole body **94.0% (0.1%)**. Used inside
  camera + gyro, the whole-body features gave 93.4% (0.1%); that isn't in the app yet (see
  docs/fusion.md).
- Every set size from 4 to 27 actions stayed at 91–94% right with at most 0.2% wrong keys. 23
  of the 27 together stay at 95%+ right with at most 1% wrong keys (out: arm curl, catch, walk
  in place, stand to sit).
- Actions nobody recorded fired something 4.9% of the time (the gyro alone on 6DMG: 10.4%).
- The segmenter cut 77% of clips in one piece, split 21% at a pause (which is why pieces that
  don't match are kept for 1.5 s and joined), and found nothing in 3%.
- Weak spots, missed rather than misfired: walking in place (13 of 31) and sitting down (19 of 32).
- Tuning found on the way: measuring joint speed frame to frame made a 30 fps webcam look three
  times jitterier than this 15 fps video, so speed is measured over 0.1 s; and real actions run
  up to ~5 s with wind-ups, so the 3 s cut-off for "that's just fidgeting" became 4 s in the app.

Scopes (judging a move on one body part): on the same video, letting each action pick its own
scope from its takes (Auto) gave 90.7% right and 0.3% wrong over all 27 (whole body: 90.6%,
0.2%); on arm actions alone, 93.4% right and 1.0% wrong (whole body: 94.0%, 0.1%). In the
simulator, 120 kicks, ducks and shoves through the engine with auto scopes (kick → right leg,
shove → both arms, duck → whole body): 120 right, 0 wrong. That run caught two scoping bugs:
legs judged from the hips made every duck look like a kick to a leg-scoped move (legs are now
judged against the floor), and the simulator's duck left the hands hanging in the air.

In the app (`lab/e2e_body_moves.py`, simulated webcam body): kick, duck and shove captured from 5
takes each, then a sequence of 8 moves fired every key in order, and a shove made while slashing
sent only the slash. The same flow runs as a test (`tests/test_camera_moves.py`).

## 7. The whole app, end to end (simulated hardware)

`--sim --sim-camera --dry-run`: two simulated Joy-Cons and a simulated webcam that sees the
simulated body holding them, driven over HTTP and WebSocket
(`lab/e2e_app.py`; the same flows run as tests in `tests/test_engine_fusion.py`):

- Captured a slash at three heights (identical to the gyro) and a chop, 5 takes each, every take
  with its hand path. Then 10 moves in a fight: every key right and in order. The camera
  corrected the gyro's pick 2 times and rescued 3–5 moves the gyro would have rejected; the
  typical delay from the end of a move to the key was ~85–100 ms (incl. the 50 ms the move needs
  to settle), with ~40–50 ms of that spent waiting for the camera on the moves that needed it.
- Which hand holds the melee weapon and the camera's delay were learned during capture.
- With the camera stopped, the three slashes were held back (0 wrong keys) and the HUD said why.
- Accuracy test: 8 prompts, 8 right, 0 keys sent; the result landed in the play log and on the
  Metrics page.
- "Wasn't me" on the last fire added that move to the ignore list.
- Earlier checks still hold: weapon clash → capture → bind → fight, hands-free arm pose,
  "WoW in front" check, all UI screens by screenshot.

**The Mac app.** `packaging/build_app.sh` builds `Mocap.app` with PyInstaller (330 MB, Python,
MediaPipe, SDL and the pose model inside, ad-hoc signed). Checked:

- `Mocap --selftest` inside the bundle: pose model loads and runs, OpenCV works, SDL starts.
- Installed in /Applications and launched through macOS (`open`): window shows the UI (read back
  from its own web view), menu-bar item, the simulated fight 10/10 and the accuracy test 8/8 in
  the installed copy, and it quits cleanly (saving first).
- Found and fixed: OpenCV's wheels carry their own SDL2, and macOS uses the Objective-C classes
  (Joy-Con rumble among them) of whichever copy loads first. The app now loads the Joy-Con SDL
  first (checked with the ObjC runtime).

## 8. Not verified yet (needs your hardware)

1. Two Joy-Cons over Bluetooth through SDL on your Mac: sensor rate, drop-outs, rumble.
2. Keys reaching WoW (Engine → Outputs → Test, or Test key on a gesture).
3. The ⌥C / key-capture tap (needs Accessibility; it reports if it can't start).
4. The overlay above WoW in Windowed (Fullscreen).
5. Your webcam's frame rate, framing and real delay (Engine → Camera + gyro shows the learned
   delay).
6. Your real moves at the tip of the pipe, with and without the camera: run Metrics → Accuracy
   test after recording, and watch the HUD's near misses and "Wasn't me" in the first sessions.
7. The app's first launch outside this machine (Gatekeeper on an unsigned app: right-click →
   Open).
