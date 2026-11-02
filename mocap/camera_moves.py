"""Body moves from the webcam alone: no weapon, no gyro.

A pose is a shape you hold; a body move is something you *do*: a kick, a stomp, a duck, a
two-handed shove, a wave. The webcam cuts one out of its stream when your body moves and
settles again, and compares it with your recorded takes by DTW on where your hands, elbows,
feet and knees went (measured from your hips, in shoulder widths, so standing nearer or
further doesn't matter) and where your whole body went (for hops, steps and ducks).
Calibration is the same as for weapon motions: each move's threshold comes from how much your
own takes vary and how close your other moves are; slow or tiny versions don't count.

Slower than weapon motions: the camera runs ~30 times a second and lags, and a move only ends
when you stop moving, so expect roughly 0.2-0.3 s from the end of the move to the key.
"""

from collections import deque

import numpy as np

from .motion import _thresholds, dtw_batch

# Wrists, elbows, ankles, knees (MediaPipe pose indices), then the hips' midpoint itself.
JOINTS = (15, 16, 13, 14, 27, 28, 25, 26)
NAMES = ("left wrist", "right wrist", "left elbow", "right elbow", "left ankle", "right ankle",
         "left knee", "right knee", "body")
DIMS = 2 * (len(JOINTS) + 1)
# Which body points a move is judged on. "auto" = the parts that moved in your takes.
SCOPES = {
    "whole": (0, 1, 2, 3, 4, 5, 6, 7, 8),
    "left_arm": (0, 2), "right_arm": (1, 3), "arms": (0, 1, 2, 3),
    "left_leg": (4, 6), "right_leg": (5, 7), "legs": (4, 5, 6, 7),
    "body": (8,),
}
SCOPE_LABELS = {"whole": "Whole body", "left_arm": "Left arm", "right_arm": "Right arm", "arms": "Both arms",
                "left_leg": "Left leg", "right_leg": "Right leg", "legs": "Both legs",
                "body": "Whole-body position (duck, hop, step)"}
# Legs are judged against the floor, not the hips: ducking moves your feet relative to your
# hips but not in the room, and that mustn't look like a kick.
GROUNDED = {"left_leg", "right_leg", "legs"}
SCOPE_SIDES = {"whole": {"left", "right"}, "arms": {"left", "right"}, "left_arm": {"left"},
               "right_arm": {"right"}, "left_leg": set(), "right_leg": set(), "legs": set(), "body": set()}
N = 16                     # points per move after resampling
MIN_VIS = 0.4
START = 1.4                # shoulder widths per second (top-2 joint speed) that starts a move
MIN_THRESHOLD, MAX_THRESHOLD, DEFAULT_THRESHOLD = 0.08, 0.9, 0.35
SPEED_FLOOR = 0.5          # a move must reach this fraction of your slowest take's peak speed
SIZE_FLOOR = 0.45          # ...and travel this fraction of your shortest take's distance
SIZE_WEIGHT = 0.15         # |log(size ratio)| added to the shape distance
MIRROR_WINDOW = 1.0
SPEED_SPAN = 0.1           # seconds over which joint speed is measured


def body_points(landmarks, aspect, scale):
    """(9, 2) points: 8 joints from the hips' midpoint, then the hips' midpoint in the image,
    all in shoulder widths with y up. NaN where a joint isn't visible. None if the body isn't."""
    lm = np.asarray(landmarks, dtype=float)
    vis = lm[:, 3]
    if min(vis[11], vis[12], vis[23], vis[24]) < MIN_VIS or not scale:
        return None
    xy = np.stack([lm[:, 0] * aspect, -lm[:, 1]], axis=1) / scale
    hips = (xy[23] + xy[24]) / 2
    out = np.full((len(JOINTS) + 1, 2), np.nan)
    for k, i in enumerate(JOINTS):
        if vis[i] >= MIN_VIS:
            out[k] = xy[i] - hips
    out[-1] = hips
    return out


