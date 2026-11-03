# Camera + gyro

The Joy-Con's gyro sees exactly how the weapon turns, 200 times a second, but not where your
hand is: a high slash and a low slash turn the weapon the same way. The webcam sees where your
hands are, but only ~30 times a second, blurry on fast swings, and it can't see the pipe, how
far your hand is from the screen, or the pipe twisting in your hand. Each covers the other's
blind spots. This page explains how they're combined and what that was measured to do.

Code: `mocap/fusion.py` (camera track, hand paths, stillness, hand and delay finding),
`mocap/motion.py` (fused decisions), `mocap/calibration.py` (bias and mount correction),
`mocap/engine.py` (when to wait for the camera).

## What it does

**1. Matching moves on gyro + hand path.** Every motion take also stores the path the weapon
hand took, as the camera saw it: 16 points, in shoulder widths from the shoulder midpoint, with
the sideways position measured from where that hand usually rests (so turning your body a
little doesn't shift every move). A live move is compared with your takes on both:

    distance = ½ · gyro distance + ½ · κ · hand-path distance

κ puts both on the same footing automatically: it's the ratio of how much your own takes vary
in each. Thresholds are calibrated on this combined distance the same way they are for the gyro
alone (your takes' spread, the nearest other move, a margin).

**2. Deciding without waiting when it can.** The gyro decides first. If it's sure (well inside
one move's threshold, and that move has no "gyro twin"), the key goes out immediately. Otherwise
the motion waits, up to 150 ms (setting), for camera frames covering it, then decides on both.
A *gyro twin* is a move whose takes come within 0.7× of another move's threshold on the gyro
alone, such as a high and a low slash.

**3. Holding back instead of guessing.** If a twin is recognized but the camera can't see the
hand (out of frame, blocked, camera off), it's held back and shown in the HUD. Its lockout and
mirror suppression still apply, so the swing back doesn't fire either. Moves with no twin keep
working on the gyro.

**4. Keeping the gyro calibrated from your body.**
- *Drift:* whenever the camera sees the weapon hand, its elbow and both shoulders hold still for
  half a second, the gyro's average over that stretch is its zero offset (as long as the gyro
  itself is steady, which rules out twisting the pipe in place). The gyro-only tracker can only
  notice stillness when the reading is already near zero, so it can't find a large offset, and
  it can absorb a slow deliberate movement as drift. The camera can.
- *Slipped mount:* each move recognized with the camera agreeing (or a very clean gyro match)
  pairs the live gyro with your recorded take, aligned in time with DTW. Their cross-covariance,
  summed over the last ~10 such moves, gives the rotation from live to recorded (Kabsch). Nothing
  is applied until moves about at least two different axes agree, the estimate is over 4°, and
  then only half of it, capped at 5° a step and 75° in total (beyond that, recalibrate).

**5. Working out the setup.** Which wrist holds which weapon is whichever one's speed follows
that Joy-Con's gyro (so a mirrored camera can't swap them). How far the camera runs behind the
gyro is found the same way, by lining up gyro bursts with wrist speed. Each Joy-Con's own clock
is mapped onto the computer's from the smallest arrival gap over the last 2 s, so a Joy-Con that
reconnects and restarts its clock is followed within seconds.

## What it was measured to do

### Real people: wrist gyro + video (UTD-MHAD)

8 people × 21 arm actions × 4 trials, with a gyro/accelerometer on the right wrist and a camera
recording at the same time. The app's own code ran on it, with MediaPipe on the real video (the
same model the app uses). Per person: 3 trials of each action recorded, the 4th tested, rotated.
Rejection on, so a rejected move counts as a miss, not a wrong key.

| 21 arm actions | Right | Wrong key |
|---|---|---|
| Gyro only | 87.8% | 0.9% |
| Camera only | 84.8% | 1.6% |
| **Camera + gyro** | **90.3%** | **0.3%** |
| Camera blocked (recorded with it, played without) | 84.6% | 0.7% |

- At every set size from 4 to 21 actions, camera + gyro was 1–3 points ahead of the gyro alone
  with 3–8× fewer wrong keys.
- Largest set at 95%+ right and at most 1% wrong keys: **10 actions on the gyro alone, 15 with
  camera + gyro**.
