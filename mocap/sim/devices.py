"""Simulated weapons and webcam for running the whole app without hardware: ./run.sh --sim
(add --sim-camera for a webcam that sees the simulated body holding them). Gestures are
played on request through POST /api/sim, using the same stick physics as the lab, so the
engine sees realistic gyro/accelerometer data, and the webcam sees the weapon hand move.
"""

import collections
import threading
import time
from dataclasses import replace

import numpy as np

from .body import GUARD, SIDE_OF, Webcam, project, skeleton
from .stick import BOW, CATALOGUE, MELEE, Stroke, human_variation, reverse, simulate

RATE = 200
ARM = {"melee": replace(MELEE, pivot_back=0.30), "bow": replace(BOW, pivot_back=0.12)}


class SimRig(threading.Thread):
    DEVICES = {"Joy-Con (L)": ("bow", BOW), "Joy-Con (R)": ("melee", MELEE)}

    def __init__(self, engine, stop, seed=7):
        super().__init__(name="sim-rig", daemon=True)
        self.engine = engine
        self.stop = stop
        self.rng = np.random.default_rng(seed)
        self.queues = {name: collections.deque() for name in self.DEVICES}
        self.lock = threading.Lock()
        self.t = 0.0
        # Where each weapon hand is (displacement from guard, metres), with history for the camera.
        self.hand = {role: np.zeros(3) for role, _ in self.DEVICES.values()}
        self.hand_history = collections.deque(maxlen=400)

    def run(self):
        for name in self.DEVICES:
            self.engine.on_device(name, True, {"sensors": ["gyro", "accel"], "raw_name": name,
                                               "battery": "full", "simulated": True})
        rest = {name: self._rest(stick) for name, (_, stick) in self.DEVICES.items()}
        next_t = time.monotonic()
        while not self.stop.is_set():
            self.t += 1 / RATE
            for name, (role, _) in self.DEVICES.items():
                with self.lock:
                    sample = self.queues[name].popleft() if self.queues[name] else None
                if sample is None:
                    g = self.rng.normal(0, 0.03, 3)
                    a = rest[name] + self.rng.normal(0, 0.08, 3)
                else:
                    g, a, h = sample
                    self.hand[role] = h
                self.engine.on_imu(name, self.t, g, a)
            self.hand_history.append((time.monotonic(), {r: h.copy() for r, h in self.hand.items()}))
            next_t += 1 / RATE
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.monotonic()

    @staticmethod
    def _rest(stick):
        return stick.mount.T @ (stick.rest.T @ np.array([0, 0, 9.81]))

    def _device_for(self, role):
        return next(n for n, (r, _) in self.DEVICES.items() if r == role)

    def hands_at(self, t):
        """Weapon-hand displacements as they were at host time t."""
        best = None
        for ht, hands in reversed(self.hand_history):
            best = hands
            if ht <= t:
                break
        return best or self.hand

    def perform(self, role, gesture, spread=1.0, offset=(0.0, 0.0, 0.0)):
        """The move (optionally with the hand raised/lowered/out to the side by `offset` metres),
        a short pause, the natural slower swing back to guard, and the hand back to guard."""
        stick, catalogue = CATALOGUE[role]
        moves = catalogue[gesture]
        offset = tuple(float(x) for x in offset)
        strokes = ([Stroke(0.6, move=offset)] if any(offset) else []) + list(moves) \
            + [Stroke(self.rng.uniform(0.1, 0.3))] + reverse(moves, slow=self.rng.uniform(1.6, 2.4)) \
            + ([Stroke(0.9, move=tuple(-np.asarray(offset)))] if any(offset) else [])
        _, gyro, accel, hand = simulate(strokes, replace(ARM[role], mount=stick.mount, rest=stick.rest),
                                        self.rng, human_variation(self.rng, spread), with_hand=True)
        with self.lock:
            self.queues[self._device_for(role)].extend(zip(gyro, accel, hand))
        return len(gyro) / RATE

    def clash(self):
        """Tap the shafts together: a sharp jolt on both weapons in the same instant."""
        for name, (role, stick) in self.DEVICES.items():
            base = self._rest(stick)
            hit = self.rng.normal(size=3)
            hit = hit / np.linalg.norm(hit) * self.rng.uniform(40, 70)
            h = self.hand[role]
            samples = [(self.rng.normal(0, 0.3, 3), base + hit, h),
                       (self.rng.normal(0, 0.3, 3), base - 0.5 * hit, h),
                       (self.rng.normal(0, 0.1, 3), base + 0.2 * hit, h)]
            with self.lock:
                self.queues[name].extend(samples)


