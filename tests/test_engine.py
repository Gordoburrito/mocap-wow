import json

import numpy as np
import pytest

from mocap import profile as profiles
from mocap.engine import Engine
from mocap.keys import KeyOutput
from mocap.sim.stick import CATALOGUE, Stroke, human_variation, reverse, simulate, take
from synth import ASPECT, pose

MELEE, MELEE_G = CATALOGUE["melee"]
BOW, BOW_G = CATALOGUE["bow"]
L, R = "Joy-Con (L)", "Joy-Con (R)"


class Rig:
    def __init__(self, tmp_path):
        self.clock_t = 100.0
        self.sensor_t = 0.0
        self.front = "World of Warcraft"
        self.keys = KeyOutput(dry_run=True, tap_ms=1)
        self.engine = Engine(str(tmp_path / "p.json"), self.keys, clock=lambda: self.clock_t,
                             front_app=lambda: self.front, profiles_dir=str(tmp_path))
        self.engine.settings["capture_style"] = "free"     # these tests are about recognition
        self.events = []
        self.engine.listeners.append(self.events.append)
        self.rng = np.random.default_rng(11)
        self.engine.on_device(L, True, {})
        self.engine.on_device(R, True, {})

    def stream(self, device, stream, tick_every=10):
        t, g, a = stream
        for i in range(len(t)):
            self.sensor_t += 1 / 200
            self.clock_t += 1 / 200
            self.engine.on_imu(device, self.sensor_t, g[i], a[i])
            if i % tick_every == 0:
                self.engine.tick()

    def rest(self, device, seconds):
        stick = BOW if device == L else MELEE
        self.stream(device, simulate([], stick, self.rng, pre=seconds, post=0.0))

    def move(self, device, name, with_return=True):
        stick, cat = (BOW, BOW_G) if device == L else (MELEE, MELEE_G)
        strokes = cat[name] + ([Stroke(0.2)] + reverse(cat[name], slow=2.0) if with_return else [])
        self.stream(device, simulate(strokes, stick, self.rng, human_variation(self.rng)))

    def clash(self):
        for device, stick in ((L, BOW), (R, MELEE)):
            base = stick.mount.T @ (stick.rest.T @ np.array([0, 0, 9.81]))
            self.engine.on_imu(device, self.sensor_t, np.zeros(3), base)
            self.engine.on_imu(device, self.sensor_t + 0.001, np.zeros(3), base + [55, 0, 0])

    def record(self, device, name, key, takes=5):
        self.engine.start_capture("ui")
        self.clock_t += 1.0
        for _ in range(takes + 2):
            if self.engine.capture is None or self.engine.capture["phase"] != "perform":
                break
            self.move(device, name)
        assert self.engine.capture["phase"] == "bind"
        self.engine.capture_key({"code": key, "mods": []})
        return self.engine.save_capture()

    def pose_frames(self, name, seconds, tick=True):
        rng = np.random.default_rng(int(self.clock_t))
        for _ in range(int(seconds * 30)):
            self.clock_t += 1 / 30
            self.engine.on_pose(pose(name, rng), ASPECT, self.clock_t)
            if tick:
                self.engine.tick()

    def sent(self):
        self.keys.flush()
        return [code for code, down, _ in self.keys.sent if down and code not in (56, 59, 58, 55)]


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def test_roles_default_to_bow_left_melee_right(rig):
    assert rig.engine.devices[L].role == "bow" and rig.engine.devices[R].role == "melee"


def test_weapon_clash_opens_capture_and_takes_become_a_live_gesture(rig, tmp_path):
    rig.clash()
    assert rig.engine.capture and rig.engine.capture["trigger"] == "clash"
    rig.rest(R, 1.5)
    for _ in range(7):
        if rig.engine.capture["phase"] != "perform":
            break
        rig.move(R, "slash_left")
    c = rig.engine.capture
    assert c["phase"] == "bind" and c["source"] == "melee" and c["kind"] == "motion"
    rig.engine.capture_key({"code": "Digit1", "mods": []})
    gid = rig.engine.save_capture()
    g = rig.engine.gesture(gid)
    assert 3 <= len(g["templates"]) <= 5 and g["source"] == "melee"
    assert json.loads((tmp_path / "p.json").read_text())["gestures"][0]["id"] == gid
    rig.engine.set_armed(True)
    rig.move(R, "slash_left")
    assert rig.sent() == [18]


