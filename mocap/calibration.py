"""Calibration for IMU weapons.

1. Gyro bias: hold still ~2 s; the average gyro reading is subtracted from then on. While
   playing, any stretch where the weapon is clearly still keeps nudging the estimate (the same
   idea as JoyShockMapper/Steam's continuous calibration), so temperature drift is absorbed.
2. Mount alignment: point the weapon at the screen and hold, then point it straight up and
   hold. The two gravity directions pin down how the Joy-Con sits on the pipe. If you remount
   it at a different angle, repeat the two poses and your recordings keep working, because
   new readings are rotated back into the frame they were recorded in.
3. Trigger strength: from how much the weapon jiggles while you stand in guard, so motions
   start above your own noise floor.
"""

from collections import deque

import numpy as np

STILL_GYRO = 0.25          # rad/s: below this (after bias) the weapon counts as still
STILL_SECONDS = 1.5


def kabsch(src, dst):
    """Best rotation R with R @ src[i] ~= dst[i] (rows are vectors)."""
    src = np.asarray(src, dtype=float)
    dst = np.asarray(dst, dtype=float)
    h = src.T @ dst
    u, _, vt = np.linalg.svd(h)
    d = np.sign(np.linalg.det(vt.T @ u.T))
    return vt.T @ np.diag([1.0, 1.0, d]) @ u.T


def mount_alignment(ref_forward, ref_up, new_forward, new_up):
    """Rotation taking readings from the new mounting into the reference mounting.

    Each argument is the mean accelerometer vector (gravity) measured while holding that pose.
    """
    def frame(a, b):
        a = np.asarray(a, float) / np.linalg.norm(a)
        b = np.asarray(b, float) / np.linalg.norm(b)
        c = np.cross(a, b)
        c /= max(np.linalg.norm(c), 1e-9)
        return np.stack([a, b, c])
    return kabsch(frame(new_forward, new_up), frame(ref_forward, ref_up))


def pose_quality(forward, up):
    """Angle between the two calibration poses' gravity readings. Near 90 deg is ideal; under
    30 deg the alignment is unreliable and the user should be asked to redo it."""
    a = np.asarray(forward, float)
    b = np.asarray(up, float)
    cos = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.degrees(np.arccos(np.clip(cos, -1, 1))))


class StillCapture:
    """Collects samples while the user holds a weapon still, then reports mean gyro/accel.
    `ok` is False if it moved too much to trust."""

    def __init__(self, seconds=2.0):
        self.seconds = seconds
        self.gyro, self.accel, self.t0 = [], [], None

    def feed(self, t, gyro, accel):
        if self.t0 is None:
            self.t0 = t
        self.gyro.append(np.asarray(gyro, float))
        self.accel.append(np.asarray(accel, float))
        return t - self.t0 >= self.seconds

    def result(self):
        g = np.array(self.gyro)
        a = np.array(self.accel)
        spread = float(np.linalg.norm(g - g.mean(axis=0), axis=1).max()) if len(g) else np.inf
        return {"gyro_bias": g.mean(axis=0).tolist(), "gravity": a.mean(axis=0).tolist(),
                "ok": len(g) > 20 and spread < 0.6, "jitter": spread}


class DeviceCalibration:
    """Per-weapon correction applied to every raw sample before recognition."""

    def __init__(self, data=None):
        data = data or {}
        self.bias = np.asarray(data.get("gyro_bias", [0, 0, 0]), float)
        self.align = np.asarray(data.get("align", np.eye(3).tolist()), float)
        self.ref_forward = data.get("ref_forward")
        self.ref_up = data.get("ref_up")
        self.trigger = data.get("trigger")
        self.manual_align = np.asarray(data.get("manual_align", self.align.tolist()), float)
        self._still_t0 = None
        self._still = []
        self._raw = deque(maxlen=800)       # (t, raw gyro) for camera-confirmed stillness
        self.auto = AutoAlign()
        self.bias_log = deque(maxlen=200)   # (t, |bias| rad/s, source) for the metrics page

    def to_dict(self):
        return {"gyro_bias": self.bias.tolist(), "align": self.align.tolist(),
                "manual_align": self.manual_align.tolist(),
                "ref_forward": self.ref_forward, "ref_up": self.ref_up, "trigger": self.trigger}

    @property
    def auto_angle(self):
        """Degrees the automatic mount correction has moved away from the last calibration."""
        return rotation_angle(self.align @ self.manual_align.T)

    def apply(self, t, gyro, accel):
        """Bias-correct and align one sample. Also keeps the bias fresh when held still."""
        raw = np.asarray(gyro, float)
        self._raw.append((t, raw))
        gyro = raw - self.bias
        accel = np.asarray(accel, float)
        self._track_still(t, gyro)
        return self.align @ gyro, self.align @ accel

    def _track_still(self, t, gyro):
        if np.linalg.norm(gyro) < STILL_GYRO:
            if self._still_t0 is None:
                self._still_t0, self._still = t, []
            self._still.append(gyro)
            if t - self._still_t0 >= STILL_SECONDS and len(self._still) > 50:
                self.bias = self.bias + 0.2 * np.mean(self._still, axis=0)
                self.bias_log.append((t, float(np.linalg.norm(self.bias)), "gyro"))
                self._still_t0, self._still = None, []
        else:
            self._still_t0, self._still = None, []

    def camera_still(self, t0, t1, max_std=0.08, rate=0.3):
        """The webcam saw the weapon hand hold still from t0 to t1 (sensor time), so the gyro's
        average over that stretch is its zero offset. Returns the correction (rad/s) or None.

        Unlike the gyro-only tracker this works however large the offset is, and it can't
        mistake a slow deliberate movement for drift: the camera would have seen the hand move.
        The spread check still refuses a stretch where the pipe was twisting in place. (The
        engine also waits for a swing to settle first: the slow end of a swing back to guard
        looks still to the camera.)"""
        g = np.array([v for t, v in self._raw if t0 <= t <= t1])
        if len(g) < 20:
            return None
        if float(g.std(axis=0).max()) > max_std:
            return None
        change = g.mean(axis=0) - self.bias
        self.bias = self.bias + rate * change
        self.bias_log.append((t1, float(np.linalg.norm(self.bias)), "camera"))
        return float(np.linalg.norm(change))

    def set_reference(self, forward, up):
        """First-time mount calibration: remember where gravity points in both poses."""
        self.ref_forward = list(map(float, forward))
        self.ref_up = list(map(float, up))
        self.align = np.eye(3)
        self.manual_align = np.eye(3)
        self.auto = AutoAlign()

    def realign(self, forward, up):
        """After remounting: rotate new readings back into the recorded frame."""
        if self.ref_forward is None:
            self.set_reference(forward, up)
            return np.eye(3)
        self.align = mount_alignment(self.ref_forward, self.ref_up, forward, up)
        self.manual_align = self.align.copy()
        self.auto = AutoAlign()
        return self.align

    def learn_alignment(self, live, template, t=None):
        """A move was recognized with confidence: compare its gyro with your recorded take and
        nudge the mount correction if the Joy-Con seems to have turned on the pipe.
        Returns the correction applied (degrees) or 0."""
        step = self.auto.observe(live, template)
        if step is None:
            return 0.0
        new = step @ self.align
        if rotation_angle(new @ self.manual_align.T) > AutoAlign.MAX_TOTAL:
            return 0.0                      # that far off means recalibrate, not creep
        self.align = new
        return rotation_angle(step)


