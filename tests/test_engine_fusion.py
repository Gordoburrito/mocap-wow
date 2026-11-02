"""The engine with a webcam that sees the weapon hand: capture stores hand paths, the same swing
at two heights becomes two moves, a blocked camera holds them back instead of guessing, the
accuracy test scores moves without sending keys, and the play log stays in its folder."""

from dataclasses import replace

import numpy as np
import pytest

from mocap.engine import Engine
from mocap.keys import KeyOutput
from mocap.sim.body import GUARD, Webcam
from mocap.sim.stick import MELEE, MELEE_GESTURES, Stroke, human_variation, reverse, simulate

R = "Joy-Con (R)"
ARM = replace(MELEE, pivot_back=0.30)
HIGH = (0, 0, 0.40)
CODES = {"Digit1": 18, "Digit2": 19}


class Rig:
    def __init__(self, tmp_path, metrics=False):
        self.clock_t = 100.0
        self.sensor_t = 0.0
        self.keys = KeyOutput(dry_run=True, tap_ms=1)
        self.engine = Engine(str(tmp_path / "p.json"), self.keys, clock=lambda: self.clock_t,
                             front_app=lambda: "World of Warcraft", profiles_dir=str(tmp_path),
                             metrics_dir=str(tmp_path / "metrics") if metrics else None)
        self.rng = np.random.default_rng(21)
        self.cam = Webcam(jitter=0.006, lag=0.04)
        self.camera_on = True
        self.engine.on_device(R, True, {})
        self.engine.set_camera(ok=True, fps=30.0, message="")

    def play(self, offset=(0, 0, 0), with_return=True, rest=0.4):
        moves = MELEE_GESTURES["slash_left"]
        strokes = ([Stroke(0.6, move=offset)] if any(offset) else []) + list(moves)
        if with_return:
            strokes += [Stroke(0.2)] + reverse(moves, slow=2.0)
            strokes += [Stroke(0.9, move=tuple(-np.asarray(offset)))] if any(offset) else []
        t, g, a, hand = simulate(strokes, ARM, self.rng, human_variation(self.rng), pre=rest,
                                 post=0.4, with_hand=True)
        frames = self.cam.frames(t, {"right": GUARD["melee"] + hand}, self.rng)
        base = self.clock_t
        fi = 0
        for i in range(len(t)):
            self.sensor_t += 1 / 200
            self.clock_t = base + t[i]
            while fi < len(frames) and base + frames[fi][0] <= self.clock_t:
                if self.camera_on:
                    self.engine.on_pose(frames[fi][1], frames[fi][2], base + frames[fi][0])
                fi += 1
            self.engine.on_imu(R, self.sensor_t, g[i], a[i])
            if i % 10 == 0:
                self.engine.tick()

    def record(self, offset, key, name):
        self.engine.start_capture("ui")
        self.wait(1.0)
        for _ in range(8):
            if self.engine.capture is None or self.engine.capture["phase"] != "perform":
                break
            self.play(offset)
        if self.engine.capture["phase"] == "perform":
            self.engine.finish_perform()
        assert self.engine.capture["phase"] == "bind"
        self.engine.capture_key({"code": key, "mods": []})
        self.engine.capture["name"] = name
        return self.engine.save_capture()

    def wait(self, seconds):
        """Time passes; the Joy-Con keeps streaming (its clock moves too)."""
        self.clock_t += seconds
        self.sensor_t += seconds

    def sent(self):
        self.keys.flush()
        return [code for code, down, _ in self.keys.sent if down]


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    r.engine.settings["require_wow_front"] = False
    r.play(rest=1.0, with_return=False)          # learn the clocks, the hand, the camera delay
    return r


def test_capture_keeps_hand_paths_and_heights_become_two_moves(rig):
    mid = rig.record((0, 0, 0), "Digit1", "Slash")
    high = rig.record(HIGH, "Digit2", "High slash")
    gs = {g["id"]: rig.engine.public_gesture(g) for g in rig.engine.profile["gestures"]}
    for gid in (mid, high):
        assert gs[gid]["camera_takes"] >= gs[gid]["count"] - 1
        assert gs[gid]["uses_camera"]
    assert gs[mid]["twins"] == ["High slash"]          # same swing to the gyro
    rig.engine.set_armed(True)
    before = len(rig.sent())
    plan = [(0, 0, 0), HIGH, HIGH, (0, 0, 0), HIGH, (0, 0, 0)]
    for off in plan:
        rig.play(off)
    got = rig.sent()[before:]
    want = [CODES["Digit2"] if off == HIGH else CODES["Digit1"] for off in plan]
    assert got == want


def test_blocked_camera_holds_twins_back_instead_of_guessing(rig):
    rig.record((0, 0, 0), "Digit1", "Slash")
    rig.record(HIGH, "Digit2", "High slash")
    rig.engine.set_armed(True)
    before = len(rig.sent())
    rig.camera_on = False
    rig.wait(2.0)                                       # the camera's last frame is now old
    for off in [(0, 0, 0), HIGH, (0, 0, 0)]:
        rig.play(off)
    assert rig.sent()[before:] == []
    held = [a for a in rig.engine.activity if a["outcome"] == "held back"]
    assert held and "camera" in held[-1]["detail"]


def test_accuracy_test_scores_moves_and_sends_nothing(tmp_path):
    rig = Rig(tmp_path, metrics=True)
    rig.engine.settings["require_wow_front"] = False
    rig.play(rest=1.0, with_return=False)
    ids = {rig.record((0, 0, 0), "Digit1", "Slash"): (0, 0, 0),
           rig.record(HIGH, "Digit2", "High slash"): HIGH}
    rig.engine.set_armed(True)
    before = len(rig.sent())
    rig.engine.start_drill(reps=2)
    for _ in range(200):
        d = rig.engine.drill
        if d is None:
            break
        if d.phase == "go":
            rig.play(ids[d.current["id"]])
        else:
            rig.wait(0.1)
            rig.engine.tick()
    assert rig.engine.drill is None
    assert rig.sent()[before:] == []
    hist = rig.engine.metrics.history()
    drill = hist[-1]["drills"][-1]
    assert drill["n"] == 4 and drill["accuracy"] >= 75
    assert (tmp_path / "metrics").is_dir()


def test_wrong_report_ignores_that_move(rig):
    gid = rig.record((0, 0, 0), "Digit1", "Slash")
    rig.engine.set_armed(True)
    rig.play()
    assert rig.engine.last_fire["gid"] == gid
    rig.engine.report_wrong()
    assert len(rig.engine.profile["ignore"]["melee"]) == 1
    assert rig.engine.metrics.corrections["wrong"] == 1


def test_no_metrics_folder_means_nothing_written(rig, tmp_path):
    rig.record((0, 0, 0), "Digit1", "Slash")
    assert not rig.engine.metrics.enabled
    assert not (tmp_path / "metrics").exists()
