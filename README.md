# Mocap · play WoW with your body

A Mac app for playing World of Warcraft with your body and two PVC weapons. A Joy-Con on the
end of each pipe (L = bow, R = melee) reads your swings; the webcam reads your poses, your
movement, and where your weapon hands are. Capture any move on the fly, bind it to the key
you set in WoW, and it's live.

- **Capture on the fly:** tap the weapon shafts together (or press ⌥C), do the move five
  times, press the key. No training step and no config files.
- **Body moves with no weapon:** a kick, a duck, a two-handed shove, seen by the webcam alone.
- **Camera + gyro together:** the gyro knows exactly how the weapon turns; the camera knows
  where your hand is. Combined, the same swing at head height and at the waist can be two
  different moves, the camera keeps the gyro calibrated, and lookalike moves stop misfiring.
- **One gesture = one key press.** It never sends sequences or repeats and never reads the
  game. Write and change your macros in WoW whenever you like.
- **Metrics:** an accuracy test on your own moves and hardware, a play log, and the data from
  how it was tested.

## Start it

- **The app:** open **Mocap** from Applications (or Spotlight: ⌘Space, type "Mocap"). A window
  opens with the setup UI and a ⚔ appears in the menu bar. Closing the window keeps it running
  there; quit from the ⚔ menu or with ⌘Q.
- **From the source folder:** `./run.sh` (opens the UI in your browser), or
  `./run.sh --overlay` for the HUD panel beside the game.
- **Try it without hardware:** `./run.sh --sim --sim-camera --dry-run` gives you two simulated
  Joy-Cons and a simulated webcam (drive them with `POST /api/sim`, see CONTRIBUTING.md).

## Install

**The app:** download `Mocap.app` from Releases, or build it yourself:

```
packaging/build_app.sh --install      # builds dist/Mocap.app and copies it to /Applications
```

The app is self-contained: Python, MediaPipe, SDL and the pose model are all inside. It isn't
notarized, so the first time, right-click it → **Open**.

**From source** (Python 3.11 or 3.12):

```
./run.sh                 # sets up .venv, downloads the pose model, opens the UI in your browser
./run.sh --overlay       # plus the slim HUD panel beside the game
```

## One-time Mac setup

1. **Camera:** macOS asks the first time. (Running from source, allow your terminal app under
   System Settings → Privacy & Security → Camera.)
2. **Accessibility** (to press keys, and for the ⌥C hotkey): System Settings → Privacy &
   Security → Accessibility → turn on **Mocap** (or your terminal app). Restart Mocap.
3. **Joy-Cons:** hold the sync button on each rail until the lights run, then connect them in
   System Settings → Bluetooth. (L) becomes the bow and (R) the melee weapon; swap on the
   Engine page.
4. **Calibrate each weapon** (Engine page, 10 s): rest it, point it at the screen, point it up,
   stand in guard.
5. **Record Neutral** (Engine page): 6 s of you standing normally, so poses know what "nothing"
   looks like.
6. WoW: use **Windowed (Fullscreen)** for the HUD overlay, and leave WoW's gamepad support off.

Check everything at once: `Mocap.app/Contents/MacOS/Mocap --selftest` (or
`.venv/bin/python -m mocap --selftest`) loads the pose model, OpenCV and SDL and reports
whether key output is allowed.

## Capturing a move

1. **Tap the weapon shafts together** (not the tips), press **⌥C**, or click **Capture**. You see
   yourself on the webcam with your skeleton drawn on top.
2. **Each take: 3, 2, 1, GO**, then 2.5 s of recording (red border, REC timer): do the move once.
   "✓ Take 1 of 5", and the next countdown starts; if it didn't catch anything it says so and
   asks again. It works out whether you swung a weapon, did a body move or held a pose (or
   press "It's a pose" / "It's a body move"). With the webcam on, weapon takes also record
   your hand's path. Countdown and recording length are on the Engine page, which also has
   the old "just do it 5 times" style.
3. Each kept take lands in the **take strip** under the video: a photo of you at the peak of
   the take with your skeleton on top.
4. **Press the key** you bound in WoW, name it, and **Save and go live**.

Every gesture keeps photos of you doing it: a few webcam frames per take (start, peak, end)
with the skeleton found on each. Tiles show your most typical take (hover to play it); a
gesture's page shows every take. They stay on your Mac, in the data folder
(`~/Library/Application Support/Mocap/photos`), and go when you remove the take or gesture.

## Playing

