"""Webcam + gyro, together.

The Joy-Con's gyro sees exactly how the weapon turns, 200 times a second, but not where your
hand is: a high slash and a low slash turn the weapon the same way. The webcam sees where your
hands are, but only ~30 times a second, blurry on fast swings, and it can't see the pipe, how
far your hand is from the screen, or the pipe twisting. Each covers the other's blind spots:

- Recognition (motion.SwingClassifier): every motion take also keeps the path your weapon
  hand took as the camera saw it. Matching uses both, so the same swing at head height and at
  the waist can be two different moves, and gyro lookalikes are told apart by where the hand went.
- Keeping the gyro calibrated from your body:
  - Bias: when the camera sees your weapon hand and shoulders hold still, the gyro's reading
    is its zero offset (calibration.DeviceCalibration.camera_still). This works even when the
    offset is too large for the gyro to notice stillness by itself, and slow movements the
    gyro can't tell from drift aren't absorbed.
  - Slipped mount: when a move is recognized and the camera agrees, the rotation between the
    live gyro and your recorded takes shows whether the Joy-Con has turned on the pipe. It's
    corrected a little at a time (calibration.AutoAlign).
- Which hand holds which weapon: the wrist whose speed rises and falls with the Joy-Con's gyro.
- Camera delay: found the same way, by lining up gyro bursts with wrist speed.

All times here are host times (time.monotonic()); the engine converts Joy-Con sensor time.
"""

from collections import deque

import numpy as np

PATH_N = 16             # points per hand path
PATH_PRE = 0.10         # seconds of camera before the motion started (the wind-up position)
MIN_VIS = 0.5
DEFAULT_LAG = 0.04      # camera frames are stamped this much later than the matching IMU samples
STILL_LIMIT = 0.12      # shoulder widths a "still" hand may wander (MediaPipe on real video:
                        # median 0.06 over half a second); the gyro check backs this up
REST_SPEED = 0.3        # shoulder widths/s: slower than this, the hand counts as resting
REST_ALPHA = 0.02       # how fast the remembered resting spot follows you (~1.5 s)
# Landmark indices (MediaPipe pose): shoulders, elbows, wrists.
L_SH, R_SH, L_EL, R_EL, L_WR, R_WR = 11, 12, 13, 14, 15, 16
SIDES = ("left", "right")
_TRACKED = (L_WR, L_EL, R_WR, R_EL)
_RAW = (L_SH, R_SH, L_EL, R_EL, L_WR, R_WR)


def _side_cols(side):
    return (0, 1) if side == "left" else (2, 3)        # (wrist, elbow) in the tracked points


