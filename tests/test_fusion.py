"""Camera + gyro: hand paths, stillness, hand/lag finding, fused recognition, and keeping the
gyro calibrated from the body."""

import numpy as np

from mocap.calibration import STILL_GYRO, AutoAlign, DeviceCalibration, rotation_angle
from mocap.fusion import CameraTrack, ClockMap, HandFinder, LagFinder, path_size
from mocap.motion import SwingClassifier, held_back
from mocap.sim.body import GUARD, Webcam, project, skeleton
from mocap.sim.stick import MELEE, MELEE_GESTURES, Stroke, human_variation, rot, simulate
from dataclasses import replace

ASPECT = 16 / 9
ARM = replace(MELEE, pivot_back=0.3)


def frame(right_hand=None, left_hand=None, noise=0.0, rng=None):
    pts = skeleton(left_hand, right_hand)
    if noise:
        pts = pts + rng.normal(0, noise, pts.shape)
    uv, _ = project(pts, aspect=ASPECT)
    lm = np.zeros((33, 4))
    lm[:, :2] = uv
    lm[:, 3] = 0.95
    return lm


def test_path_is_in_body_units_and_follows_the_hand():
    track = CameraTrack()
    track.lag = 0.0
    for k in range(60):
        t = k / 30
        z = 1.12 + (0.4 if 0.8 <= t <= 1.2 else 0.0)     # hand raised to head height mid-way
        track.add(t, frame(GUARD["melee"] + [0, 0, z - 1.12]), ASPECT)
    path = track.path("right", 0.9, 1.1)
    rest = track.path("right", 1.6, 1.8)
    assert path.shape == (16, 2)
    assert path[:, 1].mean() - rest[:, 1].mean() > 0.8      # 0.4 m up is about a shoulder width
    assert track.path("right", 5.0, 5.2) is None             # no frames there


def test_path_is_none_when_the_wrist_is_lost():
    track = CameraTrack()
    track.lag = 0.0
    for k in range(30):
        lm = frame(GUARD["melee"])
        lm[16, 3] = 0.1                                      # right wrist not visible
        track.add(k / 30, lm, ASPECT)
    assert track.path("right", 0.3, 0.6) is None


def test_stillness_from_the_camera():
    rng = np.random.default_rng(0)
    still, moving = CameraTrack(), CameraTrack()
    still.lag = moving.lag = 0.0
    for k in range(45):
        t = k / 30
        still.add(t, frame(GUARD["melee"], noise=0.004, rng=rng), ASPECT)
        moving.add(t, frame(GUARD["melee"] + [0, 0.3 * np.sin(4 * t), 0]), ASPECT)
    assert still.still("right", 0.6, 1.4)
    assert not moving.still("right", 0.6, 1.4)


def _swing_with_camera(rng, offset=(0, 0, 0), lag=0.05, t0=0.0):
    strokes = ([Stroke(0.6, move=offset)] if any(offset) else []) + MELEE_GESTURES["slash_left"]
    t, g, a, hand = simulate(strokes, ARM, rng, human_variation(rng), with_hand=True)
    frames = Webcam(jitter=0.006, lag=lag).frames(t, {"right": GUARD["melee"] + hand}, rng)
    return (t + t0, g, a), [(ft + t0, lm, asp) for ft, lm, asp in frames]


def _segment(stream):
    from mocap.motion import GravityTracker, Segmenter
    seg, grav, out = Segmenter(), GravityTracker(), []
    t, gyro, accel = stream
    for i in range(len(t)):
        s = seg.feed(t[i], gyro[i], grav.linear(gyro[i], accel[i], t[i]))
        if s is not None:
            out.append(s)
    return max(out, key=lambda s: np.abs(s[:, 1:4]).max())


def test_hand_and_lag_finders():
    rng = np.random.default_rng(1)
    hands, lags = HandFinder(), LagFinder()
    for k in range(6):
        stream, frames = _swing_with_camera(rng, lag=0.08, t0=10.0 * k)
        track = CameraTrack(seconds=1e9)
        track.lag = 0.0
        for ft, lm, asp in frames:
            track.add(ft, lm, asp)
        seg = _segment(stream)
        mag = np.linalg.norm(seg[:, 1:4], axis=1)
        hands.observe(seg[:, 0], mag, track)
        lags.observe(seg[:, 0], mag, track, "right")
    assert hands.side == "right"
    assert abs(lags.lag - 0.08) <= 0.04


def test_clock_map_follows_a_restarted_joycon_clock():
    cm = ClockMap()
    for k in range(400):
        cm.feed(5.0 + k / 200, 100.0 + k / 200 + 0.01 + 0.005 * (k % 3))
    assert abs(cm.host(5.25) - 100.26) < 0.003
    for k in range(800):                                 # reconnects: its clock starts over at 0
        cm.feed(k / 200, 103.0 + k / 200 + 0.01 + 0.005 * (k % 3))
    assert abs(cm.host(3.0) - 106.01) < 0.003