- The weapon hand was found from one move 86.6% of the time, from three 97.2% (the dataset's
  video is mirrored; the finder followed the gyro, not MediaPipe's left/right label).
- The camera's delay was found from the moves: the dataset's own 260 ms, plus an added 60 ms
  (8 of 8 people within 20 ms) or 120 ms (7 of 8).

**Against the camera alone.** On the same 21 arm actions, the webcam watching your whole body
(the body-move recognizer, docs/body-moves.md) got 94.0% right with 0.1% wrong keys, better than
camera + gyro with the weapon wrist's path. Used inside camera + gyro, those whole-body features
raised it from 90.3% to 93.4% right (0.1% wrong). That swap isn't in the app yet: the weapon-hand
path measures sideways position from where your hand rests, which is what keeps turning your body
from shifting every move, and whole-body features would need the same care first.

### A Joy-Con on a pipe + webcam (simulated)

No dataset has a Joy-Con at the tip of a pipe *and* a webcam, so this part is simulated: the
stick physics from `mocap/sim/stick.py` (now with the arm swinging the weapon), a body holding
both weapons and a webcam with MediaPipe-like errors (`mocap/sim/body.py`), the jitter sized from
MediaPipe on the real UTD-MHAD video. Fights of 3 × 60 moves, each followed by its swing back;
everything through the app's pipeline sample by sample.

| Set (180 moves) | Gyro only | Camera only | Camera + gyro | Camera blocked |
|---|---|---|---|---|
| Melee: slash L, slash R, chop, thrust, circle | 176, 0 wrong keys | 120, 9 | **180, 0** | 178, 0 |
| Melee 9: + high/low slash, high/low thrust | 87, 32 wrong | 106, 2 | **179, 0** | 55, 0 |
| Melee 12: + cross chop, overhand lob, quick jab | 74, 41 wrong | 88, 11 | 149, 6 | 29, 0 |
| Bow: point, punch, yank, cant, twist | 180, 0 | 17, 0 | **180, 0** | 178, 0 |
| Bow 8: + high/low punch, wide yank | 100, 26 wrong | 49, 0 | **179, 0** | 61, 0 |

"Gyro only" means moves recorded and played with no camera at all. "Camera only" is the
body-move recognizer (docs/body-moves.md) on the webcam alone: it can't see twists, cants or
thrusts at the screen. "Camera blocked" means moves recorded with the camera and then played
without it: twins are held back, so it's safe (0 wrong keys) but misses them.

With the starter sets, the gyro alone was sure about 85–89% of motions, so only 12–15% waited for
the camera. With the 9-move set, 71% waited.

**Webcam conditions** (melee 9-move set, camera + gyro, 180 moves):

| Condition | Right | Wrong keys |
|---|---|---|
| Normal webcam, 30 fps | 179 | 0 |
| 15 fps webcam | 178 | 0 |
| Twice the blur and lost frames | 179 | 0 |
| Camera delay misjudged by 80 ms | 178 | 0 |
| Player turned 30° from the camera | 140 | 0 |
| A much sloppier player (1.6× the variation) | 166 | 1 |

**Keeping the gyro calibrated:**

| | First 40 moves | Last 40 moves | Mount still off by |
|---|---|---|---|
| Joy-Con slipped 35° on the pipe, no correction | 56.7% | 58.3% | 35° |
| Auto mount correction, gyro decides | 59.2% | 74.2% | 26.5° |
| Auto mount correction, camera + gyro decide | 99.2% | 99.2% | 3.9° |
| No slip, correction on (should stay put) | 100% | 99.2% | 3.1° |

| Never-calibrated Joy-Con, ~20°/s gyro offset drifting as it warms | Offset left at the end | Moves right |
|---|---|---|
| Gyro-only drift tracker | 33.8°/s | 79.3% |
| + camera stillness | 11.3°/s | 97.5% |

Camera stillness only counts once a swing has settled (1 s after the weapon last moved, judged
0.3 s late): the slow end of a swing back to guard, and the gentle start of the next swing, look
still to the camera but aren't. Without that rule the simulated app re-zeroed the gyro wrongly
18 times in 25 s of sword swings (by up to 5.9°/s); with it, never in three runs. The cost is
slower tracking in this non-stop test (it was 2.6°/s left without the rule), with moves just as
right.

## Limits

- The camera sees in 2D. A thrust *toward* the screen barely moves the hand on camera; that's
  the accelerometer's job. Moves that differ only in depth aren't helped.
- It can't separate moves that happen in the same place (the chop vs an overhand lob).
- Turning well away from the camera costs accuracy (30°: 140/180 in the 9-move set). Face the
  screen.
- Moves that need the camera wait for it: ~40 ms typically, up to 150 ms.
- Real-data caveats: UTD-MHAD's sensor is on the wrist, not at the tip of a pipe, and its video
  is 15 fps. The stick results are simulated. Real numbers on your setup come from the app's
  accuracy test (Metrics page).

Re-run: `.venv/bin/python -m lab.utd_landmarks` (once), `.venv/bin/python -m lab.fusion_real_lab`,
`.venv/bin/python -m lab.fusion_sim_lab`.
