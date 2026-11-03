# Gesture guide: what works with a Joy-Con on the end of a stick

Short version: pick **big swings in clearly different directions**, add a **thrust** and one
**twist or circle**, record **5 takes** of each, and calibrate once. Avoid pairs that follow the
same path (chop vs overhand lob), diagonals next to straight swings, and gentle sweeps.
**With the webcam on**, you can also reuse a swing at a different height or side (a high and a
low slash) as a separate move: see [More moves with the camera](#more-moves-with-the-camera).

Everything below is backed by three things: prior art (games and papers), the engine's
recognizer run on **real** handheld gyro data (6DMG, 28 people), and a physics simulation of a
Joy-Con at the tip of a PVC pipe (see [verification.md](verification.md)).

## Starter sets

These are the sets that held up best in simulated fights (each move followed by its natural
swing back to guard, ~180 moves per set). Rename them, bind your own spells; the engine doesn't
care what they cast.

**Melee weapon (Joy-Con R at the tip of a ~0.7 m pipe)**

| Move | How | Why it works |
|---|---|---|
| Slash left | Big horizontal swing, right to left | Pure rotation, gyro sign says it all |
| Slash right | The same, left to right | Works alongside slash left thanks to mirror suppression (below) |
| Overhead chop | From raised to forward-down | Different axis from the slashes |
| Thrust | Straight jab forward, little rotation | Accelerometer-led, unlike everything else |
| Circle | Draw a circle with the tip | Distinct, but slower (~0.8 s) so keep it for a cooldown |

Swap in **wrist twist** (rotate the pipe about its own axis, ~120°) for any of these.

**Bow (Joy-Con L at the top of a ~1.2 m pipe, held in the middle)**

| Move | How |
|---|---|
| Point forward | Tip the bow from upright to pointing at the screen |
| Punch | Shove the bow straight forward |
| Yank back | Pull it sharply toward your chest |
| Cant | Tilt it sideways ~50° |
| Twist | Roll it about its own axis |

**Poses (webcam):** 3–5 big, distinct silhouettes (both arms up, arms crossed, hands on hips,
drop to a crouch, draw the bow). Make one of them your **arm/disarm** pose (e.g. weapons
crossed overhead) by setting it to "Toggles armed". Record **Neutral** first.

**Movement (webcam):** jog in place → hold W; lean left / right / back → hold Q / E / S (poses set
to Hold); hop → jump (beta, off by default). Turning stays on the mouse or A / D.

## More moves with the camera

The gyro can't tell *where* your hand is, only how the weapon turns. With the webcam watching,
each take also records your weapon hand's path, so the same motion done in a clearly different
place becomes its own move. Simulated fights, 180 moves each, every move followed by its swing
back (details in [verification.md](verification.md)):

| Set | Gyro only | Camera only | Camera + gyro |
|---|---|---|---|
| Melee: the 5 above + high slash, low slash, high thrust, low thrust (9) | 87 right, 32 wrong keys | 106 right, 2 wrong | **179 right, 0 wrong** |
| Bow: the 5 above + high punch, low punch, wide yank (8) | 100 right, 26 wrong | 49 right, 0 wrong | **179 right, 0 wrong** |
| Melee 12: + cross-body chop, overhand lob, quick jab | 74 right, 41 wrong | 88 right, 11 wrong | 149 right, 6 wrong |

The camera alone can't replace the Joy-Cons for weapon moves: it can't see a twist or a cant, or
a thrust straight at the screen (the bow's 5 starter moves: 17 of 180 by camera alone, 180 with
the gyro).

What works:

- **Height:** about a head higher or lower (≥ 0.4 m). Raise or drop your hand *before* the
  move, then do it there.
- **Side:** a clearly different side (e.g. the bow pulled back out wide).
- **Not:** pairs that the camera *also* can't tell apart. The chop vs overhand lob and thrust vs
  quick jab happen in the same place, which is why the 12-move set stays unreliable.

How it behaves:

- If the gyro alone is sure (the starter sets, most of the time), the key goes out immediately.
  Moves that need the camera wait for it, typically about 40 ms.
- If the camera loses you (you walked out of frame, a cat), those moves are **held back, not
  guessed**: in the same simulation, 0 wrong keys with the camera blocked. Everything that
  doesn't need the camera keeps working.
- The gesture page says which moves use the camera and which ones are "gyro twins".

**Camera placement:** about 2 m away, chest height, with your hands in frame when raised. It
copes with a 15 fps camera, motion blur and a misjudged delay; turning 30° away costs some
accuracy (140/180 in the 9-move set), so face the screen.

## Moves without a weapon

Body moves (a kick, a duck, a two-handed shove) are seen by the webcam alone and add keys
without using up weapon moves. On real video the camera alone got 94% of 21 arm actions right
with 0.1% wrong keys. See [body-moves.md](body-moves.md) for what works.

## Ranked: what to use and what to avoid

1. **Big swings in different directions** — best. Real data: 4 direction swipes 98.3% correct,
   0% wrong key. Skyward Sword's combat is built on swing direction.
2. **Thrusts / pokes** — very good. Real data: 99.6%. But don't record a thrust *and* a quick
   jab: in the simulation they confuse each other 22–40% of the time.
3. **Twists** — very good (real data, 4 swipes + 2 twists: 98.4%, 0% wrong). Use them as a
   single move, not a dial; the wrist only turns about 120°.
4. **Circles** — good (real data: 95.4%) but slow, so they add latency.
5. **Same path, different speed** — avoid having both of: overhead chop + overhand lob, rising
   slash + underhand toss, thrust + quick jab, spin + slash. The engine compares shape and size,
   and these differ too little.
6. **Diagonals next to straight swings** — avoid. Real data: 8 swipes with diagonals drops to
   89% (mostly rejected). Every source reports this as the most confused family.
7. **Gentle sweeps** (e.g. fanning the bow) — avoid. Small wrist fidgets look like them.
8. **Mirror pairs** (slash left + slash right, chop + rising slash) — fine *now*. Without help,
   the swing back from one looks exactly like the other (simulated fights: 75 wrong extra fires
   in 180). The engine detects mirror pairs and, for 1 s after a hit, treats a sloppy copy of the
   mirror move as the swing back: 0 extra fires in 180, while a crisp deliberate strike still counts.

Keep the total small. The Kinect design guidelines found people reliably remember about six
gestures; spread them across weapons, poses and movement rather than stacking one weapon.

## Calibration (Engine → weapon → Calibrate, 10 s)

1. **Rest it and let go (2 s): gyro bias.** SDL2 (what talks to the Joy-Cons) reads Nintendo's
   factory calibration but never removes the gyro's zero offset, which is typically up to ±10 °/s
   on this IMU and drifts with temperature. The engine measures it here and keeps re-measuring
   whenever the weapon is held still during play.
2. **Point it at the screen (2 s), then straight up (2 s): mount.** Two gravity directions pin
   down how the Joy-Con sits on the pipe. If you remount it later, run Calibrate again and your
   recordings keep working (simulation: 14–17 / 60 right after a 35° remount, 58–60 / 60 after
   re-aligning).
3. **Stand in guard (4 s): trigger strength.** How much you naturally jiggle sets how hard a move
   has to be to count.

Per move, calibration is automatic: each move's threshold comes from how consistent your takes
are *and* how close your other moves (and moves you marked "ignore") are. The gesture page shows
the nearest other move and the margin; under 1.2 means they're too alike.

**The camera keeps it calibrated while you play** (Engine → Camera + gyro):

- **Drift:** whenever the camera sees your weapon hand hold still, the gyro's reading is its
  drift, and it's re-measured. This works even for a large offset the gyro can't notice on its
  own (simulated 20°/s uncalibrated offset during non-stop play: off by 34°/s without the camera,
  11°/s with it). It waits for a swing to settle first, so a slow swing back isn't taken for drift.
- **A slipped Joy-Con:** moves recognized with the camera agreeing show if the Joy-Con turned on
  the pipe, and the mount is corrected a few degrees at a time. Simulated 35° slip: 58% of moves
  right uncorrected, 99% once it corrected itself; with no slip it stays within about 3°.
  Still re-run Calibrate after you remount on purpose.

## Recording tips

- **5 takes.** Real data: 1 take 90.5%, 3 takes 97.5%, 5 takes 98.4%.
- **Pause between takes** and swing back to guard naturally. The engine ignores the swing back
  (lockout plus a speed check), and drops an odd take when you finish.
- **Same grip every time.** Direction is measured in the Joy-Con's own frame.
- **Not too gentle.** Each move gets a speed floor and a size floor from your slowest and smallest
  takes, which is what stops lazy returns and fidgets firing it.
- **Getting false fires?** Press "Wasn't me" in the HUD, answer "No, ignore that move" to a near
  miss, or record moves to ignore. **Missing you?** Answer "Yes, loosen it", or slide toward
  Loose on the gesture page.
- **Check it:** Metrics → Accuracy test asks for each move a few times and shows what it
  recognized, with no keys sent. Run it after recording or calibrating.

## About the Joy-Con being at the tip

The gyro reads the same anywhere on a rigid pipe, so the tip is fine for recognition. The
accelerometer is different: at 0.7 m, centripetal acceleration passes the Joy-Con's ±8 g limit at
about 600 °/s, and fast swings reach 1000–2000 °/s. The simulation shows lobs, spins and circles
clipping 14–26% of the time. That's why recognition leans on the gyro and treats acceleration as
secondary. Tap the **shafts** together for quick capture, never the tips, and pad the tips.

## Sources

- Nintendo: [Iwata Asks — Skyward Sword](https://iwataasks.nintendo.com/interviews/wii/zelda-skyward-sword/0/1), [Wii Sports Resort](https://iwataasks.nintendo.com/interviews/wii/wiisportsresort/0/2), [Ask the Developer — Switch Sports](https://www.nintendo.com/ph/interview/as8s/02.html)
- [Kinect Human Interface Guidelines 2.0](https://download.microsoft.com/download/6/7/6/676611B4-1982-47A4-A42E-4CF84E1095A8/KinectHIG.2.0.pdf) (gesture count, return-stroke design)
- [uWave (Liu et al., PerCom 2009)](https://www.yecl.org/publications/liu09percom.pdf); [6DMG (Chen et al., MMSys 2012)](https://web.cs.wpi.edu/~claypool/mmsys-dataset/2012/6dmg/p83-chen.pdf), data on [Zenodo](https://zenodo.org/records/10471794) (CC BY 4.0); [Jackknife](https://github.com/ISUE/Jackknife)
- Joy-Con IMU: [dekuNukem's reverse-engineering notes](https://github.com/dekuNukem/Nintendo_Switch_Reverse_Engineering/blob/master/imu_sensor_notes.md)
- Full notes: `research_notes/gyro stick prior art/notes.md`
