"""Capture like recording software: 3-2-1, a few seconds of recording, per take. Moves made
during the countdown don't count; doing nothing asks again; the kind is still worked out."""

import numpy as np
import pytest

from mocap.engine import Engine
from mocap.keys import KeyOutput
from test_camera_moves import ASPECT, frames
from test_engine import R, Rig


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    r.engine.settings["capture_style"] = "countdown"
    return r


def until(rig, phase, device=R, limit=15.0):
    """Let time pass (the weapon resting) until the take reaches `phase`."""
    t = 0.0
    while rig.engine.capture is not None and rig.engine.capture.get("take_phase") != phase and t < limit:
        rig.rest(device, 0.1)
        t += 0.1
    return rig.engine.capture


def test_weapon_takes_record_only_in_the_window(rig):
    eng = rig.engine
    eng.start_capture("ui")
    c = eng.capture
    assert c["take_phase"] == "countdown" and abs(c["take_until"] - rig.clock_t - 3.0) < 1e-6
    rig.move(R, "slash_left")                      # during the countdown: ignored
    for i in range(5):
        c = until(rig, "recording")
        assert c["phase"] == "perform" and len(c["takes"]) == i
        rig.move(R, "slash_left")
        c = until(rig, "review")
        if c["phase"] == "perform":
            assert c["take_message"].startswith("✓ Take")
    c = until(rig, None)
    assert c["phase"] == "bind" and c["kind"] == "motion" and c["source"] == "melee"
    assert len(c["takes"]) >= 3


def test_doing_nothing_asks_again(rig):
    eng = rig.engine
    eng.start_capture("ui")
    until(rig, "recording")
    c = until(rig, "review")
    assert len(c["takes"]) == 0 and "Again" in c["take_message"] and c["misses"] == 1
    c = until(rig, "countdown")
    assert c["take_phase"] == "countdown"


def _engine(tmp_path):
    clock = {"t": 100.0}
    eng = Engine(str(tmp_path / "p.json"), KeyOutput(dry_run=True), clock=lambda: clock["t"],
                 front_app=lambda: "World of Warcraft", profiles_dir=str(tmp_path))
    eng.settings["capture_style"] = "countdown"
    return eng, clock


def _webcam(eng, clock, move, rng, seconds):
    """Feed webcam frames of the body (doing `move` once, or standing) for `seconds`."""
    fr = frames(move, rng, pre=0.2, post=max(0.1, seconds - 0.9)) if move else \
        frames("none", rng, pre=0.0, seconds=0.01, post=seconds)
    for t, lm in fr:
        clock["t"] += 1 / 30
        eng.on_pose(lm, ASPECT, clock["t"])
        eng.tick()


def _until(eng, clock, phase, rng, limit=15.0):
    t0 = clock["t"]
    while eng.capture is not None and eng.capture.get("take_phase") != phase and clock["t"] - t0 < limit:
        _webcam(eng, clock, None, rng, 0.1)
    return eng.capture


def test_body_move_takes(tmp_path):
    eng, clock = _engine(tmp_path)
    rng = np.random.default_rng(1)
    eng.start_capture("ui", kind="move")
    for i in range(5):
        _until(eng, clock, "recording", rng)
        _webcam(eng, clock, "kick", rng, 1.8)
        c = _until(eng, clock, "review", rng)
        assert c["take_message"].startswith("✓"), c["take_message"]
    c = _until(eng, clock, None, rng)
    assert c["phase"] == "bind" and c["kind"] == "move" and len(c["takes"]) == 5
    assert len(c["skeletons"]) == 5 and c["auto_scope"] == "right_leg"


def test_pose_is_one_held_take(tmp_path):
    eng, clock = _engine(tmp_path)
    rng = np.random.default_rng(2)
    eng.start_capture("ui", kind="pose")
    _until(eng, clock, "recording", rng)
    from test_camera_moves import GUARD, project, skeleton
    while eng.capture.get("take_phase") == "recording":
        pts = skeleton(GUARD["bow"] + [0, 0, 0.7], GUARD["melee"] + [0, 0, 0.7])   # both arms up, held
        uv, _ = project(pts, aspect=ASPECT)
        lm = np.zeros((33, 4))
        lm[:, :2] = uv
        lm[:, 3] = 0.95
        clock["t"] += 1 / 30
        eng.on_pose(lm, ASPECT, clock["t"])
        eng.tick()
    c = _until(eng, clock, None, rng)
    assert c["phase"] == "bind" and c["kind"] == "pose" and len(c["takes"]) >= 30