class CameraTrack:
    """The last few seconds of webcam landmarks, reduced to what fusion needs: both arms in
    body units (shoulder widths from the shoulder midpoint, x to the image's right, y up) and
    the raw image positions (for stillness)."""

    def __init__(self, seconds=5.0):
        self.seconds = seconds
        self._t = deque()
        self._pts = deque()             # (4, 2) wrist L, elbow L, wrist R, elbow R; NaN if unseen
        self._raw = deque()             # (6, 2) shoulders, elbows, wrists in image / shoulder width
        self._rest = deque()            # (2,) remembered resting x of each wrist at that frame
        self._sw = deque(maxlen=90)
        self._rest_x = [np.nan, np.nan]
        self.lag = DEFAULT_LAG

    @property
    def latest(self):
        return self._t[-1] if self._t else -np.inf

    def clear(self):
        self._t.clear()
        self._pts.clear()
        self._raw.clear()
        self._rest.clear()

    def add(self, t, landmarks, aspect):
        pts = np.full((4, 2), np.nan)
        raw = np.full((6, 2), np.nan)
        if landmarks is not None:
            lm = np.asarray(landmarks, dtype=float)
            xy = np.stack([lm[:, 0] * aspect, lm[:, 1]], axis=1)
            vis = lm[:, 3]
            if vis[L_SH] >= MIN_VIS and vis[R_SH] >= MIN_VIS:
                sw = float(np.linalg.norm(xy[L_SH] - xy[R_SH]))
                if sw > 1e-3:
                    # Median over ~3 s: turning your body shrinks the shoulders on camera, and
                    # that shouldn't read as your hands moving outward.
                    self._sw.append(sw)
                    scale = float(np.median(self._sw))
                    mid = (xy[L_SH] + xy[R_SH]) / 2
                    for k, i in enumerate(_TRACKED):
                        if vis[i] >= MIN_VIS:
                            d = (xy[i] - mid) / scale
                            pts[k] = (d[0], -d[1])
                    for k, i in enumerate(_RAW):
                        if vis[i] >= MIN_VIS:
                            raw[k] = xy[i] / scale
        # Where each hand rests between moves (your guard), sideways. Paths measure sideways
        # position from there, so turning your body a little doesn't shift every move.
        if self._t:
            dt = max(t - self._t[-1], 1e-3)
            prev = self._pts[-1]
            for side, k in ((0, 0), (1, 2)):
                if np.isnan(pts[k, 0]):
                    continue
                if np.isnan(self._rest_x[side]):
                    self._rest_x[side] = pts[k, 0]
                elif not np.isnan(prev[k, 0]) and np.linalg.norm(pts[k] - prev[k]) / dt < REST_SPEED:
                    self._rest_x[side] += REST_ALPHA * (pts[k, 0] - self._rest_x[side])
        self._t.append(float(t))
        self._pts.append(pts)
        self._raw.append(raw)
        self._rest.append(tuple(self._rest_x))
        while self._t and t - self._t[0] > self.seconds:
            self._t.popleft()
            self._pts.popleft()
            self._raw.popleft()
            self._rest.popleft()

    def _window(self, t0, t1, rest=False):
        if not self._t:
            empty = (np.empty(0), np.empty((0, 4, 2)), np.empty((0, 6, 2)))
            return empty + (np.empty((0, 2)),) if rest else empty
        ts = np.fromiter(self._t, float, len(self._t))
        m = (ts >= t0) & (ts <= t1)
        idx = np.flatnonzero(m)
        pts = np.array([self._pts[i] for i in idx]) if len(idx) else np.empty((0, 4, 2))
        raw = np.array([self._raw[i] for i in idx]) if len(idx) else np.empty((0, 6, 2))
        if rest:
            rx = np.array([self._rest[i] for i in idx]) if len(idx) else np.empty((0, 2))
            return ts[m], pts, raw, rx
        return ts[m], pts, raw

    def covers(self, t_end):
        """Has a frame arrived from after the motion ended (allowing for camera delay)?"""
        return self.latest >= t_end + self.lag

    def path(self, side, t0, t1, n=PATH_N):
        """The weapon hand's path over a motion that ran from t0 to t1 (host time), as an
        (n, 2) array of wrist positions in body units. None if the camera didn't see it well."""
        a, b = t0 - PATH_PRE + self.lag, t1 + self.lag
        ts, pts, _, rest = self._window(a - 0.07, b + 0.07, rest=True)
        if not len(ts):
            return None
        w = pts[:, _side_cols(side)[0]].copy()
        rx = rest[0, 0 if side == "left" else 1]
        if np.isfinite(rx):
            w[:, 0] -= rx
        ok = ~np.isnan(w[:, 0])
        inside = (ts >= a) & (ts <= b)
        if ok.sum() < 3 or (inside.sum() and ok[inside].mean() < 0.5):
            return None
        if ts[ok][0] > a + 0.1 or ts[ok][-1] < b - 0.1:
            return None             # the hand vanished at one end of the move
        grid = np.linspace(a, b, n)
        return np.stack([np.interp(grid, ts[ok], w[ok, j]) for j in range(2)], axis=1)

    def still(self, side, t0, t1, limit=STILL_LIMIT, min_fps=10):
        """True if the camera saw the weapon hand, its elbow and both shoulders hold still."""
        ts, _, raw = self._window(t0 + self.lag, t1 + self.lag)
        if len(ts) < min_fps * (t1 - t0):
            return False
        cols = [0, 1] + ([2, 4] if side == "left" else [3, 5])
        r = raw[:, cols]
        seen = ~np.isnan(r).any(axis=(1, 2))
        if seen.mean() < 0.8 or seen.sum() < 5:
            return False
        r = r[seen]
        return float(np.linalg.norm(r - np.median(r, axis=0), axis=2).max()) < limit

    def wrist_speed(self, side, t0, t1):
        """(times, speed in shoulder widths per second) of one wrist, from the camera."""
        ts, pts, _ = self._window(t0, t1)
        w = pts[:, _side_cols(side)[0]]
        ok = ~np.isnan(w[:, 0])
        if ok.sum() < 4:
            return None, None
        ts, w = ts[ok], w[ok]
        dt = np.diff(ts)
        good = dt > 1e-3
        speed = np.linalg.norm(np.diff(w, axis=0), axis=1)[good] / dt[good]
        mids = ((ts[1:] + ts[:-1]) / 2)[good]
        return mids, speed