def rotation_angle(r):
    """Angle of a rotation matrix, in degrees."""
    return float(np.degrees(np.arccos(np.clip((np.trace(r) - 1) / 2, -1, 1))))


def dtw_pairs(a, b, band=0.25):
    """Index pairs (i, j) along the best DTW alignment of sequences a and b."""
    n, m = len(a), len(b)
    cost = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=2)
    r = max(1, int(np.ceil(band * max(n, m))))
    acc = np.full((n + 1, m + 1), np.inf)
    acc[0, 0] = 0
    for i in range(1, n + 1):
        for j in range(max(1, i - r), min(m, i + r) + 1):
            acc[i, j] = cost[i - 1, j - 1] + min(acc[i - 1, j], acc[i - 1, j - 1], acc[i, j - 1])
    i, j, out = n, m, []
    while i > 0 and j > 0:
        out.append((i - 1, j - 1))
        k = int(np.argmin([acc[i - 1, j - 1], acc[i - 1, j], acc[i, j - 1]]))
        i, j = (i - 1, j - 1) if k == 0 else (i - 1, j) if k == 1 else (i, j - 1)
    return out[::-1]


class AutoAlign:
    """Learns, from moves you make anyway, how far the Joy-Con has turned on the pipe.

    Each confidently recognized move pairs the live gyro with your recorded take (aligned in
    time with DTW). Their cross-covariance, summed over recent moves, gives the best rotation
    from live to recorded (Kabsch). One move only pins down its own turning axis, so nothing is
    applied until moves about at least two different axes agree. Steps are small and capped.
    """

    MAX_STEP = 5.0          # degrees per update
    MIN_ANGLE = 4.0         # don't chase noise: takes naturally vary by several degrees
    MAX_TOTAL = 75.0        # beyond this, ask for a recalibration instead
    DECAY = 0.9             # about the last 10 moves count
    MIN_MOVES = 5
    DAMPING = 0.5           # apply half of what's estimated each time

    def __init__(self):
        self.h = np.zeros((3, 3))
        self.n = 0

    def observe(self, live, template):
        live = np.asarray(live, float)[:, :3]
        template = np.asarray(template, float)[:, :3]
        pairs = dtw_pairs(live, template)
        a = np.array([live[i] for i, _ in pairs])
        b = np.array([template[j] for _, j in pairs])
        self.h = self.DECAY * self.h + a.T @ b / len(pairs)
        self.n += 1
        if self.n < self.MIN_MOVES:
            return None
        u, sv, vt = np.linalg.svd(self.h)
        if sv[0] <= 1e-9 or sv[1] / sv[0] < 0.2:
            return None                     # all recent moves turned about one axis
        d = np.sign(np.linalg.det(vt.T @ u.T))
        r = vt.T @ np.diag([1.0, 1.0, d]) @ u.T
        angle = rotation_angle(r)
        if angle < self.MIN_ANGLE:
            return None
        r = _partial(r, min(self.DAMPING, self.MAX_STEP / angle))
        # Readings will now arrive rotated by r, so rotate what we've accumulated to match.
        self.h = r @ self.h
        return r


def _partial(r, fraction):
    """A fraction of rotation r (same axis, smaller angle)."""
    angle = np.arccos(np.clip((np.trace(r) - 1) / 2, -1, 1))
    if angle < 1e-9:
        return np.eye(3)
    axis = np.array([r[2, 1] - r[1, 2], r[0, 2] - r[2, 0], r[1, 0] - r[0, 1]]) / (2 * np.sin(angle))
    a = angle * fraction
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + np.sin(a) * k + (1 - np.cos(a)) * (k @ k)


def trigger_from_noise(energies, floor=2.0, factor=4.0, ceiling=6.0):
    """Trigger strength from motion energy samples recorded while standing in guard."""
    e = np.asarray(energies, float)
    if len(e) < 20:
        return None
    return float(np.clip(factor * np.percentile(e, 99), floor, ceiling))
