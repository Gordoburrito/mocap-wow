"""Photos of your takes: a few webcam frames per take (start, peak, end) with the skeleton found
on each, kept with the gesture, shown on its tile; dropped when nothing uses them any more."""

import json
import struct

import numpy as np

from mocap.engine import Engine
from mocap.keys import KeyOutput
from mocap.photos import NAME, PhotoBook, peak_index
from test_camera_moves import ASPECT, frames


def _engine(tmp_path):
    clock = {"t": 100.0}
    eng = Engine(str(tmp_path / "p.json"), KeyOutput(dry_run=True), clock=lambda: clock["t"],
                 front_app=lambda: "World of Warcraft", profiles_dir=str(tmp_path),
                 photos_dir=str(tmp_path / "photos"))
    eng.settings["capture_style"] = "countdown"
    return eng, clock


def _feed(eng, clock, fr):
    """Webcam frames: landmarks, and (while capturing) the picture they were found on."""
    for _, lm in fr:
        clock["t"] += 1 / 30
        eng.on_pose(lm, ASPECT, clock["t"])
        if eng.photos_wanted:
            eng.on_photo(clock["t"], b"\xff\xd8fake" + struct.pack("d", clock["t"]))
        eng.tick()


def _until(eng, clock, phase, rng, limit=15.0):
    t0 = clock["t"]
    while eng.capture is not None and eng.capture.get("take_phase") != phase and clock["t"] - t0 < limit:
        _feed(eng, clock, frames("none", rng, pre=0.0, seconds=0.01, post=0.1))
    return eng.capture


def _kicks(eng, clock, rng, n):
    for _ in range(n):
        _until(eng, clock, "recording", rng)
        _feed(eng, clock, frames("kick", rng, pre=0.2, post=0.9))
        _until(eng, clock, "review", rng)


def _files(tmp_path):
    return sorted(p.name for p in (tmp_path / "photos").glob("*.jpg"))


def test_body_move_takes_get_photos_at_start_peak_and_end(tmp_path):
    eng, clock = _engine(tmp_path)
    rng = np.random.default_rng(1)
    eng.start_capture("ui", kind="move")
    _kicks(eng, clock, rng, 5)
    c = _until(eng, clock, None, rng)
    assert c["phase"] == "bind" and len(c["photos"]) == 5
    eng.capture_key({"code": "KeyK", "mods": []})
    gid = eng.save_capture()
    g = eng.gesture(gid)
    assert len(g["photos"]) == 5
    for rec in g["photos"]:
        assert 3 <= len(rec["frames"]) <= 5 and all(f["lm"] for f in rec["frames"])
        lm = np.array([f["lm"] for f in rec["frames"]])
        # The peak is where the kicking foot (right ankle) is furthest from where it started.
        reach = np.linalg.norm(lm[:, 28, :2] - lm[0, 28, :2], axis=1)
        assert rec["peak"] == int(np.argmax(reach)) and reach[rec["peak"]] > 0.05
        assert "28" in rec["trails"]                       # the kicking foot's path, for the overlay
    on_disk = _files(tmp_path)
    assert on_disk and all(NAME.match(n) for n in on_disk)
    pub = eng.public_gesture(g)
    assert pub["photo"]["frames"] and len(pub["take_photos"]) == 5 and "photos" not in pub
    assert all(p["file"] in on_disk for p in pub["take_photos"])


def test_photos_follow_the_takes_and_go_when_unused(tmp_path):
    eng, clock = _engine(tmp_path)
    rng = np.random.default_rng(2)
    eng.start_capture("ui", kind="move")
    _kicks(eng, clock, rng, 2)
    assert _files(tmp_path)
    eng.cancel_capture()
    assert _files(tmp_path) == []                          # cancelled: nothing kept

    eng.start_capture("ui", kind="move")
    _kicks(eng, clock, rng, 5)
    _until(eng, clock, None, rng)
    eng.capture_key({"code": "KeyK", "mods": []})
    gid = eng.save_capture()
    g = eng.gesture(gid)
    first = {f["file"] for f in g["photos"][0]["frames"]}
    # Another profile uses one of take 1's photos (a copied profile, say): that one stays.
    shared = sorted(first)[0]
    (tmp_path / "other.json").write_text(json.dumps({"gestures": [{"photos": [{"frames": [{"file": shared}]}]}]}))
    eng.handle({"op": "remove_take", "id": gid, "index": 0})
    left = set(_files(tmp_path))
    assert shared in left and not (first - {shared}) & left
    assert len(eng.gesture(gid)["photos"]) == 4
    eng.handle({"op": "delete_gesture", "id": gid})
    assert _files(tmp_path) == [shared]


def test_photo_book_picks_the_nearest_frame_and_the_peak(tmp_path):
    book = PhotoBook(tmp_path)
    for i in range(30):
        book.add(i / 15, bytes([i]) * 8)
    assert book.nearest(1.01)[0] == 1.0 and book.nearest(5.0) is None
    still = np.zeros((33, 3))
    moved = still.copy()
    moved[16, :2] = [0.3, 0.1]
    assert peak_index([still, still, moved, still], [16], 1.78) == 2
    rec = book.take([(i / 15, still if i != 15 else moved, 1.78) for i in range(30)], 0.0, 1.9, joints=[16])
    assert rec["frames"][rec["peak"]]["lm"][16][:2] == [0.3, 0.1]
    assert not NAME.match("../p.json") and not NAME.match("x.jpg")
