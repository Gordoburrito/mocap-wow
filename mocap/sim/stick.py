"""Physics-based synthetic IMU data for a Joy-Con strapped to the tip of a stick.

World frame: x forward (toward the screen), y left, z up. Stick frame: x runs along the
stick from the hand to the tip, where the Joy-Con sits. A gesture is a list of strokes; each
stroke rotates the stick (about a world axis or about its own axis) and/or moves the hand,
with a minimum-jerk speed profile like a real arm movement.

The gyro reads the stick's angular velocity in its own frame (the same anywhere on the
stick). The accelerometer reads the specific force at the tip: linear acceleration of the tip
(including centripetal/tangential terms from rotation) minus gravity, in the stick frame.
"""

from dataclasses import dataclass, field, replace

import numpy as np

GRAVITY = np.array([0.0, 0.0, -9.81])
SIM_RATE = 1000
OUT_RATE = 200
GYRO_RANGE = np.radians(2000)
ACCEL_RANGE = 8 * 9.81


def rot(axis, angle):
    axis = np.asarray(axis, dtype=float)
    axis = axis / np.linalg.norm(axis)
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + np.sin(angle) * k + (1 - np.cos(angle)) * (k @ k)


def min_jerk(s):
    return 10 * s ** 3 - 15 * s ** 4 + 6 * s ** 5


@dataclass
class Stroke:
    duration: float                     # seconds
    axis: tuple = (0, 0, 1)             # rotation axis
    angle: float = 0.0                  # degrees
    frame: str = "world"                # "world" or "stick" (stick = twist about its own axes)
    move: tuple = (0.0, 0.0, 0.0)       # hand displacement in metres (world)


@dataclass
class Stick:
    name: str
    length: float                       # hand to Joy-Con, metres
    rest: np.ndarray = field(default_factory=lambda: np.eye(3))   # stick -> world at rest
    mount: np.ndarray = field(default_factory=lambda: np.eye(3))  # Joy-Con frame -> stick frame
    pivot_back: float = 0.0             # rotations pivot this far behind the hand (the arm swings)


def pitch_up(deg):
    """Rest orientation with the stick tilted up from pointing forward by `deg`."""
    return rot((0, 1, 0), -np.radians(deg))


MELEE = Stick("melee", 0.70, rest=pitch_up(45))
BOW = Stick("bow", 0.60, rest=pitch_up(90))


@dataclass
class Variation:
    speed: float = 1.0          # >1 = faster
    size: float = 1.0           # angle and distance scale
    axis_jitter_deg: float = 0.0
    start_jitter_deg: float = 0.0


def human_variation(rng, spread=1.0):
    return Variation(
        speed=float(np.exp(rng.normal(0, 0.15 * spread))),
        size=float(np.clip(rng.normal(1.0, 0.12 * spread), 0.6, 1.4)),
        axis_jitter_deg=float(abs(rng.normal(0, 8 * spread))),
        start_jitter_deg=float(abs(rng.normal(0, 8 * spread))),
    )


def _jitter_axis(axis, deg, rng):
    if deg <= 0:
        return np.asarray(axis, dtype=float)
    perp = rng.normal(size=3)
    return rot(perp, np.radians(deg)) @ np.asarray(axis, dtype=float)


