"""Webcam pose recognition: nearest-neighbour matching against your own recorded poses.

Poses are stored as raw MediaPipe landmarks (33 x [x, y, z, visibility]) so the feature
recipe can change later without re-recording. Features are measured relative to the
shoulders and scaled by shoulder width, so standing closer or further away doesn't matter.
"""

import numpy as np

# Landmark index -> weight. Arms and hands carry the pose; legs are often out of frame.
_WEIGHTS = {
    0: 1.0,                       # nose (head tilt / lean)
    11: 1.0, 12: 1.0,             # shoulders
    13: 1.5, 14: 1.5,             # elbows
    15: 2.0, 16: 2.0,             # wrists
    19: 1.5, 20: 1.5,             # index fingers
    23: 0.5, 24: 0.5,             # hips
    25: 0.5, 26: 0.5,             # knees
    27: 0.5, 28: 0.5,             # ankles
}
IDX = np.array(sorted(_WEIGHTS))
WEIGHTS = np.array([_WEIGHTS[i] for i in IDX])
VISIBLE = 0.5
DEFAULT_THRESHOLD = 0.30          # weighted RMS distance, in shoulder widths
MIN_WEIGHT = 6.0                  # need shoulders + some arm landmarks to judge a pose
# A pose can be judged on one body part only ("left arm up", whatever the rest of you does).
SCOPES = {"whole": None, "left_arm": (13, 15, 19), "right_arm": (14, 16, 20),
          "arms": (13, 14, 15, 16, 19, 20), "left_leg": (23, 25, 27), "right_leg": (24, 26, 28),
          "legs": (23, 24, 25, 26, 27, 28), "head": (0,)}


def scope_weights(scope):
    """(weights, minimum visible weight) for judging a pose on one scope."""
    joints = SCOPES.get(scope)
    if joints is None:
        return WEIGHTS, MIN_WEIGHT
    w = np.array([_WEIGHTS[i] if i in joints else 0.0 for i in IDX])
    return w, min(MIN_WEIGHT, 0.6 * float(w.sum()))


def features(landmarks, aspect):
    """landmarks: (33, 4) array in normalized image coords. Returns (points (K,2), vis (K,))."""
    lm = np.asarray(landmarks, dtype=float)
    if lm.shape != (33, 4) or lm[11, 3] < VISIBLE or lm[12, 3] < VISIBLE:
        return None
    xy = np.stack([lm[:, 0] * aspect, lm[:, 1]], axis=1)
    center = (xy[11] + xy[12]) / 2
    scale = np.linalg.norm(xy[11] - xy[12])
    if scale < 1e-3:
        return None
    return (xy[IDX] - center) / scale, lm[IDX, 3]


def distances(pts, vis, bank_pts, bank_vis, weights=WEIGHTS, min_weight=MIN_WEIGHT):
    """Weighted RMS distance from one pose to each pose in a bank. Returns (N,) array."""
    if len(bank_pts) == 0:
        return np.empty(0)
    mask = (bank_vis > VISIBLE) & (vis > VISIBLE)
    w = weights * mask
    wsum = w.sum(axis=1)
    d2 = ((bank_pts - pts) ** 2).sum(axis=2)
    out = np.sqrt((w * d2).sum(axis=1) / np.maximum(wsum, 1e-9))
    out[wsum < min_weight] = np.inf
    return out


def _bank(samples):
    feats = [f for f in (features(s["lm"], s["aspect"]) for s in samples) if f is not None]
    if not feats:
        return np.empty((0, len(IDX), 2)), np.empty((0, len(IDX)))
    return np.stack([f[0] for f in feats]), np.stack([f[1] for f in feats])


def _knn(d, k=3):
    if len(d) == 0:
        return np.inf
    k = min(k, len(d))
    return float(np.mean(np.partition(d, k - 1)[:k]))


def _knn_bank(pts, vis, bank_pts, bank_vis, k=3, ws=(WEIGHTS, MIN_WEIGHT)):
    return _knn(distances(pts, vis, bank_pts, bank_vis, *ws), k)


def separation(bank_a, bank_b, max_samples=40, ws=(WEIGHTS, MIN_WEIGHT)):
    """Typical distance from poses in bank A to the nearest poses in bank B."""
    pts, vis = bank_a
    if not len(pts) or not len(bank_b[0]):
        return np.inf
    idx = np.linspace(0, len(pts) - 1, min(len(pts), max_samples)).round().astype(int)
    return float(np.median([_knn_bank(pts[i], vis[i], *bank_b, ws=ws) for i in idx]))


MIN_POSE_THRESHOLD, MAX_POSE_THRESHOLD = 0.22, 0.7


