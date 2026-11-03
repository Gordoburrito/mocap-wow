"""Demo mode: the sample profile builds, everything in it is ready, it's recognized, and it
lives in its own folder (the real one is never touched)."""

import numpy as np

from mocap import demo
from mocap.engine import Engine
from mocap.keys import KeyOutput
from mocap.metrics import Metrics


def test_sample_profile_and_history(tmp_path):
    path = demo.prepare(tmp_path)
    eng = Engine(path, KeyOutput(dry_run=True), profiles_dir=str(tmp_path / "profiles"),
                 metrics_dir=str(tmp_path / "metrics"))
    gs = eng.profile["gestures"]
    kinds = {g["kind"] for g in gs}
    assert kinds == {"motion", "move", "pose", "jog", "hop"}
    assert all(eng._ready(g) for g in gs if g.get("action") != "arm_toggle")
    assert any(g.get("action") == "arm_toggle" for g in gs)
    assert all(len(g["templates"]) == 5 for g in gs if g["kind"] in ("motion", "move"))
    assert all(len(g["skeletons"]) == 5 for g in gs if g["kind"] == "move")
    assert {g["auto_scope"] for g in gs if g["kind"] == "move"} >= {"right_leg", "arms"}
    history = Metrics(str(tmp_path / "metrics")).history()
    assert len(history) >= 7 and any(d["drills"] for d in history)
    assert str(tmp_path) in path


def test_sample_body_moves_are_recognized(tmp_path):
    path = demo.prepare(tmp_path)
    clock = {"t": 100.0}
    keys = KeyOutput(dry_run=True, tap_ms=1)
    eng = Engine(path, keys, clock=lambda: clock["t"], front_app=lambda: "World of Warcraft",
                 profiles_dir=str(tmp_path / "profiles"))
    eng.set_armed(True)
    rng = np.random.default_rng(3)
    want = {g["demo"]["body"]: g["key"]["code"] for g in eng.profile["gestures"] if g["kind"] == "move"}
    order = ["kick", "duck", "shove", "stomp", "kick"]
    for move in order:
        for t, lm in demo.body_frames(move, rng, post=1.8):
            clock["t"] += 1 / 30
            eng.on_pose(lm, demo.ASPECT, clock["t"])
    keys.flush()
    from mocap.keys import KEYCODES
    sent = [c for c, down, _ in keys.sent if down]
    assert sent == [KEYCODES[want[m]] for m in order]


def test_sample_photos_attach_and_older_demo_folders_get_them(tmp_path, monkeypatch):
    shots = tmp_path / "shots"
    shots.mkdir()
    lm = [[0.5, 0.2 + i * 0.02, 0.9] for i in range(33)]
    credits = {}
    for slug in ("kick", "draw_aim", "arms_up"):
        (shots / f"{slug}.jpg").write_bytes(b"\xff\xd8" + slug.encode())
        credits[slug] = {"file": f"{slug}.jpg", "title": f"File:{slug}.jpg", "source": "https://commons.wikimedia.org/",
                         "author": "Someone", "license": "CC BY 4.0", "license_url": "", "aspect": 0.75, "lm": lm}
    (shots / "credits.json").write_text(__import__("json").dumps(credits))
    monkeypatch.setattr(demo, "PHOTOS_DIR", shots)
    # An older demo folder (made before photos) gets them added, without losing anything.
    profile = demo.build_profile()
    path = tmp_path / "data" / "profiles" / "hunter-sample.json"
    demo.profiles.save(str(path), profile)
    demo.prepare(tmp_path / "data")
    eng = Engine(str(path), KeyOutput(dry_run=True), profiles_dir=str(path.parent),
                 photos_dir=str(tmp_path / "data" / "photos"))
    by_name = {g["name"]: g for g in eng.profile["gestures"]}
    kick = eng.public_gesture(by_name["Muzzle kick"])
    assert kick["photo"]["credit"]["author"] == "Someone" and kick["photo"]["frames"][0]["lm"] == lm
    assert len(by_name["Muzzle kick"]["photos"]) == 5
    assert kick["take_photos"] == [None] * 5            # a sample photo isn't one of the takes
    assert eng.public_gesture(by_name["Draw and aim"])["photo"] and eng.public_gesture(by_name["Arms up: arm / disarm"])["photo"]
    assert not eng.public_gesture(by_name["Stomp"]).get("photo")          # no sample photo: drawn as before
    assert len(list((tmp_path / "data" / "photos").glob("*.jpg"))) == 3
    assert not demo.attach_photos(eng.profile, tmp_path / "data" / "photos")   # already there: no change
