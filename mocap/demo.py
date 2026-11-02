"""Demo mode: the whole app filled with sample data, on simulated hardware, in its own folder.

    ./run.sh --demo          or          Mocap → ⚔ → Open the demo

It never touches your real profiles or play log: everything lives in a separate data folder
("Mocap Demo"), keys are never sent, and the Joy-Cons and webcam are simulated. The sample
profile is recorded through the app's own pipeline from the simulator (weapon takes with hand
paths, body moves with skeleton keyframes, poses from simulated skeletons), so it behaves like
real recordings; the play log holds two weeks of made-up sessions so the Metrics page has
something to show. A demo player performs moves every few seconds (and answers the accuracy
test) so you can watch it work.
"""

import json
import os
import random
import secrets
import threading
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

from . import camera_moves
from . import profile as profiles
from .fusion import CameraTrack
from .motion import GravityTracker, Segmenter, peak
from .sim.body import GUARD, SIDE_OF, Webcam, project, skeleton
from .sim.stick import BOW, CATALOGUE, MELEE, Stroke, human_variation, pitch_up, simulate

ARM = {"melee": replace(MELEE, pivot_back=0.30), "bow": replace(BOW, pivot_back=0.12)}
OTHER = {"melee": "bow", "bow": "melee"}
CAMERA = Webcam(jitter=0.009)
ASPECT = 16 / 9

# (name, casts, key, how) per kind. Moves match the ability: drawing the bow aims, a slash strikes.
WEAPON = [
    ("melee", "slash_left", (0, 0, 0), "Raptor Strike", "Raptor Strike", "Digit1"),
    ("melee", "slash_right", (0, 0, 0), "Mongoose Bite", "Mongoose Bite", "Digit2"),
    ("melee", "slash_left", (0, 0, -0.40), "Wing Clip", "Wing Clip", "Digit3"),
    ("melee", "thrust", (0, 0, 0), "Harpoon", "Harpoon", "Digit4"),
    ("melee", "circle", (0, 0, 0), "Carve", "Carve", "Digit5"),
    ("bow", "point_forward", (0, 0, 0), "Draw and aim", "Aimed Shot", "KeyQ"),
    ("bow", "punch", (0, 0, 0), "Snap shot", "Arcane Shot", "KeyE"),
    ("bow", "punch", (0, 0, 0.35), "Shoot to the sky", "Volley", "KeyR"),
    ("bow", "yank_back", (0, 0, 0), "Pull the string", "Rapid Fire", "KeyT"),
    ("bow", "twist", (0, 0, 0), "Turn the bow", "Multi-Shot", "KeyG"),
]
BODY = [
    ("duck", "Feign Death", "Feign Death", "KeyF"),
    ("kick", "Muzzle kick", "Muzzle", "KeyX"),
    ("stomp", "Stomp", "Tar Trap", "KeyC"),
    ("shove", "Two-handed shove", "Intimidation", "KeyV"),
]
POSES = [
    ("arms_up", "Arms up: arm / disarm", "", None, "whole", "arm_toggle"),
    ("right_up", "Right arm raised", "Aspect of the Eagle", "KeyB", "right_arm", "key"),
]


def demo_dir():
    return Path.home() / "Library" / "Application Support" / "Mocap Demo"


def _key(code):
    return {"code": code, "mods": []}


def _gesture(kind, source, name, casts, key, **extra):
    g = profiles.new_gesture(kind, source, name)
    g.update(casts=casts, key=_key(key) if key else None, **extra)
    g["id"] = "g" + secrets.token_hex(4)
    return g


# ----------------------------------------------------------------------------- recordings

def _segments(t, gyro, accel):
    seg, grav, out = Segmenter(), GravityTracker(), []
    for i in range(len(t)):
        s = seg.feed(t[i], gyro[i], grav.linear(gyro[i], accel[i], t[i]))
        if s is not None:
            out.append(s)
    return out