class PoseClassifier:
    def __init__(self):
        self.gestures = []      # (gid, pts, vis, threshold)
        self.neutral = (np.empty((0, len(IDX), 2)), np.empty((0, len(IDX))))
        self.calibration = {}
        self.neutral_spread = 0.0

    def set_data(self, gestures, neutral_samples, sensitivity=None):
        """gestures: iterable of (gid, samples, threshold[, scope]). A threshold of None means
        calibrate: half the distance to the nearest other pose (or to neutral), so poses that are
        far from everything else tolerate more natural variation. A scope ("left_arm"...) judges
        the pose on that body part only."""
        sensitivity = sensitivity or {}
        self.neutral = _bank(neutral_samples)
        self.neutral_spread = self._self_spread(self.neutral)
        banks = []
        for item in gestures:
            gid, samples, threshold = item[:3]
            scope = item[3] if len(item) > 3 else "whole"
            pts, vis = _bank(samples)
            if len(pts):
                banks.append((gid, pts, vis, threshold, scope_weights(scope)))
        self.gestures, self.calibration = [], {}
        for gid, pts, vis, threshold, ws in banks:
            nearest, nearest_gid = separation((pts, vis), self.neutral, ws=ws), "neutral"
            for other, opts, ovis, _, _ in banks:
                if other != gid:
                    d = separation((pts, vis), (opts, ovis), ws=ws)
                    if d < nearest:
                        nearest, nearest_gid = d, other
            if threshold is None:
                base = 0.5 * nearest if np.isfinite(nearest) else DEFAULT_THRESHOLD
                threshold = float(np.clip(base, MIN_POSE_THRESHOLD, MAX_POSE_THRESHOLD))
                threshold *= sensitivity.get(gid, 1.0)
            self.calibration[gid] = {"threshold": threshold,
                                     "nearest": nearest if np.isfinite(nearest) else None,
                                     "nearest_gid": nearest_gid if np.isfinite(nearest) else None}
            self.gestures.append((gid, pts, vis, threshold, ws))

    @staticmethod
    def _self_spread(bank, max_samples=40):
        """How much a bank wobbles: typical distance from one frame to its nearest others."""
        pts, vis = bank
        if len(pts) < 4:
            return 0.0
        idx = np.linspace(0, len(pts) - 1, min(len(pts), max_samples)).round().astype(int)
        out = []
        for i in idx:
            d = distances(pts[i], vis[i], pts, vis)
            d[i] = np.inf
            out.append(_knn(d))
        return float(np.median(out))

    def away_from_neutral(self, feat):
        """True when the body is clearly in some pose, not just standing around."""
        if feat is None or not len(self.neutral[0]):
            return False
        d = float(np.min(distances(feat[0], feat[1], *self.neutral)))
        return d > max(0.25, 2.5 * self.neutral_spread)

    def classify(self, feat):
        """Returns (best gid or None, {gid: match 0..1}). Match > 0.5 means within threshold."""
        if feat is None or not self.gestures:
            return None, {}
        pts, vis = feat
        matches, best, best_ratio = {}, None, 1.0
        for gid, bank_pts, bank_vis, threshold, ws in self.gestures:
            neutral_d = _knn(distances(pts, vis, *self.neutral, *ws))
            d = _knn(distances(pts, vis, bank_pts, bank_vis, *ws))
            ratio = d / threshold
            matches[gid] = float(np.clip(1 - ratio / 2, 0, 1)) if np.isfinite(d) else 0.0
            # Must be inside its threshold and closer to the pose than to "doing nothing".
            if ratio < best_ratio and d < neutral_d:
                best, best_ratio = gid, ratio
        return best, matches


class PoseTrigger:
    """Turns the per-frame best pose into single latch/release events.

    A pose latches after being held for its hold time, fires once, and must be dropped for
    `release_s` before anything else can latch. Holding a pose never re-fires it.
    """

    def __init__(self, release_s=0.2):
        self.release_s = release_s
        self.candidate = None
        self.since = 0.0
        self.latched = None
        self.lost_since = None

    def reset(self):
        released = self.latched
        self.candidate = self.latched = self.lost_since = None
        return [("release", released)] if released else []

    def update(self, gid, t, hold_s):
        events = []
        if self.latched is not None:
            if gid == self.latched:
                self.lost_since = None
                return events
            if self.lost_since is None:
                self.lost_since = t
            if t - self.lost_since < self.release_s:
                return events
            events.append(("release", self.latched))
            self.latched = self.lost_since = None
            self.candidate = None
        if gid is None:
            self.candidate = None
            return events
        if gid != self.candidate:
            self.candidate, self.since = gid, t
        if t - self.since >= hold_s(gid):
            self.latched, self.lost_since = gid, None
            events.append(("latch", gid))
        return events
