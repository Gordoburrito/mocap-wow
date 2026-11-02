"""Synthetic poses and swings for tests."""

import numpy as np

ASPECT = 16 / 9
BODY = [11, 12, 13, 14, 15, 16, 19, 20, 0, 23, 24]

POSES = {
    # (left elbow, right elbow, left wrist, right wrist) in normalized image coords
    "neutral": ((0.38, 0.55), (0.62, 0.55), (0.37, 0.70), (0.63, 0.70)),
    "arms_up": ((0.38, 0.25), (0.62, 0.25), (0.37, 0.10), (0.63, 0.10)),
    "bow": ((0.75, 0.40), (0.55, 0.42), (0.90, 0.40), (0.50, 0.36)),
}


def pose(name, rng=None, noise=0.006, shift=(0.0, 0.0), scale=1.0):
    rng = rng or np.random.default_rng(0)
    le, re, lw, rw = POSES[name]
    lm = np.zeros((33, 4))
    pts = {11: (0.40, 0.40), 12: (0.60, 0.40), 13: le, 14: re, 15: lw, 16: rw,
           19: (lw[0], lw[1] - 0.02), 20: (rw[0], rw[1] - 0.02), 0: (0.50, 0.28),
           23: (0.43, 0.75), 24: (0.57, 0.75)}
    for i, (x, y) in pts.items():
        x = 0.5 + (x - 0.5) * scale + shift[0]
        y = 0.5 + (y - 0.5) * scale + shift[1]
        lm[i] = [x + rng.normal(0, noise), y + rng.normal(0, noise), 0.0, 0.95]
    return lm


AXES = {"x+": (1, 0, 0), "x-": (-1, 0, 0), "z+": (0, 0, 1), "y+": (0, 1, 0)}


def swing(axis, t0=0.0, rng=None, rate=200, duration=0.25, peak=8.0, quiet=0.3):
    """Stream of (t, gyro, accel) for one swing: quiet, a half-sine burst, quiet."""
    rng = rng or np.random.default_rng(1)
    v = np.array(AXES[axis], dtype=float) + rng.normal(0, 0.08, 3)
    v /= np.linalg.norm(v)
    out = []
    total = quiet + duration + quiet
    for k in range(int(total * rate)):
        t = k / rate
        g = np.zeros(3)
        if quiet <= t < quiet + duration:
            g = v * peak * np.sin(np.pi * (t - quiet) / duration)
        g = g + rng.normal(0, 0.05, 3)
        a = np.array([0, 0, 9.81]) + rng.normal(0, 0.05, 3)
        out.append((t0 + t, g, a))
    return out


def varied_swing(axis, t0, seed):
    rng = np.random.default_rng(seed)
    return swing(axis, t0, rng, duration=rng.uniform(0.2, 0.3), peak=rng.uniform(6, 10))