def shoulder_width(landmarks, aspect):
    lm = np.asarray(landmarks, dtype=float)
    if min(lm[11, 3], lm[12, 3]) < MIN_VIS:
        return None
    d = np.array([(lm[11, 0] - lm[12, 0]) * aspect, lm[11, 1] - lm[12, 1]])
    return float(np.linalg.norm(d)) or None


class MoveSegmenter:
    """Cuts body moves out of the webcam stream: starts when the two fastest joints move faster
    than `start` (shoulder widths per second), ends after `quiet_s` below `end_ratio` of that.
    Short pauses inside a move (a double wave) stay inside it. Returns (n, 1 + 18) arrays:
    time, then x, y of each body point."""

    def __init__(self, start=START, end_ratio=0.45, quiet_s=0.25, min_s=0.2, max_s=4.0,
                 preroll_s=0.2, postroll_s=0.1, points=None, grounded=False):
        self.points = None if points is None else list(points)   # watch only these body points
        self.grounded = grounded
        self.start = start
        self.end_ratio = end_ratio
        self.quiet_s = quiet_s
        self.min_s = min_s
        self.max_s = max_s
        self.preroll_s = preroll_s
        self.postroll_s = postroll_s
        self._sw = deque(maxlen=90)
        self._recent = deque()
        self._smooth = None
        self._hist = deque()
        self._active = None
        self._quiet_since = None
        self.energy = 0.0
        self.peak = 0.0
        self._cooldown_until = -np.inf

    def reset(self):
        self._recent.clear()
        self._hist.clear()
        self._active = None
        self._smooth = None

    def feed(self, t, landmarks, aspect):
        if landmarks is None:
            return self._end(t) if self._active else None
        sw = shoulder_width(landmarks, aspect)
        if sw:
            self._sw.append(sw)
        if not self._sw:
            return None
        pts = body_points(landmarks, aspect, float(np.median(self._sw)))
        if pts is None:
            return self._end(t) if self._active else None
        # Light smoothing: MediaPipe jitters a joint by ~2% of your shoulder width per frame.
        if self._smooth is None or np.isnan(self._smooth).all():
            self._smooth = pts.copy()
        else:
            fill = np.isnan(self._smooth) & ~np.isnan(pts)
            self._smooth[fill] = pts[fill]
            ok = ~np.isnan(pts)
            self._smooth[ok] = 0.5 * self._smooth[ok] + 0.5 * pts[ok]
        cur = self._smooth.copy()
        pos = cur.copy()
        if self.grounded:
            pos[:-1] = pos[:-1] + pos[-1]       # speed of joints in the room, not from the hips
        # Speed over the last ~0.1 s rather than frame to frame: the same moves read the same
        # at 15 or 60 fps, and a webcam's jitter doesn't add up into speed at high frame rates.
        energy = 0.0
        ref = None
        for ht, hp in self._hist:
            if t - ht >= SPEED_SPAN:
                ref = (ht, hp)
            else:
                break
        if ref is None and self._hist:
            ref = self._hist[0]
        if ref is not None:
            dt = max(t - ref[0], 1e-3)
            speed = np.linalg.norm(pos - ref[1], axis=1) / dt
            if self.points is not None:
                speed = speed[self.points]
            speed = speed[np.isfinite(speed)]
            if len(speed):
                energy = float(np.mean(np.sort(speed)[-2:]))
        self._hist.append((t, pos))
        while len(self._hist) > 2 and t - self._hist[1][0] >= SPEED_SPAN:
            self._hist.popleft()
        self.energy = energy
        row = np.concatenate([[t], np.nan_to_num(cur.ravel(), nan=0.0)])
        if self._active is None:
            self._recent.append(row)
            while self._recent and t - self._recent[0][0] > self.preroll_s:
                self._recent.popleft()
            if energy >= self.start and t >= self._cooldown_until:
                self._active = list(self._recent)
                self._quiet_since = None
                self.peak = energy
            return None
        self._active.append(row)
        self.peak = max(self.peak, energy)
        if energy < self.start * self.end_ratio:
            self._quiet_since = self._quiet_since if self._quiet_since is not None else t
        else:
            self._quiet_since = None
        if t - self._active[0][0] > self.max_s:
            self._active = None              # continuous movement isn't a move
            self._recent.clear()
            self._cooldown_until = t + 0.4
            return None
        if self._quiet_since is not None and t - self._quiet_since >= self.quiet_s:
            return self._end(t)
        return None

    def flush(self, t):
        """The stream ended (or the camera stopped) mid-move: finish what's there."""
        return self._end(t) if self._active else None

    def _end(self, t):
        seg = np.array(self._active)
        self._active = None
        self._recent.clear()
        # Keep a little of the settle so the end position is in the move.
        if self._quiet_since is not None:
            seg = seg[seg[:, 0] <= self._quiet_since + self.postroll_s]
        if len(seg) < 4 or seg[-1, 0] - seg[0, 0] < self.min_s:
            return None
        return seg


