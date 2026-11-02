"""Camera + gyro on the stick, simulated: what the webcam adds when it can see your weapon hand.

No dataset has a Joy-Con at the tip of a pipe AND a webcam, so this one is simulated: the
stick physics from sim/stick.py (now with the arm swinging the weapon), the body and webcam
from sim/body.py, with MediaPipe-like errors sized from the real UTD-MHAD runs
(lab/fusion_real_lab.py). Everything goes through the app's own pipeline, sample by sample:
calibration, gravity tracker, segmenter, classifier, lockout, mirror suppression, the
"fire now if the gyro is sure, else wait for the camera" rule, auto mount alignment and
camera-gated bias.

    .venv/bin/python -m lab.fusion_sim_lab           # writes lab/fusion_sim_results.json
"""

import argparse
import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from mocap import motion
from mocap.calibration import DeviceCalibration, rotation_angle
from mocap.camera_moves import MoveClassifier, MoveJoiner, MoveSegmenter
from mocap.fusion import CameraTrack
from mocap.sim.body import GUARD, SIDE_OF, Webcam
from mocap.sim.stick import BOW, BOW_GESTURES, MELEE, MELEE_GESTURES, Stroke, human_variation, reverse, rot, simulate

OUT = Path(__file__).resolve().parent / "fusion_sim_results.json"
STICKS = {"melee": replace(MELEE, pivot_back=0.30), "bow": replace(BOW, pivot_back=0.12)}
OTHER = {"melee": "bow", "bow": "melee"}

# Each move: (strokes, where the hand is while doing it, as an offset from guard in metres).
M, B = MELEE_GESTURES, BOW_GESTURES
MOVES = {
    "melee": {
        "slash_left": (M["slash_left"], (0, 0, 0)),
        "slash_right": (M["slash_right"], (0, 0, 0)),
        "overhead_chop": (M["overhead_chop"], (0, 0, 0)),
        "thrust": (M["thrust"], (0, 0, 0)),
        "circle": (M["circle"], (0, 0, 0)),
        "high_slash_left": (M["slash_left"], (0, 0, 0.40)),
        "low_slash_left": (M["slash_left"], (0, 0, -0.40)),
        "high_thrust": (M["thrust"], (0, 0, 0.38)),
        "low_thrust": (M["thrust"], (0, 0, -0.38)),
        "cross_chop": (M["overhead_chop"], (0, 0.42, 0)),      # chop over on your left side
        "overhand_lob": (M["overhand_lob"], (0, 0, 0)),        # the gyro's twin of the chop
        "quick_jab": (M["quick_jab"], (0, 0, 0)),              # the gyro's twin of the thrust
    },
    "bow": {
        "point_forward": (B["point_forward"], (0, 0, 0)),
        "punch": (B["punch"], (0, 0, 0)),
        "yank_back": (B["yank_back"], (0, 0, 0)),
        "cant_left": (B["cant_left"], (0, 0, 0)),
        "twist": (B["twist"], (0, 0, 0)),
        "high_punch": (B["punch"], (0, 0, 0.35)),             # shove the bow up and out
        "low_punch": (B["punch"], (0, 0, -0.35)),
        "wide_yank": (B["yank_back"], (0, 0.35, 0)),          # pull back out to your side
    },
}
SETS = {
    "melee": {
        "5 distinct (the starter set)": ["slash_left", "slash_right", "overhead_chop", "thrust", "circle"],
        "9: + high/low slash and thrust": ["slash_left", "slash_right", "overhead_chop", "thrust", "circle",
                                           "high_slash_left", "low_slash_left", "high_thrust", "low_thrust"],
        "12: + cross chop, lob, jab": list(MOVES["melee"]),
    },
    "bow": {
        "5 distinct (the starter set)": ["point_forward", "punch", "yank_back", "cant_left", "twist"],
        "8: + high/low punch, wide yank": list(MOVES["bow"]),
    },
}
CAMERA = Webcam(jitter=0.009)        # jitter fitted to MediaPipe on the UTD-MHAD video


def strokes_for(device, name, with_return=False, rng=None):
    strokes, offset = MOVES[device][name]
    pre = [Stroke(0.6, move=offset)] if any(offset) else []
    body = list(strokes)
    if with_return:
        body += [Stroke(rng.uniform(0.08, 0.3))] + reverse(strokes, slow=rng.uniform(1.5, 2.5))
        if any(offset):
            body += [Stroke(0.9, move=tuple(-np.asarray(offset)))]
    return pre + body


