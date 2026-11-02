import numpy as np

from mocap import motion
from mocap.sim.stick import CATALOGUE, Stroke, human_variation, reverse, simulate, take

MELEE, MELEE_G = CATALOGUE["melee"]
BOW, BOW_G = CATALOGUE["bow"]


def segments(stream):
    t, g, a = stream
    seg, grav, out = motion.Segmenter(), motion.GravityTracker(), []
    for i in range(len(t)):
        s = seg.feed(t[i], g[i], grav.linear(g[i], a[i], t[i]))
        if s is not None:
            out.append(s)
    return out


def recorded(stick, strokes, rng, n=5):
    out = []
    while len(out) < n:
        segs = segments(take(stick, strokes, rng))
        if len(segs) == 1:
            out.append(segs[0])
    return out


def classifier(names, stick=MELEE, catalogue=MELEE_G, seed=0, ignore=None):
    rng = np.random.default_rng(seed)
    clf = motion.SwingClassifier()
    clf.set_data([(n, "melee", recorded(stick, catalogue[n], rng), None) for n in names], ignore or {})
    return clf


def test_one_segment_per_move_and_none_for_slow_drift():
    rng = np.random.default_rng(1)
    assert len(segments(take(MELEE, MELEE_G["slash_left"], rng))) == 1
    drift = (np.arange(400) / 200, np.tile([0.8, 0.3, 0.0], (400, 1)), np.tile([0, 0, 9.81], (400, 1)))
    assert segments(drift) == []


def test_gravity_tracker_follows_rotation_so_a_chop_ends_cleanly():
    """After a chop the weapon is at a new angle; that must not read as motion."""
    rng = np.random.default_rng(2)
    stream = simulate(MELEE_G["overhead_chop"], MELEE, rng, pre=0.4, post=1.0)
    segs = segments(stream)
    assert len(segs) == 1
    assert segs[0][-1, 0] - segs[0][0, 0] < 0.8


def test_straight_line_moves_are_not_absorbed_as_gravity():
    rng = np.random.default_rng(3)
    for name in ("punch", "yank_back"):
        assert len(segments(take(BOW, BOW_G[name], rng))) == 1


def test_classifies_direction_distinct_swings():
    names = ["slash_left", "slash_right", "overhead_chop", "rising_slash"]
    clf = classifier(names)
    rng = np.random.default_rng(9)
    hits = 0
    for name in names:
        for _ in range(5):
            segs = segments(take(MELEE, MELEE_G[name], rng))
            hits += clf.classify("melee", segs[0])["gid"] == name
    assert hits >= 19


def test_calibration_reports_spread_margin_and_floors():
    clf = classifier(["slash_left", "spin"])
    info = clf.calibration["slash_left"]
    assert motion.MIN_THRESHOLD <= info["threshold"] <= motion.MAX_THRESHOLD
    assert info["nearest_gid"] == "spin" and info["margin"] > 1.0
    assert info["floor"] > 0 and info["size_floor"] > 0


def test_slow_return_stroke_is_rejected_by_the_speed_floor():
    clf = classifier(["slash_left", "slash_right"])
    rng = np.random.default_rng(4)
    slow_return = segments(simulate(reverse(MELEE_G["slash_left"], slow=3.0), MELEE, rng))
    results = [clf.classify("melee", s) for s in slow_return]
    assert all(r["gid"] is None for r in results)


def test_ignored_moves_win_over_close_gestures():
    rng = np.random.default_rng(5)
    seg = segments(take(MELEE, MELEE_G["slash_left"], rng))[0]
    clf = classifier(["slash_left"], ignore={"melee": [seg]})
    assert clf.classify("melee", seg)["gid"] is None


def test_unknown_weapon_has_nothing_recorded():
    clf = classifier(["slash_left"])
    rng = np.random.default_rng(6)
    seg = segments(take(MELEE, MELEE_G["slash_left"], rng))[0]
    assert clf.classify("bow", seg)["gid"] is None


def test_clash_needs_both_weapons_at_once():
    c = motion.ClashDetector()
    rest = np.array([0, 0, 9.81])
    for role in ("bow", "melee"):
        c.feed(role, 0.0, rest)
    assert not c.feed("bow", 1.0, rest + [50, 0, 0])          # one weapon alone
    assert not c.feed("melee", 1.5, rest + [50, 0, 0])        # too far apart in time
    c.feed("bow", 2.0, rest)        # each settles back on its own, at different times
    c.feed("melee", 2.4, rest)
    assert c.feed("bow", 3.0, rest + [0, 45, 0]) is False
    assert c.feed("melee", 3.02, rest + [40, 0, 0]) is True


def test_fast_swings_alone_do_not_look_like_a_clash():
    c = motion.ClashDetector()
    rng = np.random.default_rng(7)
    _, _, accel = take(MELEE, MELEE_G["spin"], rng)
    _, _, accel_b = take(BOW, BOW_G["point_forward"], rng)
    n = min(len(accel), len(accel_b))
    fired = [c.feed("melee", i / 200, accel[i]) or c.feed("bow", i / 200, accel_b[i]) for i in range(n)]
    assert not any(fired)