def simulate(strokes, stick, rng, variation=None, pre=0.35, post=0.45, gyro_noise=0.03,
             accel_noise=0.08, gyro_bias=None, sway=0.06, rate=OUT_RATE, with_hand=False):
    """Returns (t, gyro rad/s (n,3), accel m/s^2 (n,3)) as the Joy-Con would report them.
    with_hand=True also returns the hand's displacement from where it started (n, 3), world."""
    v = variation or Variation()
    start = stick.rest
    if v.start_jitter_deg > 0:
        start = rot(rng.normal(size=3), np.radians(v.start_jitter_deg)) @ start
    dt = 1.0 / SIM_RATE
    rs, ps = [], []
    r, p = start.copy(), np.zeros(3)

    def hold(seconds):
        for _ in range(int(seconds * SIM_RATE)):
            rs.append(r.copy())
            ps.append(p.copy())

    hold(pre)
    for stroke in strokes:
        dur = max(0.05, stroke.duration / v.speed)
        n = max(2, int(dur * SIM_RATE))
        axis = _jitter_axis(stroke.axis, v.axis_jitter_deg, rng)
        angle = np.radians(stroke.angle) * v.size
        move = np.asarray(stroke.move, dtype=float) * v.size
        if np.linalg.norm(move) > 0 and v.axis_jitter_deg > 0:
            move = _jitter_axis(move, v.axis_jitter_deg * 0.6, rng)
        r0, p0 = r.copy(), p.copy()
        for k in range(1, n + 1):
            f = min_jerk(k / n)
            step = rot(axis, f * angle) if angle else np.eye(3)
            r = step @ r0 if stroke.frame == "world" else r0 @ step
            p = p0 + f * move
            rs.append(r.copy())
            ps.append(p.copy())
    hold(post)

    rs = np.array(rs)
    ps = np.array(ps)
    if stick.pivot_back:
        # The arm swings with the weapon: the hand rides on a lever from a pivot behind it.
        lever = stick.pivot_back * (rs[:, :, 0] - rs[0, :, 0])
        ps = ps + lever
    # Angular velocity (world) from consecutive orientations, then into the stick frame.
    rel = np.einsum("nij,nkj->nik", rs[1:], rs[:-1])           # R_{k+1} R_k^T
    w_world = np.stack([rel[:, 2, 1] - rel[:, 1, 2], rel[:, 0, 2] - rel[:, 2, 0],
                        rel[:, 1, 0] - rel[:, 0, 1]], axis=1) / (2 * dt)
    w_world = np.vstack([w_world, w_world[-1:]])
    w_body = np.einsum("nji,nj->ni", rs, w_world)
    tip = ps + rs[:, :, 0] * stick.length
    acc = np.gradient(np.gradient(tip, dt, axis=0), dt, axis=0)
    f_body = np.einsum("nji,nj->ni", rs, acc - GRAVITY)

    step = SIM_RATE // rate
    gyro = w_body[::step]
    accel = f_body[::step]
    n_out = len(gyro)
    # Slow sway from a real hand plus white sensor noise and any leftover bias.
    if sway > 0:
        drift = np.zeros((n_out, 3))
        for i in range(1, n_out):
            drift[i] = 0.97 * drift[i - 1] + rng.normal(0, sway * 0.25, 3)
        gyro = gyro + drift
    gyro = gyro + rng.normal(0, gyro_noise, gyro.shape)
    accel = accel + rng.normal(0, accel_noise, accel.shape)
    if gyro_bias is not None:
        gyro = gyro + np.asarray(gyro_bias)
    # Joy-Con IMU ranges: gyro +-2000 deg/s, accelerometer +-8 g. Fast swings at the tip of a
    # pipe exceed 8 g of centripetal acceleration, so the accelerometer clips.
    gyro = np.clip(gyro, -GYRO_RANGE, GYRO_RANGE)
    accel = np.clip(accel, -ACCEL_RANGE, ACCEL_RANGE)
    gyro = gyro @ stick.mount       # rows: stick frame -> Joy-Con frame (mount^T applied)
    accel = accel @ stick.mount
    t = np.arange(n_out) / rate
    if with_hand:
        return t, gyro, accel, ps[::step]
    return t, gyro, accel


def reverse(strokes, slow=2.0):
    """The return stroke: the same path backwards, usually slower."""
    return [replace(s, duration=s.duration * slow, angle=-s.angle,
                    move=tuple(-np.asarray(s.move))) for s in reversed(strokes)]


# ----------------------------------------------------------------------------- catalogue
# World axes: x forward, y left, z up. Positive rotation about z swings the tip to the left.