def _speeds(seg, cols=None):
    seg = np.asarray(seg, dtype=float)
    if len(seg) < 2:
        return np.zeros(1)
    data = seg[:, 1:] if cols is None else seg[:, 1:][:, cols]
    p = data.reshape(len(seg), -1, 2)
    dt = np.maximum(np.diff(seg[:, 0]), 1e-3)
    sp = np.linalg.norm(np.diff(p, axis=0), axis=2) / dt[:, None]
    return np.mean(np.sort(sp, axis=1)[:, -2:], axis=1)


def peak(seg, cols=None):
    return float(np.max(_speeds(seg, cols)))


def size(seg, cols=None):
    """Total distance travelled by the body points (shoulder widths), or just a scope's."""
    seg = np.asarray(seg, dtype=float)
    data = seg[:, 1:] if cols is None else seg[:, 1:][:, cols]
    p = data.reshape(len(seg), -1, 2)
    return float(np.linalg.norm(np.diff(p, axis=0), axis=2).sum())


def prepare(seg, n=N):
    """Raw move (k, 1 + 18) -> (n, 18), resampled evenly in time. The body point is measured from
    where the move started (a hop is the same hop wherever you stand)."""
    seg = np.asarray(seg, dtype=float)
    t = seg[:, 0] - seg[0, 0]
    data = seg[:, 1:].copy()
    data[:, -2:] -= data[0, -2:]
    if t[-1] <= 0:
        t = np.linspace(0, 1, len(seg))
    grid = np.linspace(0, t[-1], n)
    return np.stack([np.interp(grid, t, data[:, i]) for i in range(data.shape[1])], axis=1)


def distances(seq, size_, bank, bank_sizes):
    d = dtw_batch(seq, bank)
    if len(d) and size_ > 0:
        d = d + SIZE_WEIGHT * np.abs(np.log(np.maximum(size_, 1e-6) / np.maximum(bank_sizes, 1e-6)))
    return d


def mirror(seq):
    """The way back from a move: the same positions in reverse order."""
    out = seq[::-1].copy()
    out[:, -2:] -= out[0, -2:]
    return out


def columns(points):
    return [c for i in points for c in (2 * i, 2 * i + 1)]


def part_travel(takes):
    """How far each body part travelled in a gesture's takes (shoulder widths, averaged)."""
    seqs = np.stack([prepare(t) for t in takes]).reshape(len(takes), N, -1, 2)
    seqs[:, :, 4:8] += seqs[:, :, 8:9]       # legs against the floor (a duck doesn't lift your feet)
    travel = np.linalg.norm(np.diff(seqs, axis=1), axis=3).sum(axis=1).mean(axis=0)   # per point
    parts = {"left_arm": (0, 2), "right_arm": (1, 3), "left_leg": (4, 6), "right_leg": (5, 7), "body": (8,)}
    return {p: float(max(travel[i] for i in idx)) for p, idx in parts.items()}


