"""A simple body holding the two weapons, seen by a webcam, for simulating camera + gyro
together. The weapon hand follows the stick simulation (stick.simulate(..., with_hand=True));
the elbow comes from two-link arm IK; the webcam projects everything and adds the errors
MediaPipe makes: per-frame jitter, extra error and lost frames on fast swings (motion blur),
tracking smoothing, a fixed delay, and a modest frame rate.

World frame as in stick.py: x forward (toward the screen and webcam), y to your left, z up.
"""

from dataclasses import dataclass

import numpy as np

SHOULDER_Z, SHOULDER_Y = 1.45, 0.19
HIP_Z, HIP_Y = 0.95, 0.12
UPPER, FORE = 0.30, 0.28
GUARD = {"melee": np.array([0.32, -0.14, 1.12]),     # right hand, weapon tip up and forward
         "bow": np.array([0.30, 0.22, 1.22])}        # left hand, bow upright
SIDE_OF = {"melee": "right", "bow": "left"}


def _ik(shoulder, hand, outward):
    """Elbow position for a shoulder -> hand reach, elbow pointing down and a bit out."""
    v = hand - shoulder
    d = float(np.clip(np.linalg.norm(v), 0.05, UPPER + FORE - 1e-3))
    e = v / max(np.linalg.norm(v), 1e-9)
    a = (UPPER ** 2 - FORE ** 2 + d ** 2) / (2 * d)
    h = np.sqrt(max(UPPER ** 2 - a ** 2, 0.0))
    pole = np.array([0.0, 0.35 * outward, -1.0])
    p = pole - pole.dot(e) * e
    p /= max(np.linalg.norm(p), 1e-9)
    return shoulder + a * e + h * p


def skeleton(left_hand=None, right_hand=None, yaw_deg=0.0, crouch=0.0):
    """33 MediaPipe landmarks in world coordinates (metres). Hands default to hanging down."""
    z = -crouch
    pts = np.zeros((33, 3))
    ls, rs = np.array([0, SHOULDER_Y, SHOULDER_Z + z]), np.array([0, -SHOULDER_Y, SHOULDER_Z + z])
    lh = np.array(left_hand) if left_hand is not None else np.array([0.02, 0.24, 0.80 + z])
    rh = np.array(right_hand) if right_hand is not None else np.array([0.02, -0.24, 0.80 + z])
    le, re = _ik(ls, lh, +1), _ik(rs, rh, -1)
    head = np.array([0.02, 0, 1.68 + z])
    pts[0] = head + [0.09, 0, -0.02]
    for i, (dy, dz) in zip(range(1, 11), [(0.02, 0.02), (0.035, 0.02), (0.05, 0.02), (-0.02, 0.02),
                                         (-0.035, 0.02), (-0.05, 0.02), (0.075, 0.0), (-0.075, 0.0),
                                         (0.025, -0.05), (-0.025, -0.05)]):
        pts[i] = head + [0.08, dy, dz]
    pts[11], pts[12], pts[13], pts[14], pts[15], pts[16] = ls, rs, le, re, lh, rh
    for side, (wrist, elbow) in enumerate(((lh, le), (rh, re))):
        fwd = (wrist - elbow) / max(np.linalg.norm(wrist - elbow), 1e-9)
        for k, i in enumerate((17, 19, 21) if side == 0 else (18, 20, 22)):
            pts[i] = wrist + fwd * (0.07 - 0.015 * k)
    pts[23], pts[24] = [0, HIP_Y, HIP_Z + z], [0, -HIP_Y, HIP_Z + z]
    for i, y in ((25, 0.11), (26, -0.11)):
        pts[i] = [0.04, y, 0.52 + z * 0.5]
    for i, y in ((27, 0.11), (28, -0.11)):
        pts[i] = [0, y, 0.08]
    for i, y in ((29, 0.11), (30, -0.11)):
        pts[i] = [-0.04, y, 0.03]
    for i, y in ((31, 0.12), (32, -0.12)):
        pts[i] = [0.14, y, 0.02]
    if yaw_deg:
        a = np.radians(yaw_deg)
        r = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
        pts = pts @ r.T
    return pts


def project(points, cam_x=2.4, cam_z=1.3, half_fov=np.radians(36), aspect=16 / 9):
    """World points -> MediaPipe-style normalized image coords (x right, y down) and depth."""
    depth = cam_x - points[:, 0]
    tan = np.tan(half_fov)
    u = 0.5 + points[:, 1] / depth / (2 * tan)
    v = 0.5 - (points[:, 2] - cam_z) / depth / (2 * tan / aspect)
    return np.stack([u, v], axis=1), depth


@dataclass
class Webcam:
    fps: float = 30.0
    lag: float = 0.045          # frames are stamped this long after the moment they show
    jitter: float = 0.006       # metres of per-frame landmark noise on a still hand
    blur: float = 0.012         # extra metres of error per m/s of hand speed
    drop_speed: float = 2.5     # above this hand speed (m/s) the wrist starts getting lost
    drop_rate: float = 0.12     # chance of losing the wrist per extra m/s
    smoothing: float = 0.35     # MediaPipe's tracker lags fast moves a little
    aspect: float = 16 / 9
    yaw_deg: float = 0.0        # player turned away from the camera
    blocked: bool = False       # camera can't see the player at all

    def frames(self, t, hands, rng, t_offset=0.0):
        """t: times (s) of a hand trajectory; hands: {"left"/"right": (n, 3) world positions}.
        Returns [(stamp, landmarks (33, 4) or None, aspect)] at the camera's frame rate."""
        out = []
        if not len(t):
            return out
        start = t[0] + rng.uniform(0, 1 / self.fps)
        times = np.arange(start, t[-1], 1 / self.fps)
        state = {}
        for ft in times:
            if self.blocked:
                out.append((ft + t_offset + self.lag, None, self.aspect))
                continue
            pos, speed = {}, {}
            for side in ("left", "right"):
                h = hands.get(side)
                if h is None:
                    continue
                p = np.array([np.interp(ft, t, h[:, j]) for j in range(3)])
                p_prev = np.array([np.interp(ft - 1 / self.fps, t, h[:, j]) for j in range(3)])
                sp = float(np.linalg.norm(p - p_prev) * self.fps)
                sigma = self.jitter + self.blur * sp
                noisy = p + rng.normal(0, sigma, 3)
                prev = state.get(side)
                if prev is not None:
                    noisy = self.smoothing * prev + (1 - self.smoothing) * noisy
                state[side] = noisy
                pos[side], speed[side] = noisy, sp
            pts = skeleton(pos.get("left"), pos.get("right"), self.yaw_deg)
            pts[[11, 12, 23, 24]] += rng.normal(0, self.jitter * 0.5, (4, 3))
            uv, _ = project(pts, aspect=self.aspect)
            lm = np.zeros((33, 4))
            lm[:, :2] = uv
            lm[:, 3] = 0.95
            for side, wrist, elbow in (("left", 15, 13), ("right", 16, 14)):
                p_lost = np.clip(self.drop_rate * (speed.get(side, 0.0) - self.drop_speed), 0, 0.7)
                if rng.random() < p_lost:
                    lm[wrist, 3] = 0.2
                    lm[[elbow], 3] = 0.6
            out.append((ft + t_offset + self.lag, lm, self.aspect))
        return out
