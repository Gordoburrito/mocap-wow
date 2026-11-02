import numpy as np

from mocap.pose import DEFAULT_THRESHOLD, PoseClassifier, PoseTrigger, features
from synth import ASPECT, pose


def samples(name, n, seed):
    rng = np.random.default_rng(seed)
    return [{"lm": pose(name, rng).tolist(), "aspect": ASPECT} for _ in range(n)]


def classifier():
    clf = PoseClassifier()
    clf.set_data([("up", samples("arms_up", 20, 1), DEFAULT_THRESHOLD),
                  ("bow", samples("bow", 20, 2), DEFAULT_THRESHOLD)],
                 samples("neutral", 40, 3))
    return clf


def test_recognizes_recorded_poses_and_rejects_neutral():
    clf = classifier()
    rng = np.random.default_rng(9)
    assert clf.classify(features(pose("arms_up", rng), ASPECT))[0] == "up"
    assert clf.classify(features(pose("bow", rng), ASPECT))[0] == "bow"
    assert clf.classify(features(pose("neutral", rng), ASPECT))[0] is None


def test_position_and_distance_from_camera_dont_matter():
    clf = classifier()
    moved = pose("bow", np.random.default_rng(4), shift=(0.08, 0.05), scale=0.7)
    assert clf.classify(features(moved, ASPECT))[0] == "bow"


def test_no_shoulders_means_no_pose():
    lm = pose("arms_up")
    lm[11, 3] = 0.1
    assert features(lm, ASPECT) is None


def test_trigger_fires_once_per_hold_and_rearms_after_release():
    trig = PoseTrigger(release_s=0.2)
    hold = lambda gid: 0.25  # noqa: E731
    events = []
    t = 0.0
    for _ in range(30):                      # hold the pose for 1 s
        events += trig.update("up", t, hold)
        t += 1 / 30
    assert events == [("latch", "up")]
    for _ in range(9):                       # drop it for 0.3 s
        events += trig.update(None, t, hold)
        t += 1 / 30
    assert events[-1] == ("release", "up")
    for _ in range(10):
        events += trig.update("up", t, hold)
        t += 1 / 30
    assert [e for e in events if e[0] == "latch"] == [("latch", "up")] * 2


def test_brief_flicker_does_not_fire():
    trig = PoseTrigger()
    hold = lambda gid: 0.25  # noqa: E731
    events = []
    for i in range(5):                       # only ~0.13 s
        events += trig.update("bow", i / 30, hold)
    events += trig.update(None, 5 / 30, hold)
    assert events == []