- **Arm** with a pose set to "Toggles armed" (e.g. weapons crossed overhead), the ARMED button,
  or the ⚔ menu-bar item. Nothing is sent while disarmed.
- Keys only go out while WoW is the front window (Engine → Safety).
- After a weapon hit, that weapon is locked out briefly and its mirror move is treated as the
  swing back, so returns don't fire.
- The HUD shows what fired. **"Wasn't me"** marks a wrong fire and teaches it to ignore that
  move; near misses ask "loosen it?" or "ignore that move?".

## Body moves (webcam only)

Capture → **It's a body move**, do it 5 times with the weapons still, press the key. A kick, a
stomp, a duck, a two-handed shove, a wave: the webcam cuts each one out of the video and matches
where your hands, elbows, feet, knees and body went. Each move is judged only on the part of you
that does it (auto, or pick: an arm, a leg, both, whole body), so raising your arm works whatever
your legs are doing; poses can be scoped the same way, and weapon motions can ignore the camera.
Tiles show a photo of you doing it, with your skeleton and the path of what it's judged on.

On real people (UTD-MHAD video, 8 people), the camera alone got **94.0% of 21 arm actions right
with 0.1% wrong keys**, better than a wrist gyro (87.8%, 0.9% wrong), and 23 of 27 actions
together stay at 95%+ right. Weapon moves still need the Joy-Cons: the camera can't see a twist
or a thrust at the screen. Details: [docs/body-moves.md](docs/body-moves.md).

## Camera + gyro

When the webcam can see your weapon hand:

- **More moves.** Moves are matched on the gyro *and* the hand's path. In the simulator, a bow
  with 8 moves (incl. high and low punches) went from 100/180 right with 26 wrong keys on the
  gyro alone to 179/180 with none; melee with 9 moves from 87/180 (32 wrong) to 179/180 (0).
  On real people (UTD-MHAD: wrist gyro + video, 8 people, 21 arm actions): 90.3% right with
  0.3% wrong keys, against 87.8% and 0.9% for the gyro alone.
- **No waiting when it's obvious.** If the gyro alone is sure, the key goes out at once.
  Otherwise it waits for the camera frames covering the move (typically ~40 ms).
- **It keeps the gyro calibrated.** When the camera sees your hand hold still, it re-measures
  gyro drift, even drift too large for the gyro to notice itself. Moves recognized with the
  camera agreeing reveal a Joy-Con that slipped on the pipe; it's corrected a few degrees at a
  time (simulated 35° slip: 58% → 99% of moves right).
- **It figures out the setup.** Which hand holds which weapon, and how far the camera lags the
  gyro, are learned from your moves.
- **If the camera can't see you,** moves that need it (e.g. a high vs a low slash) are held
  back rather than guessed, and everything else keeps working on the gyro.

Details: [docs/fusion.md](docs/fusion.md).

## Metrics

The Metrics page (or ⚔ → Accuracy test…):

- **Accuracy test:** the app asks for each of your moves a few times in random order and
  scores what it recognized: % right, wrong keys, misses, delay, and which move got mistaken
  for which. No keys are sent.
- **This session and day by day:** what fired, near misses, why moves didn't fire, what the
  camera changed (agreed, rescued, corrected, stopped a wrong fire), and calibration updates.
  The log stays on your Mac (`~/Library/Application Support/Mocap/metrics`); turn it off on the
  Engine page.
- **How it was tested:** the results from real datasets and simulation, drawn from
  `web/lab-summary.json`.

## Which moves work best

Big swings in clearly different directions, a thrust, and a twist or circle. With the camera
on, the same move at different heights works too. See
[docs/gesture-guide.md](docs/gesture-guide.md). How it was tested:
[docs/verification.md](docs/verification.md).

## Your data

Profiles (your recordings, keys and weapon calibration) and the play log live in
`~/Library/Application Support/Mocap/`. Nothing is uploaded anywhere: the UI is served on
127.0.0.1 only, and video never leaves the pose model. `--sim` runs use a throwaway folder.

## Options

`--overlay` · `--profile path.json` · `--data-dir DIR` · `--camera 1` · `--dry-run` (recognize,
never send keys) · `--sim` (simulated Joy-Cons) · `--sim-camera` (a simulated webcam that sees
them) · `--no-camera` · `--no-hotkeys` · `--port` · `--selftest`

## Development

`.venv/bin/python -m pytest` runs the tests (no hardware needed). See
[CONTRIBUTING.md](CONTRIBUTING.md) for the layout, the simulator and the labs, and
[THIRD_PARTY.md](THIRD_PARTY.md) for licences. MIT licensed.
