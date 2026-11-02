"""Whole-body movement from webcam landmarks: jogging in place and hopping.

Both work on MediaPipe's 33 landmarks (normalized image coords). Distances are measured in
shoulder widths so standing nearer or further from the camera doesn't matter.

Jog: the feet take turns lifting. Real jogging in place is mostly heel kicks, so we follow the
left-minus-right ANKLE height (knees as a fallback); each time it swings past the other side
(with some hysteresis) that's a step. Three steps within 1.5 s = jogging, and it stops 0.7 s
after the last step. Meant to hold a movement key down.

Hop: the hips rise well above where they normally sit and come back down quickly, with both
ankles level (a jogging bounce has one foot up). Standing up from a crouch also lifts the hips
but doesn't come back down, so it isn't a hop.
"""

from collections import deque

import numpy as np

VISIBLE = 0.5
L_SHOULDER, R_SHOULDER, L_HIP, R_HIP, L_KNEE, R_KNEE, L_ANKLE, R_ANKLE = 11, 12, 23, 24, 25, 26, 27, 28


def _alternation(lm, scale):
    """Right-minus-left foot height in shoulder widths (ankles if visible, else knees)."""
    for a, b in ((L_ANKLE, R_ANKLE), (L_KNEE, R_KNEE)):
        if lm[a, 3] > VISIBLE and lm[b, 3] > VISIBLE:
            # Image y grows downward, so a lifted foot has a *smaller* y.
            return (lm[b, 1] - lm[a, 1]) / scale
    return None


def _scale(lm, aspect):
    if lm[L_SHOULDER, 3] < VISIBLE or lm[R_SHOULDER, 3] < VISIBLE:
        return None
    a = np.array([lm[L_SHOULDER, 0] * aspect, lm[L_SHOULDER, 1]])
    b = np.array([lm[R_SHOULDER, 0] * aspect, lm[R_SHOULDER, 1]])
    s = float(np.linalg.norm(a - b))
    return s if s > 1e-3 else None


class JogDetector:
    def __init__(self, swing=0.1, steps_needed=3, window=1.5, stop_after=0.7):
        self.swing = swing
        self.steps_needed = steps_needed
        self.window = window
        self.stop_after = stop_after
        self.side = 0
        self.steps = deque()
        self.active = False

    def reset(self):
        self.side = 0
        self.steps.clear()
        self.active = False

    def update(self, lm, aspect, t):
        """Returns "start", "stop" or None."""
        event = None
        if lm is not None:
            scale = _scale(lm, aspect)
            diff = _alternation(lm, scale) if scale else None
            if diff is not None:
                side = 1 if diff > self.swing else -1 if diff < -self.swing else 0
                if side and side != self.side:
                    if self.side:
                        self.steps.append(t)
                    self.side = side
        while self.steps and t - self.steps[0] > self.window:
            self.steps.popleft()
        if not self.active and len(self.steps) >= self.steps_needed:
            self.active, event = True, "start"
        elif self.active and (not self.steps or t - self.steps[-1] > self.stop_after):
            self.active, event = False, "stop"
            self.steps.clear()
        return event


class HopDetector:
    def __init__(self, rise=0.2, max_air=0.7, settle=2.0):
        self.rise = rise
        self.max_air = max_air
        self.history = deque()          # (t, hip height in shoulder widths)
        self.settle = settle
        self.up_since = None
        self.cooldown_until = -np.inf

    def update(self, lm, aspect, t):
        """Returns "hop" when a hop lands, else None."""
        if lm is None or lm[L_HIP, 3] < VISIBLE or lm[R_HIP, 3] < VISIBLE:
            return None
        scale = _scale(lm, aspect)
        if not scale:
            return None
        height = -((lm[L_HIP, 1] + lm[R_HIP, 1]) / 2) / scale     # bigger = higher
        while self.history and t - self.history[0][0] > self.settle:
            self.history.popleft()
        baseline = np.median([h for _, h in self.history]) if len(self.history) >= 10 else None
        event = None
        if baseline is not None and t >= self.cooldown_until:
            lifted = height - baseline
            level = _alternation(lm, scale)
            feet_level = level is None or abs(level) < 0.15
            if self.up_since is None and lifted > self.rise and feet_level:
                self.up_since = t
            elif self.up_since is not None:
                if lifted < self.rise * 0.4:
                    if t - self.up_since <= self.max_air:
                        event = "hop"
                        self.cooldown_until = t + 0.4
                    self.up_since = None
                elif t - self.up_since > self.max_air:
                    self.up_since = None       # stayed up: stood up from a crouch, not a hop
                    self.history.clear()
        if self.up_since is None:
            self.history.append((t, height))
        return event