def _recorded(rng, n=5):
    temps, paths = {}, {}
    for name, off in (("mid", (0, 0, 0)), ("high", (0, 0, 0.4))):
        temps[name], paths[name] = [], []
        for _ in range(n):
            stream, frames = _swing_with_camera(rng, off, lag=0.0)
            track = CameraTrack(seconds=1e9)
            track.lag = 0.0
            for ft, lm, asp in frames:
                track.add(ft, lm, asp)
            seg = _segment(stream)
            temps[name].append(seg)
            paths[name].append(track.path("right", seg[0, 0], seg[-1, 0]))
    return temps, paths


def test_camera_tells_apart_the_same_swing_at_two_heights():
    rng = np.random.default_rng(2)
    temps, paths = _recorded(rng)
    clf = SwingClassifier()
    clf.set_data([(n, "melee", temps[n], None) for n in temps], {}, paths=paths)
    assert clf.twin_of["mid"] == {"high"}               # the gyro alone can't separate them
    assert "mid" in clf.fused and "high" in clf.fused
    right = held = 0
    for name, off in (("mid", (0, 0, 0)), ("high", (0, 0, 0.4))) * 6:
        stream, frames = _swing_with_camera(rng, off, lag=0.0)
        track = CameraTrack(seconds=1e9)
        track.lag = 0.0
        for ft, lm, asp in frames:
            track.add(ft, lm, asp)
        seg = _segment(stream)
        right += clf.classify("melee", seg, track.path("right", seg[0, 0], seg[-1, 0]))["gid"] == name
        blind = clf.classify("melee", seg)                # no view of the hand
        held += blind["gid"] is None and held_back(blind) in ("mid", "high")
    assert right >= 11
    assert held >= 10                                     # held back rather than guessed


def test_camera_finds_a_gyro_offset_too_big_for_the_gyro_to_notice():
    rng = np.random.default_rng(3)
    bias = np.array([0.35, -0.2, 0.25])                   # ~27 deg/s, over STILL_GYRO
    assert np.linalg.norm(bias) > STILL_GYRO
    cal_gyro, cal_cam = DeviceCalibration(), DeviceCalibration()
    for k in range(400):                                   # 2 s held still
        g = bias + rng.normal(0, 0.02, 3)
        cal_gyro.apply(k / 200, g, [0, 0, 9.81])
        cal_cam.apply(k / 200, g, [0, 0, 9.81])
    for _ in range(12):
        cal_cam.camera_still(1.0, 2.0)
    assert np.linalg.norm(cal_gyro.bias - bias) > 0.3     # gyro-only never saw it as still
    assert np.linalg.norm(cal_cam.bias - bias) < 0.02


def test_camera_still_refuses_a_twist_in_place():
    cal = DeviceCalibration()
    for k in range(400):
        cal.apply(k / 200, [2.0 * np.sin(k / 20), 0, 0], [0, 0, 9.81])
    assert cal.camera_still(0.0, 2.0) is None


def _move(axis, rng):
    a = np.asarray(axis, float) / np.linalg.norm(axis)
    return np.outer(np.sin(np.linspace(0, np.pi, 24)), a) * 5 + rng.normal(0, 0.15, (24, 3))


def test_auto_align_learns_a_slip_and_ignores_noise():
    rng = np.random.default_rng(4)
    slip = rot((1, 0.2, 0.1), np.radians(30))
    cal = DeviceCalibration()
    axes = [(0, 0, 1), (0, 1, 0), (1, 0, 0)]
    for k in range(60):
        tmpl = _move(axes[k % 3], rng)
        live = (cal.align @ (slip.T @ tmpl.T)).T
        cal.learn_alignment(live, tmpl)
    assert rotation_angle(cal.align @ slip.T) < 6
    steady = DeviceCalibration()
    for k in range(60):
        tmpl = _move(axes[k % 3], rng)
        steady.learn_alignment(_move(axes[k % 3], rng) * 0 + tmpl + rng.normal(0, 0.15, tmpl.shape), tmpl)
    assert steady.auto_angle < 4


def test_auto_align_needs_two_axes():
    rng = np.random.default_rng(5)
    a = AutoAlign()
    slip = rot((1, 0, 0), np.radians(30))
    steps = []
    for _ in range(20):
        tmpl = _move((0, 0, 1), rng)            # every move turns about the same axis
        steps.append(a.observe((slip @ tmpl.T).T, tmpl))
    assert all(s is None for s in steps)        # so a full rotation can't be pinned down


def test_path_size():
    assert path_size([[0, 0], [3, 4]]) == 5.0
