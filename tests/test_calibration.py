import numpy as np

from mocap.calibration import (DeviceCalibration, StillCapture, kabsch, mount_alignment,
                               pose_quality, trigger_from_noise)
from mocap.sim.stick import Stick, pitch_up, rot, simulate


def test_kabsch_recovers_a_rotation():
    r = rot((0.3, 1, 0.2), 0.7)
    src = np.random.default_rng(0).normal(size=(6, 3))
    assert np.allclose(kabsch(src, src @ r.T), r, atol=1e-9)


def gravity(stick, pose, rng):
    posed = Stick(stick.name, stick.length, pose, stick.mount)
    return simulate([], posed, rng, pre=1.0, post=1.0)[2].mean(axis=0)


def test_two_pose_alignment_undoes_a_remount():
    rng = np.random.default_rng(1)
    stick = Stick("melee", 0.7)
    remount = rot((1, 0, 0), np.radians(35)) @ rot((0, 0, 1), np.radians(20))
    moved = Stick("melee", 0.7, mount=remount)
    ref = gravity(stick, pitch_up(0), rng), gravity(stick, pitch_up(90), rng)
    new = gravity(moved, pitch_up(0), rng), gravity(moved, pitch_up(90), rng)
    align = mount_alignment(ref[0], ref[1], new[0], new[1])
    # A reading in the new mounting, rotated back, matches the old mounting's reading.
    v_stick = np.array([0.3, -1.2, 0.8])
    assert np.allclose(align @ (remount.T @ v_stick), v_stick, atol=0.05)
    assert pose_quality(*ref) > 80


def test_still_capture_measures_bias_and_rejects_movement():
    cap = StillCapture(1.0)
    rng = np.random.default_rng(2)
    t = 0.0
    while not cap.feed(t, [0.02, -0.01, 0.05] + rng.normal(0, 0.01, 3), [0, 0, 9.81]):
        t += 0.005
    res = cap.result()
    assert res["ok"] and np.allclose(res["gyro_bias"], [0.02, -0.01, 0.05], atol=0.01)
    moving = StillCapture(0.5)
    t = 0.0
    while not moving.feed(t, [2.0 * np.sin(t * 20), 0, 0], [0, 0, 9.81]):
        t += 0.005
    assert not moving.result()["ok"]


def test_bias_keeps_updating_while_held_still():
    cal = DeviceCalibration()
    t = 0.0
    for _ in range(3000):
        g, _ = cal.apply(t, [0.08, 0, 0], [0, 0, 9.81])
        t += 0.005
    assert abs(g[0]) < 0.03


def test_trigger_from_noise_sits_above_your_jiggle():
    calm = np.abs(np.random.default_rng(3).normal(0, 0.2, 800))
    assert 2.0 <= trigger_from_noise(calm) <= 6.0
    assert trigger_from_noise([0.1] * 5) is None
