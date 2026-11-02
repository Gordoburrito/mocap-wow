"""Play log and accuracy test bookkeeping."""

import json

from mocap.metrics import DRILL_READY_S, DRILL_WINDOW_S, Drill, Metrics


def test_session_counts_and_the_camera_story(tmp_path):
    m = Metrics(str(tmp_path), clock=lambda: 1_790_000_000.0)
    m.motion("melee", {"gid": "a", "gyro_gid": "a", "fused": True}, "fired", 80, 30, "Slash")
    m.motion("melee", {"gid": "b", "gyro_gid": None, "fused": True}, "fired", 90, 40, "High")
    m.motion("melee", {"gid": None, "gyro_gid": "a", "fused": True, "reason": "x"}, "rejected", 85, 35)
    m.motion("bow", {"gid": "c", "gyro_gid": "c"}, "fired", 60, None, "Punch")
    m.fire("a", "Slash", "melee", True)
    m.fire("a", "Slash", "melee", False, "disarmed")
    s = m.session()
    assert s["motions"] == 4
    assert s["fusion"] == {"agreed": 1, "rescued": 1, "vetoed": 1, "gyro_only": 1}
    assert s["held"] == {"disarmed": 1}
    assert s["latency_ms"]["median"] == 85
    lines = (tmp_path / "2026-09-21.jsonl").read_text().splitlines()
    assert len(lines) == 6 and json.loads(lines[0])["k"] == "motion"
    day = m.history()[0]
    assert day["fired"] == 3 and day["rejected"] == 1 and day["fused"] == 3


def test_disabled_without_a_folder():
    m = Metrics(None)
    m.motion("melee", {"gid": "a"}, "fired", 1, None, "x")
    assert not m.enabled and m.history() == []


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_drill_scores_right_wrong_and_missed():
    clock = Clock()
    gs = [{"id": "a", "name": "A", "source": "melee"}, {"id": "b", "name": "B", "source": "bow"}]
    d = Drill(gs, reps=2, clock=clock, seed=1)
    assert len(d.queue) == 4
    assert all(d.queue[i]["id"] != d.queue[i + 1]["id"] for i in range(3))
    answers = []
    while not d.done:
        clock.t += DRILL_READY_S + 0.01
        d.tick()
        assert d.phase == "go"
        want = d.current["id"]
        if len(answers) == 0:
            d.observe(want, "x")                    # right
        elif len(answers) == 1:
            d.observe("b" if want == "a" else "a", "x")   # wrong
        else:
            clock.t += DRILL_WINDOW_S + 0.01        # nothing happens: missed
            d.tick()
        answers.append(want)
        clock.t += 1.0
        d.tick()
    s = d.summary()
    assert s["n"] == 4 and s["accuracy"] == 25.0 and s["wrong"] == 25.0 and s["missed"] == 50.0