def test_nothing_fires_unless_armed_and_wow_in_front(rig):
    rig.record(R, "slash_left", "Digit1")
    rig.move(R, "slash_left")
    assert rig.sent() == []
    assert any(e.get("outcome") == "not sent — disarmed" for e in rig.events)
    rig.engine.set_armed(True)
    rig.front = "Discord"
    rig.clock_t += 1
    rig.move(R, "slash_left")
    assert rig.sent() == []
    assert any(e.get("outcome") == "held back" for e in rig.events)


def test_lockout_stops_the_return_stroke_firing_the_mirror_move(rig):
    rig.record(R, "slash_left", "Digit1")
    rig.record(R, "slash_right", "Digit2")
    rig.engine.set_armed(True)
    for _ in range(4):
        rig.move(R, "slash_left")       # each followed by its swing back (which looks like slash_right)
    assert rig.sent() == [18, 18, 18, 18]


def test_two_weapons_fire_their_own_moves(rig):
    rig.record(R, "overhead_chop", "Digit1")
    rig.record(L, "punch", "KeyQ")
    rig.engine.set_armed(True)
    rig.move(L, "punch")
    rig.move(R, "overhead_chop")
    rig.move(L, "punch")
    assert rig.sent() == [12, 18, 12]


def test_nothing_fires_while_capturing(rig):
    rig.record(R, "slash_left", "Digit1")
    rig.engine.set_armed(True)
    rig.engine.start_capture("ui")
    rig.clock_t += 1
    rig.move(R, "slash_left")
    assert rig.sent() == []


def test_undo_removes_the_last_capture(rig):
    gid = rig.record(R, "slash_left", "Digit1")
    rig.engine.undo_capture()
    assert rig.engine.gesture(gid) is None


def test_pose_capture_auto_detects_a_held_pose_and_arm_toggle_works_hands_free(rig):
    rig.engine.handle({"op": "record_neutral"})
    rig.pose_frames("neutral", 9.5)
    assert len(rig.engine.profile["neutral"]) > 100
    rig.engine.start_capture("ui")
    rig.clock_t += 1.0
    rig.pose_frames("arms_up", 3.5)
    c = rig.engine.capture
    assert c["phase"] == "bind" and c["kind"] == "pose"
    rig.engine.capture_key({"code": "F1", "mods": []})
    gid = rig.engine.save_capture()
    rig.engine.handle({"op": "update_gesture", "id": gid, "fields": {"action": "arm_toggle"}})
    assert not rig.engine.armed
    rig.pose_frames("arms_up", 1.2)
    assert rig.engine.armed
    rig.pose_frames("neutral", 0.6)
    rig.pose_frames("arms_up", 3.0)          # holding it a long time toggles only once
    assert not rig.engine.armed
    assert rig.sent() == []                 # toggling armed never presses a key


def test_jog_holds_its_key_while_jogging(rig):
    rig.engine.handle({"op": "add_gesture", "kind": "jog"})
    g = [g for g in rig.engine.profile["gestures"] if g["kind"] == "jog"][0]
    rig.engine.handle({"op": "update_gesture", "id": g["id"], "fields": {"key": {"code": "KeyW", "mods": []}}})
    rig.engine.set_armed(True)
    base = pose("neutral")
    base[[25, 26, 27, 28], 3] = 0.95
    base[25, :2] = (0.46, 0.85)
    base[26, :2] = (0.54, 0.85)
    base[27, :2] = (0.46, 0.97)
    base[28, :2] = (0.54, 0.97)
    for i in range(90):
        rig.clock_t += 1 / 30
        lm = base.copy()
        lift = 0.05 * np.sin(2 * np.pi * 1.4 * i / 30)
        lm[[25, 27], 1] -= max(lift, 0)
        lm[[26, 28], 1] -= max(-lift, 0)
        rig.engine.on_pose(lm, ASPECT, rig.clock_t)
    assert rig.keys.is_held(g["id"])
    for _ in range(40):
        rig.clock_t += 1 / 30
        rig.engine.on_pose(base, ASPECT, rig.clock_t)
    assert not rig.keys.is_held(g["id"])