def perform(device, strokes, rng, spread=1.0, stick=None, cam=CAMERA, t0=0.0, pre=None, **kw):
    """One stretch of play: IMU stream for the weapon plus the webcam frames of the body."""
    stick = stick or STICKS[device]
    t, g, a, hand = simulate(strokes, stick, rng, human_variation(rng, spread),
                             pre=pre if pre is not None else rng.uniform(0.3, 0.8), post=0.3,
                             with_hand=True, **kw)
    side = SIDE_OF[device]
    sway = np.cumsum(rng.normal(0, 0.0008, (len(t), 3)), axis=0)
    hands = {side: GUARD[device] + hand + sway,
             SIDE_OF[OTHER[device]]: np.tile(GUARD[OTHER[device]], (len(t), 1))}
    frames = cam.frames(t, hands, rng)
    return (t + t0, g, a), [(ft + t0, lm, asp) for ft, lm, asp in frames]


def track_of(frames, lag):
    tr = CameraTrack(seconds=1e9)
    tr.lag = lag
    for ft, lm, asp in frames:
        tr.add(ft, lm, asp)
    return tr


def segments(stream):
    t, gyro, accel = stream
    seg, grav, out = motion.Segmenter(), motion.GravityTracker(), []
    for i in range(len(t)):
        s = seg.feed(t[i], gyro[i], grav.linear(gyro[i], accel[i], t[i]))
        if s is not None:
            out.append(s)
    return out


def record(device, names, rng, n=5, cam=CAMERA, stick=None, with_paths=True):
    temps, paths = {}, {}
    for name in names:
        temps[name], paths[name] = [], []
        tries = 0
        while len(temps[name]) < n and tries < 4 * n:
            tries += 1
            stream, frames = perform(device, strokes_for(device, name), rng, stick=stick, cam=cam)
            segs = segments(stream)
            if not segs:
                continue
            s = max(segs, key=motion.peak)
            temps[name].append(s)
            paths[name].append(track_of(frames, cam.lag).path(SIDE_OF[device], s[0, 0], s[-1, 0]))
    clf = motion.SwingClassifier()
    clf.set_data([(nm, device, temps[nm], None) for nm in names], {}, paths=paths if with_paths else None)
    return clf


def fight_stream(device, names, rng, n, spread=1.0, cam=CAMERA, stick=None, slip_at=None, slip=None,
                 bias=None):
    """n moves, each followed by its swing back and a pause. Returns IMU, frames, expected."""
    ts, gs, accs, frames, expected, starts = [], [], [], [], [], []
    clock = 0.0
    stick = stick or STICKS[device]
    for k in range(n):
        if slip_at is not None and k == slip_at:
            stick = replace(stick, mount=slip @ stick.mount)
        name = names[rng.integers(len(names))]
        (t, g, a), fr = perform(device, strokes_for(device, name, True, rng), rng, spread, stick, cam,
                                t0=clock, gyro_bias=bias(clock) if bias else None)
        ts.append(t)
        gs.append(g)
        accs.append(a)
        frames += fr
        expected.append(name)
        starts.append(clock)
        clock = t[-1] + 1 / 200
    stream = (np.concatenate(ts), np.concatenate(gs), np.concatenate(accs))
    fight_stream.starts = starts
    return stream, frames, expected