def _weapon_take(role, gesture, offset, rng):
    strokes = ([Stroke(0.6, move=offset)] if any(offset) else []) + list(CATALOGUE[role][1][gesture])
    t, g, a, hand = simulate(strokes, ARM[role], rng, human_variation(rng), pre=0.6, post=0.4, with_hand=True)
    hands = {SIDE_OF[role]: GUARD[role] + hand, SIDE_OF[OTHER[role]]: np.tile(GUARD[OTHER[role]], (len(t), 1))}
    frames = CAMERA.frames(t, hands, rng)
    segs = _segments(t, g, a)
    if not segs:
        return None, None
    s = max(segs, key=peak)
    track = CameraTrack(seconds=1e9)
    track.lag = CAMERA.lag
    for ft, lm, asp in frames:
        track.add(ft, lm, asp)
    path = track.path(SIDE_OF[role], s[0, 0], s[-1, 0])
    return np.round(s, 4).tolist(), (np.round(path, 4).tolist() if path is not None else None)


def body_frames(move, rng, size=1.0, seconds=0.7, pre=0.6, post=1.0, fps=30):
    """Landmark frames of the simulated body doing a body move once (the demo's webcam)."""
    out, noise = [], np.zeros((33, 3))
    for k in range(int((pre + seconds + post) * fps)):
        t = k / fps
        f = (t - pre) / seconds
        s = np.sin(np.pi * f) ** 2 * size if 0 <= f < 1 else 0.0
        lh, rh = GUARD["bow"].copy(), GUARD["melee"].copy()
        crouch, feet = 0.0, None
        if move == "duck":
            crouch = 0.35 * s
            lh[2] -= crouch
            rh[2] -= crouch
        elif move == "kick":
            feet = {"right": 0.55 * s}
        elif move == "stomp":
            feet = {"left": 0.35 * s}
        elif move == "shove":
            lh[2] += 0.45 * s
            rh[2] += 0.45 * s
        noise = 0.85 * noise + rng.normal(0, 0.003, (33, 3))
        pts = skeleton(lh, rh, 0, crouch, feet) + noise
        uv, _ = project(pts, aspect=ASPECT)
        lm = np.zeros((33, 4))
        lm[:, :2] = uv
        lm[:, 3] = 0.95
        out.append((t, lm))
    return out


def _body_take(move, rng):
    fr = body_frames(move, rng, size=rng.uniform(0.85, 1.15), seconds=0.7 * rng.uniform(0.85, 1.15))
    seg = camera_moves.MoveSegmenter()
    pieces = [p for p in (seg.feed(t, lm, ASPECT) for t, lm in fr) if p is not None]
    if not pieces:
        return None, None
    take = np.concatenate(pieces)
    t0, t1 = take[0, 0], take[-1, 0]
    keep = [lm for t, lm in fr if t0 - 0.05 <= t <= t1 + 0.05]
    pick = np.linspace(0, len(keep) - 1, min(12, len(keep))).round().astype(int)
    skel = {"aspect": round(ASPECT, 4), "frames": [np.round(keep[i][:, [0, 1, 3]], 3).tolist() for i in pick]}
    return np.round(take, 4).tolist(), skel


def pose_landmarks(name, rng):
    lh, rh = GUARD["bow"].copy(), GUARD["melee"].copy()
    if name in ("arms_up", "right_up"):
        rh = np.array([0.05, -0.30, 1.95])
    if name == "arms_up":
        lh = np.array([0.05, 0.30, 1.95])
    pts = skeleton(lh, rh) + rng.normal(0, 0.004, (33, 3))
    uv, _ = project(pts, aspect=ASPECT)
    lm = np.zeros((33, 4))
    lm[:, :2] = uv
    lm[:, 3] = 0.95
    return lm