def test_near_miss_choices(rig):
    gid = rig.record(R, "slash_left", "Digit1")
    seg = np.array(rig.engine.gesture(gid)["templates"][0])
    rig.engine.near = {"gid": gid, "role": "melee", "segment": seg, "match": 0.45, "t": 0}
    rig.engine.resolve_near("loosen")
    assert rig.engine.gesture(gid)["sensitivity"] > 1.0
    rig.engine.near = {"gid": gid, "role": "melee", "segment": seg, "match": 0.45, "t": 0}
    rig.engine.resolve_near("ignore")
    assert len(rig.engine.profile["ignore"]["melee"]) == 1


def test_profiles_new_copy_and_switch(rig, tmp_path):
    rig.record(R, "slash_left", "Digit1")
    first = rig.engine.path
    rig.engine.new_profile("Marksmanship")
    assert rig.engine.profile["name"] == "Marksmanship" and rig.engine.profile["gestures"] == []
    rig.engine.switch_profile(first)
    assert len(rig.engine.profile["gestures"]) == 1
    rig.engine.new_profile("Survival copy", copy=True)
    assert len(rig.engine.profile["gestures"]) == 1
    names = {p["name"] for p in profiles.list_profiles(str(tmp_path))}
    assert {"Marksmanship", "Survival copy"} <= names


def test_v1_profile_migrates(tmp_path):
    v1 = {"settings": {"rumble": False}, "neutral": [],
          "gestures": [{"id": "a", "kind": "pose", "name": "Bow", "samples": [], "key": None, "mode": "tap"},
                       {"id": "b", "kind": "swing", "name": "Slash", "device": "Joy-Con (R)", "templates": []},
                       {"id": "c", "kind": "button", "name": "Arm", "device": "Joy-Con (R)", "button": "a"}],
          "ignore": {"Joy-Con (L)": [[[0, 0, 0, 0, 0, 0, 0]]]}}
    path = tmp_path / "old.json"
    path.write_text(json.dumps(v1))
    p = profiles.load(str(path))
    kinds = [(g["kind"], g["source"]) for g in p["gestures"]]
    assert kinds == [("pose", "webcam"), ("motion", "melee")]
    assert p["settings"]["rumble"] is False and "bow" in p["ignore"]


def test_calibration_wizard_measures_bias_and_mount_then_realigns(rig):
    from mocap.engine import CALIBRATION_STEPS
    from mocap.sim.stick import pitch_up
    bias = np.array([0.05, -0.03, 0.02])

    def run(mount):
        rig.engine.start_calibration(R)
        poses = {"still": pitch_up(45), "forward": pitch_up(0), "up": pitch_up(90), "guard": pitch_up(45)}
        guard = 0
        while rig.engine.calib is not None and guard < 20000:
            step = rig.engine.calib["step"]
            g_world = poses[step].T @ np.array([0, 0, 9.81])
            accel = mount.T @ g_world + rig.rng.normal(0, 0.05, 3)
            gyro = bias + rig.rng.normal(0, 0.01, 3)
            rig.sensor_t += 1 / 200
            rig.clock_t += 1 / 200
            rig.engine.on_imu(R, rig.sensor_t, gyro, accel)
            if guard % 10 == 0:
                rig.engine.tick()
            guard += 1
        return [e for e in rig.events if e.get("type") == "calibration" and e.get("phase") == "done"][-1]["results"]

    first = run(np.eye(3))
    dev = rig.engine.devices[R]
    assert np.allclose(dev.cal.bias, bias, atol=0.02)
    assert dev.cal.ref_forward is not None and first["pose_angle"] > 80
    assert dev.cal.trigger is not None
    # Record something on the melee weapon, then remount the Joy-Con and calibrate again.
    rig.engine.profile["gestures"].append(dict(profiles.new_gesture("motion", "melee"), templates=[[[0] * 7] * 3]))
    from mocap.sim.stick import rot
    remount = rot((1, 0, 0), np.radians(40))
    second = run(remount)
    assert second.get("realigned")
    assert not np.allclose(dev.cal.align, np.eye(3))
    assert len(CALIBRATION_STEPS) == 4