class Pipeline:
    """The engine's motion path for one weapon, minus the key output."""

    def __init__(self, clf, device, track, mode="fused", learn=False, camera_bias=False,
                 cal=None, lockout=0.3):
        self.clf, self.device, self.track, self.mode = clf, device, track, mode
        self.learn, self.camera_bias = learn, camera_bias
        self.cal = cal or DeviceCalibration()
        self.lockout = lockout
        self.side = SIDE_OF[device]
        self.fired, self.fire_times, self.waited, self.decisions = [], [], 0, 0
        self.locked_until, self.block = -np.inf, (-np.inf, set())
        self.learned = []

    def run(self, stream):
        t, gyro, accel = stream
        seg, grav = motion.Segmenter(), motion.GravityTracker()
        next_still = 0.0
        for i in range(len(t)):
            g, a = self.cal.apply(t[i], gyro[i], accel[i])
            s = seg.feed(t[i], g, grav.linear(g, a, t[i]))
            if s is not None:
                self.on_segment(s)
            if self.camera_bias and t[i] >= next_still:
                next_still = t[i] + 1 / 30
                if self.track.still(self.side, t[i] - 0.5, t[i]):
                    self.cal.camera_still(t[i] - 0.5, t[i])
        return self

    def on_segment(self, s):
        if s[0, 0] < self.locked_until:
            return
        r = self.clf.classify(self.device, s)
        self.decisions += 1
        if self.mode == "fused" and not r["confident"] and r.get("needs_camera"):
            self.waited += 1
            path = self.track.path(self.side, s[0, 0], s[-1, 0])
            if path is not None:
                r = self.clf.classify(self.device, s, path)
        gid = r["gid"]
        if gid and gid in self.block[1] and s[0, 0] < self.block[0] and r["matches"].get(gid, 0) < 0.75:
            return
        held = motion.held_back(r)
        if not gid and held:
            # A real move that we chose not to send: its swing back still mustn't fire.
            self.locked_until = s[-1, 0] + self.lockout
            self.block = (s[-1, 0] + motion.MIRROR_WINDOW, self.clf.mirrors.get(held, set()))
        if not gid:
            return
        self.fired.append(gid)
        self.fire_times.append(float(s[0, 0]))
        self.locked_until = s[-1, 0] + self.lockout
        self.block = (s[-1, 0] + motion.MIRROR_WINDOW, self.clf.mirrors.get(gid, set()))
        if self.learn and r["template"] is not None:
            trusted = (r["fused"] and r.get("vision", {}).get(gid) is not None) or \
                (r["distance"] is not None and r["distance"] < 0.6 * self.clf.calibration[gid]["threshold"])
            if trusted:
                step = self.cal.learn_alignment(motion.prepare(s)[0],
                                                self.clf.template_seq(self.device, r["template"]))
                self.learned.append(step)


def score(fired, times, expected, starts):
    """Each fire belongs to the move it happened during. A move is right if its first fire is
    that move; any other fire during it is an extra (a wrong key)."""
    edges = list(starts) + [np.inf]
    by_move = {}
    for g, t in zip(fired, times):
        k = int(np.searchsorted(edges, t, side="right")) - 1
        if 0 <= k < len(expected):
            by_move.setdefault(k, []).append(g)
    correct = sum(1 for k, gs in by_move.items() if gs[0] == expected[k])
    extra = sum(len(gs) - (1 if gs[0] == expected[k] else 0) for k, gs in by_move.items())
    return {"moves": len(expected), "correct": correct, "missed": len(expected) - correct,
            "extra": extra, "correct_pct": round(100 * correct / len(expected), 1)}


def fight(device, names, mode, seeds=(1, 2, 3), n=60, cam=CAMERA, rec_cam=CAMERA, lag_error=0.0,
          spread=1.0):
    total = {"moves": 0, "correct": 0, "missed": 0, "extra": 0, "waited": 0}
    for seed in seeds:
        rng = np.random.default_rng(seed)
        # "gyro": moves recorded and played without a camera at all (the app before fusion).
        clf = record(device, names, rng, cam=rec_cam, with_paths=mode != "gyro")
        stream, frames, expected = fight_stream(device, names, rng, n, spread, cam)
        p = Pipeline(clf, device, track_of(frames, cam.lag + lag_error), mode).run(stream)
        sc = score(p.fired, p.fire_times, expected, fight_stream.starts)
        for k in ("moves", "correct", "missed", "extra"):
            total[k] += sc[k]
        total["waited"] += p.waited
        total["decisions"] = total.get("decisions", 0) + p.decisions
    total["correct_pct"] = round(100 * total["correct"] / total["moves"], 1)
    total["waited_for_camera_pct"] = round(100 * total["waited"] / max(total["decisions"], 1), 1)
    return total


def _camera_segments(frames, flush=True):
    seg = MoveSegmenter()
    out = []
    for ft, lm, asp in frames:
        r = seg.feed(ft, lm, asp)
        if r is not None:
            out.append(r)
    if flush and frames:
        r = seg.flush(frames[-1][0])
        if r is not None:
            out.append(r)
    return out


