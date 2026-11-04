"""Clean takes for body moves: a recording window's pieces become one move (not two things glued
together), a move started a beat before GO still counts, an odd take is dropped, and takes that
don't agree are flagged instead of quietly making a move that fires on everything."""

import numpy as np

from mocap import camera_moves as cm
from mocap.engine import _main_move
from test_camera_moves import ASPECT, frames
from test_photos import _engine, _feed, _until


def _cut(move, rng, size=1.0):
    seg = cm.MoveSegmenter()
    out = None
    for t, lm in frames(move, rng, size=size):
        r = seg.feed(t, lm, ASPECT)
        out = r if r is not None else out
    return out


def _shifted(seg, dt):
    seg = seg.copy()
    seg[:, 0] += dt
    return seg


def test_a_window_with_two_movements_keeps_the_move_not_both():
    rng = np.random.default_rng(1)
    kick, fidget = _cut("kick", rng), _cut("shove", rng, size=0.4)
    later = _shifted(fidget, kick[-1, 0] - fidget[0, 0] + 1.2)        # a separate, smaller movement after
    assert np.array_equal(_main_move([later, kick]), kick)
    # Two halves of one move with a brief pause are joined.
    half = len(kick) // 2
    a, b = kick[:half], _shifted(kick[half:], 0.2)
    assert len(_main_move([b, a])) == len(kick)


def test_a_move_started_just_before_go_still_counts(tmp_path):
    eng, clock = _engine(tmp_path)
    rng = np.random.default_rng(2)
    eng.start_capture("ui", kind="move")
    while eng.capture["take_phase"] == "countdown" and eng.capture["take_until"] - clock["t"] > 0.4:
        _feed(eng, clock, frames("none", rng, pre=0.0, seconds=0.01, post=0.05))
    _feed(eng, clock, frames("kick", rng, pre=0.0, post=1.0))          # starts 0.4 s before GO
    c = _until(eng, clock, "review", rng)
    assert c["take_message"].startswith("✓"), c["take_message"]


def test_odd_takes_are_dropped_and_mixed_takes_flagged():
    rng = np.random.default_rng(3)
    kicks = [_cut("kick", rng) for _ in range(4)]
    odd = _cut("shove", rng)
    assert cm.keep_takes(kicks + [odd]) == [0, 1, 2, 3]
    assert cm.keep_takes(kicks) == [0, 1, 2, 3]                        # nothing odd: keep them all
    assert cm.takes_spread(kicks) < cm.LOOSE_SPREAD
    mixed = [_cut(m, rng) for m in ("kick", "duck", "shove", "kick", "duck")]
    assert cm.takes_spread(mixed) > cm.LOOSE_SPREAD