def auto_scope(takes):
    """The scope a move should be judged on: only the parts that clearly moved in your takes
    (so for a kick, what your arms do doesn't matter)."""
    if not takes:
        return "whole"
    travel = part_travel(takes)
    top = max(travel.values())
    moved = {p for p, v in travel.items() if v >= max(0.35 * top, 0.3)}
    for scope, parts in (("left_arm", {"left_arm"}), ("right_arm", {"right_arm"}),
                         ("arms", {"left_arm", "right_arm"}), ("left_leg", {"left_leg"}),
                         ("right_leg", {"right_leg"}), ("legs", {"left_leg", "right_leg"}),
                         ("body", {"body"})):
        if moved == parts:
            return scope
    return "whole"


class MoveClassifier:
    """Matches body moves against your recorded takes (same rules as weapon motions), judged
    on the body points of one scope."""

    def __init__(self, points=None, grounded=False):
        self.cols = None if points is None else columns(points)
        self.grounded = grounded
        self.bank = None            # (T, N, dims)
        self.owners = []
        self.sizes = np.empty(0)
        self.info = {}
        self.calibration = {}
        self.mirrors = {}
        self.ignore = None

    def set_data(self, gestures, ignore=(), sensitivity=None):
        """gestures: iterable of (gid, takes); ignore: raw moves that must never fire."""
        sensitivity = sensitivity or {}
        seqs, owners, sizes, peaks = [], [], [], {}
        for gid, takes in gestures:
            for t in takes or ():
                seqs.append(self._prep(t))
                owners.append(gid)
                sizes.append(size(t, self.cols))
                peaks.setdefault(gid, []).append(peak(t, self.cols))
        self.owners, self.info, self.calibration, self.mirrors = owners, {}, {}, {}
        self.ignore = None
        if not seqs:
            self.bank = None
            return
        self.bank, self.sizes = np.stack(seqs), np.array(sizes)
        D = np.stack([distances(s, sizes[i], self.bank, self.sizes) for i, s in enumerate(self.bank)])
        I = None
        if len(ignore):
            ign = np.stack([self._prep(t) for t in ignore])
            ign_sizes = np.array([size(t, self.cols) for t in ignore])
            self.ignore = (ign, ign_sizes)
            I = np.stack([distances(s, sizes[i], ign, ign_sizes) for i, s in enumerate(self.bank)])
        cal = _thresholds(D, owners, I, lo=MIN_THRESHOLD, hi=MAX_THRESHOLD, default=DEFAULT_THRESHOLD)
        for gid, c in cal.items():
            idx = [i for i, o in enumerate(owners) if o == gid]
            c["threshold"] *= sensitivity.get(gid, 1.0)
            c["floor"] = SPEED_FLOOR * min(peaks[gid])
            c["size_floor"] = SIZE_FLOOR * float(min(self.sizes[idx]))
            self.info[gid] = c
        self.calibration = self.info
        for gid in self.info:
            idx = [i for i, o in enumerate(owners) if o == gid]
            back = mirror(np.mean(self.bank[idx], axis=0))
            d = dtw_batch(back, self.bank)
            self.mirrors[gid] = {o for o, dist in zip(owners, d)
                                 if o != gid and dist <= 1.3 * self.info[o]["threshold"]}

    def _prep(self, seg):
        seq = prepare(seg)
        if self.grounded:
            seq = seq.copy()
            seq[:, :-2] = seq[:, :-2] + np.tile(seq[:, -2:], (1, len(JOINTS)))
        return seq if self.cols is None else seq[:, self.cols]

    def classify(self, seg):
        result = {"gid": None, "reason": "no body moves recorded", "matches": {}, "near": None,
                  "best": None, "distance": None, "peak": peak(seg, self.cols)}
        if self.bank is None:
            return result
        seq, sz = self._prep(seg), size(seg, self.cols)
        d = distances(seq, sz, self.bank, self.sizes)
        per = {}
        for gid, dist in zip(self.owners, d):
            per[gid] = min(per.get(gid, np.inf), float(dist))
        info = self.info
        result["matches"] = {g: float(np.clip(1 - v / (2 * info[g]["threshold"]), 0, 1)) for g, v in per.items()}
        ranked = sorted(per.items(), key=lambda kv: kv[1])
        gid, dist = ranked[0]
        thr = info[gid]["threshold"]
        result.update(best=gid, distance=dist)
        if dist > thr:
            result["reason"] = "didn't match any body move closely enough"
            if dist < 1.35 * thr:
                result["near"] = gid
            return result
        if result["peak"] < info[gid]["floor"]:
            result["reason"] = "too slow"
            return result
        if sz < info[gid]["size_floor"]:
            result["reason"] = "too small"
            return result
        if self.ignore is not None and distances(seq, sz, *self.ignore).min() < dist:
            result["reason"] = "looked like one of your ignored moves"
            return result
        if len(ranked) > 1:
            other, od = ranked[1]
            if od <= info[other]["threshold"] and dist > 0.85 * od:
                result["reason"] = "too close between two body moves"
                result["ambiguous"] = [gid, other]
                return result
        result["gid"] = gid
        result["reason"] = "matched"
        return result