class SimCamera(threading.Thread):
    """Feeds pose landmarks at 30 fps. With a rig, it renders a body holding both weapons whose
    weapon hands follow the simulated moves (MediaPipe-like noise, 45 ms late). A named pose
    from `library` ({name: [frames of (33, 4) landmarks]}) can be shown on request instead."""

    def __init__(self, engine, stop, library=None, aspect=16 / 9, rig=None, webcam=None):
        super().__init__(name="sim-camera", daemon=True)
        self.engine = engine
        self.stop = stop
        self.library = library or {}
        self.aspect = aspect
        self.rig = rig
        self.webcam = webcam or Webcam(jitter=0.009, aspect=aspect)
        self.current = "neutral"
        self.until = 0.0
        self.rng = np.random.default_rng(3)

    # Body moves for the simulated webcam: what moves, how far (metres), how long (seconds).
    BODY_MOVES = {
        "kick": ({"right_foot": 0.55}, 0.7),
        "stomp": ({"left_foot": 0.35}, 0.6),
        "duck": ({"crouch": 0.35}, 0.8),
        "shove": ({"hands": (0.0, 0.0, 0.45)}, 0.6),
        "wave": ({"wave": 0.3}, 1.2),
    }

    def perform_body(self, name, size=1.0):
        """Play a body move on the simulated webcam (the weapons don't move)."""
        if name not in self.BODY_MOVES:
            return None
        spec, seconds = self.BODY_MOVES[name]
        seconds *= float(self.rng.uniform(0.85, 1.15))
        self.body_anim = (name, time.monotonic(), seconds, size * float(self.rng.uniform(0.85, 1.15)))
        return seconds

    def _anim(self, now):
        """Offsets for the body move that's playing, if any: out and back, smoothly."""
        anim = getattr(self, "body_anim", None)
        if anim is None:
            return {}
        name, t0, seconds, size = anim
        f = (now - t0) / seconds
        if f < 0:
            return {}                      # not started yet (the camera shows the past)
        if f >= 1:
            self.body_anim = None
            return {}
        spec = self.BODY_MOVES[name][0]
        s = np.sin(np.pi * f) ** 2 * size
        out = {}
        if "right_foot" in spec:
            out["feet"] = {"right": spec["right_foot"] * s}
        if "left_foot" in spec:
            out["feet"] = {"left": spec["left_foot"] * s}
        if "crouch" in spec:
            out["crouch"] = spec["crouch"] * s
            out["hands"] = np.array([0.0, 0.0, -spec["crouch"] * s])    # the hands go down with you
        if "hands" in spec:
            out["hands"] = np.array(spec["hands"]) * s
        if "wave" in spec:
            out["hands"] = np.array([0.0, spec["wave"] * np.sin(4 * np.pi * f) * size, 0.25 * s])
        return out

    def show(self, pose, seconds=2.0):
        if pose in self.library:
            self.current, self.until = pose, time.monotonic() + seconds
            return True
        return False

    def _body(self, now):
        cam = self.webcam
        hands = self.rig.hands_at(now - cam.lag) if self.rig is not None else {}
        anim = self._anim(now - cam.lag)
        world = {}
        for role in ("melee", "bow"):
            p = GUARD[role] + np.asarray(hands.get(role, np.zeros(3))) + anim.get("hands", 0.0)
            world[SIDE_OF[role]] = p
        pts = skeleton(world.get("left"), world.get("right"), cam.yaw_deg, anim.get("crouch", 0.0),
                       anim.get("feet"))
        # Tracker jitter drifts rather than jumping frame to frame (as MediaPipe's does).
        noise = getattr(self, "_noise", None)
        if noise is None or noise.shape != pts.shape:
            noise = np.zeros(pts.shape)
        self._noise = 0.85 * noise + self.rng.normal(0, cam.jitter * 0.5, pts.shape)
        pts = pts + self._noise
        uv, _ = project(pts, aspect=self.aspect)
        lm = np.zeros((33, 4))
        lm[:, :2] = uv
        lm[:, 3] = 0.95
        return lm

    def run(self):
        self.engine.set_camera(ok=True, fps=30.0, message="Simulated camera")
        while not self.stop.is_set():
            now = time.monotonic()
            name = self.current if now < self.until else "neutral"
            frames = self.library.get(name) if name != "neutral" or self.rig is None else None
            if frames:
                lm = np.array(frames[self.rng.integers(len(frames))], dtype=float)
                lm[:, :2] += self.rng.normal(0, 0.003, (33, 2))
            elif self.rig is not None or not self.library:
                lm = self._body(now)
            else:
                frames = self.library.get("neutral")
                lm = np.array(frames[self.rng.integers(len(frames))], dtype=float) if frames else None
            if lm is not None:
                self.engine.on_pose(lm, self.aspect, now)
                preview = getattr(self.engine, "preview_wanted", False)
                photos = getattr(self.engine, "photos_wanted", False)
                if (preview or photos) and now - getattr(self, "_last_preview", 0) >= 1 / 15:
                    self._last_preview = now
                    jpeg = self._render(lm)
                    if jpeg is not None:
                        if preview:
                            self.engine.emit_binary(jpeg)
                        if photos:
                            self.engine.on_photo(now, jpeg)
            time.sleep(1 / 30)

    def _render(self, lm, width=480):
        """A drawn body as the 'video' (and the photos of takes), so those paths run in simulation too."""
        try:
            import cv2
        except ImportError:
            return None
        h = int(width / self.aspect)
        img = np.full((h, width, 3), (24, 20, 18), np.uint8)
        P = lambda i: (int(lm[i, 0] * width), int(lm[i, 1] * h))
        body = [(11, 12), (11, 23), (12, 24), (23, 24), (11, 13), (13, 15), (12, 14), (14, 16),
                (23, 25), (25, 27), (24, 26), (26, 28)]
        torso = np.array([P(11), P(12), P(24), P(23)], np.int32)
        cv2.fillPoly(img, [torso], (92, 84, 78))
        for a, b in body:
            cv2.line(img, P(a), P(b), (92, 84, 78), 26, cv2.LINE_AA)
        cv2.circle(img, P(0), 26, (110, 100, 94), -1, cv2.LINE_AA)
        ok, jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return jpeg.tobytes() if ok else None