def build_profile(seed=11):
    rng = np.random.default_rng(seed)
    p = profiles.empty_profile("Hunter (sample data)")
    p["settings"].update(require_wow_front=False, hop_enabled=True)
    for role, (name, stick) in (("bow", ("Joy-Con (L)", BOW)), ("melee", ("Joy-Con (R)", MELEE))):
        grav = lambda pose: (stick.mount.T @ (pose.T @ np.array([0, 0, 9.81]))).tolist()
        p["devices"][name] = {"role": role, "hand": "auto", "calibration": {
            "gyro_bias": [0.004, -0.003, 0.002], "align": np.eye(3).tolist(), "manual_align": np.eye(3).tolist(),
            "ref_forward": grav(pitch_up(0)), "ref_up": grav(pitch_up(90)),
            "trigger": 2.6}}
    for role, gesture, offset, name, casts, key in WEAPON:
        takes, paths = [], []
        while len(takes) < 5:
            seg, path = _weapon_take(role, gesture, offset, rng)
            if seg is not None:
                takes.append(seg)
                paths.append(path)
        g = _gesture("motion", role, name, casts, key, templates=takes, paths=paths)
        g["demo"] = {"role": role, "gesture": gesture, "offset": list(offset)}
        p["gestures"].append(g)
    for move, name, casts, key in BODY:
        takes, skels = [], []
        while len(takes) < 5:
            take, skel = _body_take(move, rng)
            if take is not None:
                takes.append(take)
                skels.append(skel)
        g = _gesture("move", "webcam", name, casts, key, templates=takes, skeletons=skels,
                     auto_scope=camera_moves.auto_scope(takes), scope="auto")
        g["demo"] = {"body": move}
        p["gestures"].append(g)
    for pose, name, casts, key, scope, action in POSES:
        samples = [{"lm": np.round(pose_landmarks(pose, rng), 4).tolist(), "aspect": round(ASPECT, 4)}
                   for _ in range(60)]
        g = _gesture("pose", "webcam", name, casts, key, samples=samples, scope=scope, action=action)
        g["demo"] = {"pose": pose}
        p["gestures"].append(g)
    p["gestures"].append(_gesture("jog", "webcam", "Run forward", "Move forward", "KeyW"))
    p["gestures"].append(_gesture("hop", "webcam", "Jump", "Jump", "Space"))
    # Standing in guard, as the simulated webcam sees it (its jitter drifts like a real tracker's).
    p["neutral"] = [{"lm": np.round(lm, 4).tolist(), "aspect": round(ASPECT, 4)}
                    for _, lm in body_frames("none", rng, seconds=0.1, pre=1.0, post=3.0)[:120]]
    return p


# ----------------------------------------------------------------------------- play log

def write_history(metrics_dir, profile, days=14, seed=5):
    """Two weeks of made-up sessions, getting better as you go, so Metrics has data to show."""
    r = random.Random(seed)
    os.makedirs(metrics_dir, exist_ok=True)
    moves = [g for g in profile["gestures"] if g["kind"] in ("motion", "move")]
    now = time.time()
    for d in range(days, 0, -1):
        if r.random() < 0.2:
            continue                                   # a day off
        day0 = now - d * 86400 - r.uniform(2, 6) * 3600
        skill = 0.78 + 0.17 * (days - d) / days
        lines, t = [], day0
        for _ in range(r.randint(120, 320)):
            t += r.uniform(2, 9)
            g = r.choice(moves)
            roll = r.random()
            o = "fired" if roll < skill else "near" if roll < skill + 0.08 else "rejected" if roll < 0.995 else "held"
            kind = "move" if g["kind"] == "move" else "motion"
            fused = kind == "motion" and r.random() < 0.6
            lines.append({"t": t, "k": kind, "role": g["source"], "o": o, "g": g["id"], "n": g["name"],
                          "r": None if o == "fired" else "didn't match any motion closely enough",
                          "fused": fused, "gyro": g["id"], "lat": round(r.uniform(60, 140), 1), "wait": None})
            if o == "fired":
                lines.append({"t": t, "k": "fire", "g": g["id"], "n": g["name"], "src": g["source"], "sent": True,
                              "why": ""})
        for _ in range(r.randint(0, 3)):
            g = r.choice(moves)
            lines.append({"t": t, "k": "correction", "c": r.choice(["loosen", "ignore", "wrong"]), "g": g["id"],
                          "n": g["name"]})
        if d % 4 == 1:
            n = 3 * len(moves)
            right = int(round(n * min(0.98, skill + 0.05)))
            wrong = r.randint(0, 2)
            lines.append({"t": t + 60, "k": "drill", "n": n, "accuracy": round(100 * right / n, 1),
                          "wrong": round(100 * wrong / n, 1), "missed": round(100 * (n - right - wrong) / n, 1),
                          "latency_ms": r.randint(140, 260)})
        day = time.strftime("%Y-%m-%d", time.localtime(day0))
        with open(os.path.join(metrics_dir, f"{day}.jsonl"), "a") as f:
            for rec in lines:
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")