def _threshold(result):
    m = result["matches"].get(result["gid"], 0.0)
    # matches = 1 - distance / (2 * threshold)  ->  threshold = distance / (2 * (1 - m))
    return result["distance"] / max(2 * (1 - m), 1e-9)


JOIN_GAP = 1.5             # seconds: a move that paused this briefly is still one move (real video:
                           # 20% of moves had a pause, 90% of pauses under 1.1 s)


class MoveJoiner:
    """A move with a pause in it (a wind-up, then the throw) can come out of the segmenter as
    two pieces. A piece that doesn't match anything is kept for JOIN_GAP; if another piece
    starts in time, the two are tried as one move."""

    def __init__(self, gap=JOIN_GAP):
        self.gap = gap
        self.held = None

    def offer(self, seg, classify, max_s=4.0):
        """Returns (result, segment it was decided on). The new piece is tried alone and joined
        to the held run; if both match, the closer match (relative to its threshold) wins, so a
        leftover fidget can't drag a clean move into the wrong one."""
        alone = classify(seg)
        joined, rj = None, None
        if self.held is not None and seg[0, 0] - self.held[-1, 0] <= self.gap:
            joined = np.concatenate([self.held, seg])
            joined = joined[joined[:, 0] >= joined[-1, 0] - max_s]
            rj = classify(joined)
        options = [(r, s) for r, s in ((alone, seg), (rj, joined)) if r is not None and r["gid"] is not None]
        if options:
            self.held = None
            return min(options, key=lambda o: o[0]["distance"] / max(_threshold(o[0]), 1e-9))
        # Nothing matched: keep the whole unmatched run for the next piece.
        self.held = joined if joined is not None else seg
        return alone, seg

    def expire(self, t):
        if self.held is not None and t - self.held[-1, 0] > self.gap:
            self.held = None


def calibrate_start(neutral_frames):
    """Trigger speed from a Neutral recording: comfortably above how much you sway standing."""
    seg = MoveSegmenter(start=1e9)
    energies = []
    for k, f in enumerate(neutral_frames):
        seg.feed(k / 30, np.asarray(f["lm"], dtype=float), f["aspect"])
        energies.append(seg.energy)
    e = np.array(energies[3:])
    if len(e) < 30:
        return START
    return float(np.clip(2.5 * np.percentile(e, 99), 1.0, 3.0))