def camera_fight(device, names, seeds=(1, 2, 3), n=60, cam=CAMERA):
    """The same fights, recognized by the webcam alone (the app's body-move recognizer), no gyro."""
    total = {"moves": 0, "correct": 0, "missed": 0, "extra": 0}
    for seed in seeds:
        rng = np.random.default_rng(seed)
        takes = {}
        for name in names:
            takes[name] = []
            for _ in range(5):
                _, frames = perform(device, strokes_for(device, name), rng, cam=cam)
                pieces = _camera_segments(frames)
                if pieces:
                    takes[name].append(np.concatenate(pieces))
        clf = MoveClassifier()
        clf.set_data([(nm, t) for nm, t in takes.items() if t])
        stream, frames, expected = fight_stream(device, names, rng, n, cam=cam)
        fired, times = [], []
        seg, joiner = MoveSegmenter(), MoveJoiner()
        locked, block = -np.inf, (-np.inf, set())
        for ft, lm, asp in frames:
            joiner.expire(ft)
            r = seg.feed(ft, lm, asp)
            if r is None or r[0, 0] < locked:
                continue
            res, used = joiner.offer(r, clf.classify)
            gid = res["gid"]
            if gid and gid in block[1] and used[0, 0] < block[0] and res["matches"].get(gid, 0) < 0.75:
                continue
            if gid:
                fired.append(gid)
                times.append(float(used[0, 0]) - cam.lag)
                locked = used[-1, 0] + 0.3
                block = (used[-1, 0] + 1.0, clf.mirrors.get(gid, set()))
        sc = score(fired, times, expected, fight_stream.starts)
        for k in total:
            total[k] += sc[k]
    total["correct_pct"] = round(100 * total["correct"] / total["moves"], 1)
    return total


def sets_experiment():
    out = {}
    for device, sets in SETS.items():
        for label, names in sets.items():
            row = {mode: fight(device, names, mode) for mode in ("gyro", "fused")}
            row["camera_blocked"] = fight(device, names, "fused", cam=replace(CAMERA, blocked=True))
            row["camera_only"] = camera_fight(device, names)
            out[f"{device}: {label}"] = row
            print(f"  {device:5s} {label:34s} gyro {row['gyro']['correct']}/{row['gyro']['moves']} "
                  f"(+{row['gyro']['extra']} extra)   camera+gyro {row['fused']['correct']}/{row['fused']['moves']} "
                  f"(+{row['fused']['extra']} extra, waited for the camera on {row['fused']['waited_for_camera_pct']}% of motions)"
                  f"   camera blocked {row['camera_blocked']['correct']}/{row['camera_blocked']['moves']} (+{row['camera_blocked']['extra']} extra)"
                  f"   camera only {row['camera_only']['correct']}/{row['camera_only']['moves']} (+{row['camera_only']['extra']} extra)")
    return out


def camera_robustness():
    names = SETS["melee"]["9: + high/low slash and thrust"]
    cases = {
        "normal webcam (30 fps)": dict(),
        "15 fps webcam": dict(cam=replace(CAMERA, fps=15)),
        "twice the blur and lost frames": dict(cam=replace(CAMERA, blur=0.024, drop_rate=0.24, drop_speed=1.5)),
        "camera delay misjudged by 80 ms": dict(lag_error=0.08),
        "turned 30 degrees from the camera": dict(cam=replace(CAMERA, yaw_deg=30)),
        "camera blocked (falls back to gyro)": dict(cam=replace(CAMERA, blocked=True)),
        "sloppier player (1.6x variation)": dict(spread=1.6),
    }
    out = {}
    for label, kw in cases.items():
        out[label] = fight("melee", names, "fused", **kw)
        r = out[label]
        print(f"  {label:38s} {r['correct']}/{r['moves']} correct, {r['extra']} extra")
    return out