MELEE_GESTURES = {
    "slash_left":      [Stroke(0.28, (0, 0, 1), 110)],
    "slash_right":     [Stroke(0.28, (0, 0, 1), -110)],
    "overhead_chop":   [Stroke(0.30, (0, 1, 0), 120)],
    "rising_slash":    [Stroke(0.30, (0, 1, 0), -100)],
    "diagonal_slash":  [Stroke(0.30, (0, 0.7, 0.7), 110)],
    "thrust":          [Stroke(0.24, move=(0.38, 0, 0))],
    "quick_jab":       [Stroke(0.13, move=(0.16, 0, 0))],
    "spin":            [Stroke(0.75, (0, 0, 1), 330)],
    "wrist_twist":     [Stroke(0.25, (1, 0, 0), 120, frame="stick")],
    "overhand_lob":    [Stroke(0.32, (0, 1, 0), 150, move=(0.20, 0, 0.05))],
    "underhand_toss":  [Stroke(0.32, (0, 1, 0), -110, move=(0.20, 0, 0.10))],
    "circle":          [Stroke(0.80, (1, 0, 0), 360)],
    "shake":           [Stroke(0.12, (0, 0, 1), 25), Stroke(0.12, (0, 0, 1), -50),
                        Stroke(0.12, (0, 0, 1), 50), Stroke(0.12, (0, 0, 1), -25)],
    "quick_draws_x3":  [Stroke(0.13, move=(-0.25, 0, 0)), Stroke(0.12, move=(0.25, 0, 0)),
                        Stroke(0.13, move=(-0.25, 0, 0)), Stroke(0.12, move=(0.25, 0, 0)),
                        Stroke(0.13, move=(-0.25, 0, 0)), Stroke(0.12, move=(0.25, 0, 0))],
}

BOW_GESTURES = {
    "punch":           [Stroke(0.20, move=(0.30, 0, 0))],
    "yank_back":       [Stroke(0.20, move=(-0.30, 0, 0))],
    "fan":             [Stroke(0.40, (1, 0, 0), 25, move=(0, -0.45, 0))],
    "raise":           [Stroke(0.30, move=(0, 0, 0.35))],
    "point_forward":   [Stroke(0.30, (0, 1, 0), 85)],
    "cant_left":       [Stroke(0.25, (1, 0, 0), -50)],
    "cant_right":      [Stroke(0.25, (1, 0, 0), 50)],
    "twist":           [Stroke(0.25, (1, 0, 0), 100, frame="stick")],
    "shake":           [Stroke(0.12, (1, 0, 0), 20), Stroke(0.12, (1, 0, 0), -40),
                        Stroke(0.12, (1, 0, 0), 40), Stroke(0.12, (1, 0, 0), -20)],
}

CATALOGUE = {"melee": (MELEE, MELEE_GESTURES), "bow": (BOW, BOW_GESTURES)}


def take(stick, strokes, rng, spread=1.0, **kw):
    return simulate(strokes, stick, rng, human_variation(rng, spread), **kw)


def non_gestures(stick, gestures, rng, n=40):
    """Movements that must NOT fire: return strokes, repositioning, fidgets, walking, idling."""
    out = []
    names = list(gestures)
    for i in range(n):
        kind = i % 5
        if kind == 0:   # return stroke of a real gesture
            g = gestures[names[rng.integers(len(names))]]
            out.append(("return", simulate(reverse(g, slow=rng.uniform(1.6, 2.6)), stick, rng,
                                           human_variation(rng))))
        elif kind == 1:  # slow repositioning between fights
            s = [Stroke(rng.uniform(0.6, 1.2), tuple(rng.normal(size=3)), rng.uniform(30, 70))]
            out.append(("reposition", simulate(s, stick, rng)))
        elif kind == 2:  # small quick wrist fidget
            s = [Stroke(rng.uniform(0.12, 0.25), tuple(rng.normal(size=3)), rng.uniform(10, 25))]
            out.append(("fidget", simulate(s, stick, rng)))
        elif kind == 3:  # walking/jogging with the weapon in hand
            s = []
            for _ in range(8):
                s.append(Stroke(0.25, (0, 0, 1), rng.uniform(4, 8), move=(0, 0, 0.03)))
                s.append(Stroke(0.25, (0, 0, 1), -rng.uniform(4, 8), move=(0, 0, -0.03)))
            out.append(("walk", simulate(s, stick, rng)))
        else:            # standing in guard
            out.append(("idle", simulate([], stick, rng, pre=2.0, post=2.0, sway=0.1)))
    return out