def prepare(data_dir, reset=False):
    """Make sure the demo folder has its sample profile and play log. Returns the profile path."""
    data_dir = Path(data_dir)
    path = data_dir / "profiles" / "hunter-sample.json"
    if reset or not path.exists():
        profile = build_profile()
        profiles.save(str(path), profile)
        metrics = data_dir / "metrics"
        if metrics.is_dir():
            for f in metrics.glob("*.jsonl"):
                f.unlink()
        write_history(str(metrics), profile)
    return str(path)


# ----------------------------------------------------------------------------- live player

class DemoPlayer(threading.Thread):
    """Performs a random move from the sample profile every few seconds, so the HUD, Library
    and Metrics come alive, and does whatever the accuracy test asks for. It pauses while you
    capture, calibrate or record Neutral."""

    def __init__(self, engine, rig, camera, stop, every=(3.5, 6.0)):
        super().__init__(name="demo-player", daemon=True)
        self.engine, self.rig, self.camera, self.stop = engine, rig, camera, stop
        self.every = every
        self.rng = random.Random(7)
        self.answered = None
        self.was_busy = False

    def _busy(self):
        e = self.engine
        return e.capture is not None or e.calib is not None or e.neutral_rec is not None

    def hush(self):
        """Stop whatever move is still playing, so it can't land in your capture."""
        with self.rig.lock:
            for q in self.rig.queues.values():
                q.clear()
            for role in self.rig.hand:
                self.rig.hand[role] = np.zeros(3)      # and the hands back to guard
        self.camera.body_anim = None

    def _wait(self, seconds):
        """Sleep, but notice at once if you start something (and stop the move in flight)."""
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self.stop.is_set():
            if self._busy() or self.engine.drill is not None:
                return
            self.stop.wait(0.1)

    def _check_busy(self):
        """Stop the demo's own move once, when you start something; your moves then play."""
        busy = self._busy() or self.engine.drill is not None
        if busy and not self.was_busy:
            self.hush()
        self.was_busy = busy
        return busy

    def perform(self, g):
        demo = g.get("demo") or {}
        if "gesture" in demo:
            return self.rig.perform(demo["role"], demo["gesture"], 1.0, demo.get("offset") or (0, 0, 0))
        if "body" in demo:
            return self.camera.perform_body(demo["body"])
        return None

    def run(self):
        time.sleep(3.0)
        self.engine.set_armed(True)
        while not self.stop.is_set():
            self._check_busy()
            drill = self.engine.drill
            if drill is None:
                self.answered = None
            if drill is not None:
                if drill.phase == "go" and self.answered != drill.index and drill.current:
                    self.answered = drill.index
                    g = self.engine.gesture(drill.current["id"])
                    if g is not None:
                        time.sleep(0.4)
                        self.perform(g)
                self.stop.wait(0.2)
                continue
            if self._busy():
                self.stop.wait(0.1)
                continue
            moves = [g for g in self.engine.profile["gestures"]
                     if g.get("enabled", True) and (g.get("demo") or {}).keys() & {"gesture", "body"}]
            if moves:
                self.perform(self.rng.choice(moves))
            self._wait(self.rng.uniform(*self.every))
            self._check_busy()