def path_size(path):
    """How far the hand travelled along a path (shoulder widths)."""
    p = np.asarray(path, dtype=float)
    return float(np.linalg.norm(np.diff(p, axis=0), axis=1).sum()) if len(p) > 1 else 0.0


def _corr(a, b):
    if len(a) < 4 or np.std(a) < 1e-9 or np.std(b) < 1e-9:
        return np.nan
    return float(np.corrcoef(a, b)[0, 1])


class HandFinder:
    """Which wrist holds this Joy-Con: whichever one's speed follows the gyro."""

    def __init__(self, needed=3, margin=0.15):
        self.needed = needed
        self.margin = margin
        self.scores = {s: deque(maxlen=8) for s in SIDES}

    def observe(self, imu_t, gyro_mag, track):
        """imu_t: host times of a motion's samples; gyro_mag: |gyro| at those times."""
        t0, t1 = imu_t[0] - 0.15, imu_t[-1] + 0.15
        out = {}
        for side in SIDES:
            ts, speed = track.wrist_speed(side, t0 + track.lag, t1 + track.lag)
            if ts is None:
                continue
            c = _corr(np.interp(ts - track.lag, imu_t, gyro_mag), speed)
            if np.isfinite(c):
                self.scores[side].append(c)
                out[side] = c
        return out

    @property
    def side(self):
        means = {s: np.mean(v) for s, v in self.scores.items() if len(v) >= self.needed}
        if len(means) < 2:
            return None
        best = max(means, key=means.get)
        other = min(means, key=means.get)
        return best if means[best] - means[other] >= self.margin else None

    def public(self):
        return {s: round(float(np.mean(v)), 2) if v else None for s, v in self.scores.items()}


class LagFinder:
    """Estimates how far camera timestamps run behind the IMU, by lining up gyro bursts with
    wrist speed over recent motions."""

    LAGS = np.round(np.arange(-0.10, 0.451, 0.01), 3)

    def __init__(self, keep=12):
        self.curves = deque(maxlen=keep)

    def observe(self, imu_t, gyro_mag, track, side):
        t0, t1 = imu_t[0] - 0.2, imu_t[-1] + 0.4
        ts, speed = track.wrist_speed(side, t0, t1)
        if ts is None or len(ts) < 6:
            return
        curve = np.array([_corr(np.interp(ts - lag, imu_t, gyro_mag, left=gyro_mag[0],
                                          right=gyro_mag[-1]), speed) for lag in self.LAGS])
        if np.isfinite(curve).sum() > len(curve) // 2:
            self.curves.append(np.nan_to_num(curve, nan=-1.0))

    @property
    def lag(self):
        if len(self.curves) < 4:
            return None
        return float(self.LAGS[int(np.argmax(np.mean(self.curves, axis=0)))])


class ClockMap:
    """Maps a Joy-Con's own sensor clock onto the host clock. Each sample arrives some
    (variable) time after it was taken, so the smallest host-minus-sensor gap over the last
    couple of seconds is the offset. Using a window (not all time) means a Joy-Con that
    reconnects and restarts its clock is followed within ~2 s."""

    def __init__(self, window=2.0):
        self.window = window
        self.offset = None
        self._gaps = deque()            # (host time, gap)

    def feed(self, sensor_t, host_t):
        gap = host_t - sensor_t
        while self._gaps and self._gaps[-1][1] >= gap:
            self._gaps.pop()            # keep a running minimum (monotonic queue)
        self._gaps.append((host_t, gap))
        while host_t - self._gaps[0][0] > self.window:
            self._gaps.popleft()
        self.offset = self._gaps[0][1]
        return self.offset

    def host(self, sensor_t):
        return sensor_t + (self.offset or 0.0)

    def sensor(self, host_t):
        return host_t - (self.offset or 0.0)