def auto_align_experiment(seeds=(1, 2, 3), n=120, slip_deg=35):
    """The Joy-Con turns 35 degrees around the pipe after you recorded your moves (a strap
    slipping). Does the engine notice and correct it from the moves you make anyway?"""
    device = "melee"
    names = SETS["melee"]["5 distinct (the starter set)"]
    stick = STICKS[device]
    axis = np.array([1.0, 0, 0])      # the pipe's own axis in the stick frame
    slip = rot(axis, np.radians(slip_deg))
    cases = {"no correction": ("gyro", False), "auto-align, gyro decides": ("gyro", True),
             "auto-align, camera + gyro decide": ("fused", True),
             "no slip, auto-align on (should stay put)": ("fused", True)}
    out = {}
    for label, (mode, learn) in cases.items():
        first, last, residual = [], [], []
        for seed in seeds:
            rng = np.random.default_rng(seed)
            clf = record(device, names, rng)
            control = label.startswith("no slip")
            stream, frames, expected = fight_stream(device, names, rng, n, slip_at=None if control else 0,
                                                    slip=slip)
            p = Pipeline(clf, device, track_of(frames, CAMERA.lag), mode, learn=learn).run(stream)
            # Score the first and last 40 moves separately: does it get better as it learns?
            starts = fight_stream.starts
            cut1, cut2 = starts[40], starts[n - 40]
            sc = [(g, t) for g, t in zip(p.fired, p.fire_times)]
            first.append(score([g for g, t in sc if t < cut1], [t for g, t in sc if t < cut1],
                               expected[:40], starts[:40])["correct_pct"])
            last.append(score([g for g, t in sc if t >= cut2], [t for g, t in sc if t >= cut2],
                              expected[-40:], starts[-40:])["correct_pct"])
            # How far off is the mount correction now? (slip maps stick->Joy-Con readings)
            truth = np.eye(3) if control else slip
            residual.append(rotation_angle(p.cal.align @ truth.T) if learn else slip_deg)
        out[label] = {"first_40_correct_pct": round(float(np.mean(first)), 1),
                      "last_40_correct_pct": round(float(np.mean(last)), 1),
                      "mount_error_left_deg": round(float(np.mean(residual)), 1)}
        print(f"  {label:34s} first 40 moves {out[label]['first_40_correct_pct']}%  "
              f"last 40 {out[label]['last_40_correct_pct']}%  mount still off by {out[label]['mount_error_left_deg']} deg")
    return out


def bias_experiment(seeds=(1, 2), n=80):
    """A Joy-Con that was never calibrated: a 20 deg/s gyro offset that drifts as it warms up.
    Moves with short pauses in guard between them. Who keeps track of the offset?"""
    device = "melee"
    names = SETS["melee"]["5 distinct (the starter set)"]
    base = np.array([0.22, -0.18, 0.20])        # ~20 deg/s: more than the gyro-only tracker notices

    def bias(t):
        return base * (1 + 0.004 * t)            # +40% over ~100 s of warming up

    out = {}
    for label, cam_bias in (("gyro-only still tracker", False), ("+ camera stillness", True)):
        err, acc = [], []
        for seed in seeds:
            rng = np.random.default_rng(seed)
            clf = record(device, names, rng)
            stream, frames, expected = fight_stream(device, names, rng, n, bias=bias)
            p = Pipeline(clf, device, track_of(frames, CAMERA.lag), "gyro", camera_bias=cam_bias).run(stream)
            err.append(float(np.degrees(np.linalg.norm(p.cal.bias - bias(stream[0][-1])))))
            acc.append(score(p.fired, p.fire_times, expected, fight_stream.starts)["correct_pct"])
        out[label] = {"offset_error_at_end_deg_s": round(float(np.mean(err)), 1),
                      "correct_pct": round(float(np.mean(acc)), 1),
                      "true_offset_deg_s": round(float(np.degrees(np.linalg.norm(bias(0)))), 1)}
        print(f"  {label:26s} offset still wrong by {out[label]['offset_error_at_end_deg_s']} deg/s, "
              f"{out[label]['correct_pct']}% of moves right")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    args = ap.parse_args()
    results = {}
    steps = {"sets": sets_experiment, "camera": camera_robustness, "auto_align": auto_align_experiment,
             "bias": bias_experiment}
    for name, fn in steps.items():
        if args.only and name != args.only:
            continue
        print(f"== {name}")
        results[name] = fn()
    if args.only and OUT.exists():
        merged = json.loads(OUT.read_text())
        merged.update(results)
        results = merged
    OUT.write_text(json.dumps(results, indent=1))
    print("wrote", OUT.name)


if __name__ == "__main__":
    main()
