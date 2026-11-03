"""Body moves from the webcam alone: segmenting, recognizing, joining pieces, and the engine
firing them (and leaving a move made during a weapon motion to the weapon)."""

import numpy as np

from mocap.camera_moves import MoveClassifier, MoveJoiner, MoveSegmenter, calibrate_start, prepare
from mocap.engine import Engine
from mocap.keys import KeyOutput
from mocap.sim.body import GUARD, project, skeleton

ASPECT = 16 / 9
NOISE = 0.0025             # tracker jitter (metres per frame, drifting)


def frames(move, rng, seconds=0.7, pre=0.8, post=1.0, fps=30, size=1.0):
    """Landmark frames of a body doing `move` once (kick / duck / shove / none)."""
    out, noise = [], np.zeros((33, 3))
    n = int((pre + seconds + post) * fps)
    for k in range(n):
        t = k / fps
        f = (t - pre) / seconds
        s = np.sin(np.pi * f) ** 2 * size if 0 <= f < 1 else 0.0
        lh, rh = GUARD["bow"].copy(), GUARD["melee"].copy()
        crouch, feet = 0.0, None
        if move == "kick":
            feet = {"right": 0.55 * s}
        elif move == "duck":
            crouch = 0.35 * s
            lh[2] -= crouch                   # the hands go down with you
            rh[2] -= crouch
        elif move == "shove":
            lh[2] += 0.45 * s
            rh[2] += 0.45 * s
        noise = 0.85 * noise + rng.normal(0, NOISE, (33, 3))
        pts = skeleton(lh, rh, 0, crouch, feet) + noise
        uv, _ = project(pts, aspect=ASPECT)
        lm = np.zeros((33, 4))
        lm[:, :2] = uv
        lm[:, 3] = 0.95
        out.append((t, lm))
    return out


def segments(fr, t0=0.0):
    seg = MoveSegmenter()
    got = [s for s in (seg.feed(t0 + t, lm, ASPECT) for t, lm in fr) if s is not None]
    return got


def test_segmenter_cuts_one_move_and_ignores_standing():
    rng = np.random.default_rng(0)
    assert len(segments(frames("kick", rng))) == 1
    assert segments(frames("none", rng, post=3.0)) == []


def _takes(rng, n=5):
    return {m: [segments(frames(m, rng, size=rng.uniform(0.85, 1.15)))[0] for _ in range(n)]
            for m in ("kick", "duck", "shove")}


def test_classifier_tells_moves_apart_and_rejects_small_ones():
    rng = np.random.default_rng(1)
    clf = MoveClassifier()
    clf.set_data(list(_takes(rng).items()))
    for m in ("kick", "duck", "shove") * 3:
        seg = segments(frames(m, rng, size=rng.uniform(0.85, 1.15)))[0]
        assert clf.classify(seg)["gid"] == m
    small = segments(frames("kick", rng, size=0.35))
    assert not small or clf.classify(small[0])["gid"] is None


def test_joiner_puts_a_paused_move_back_together():
    rng = np.random.default_rng(2)
    clf = MoveClassifier()
    clf.set_data(list(_takes(rng).items()))
    whole = segments(frames("shove", rng))[0]
    mid = len(whole) // 2
    first, second = whole[:mid], whole[mid:].copy()
    second[:, 0] += 0.4                       # a short pause in the middle
    j = MoveJoiner()
    r1, _ = j.offer(first, clf.classify)
    r2, used = j.offer(second, clf.classify)
    assert r1["gid"] is None and r2["gid"] == "shove" and len(used) == len(whole)


def test_neutral_sets_the_trigger_above_your_sway():
    rng = np.random.default_rng(3)
    neutral = [{"lm": lm.tolist(), "aspect": ASPECT} for _, lm in frames("none", rng, post=4.0)]
    assert 1.0 <= calibrate_start(neutral) <= 3.0


def test_engine_fires_body_moves_and_defers_to_weapons(tmp_path):
    rng = np.random.default_rng(4)
    clock = {"t": 100.0}
    keys = KeyOutput(dry_run=True, tap_ms=1)
    eng = Engine(str(tmp_path / "p.json"), keys, clock=lambda: clock["t"], front_app=lambda: "World of Warcraft",
                 profiles_dir=str(tmp_path))
    eng.settings["require_wow_front"] = False
    eng.settings["capture_style"] = "free"
    codes = {"kick": ("KeyK", 40), "duck": ("KeyD", 2)}
    for m, (key, _) in codes.items():
        eng.start_capture("ui", kind="move")
        clock["t"] += 1.0
        for _ in range(5):
            for t, lm in frames(m, rng, size=rng.uniform(0.85, 1.15), post=1.8):
                clock["t"] += 1 / 30
                eng.on_pose(lm, ASPECT, clock["t"])
        eng.finish_perform()
        assert eng.capture["phase"] == "bind" and eng.capture["kind"] == "move"
        eng.capture_key({"code": key, "mods": []})
        eng.save_capture()
    gs = {g["name"]: g for g in eng.profile["gestures"]}
    assert all(len(g["templates"]) == 5 and g["kind"] == "move" for g in gs.values())
    assert all(len(g["skeletons"]) == 5 and len(g["skeletons"][0]["frames"]) >= 6 for g in gs.values())
    eng.set_armed(True)

    def play(m, weapon_busy=False):
        start = clock["t"]
        for t, lm in frames(m, rng, post=1.8):
            clock["t"] += 1 / 30
            if weapon_busy and abs(clock["t"] - (start + 1.1)) < 1 / 60:
                eng.weapon_spans.append((start + 0.7, start + 1.4, "right"))   # a melee swing fired then
            eng.on_pose(lm, ASPECT, clock["t"])

    assert {g["name"]: g.get("auto_scope") for g in eng.profile["gestures"]} == {"Body move 1": "right_leg",
                                                                                 "Body move 2": "whole"}
    for m in ("kick", "duck", "kick"):
        play(m)
    play("duck", weapon_busy=True)     # whole body, and the weapon hand moved: left to the weapon
    play("kick", weapon_busy=True)     # judged on the right leg only: the sword arm doesn't matter
    keys.flush()
    sent = [c for c, down, _ in keys.sent if down]
    assert sent == [40, 2, 40, 40]


def test_scoped_arm_move_ignores_what_the_legs_do():
    """A move judged on the arms fires whether you stand still or kick at the same time."""
    rng = np.random.default_rng(5)
    takes = {m: [segments(frames(m, rng, size=rng.uniform(0.85, 1.15)))[0] for _ in range(5)]
             for m in ("shove", "kick")}
    from mocap.camera_moves import SCOPES, auto_scope
    assert auto_scope(takes["shove"]) == "arms"
    clf = MoveClassifier(points=SCOPES["arms"])
    clf.set_data([("shove", takes["shove"])])
    seg_arms = MoveSegmenter(points=SCOPES["arms"])
    out = []
    for t, lm in frames("shove", rng):
        r = seg_arms.feed(t, lm, ASPECT)
        if r is not None:
            out.append(clf.classify(r)["gid"])
    assert out == ["shove"]
    # The legs kicking alone never even starts an arm-scoped move.
    assert [r for r in (seg_arms.feed(10 + t, lm, ASPECT) for t, lm in frames("kick", rng)) if r is not None] == []
