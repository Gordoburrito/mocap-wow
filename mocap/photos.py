"""Photos of you doing each take: a few webcam frames (start, on the way, the peak, on the way
back, the end) with the skeleton the tracker found on each frame, so the app shows the real you
doing the move with your skeleton on top, not just a stick figure.

They stay on this Mac, in the data folder next to your profiles (never in the repo). Files are
named by their content, so two profiles can share one; a file goes when nothing uses it.
"""

import hashlib
import re
import threading
from collections import deque
from pathlib import Path

import numpy as np

NAME = re.compile(r"^[0-9a-f]{16}\.jpg$")
KEEP_S = 8.0          # seconds of recent frames kept while capturing
NEAR_S = 0.15         # a photo this close to the moment wanted is good enough
TRAIL_POINTS = 24


def file_names(obj):
    """Every photo file a profile (or part of one) refers to."""
    out = set()
    if isinstance(obj, dict):
        f = obj.get("file")
        if isinstance(f, str) and NAME.match(f):
            out.add(f)
        for v in obj.values():
            if isinstance(v, (dict, list)):
                out |= file_names(v)
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, (dict, list)):
                out |= file_names(v)
    return out


def peak_index(frames, joints, aspect):
    """The frame where the joints that matter are furthest from where they started."""
    if len(frames) < 2:
        return 0
    lm = np.stack([f[:, :2] for f in frames]) * np.array([aspect, 1.0])
    d = np.linalg.norm(lm[:, joints] - lm[0, joints], axis=2).max(axis=1)
    return int(np.argmax(d))


class PhotoBook:
    """Recent webcam frames (JPEG) while a capture runs, and the photos kept from them."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.frames = deque()
        self.lock = threading.Lock()

    def add(self, t, jpeg):
        with self.lock:
            self.frames.append((t, jpeg))
            while self.frames and t - self.frames[0][0] > KEEP_S:
                self.frames.popleft()

    def clear(self):
        with self.lock:
            self.frames.clear()

    def nearest(self, t):
        with self.lock:
            if not self.frames:
                return None
            best = min(self.frames, key=lambda f: abs(f[0] - t))
        return best if abs(best[0] - t) <= NEAR_S else None

    def store(self, jpeg):
        name = hashlib.sha1(jpeg).hexdigest()[:16] + ".jpg"
        path = self.folder / name
        if not path.exists():
            self.folder.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(jpeg)
            tmp.replace(path)
        return name

    def take(self, skeleton_frames, t0, t1, joints=None, peak_t=None, trail_joints=()):
        """Photos for a take that ran from t0 to t1 (camera time).

        skeleton_frames: [(t, (33, 3) landmarks x/y/visibility, aspect)] from the tracker.
        joints: landmark indices whose furthest point is the peak (or give peak_t).
        Returns {"aspect", "peak", "frames": [{"file", "lm"}], "trails"} or None (no camera)."""
        sk = [(t, np.asarray(lm, dtype=float), a) for t, lm, a in skeleton_frames if t0 - 0.05 <= t <= t1 + 0.05]
        aspect = sk[0][2] if sk else 16 / 9
        if peak_t is None:
            if sk and joints:
                peak_t = sk[peak_index([lm for _, lm, _ in sk], list(joints), aspect)][0]
            else:
                peak_t = (t0 + t1) / 2
        peak_t = float(np.clip(peak_t, t0, t1))
        if t1 - t0 < 0.05:
            wanted = [peak_t]
        else:
            wanted = [t0, (t0 + peak_t) / 2, peak_t, (peak_t + t1) / 2, t1]
        frames, peak, seen = [], 0, set()
        for i, t in enumerate(wanted):
            got = self.nearest(t)
            if got is None:
                continue
            ft, jpeg = got
            if ft in seen:
                if t == peak_t:
                    peak = len(frames) - 1
                continue
            seen.add(ft)
            lm = None
            if sk:
                st, slm, _ = min(sk, key=lambda s: abs(s[0] - ft))
                if abs(st - ft) <= NEAR_S:
                    lm = np.round(slm, 3).tolist()
            if t == peak_t:
                peak = len(frames)
            frames.append({"file": self.store(jpeg), "lm": lm})
        if not frames:
            return None
        trails = {}
        for j in trail_joints:
            pts = [lm[j, :2] for _, lm, _ in sk if lm[j, 2] > 0.5]
            if len(pts) >= 2:
                idx = np.linspace(0, len(pts) - 1, min(TRAIL_POINTS, len(pts))).round().astype(int)
                trails[str(j)] = np.round(np.array(pts)[idx], 3).tolist()
        return {"aspect": round(float(aspect), 4), "peak": int(min(peak, len(frames) - 1)),
                "frames": frames, "trails": trails}

    def forget(self, names, keep):
        """Delete these photo files unless something still uses them."""
        for name in set(names) - set(keep):
            if NAME.match(name):
                try:
                    (self.folder / name).unlink()
                except OSError:
                    pass
