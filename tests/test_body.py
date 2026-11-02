import numpy as np

from mocap.body import HopDetector, JogDetector

ASPECT = 16 / 9


def stance(knee_lift=(0.0, 0.0), rise=0.0):
    lm = np.zeros((33, 4))
    lm[:, 3] = 0.95
    pts = {11: (0.45, 0.35), 12: (0.55, 0.35), 23: (0.47, 0.6), 24: (0.53, 0.6),
           25: (0.47, 0.75), 26: (0.53, 0.75), 27: (0.47, 0.9), 28: (0.53, 0.9)}
    for i, (x, y) in pts.items():
        lm[i, :2] = (x, y - rise)
    lm[[25, 27], 1] -= knee_lift[0]        # a step lifts the whole leg: knee and ankle
    lm[[26, 28], 1] -= knee_lift[1]
    return lm


def test_jogging_starts_after_a_few_steps_and_stops_when_you_do():
    jog = JogDetector()
    events, t = [], 0.0
    for i in range(90):                     # 3 s of jogging at ~2.7 steps/s
        lift = 0.06 * np.sin(2 * np.pi * 1.35 * t)
        e = jog.update(stance((max(lift, 0), max(-lift, 0))), ASPECT, t)
        if e:
            events.append((e, round(t, 2)))
        t += 1 / 30
    for _ in range(45):                     # stand still 1.5 s
        e = jog.update(stance(), ASPECT, t)
        if e:
            events.append((e, round(t, 2)))
        t += 1 / 30
    assert [e for e, _ in events] == ["start", "stop"]
    assert events[0][1] < 1.6


def test_standing_still_never_jogs():
    jog = JogDetector()
    rng = np.random.default_rng(0)
    assert all(jog.update(stance(rng.normal(0, 0.005, 2)), ASPECT, i / 30) is None for i in range(150))


def test_hop_fires_once_and_standing_up_does_not():
    hop = HopDetector()
    t, fired = 0.0, []
    for _ in range(60):
        hop.update(stance(), ASPECT, t)
        t += 1 / 30
    for k in range(12):                     # 0.4 s in the air
        rise = 0.05 * np.sin(np.pi * k / 11)
        if hop.update(stance(rise=rise), ASPECT, t):
            fired.append(t)
        t += 1 / 30
    for _ in range(30):
        if hop.update(stance(), ASPECT, t):
            fired.append(t)
        t += 1 / 30
    assert len(fired) == 1
    # Crouched, then standing up for good: hips rise and stay up -> not a hop.
    hop2 = HopDetector()
    t = 0.0
    for _ in range(60):
        hop2.update(stance(rise=-0.1), ASPECT, t)
        t += 1 / 30
    assert not any(hop2.update(stance(), ASPECT, t + i / 30) for i in range(60))
