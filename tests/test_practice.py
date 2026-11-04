"""Practice: do your moves and see which one it saw. Nothing is sent meanwhile, a move it doesn't
know says so (with the closest move), and keys work again as soon as practice ends."""

import numpy as np

from test_camera_moves import ASPECT, frames
from test_photos import _engine, _feed, _kicks, _until


def _with_a_kick(tmp_path):
    eng, clock = _engine(tmp_path)
    rng = np.random.default_rng(4)
    eng.start_capture("ui", kind="move")
    _kicks(eng, clock, rng, 5)
    _until(eng, clock, None, rng)
    eng.capture_key({"code": "KeyK", "mods": []})
    gid = eng.save_capture()
    eng.settings["require_wow_front"] = False
    eng.set_armed(True)
    seen = []
    eng.listeners.append(lambda m: seen.append(m) if m.get("type") == "practice" else None)
    return eng, clock, rng, gid, seen


def _sent(eng):
    eng.keys.flush()
    return [code for code, down, _ in eng.keys.sent if down]


def test_practice_shows_the_move_and_sends_nothing(tmp_path):
    eng, clock, rng, gid, seen = _with_a_kick(tmp_path)
    before = len(_sent(eng))
    eng.set_practice(True)
    for _ in range(3):
        _feed(eng, clock, frames("kick", rng, pre=0.5, post=1.2))
    hits = [m for m in seen if m["outcome"] == "hit"]
    assert len(hits) == 3 and all(m["gid"] == gid and m["name"] for m in hits)
    assert all(0 < m["match"] <= 1 and m["key"] == "K" for m in hits)
    assert len(_sent(eng)) == before                       # practising: no keys

    # Something it doesn't know: says so, with the closest move.
    seen.clear()
    _feed(eng, clock, frames("shove", rng, pre=0.5, post=1.2))
    assert seen and all(m["outcome"] in ("miss", "near") for m in seen)
    assert seen[-1]["closest"] is None or seen[-1]["closest"]["gid"] == gid

    # Practice over: the same kick presses the key again.
    eng.set_practice(False)
    seen.clear()
    _feed(eng, clock, frames("kick", rng, pre=0.5, post=1.2))
    assert not seen and len(_sent(eng)) == before + 1


def test_the_arm_pose_doesnt_toggle_while_practising(tmp_path):
    eng, clock, rng, gid, seen = _with_a_kick(tmp_path)
    g = eng.gesture(gid)
    g["action"] = "arm_toggle"                             # stand-in: any gesture set to toggle armed
    eng.set_practice(True)
    _feed(eng, clock, frames("kick", rng, pre=0.5, post=1.2))
    assert eng.armed and seen and seen[-1]["outcome"] == "hit" and seen[-1]["key"] is None
