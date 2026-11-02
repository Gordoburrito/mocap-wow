from mocap.keys import KEYCODES, MOD_FLAGS, KeyOutput, binding_label, valid_binding


def test_labels():
    assert binding_label({"code": "Digit2", "mods": ["shift"]}) == "Shift + 2"
    assert binding_label({"code": "Numpad3", "mods": []}) == "Num 3"
    assert binding_label({"code": "KeyF", "mods": ["alt", "ctrl"]}) == "Ctrl + Alt + F"
    assert binding_label(None) == "—"


def test_common_keys_are_mapped():
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        assert f"Key{c}" in KEYCODES
    for d in range(10):
        assert f"Digit{d}" in KEYCODES
    for f in range(1, 13):
        assert f"F{f}" in KEYCODES
    assert not valid_binding({"code": "NotAKey", "mods": []})


def test_tap_with_modifier_presses_and_releases_in_order():
    keys = KeyOutput(dry_run=True, tap_ms=1)
    keys.tap({"code": "Digit2", "mods": ["shift"]})
    keys.flush()
    shift = MOD_FLAGS["shift"]
    assert keys.sent == [(56, True, shift), (19, True, shift), (19, False, shift), (56, False, 0)]


def test_hold_and_release_all():
    keys = KeyOutput(dry_run=True)
    keys.press("g1", {"code": "KeyW", "mods": []})
    keys.press("g1", {"code": "KeyW", "mods": []})  # already held: no second key-down
    keys.release_all()
    keys.flush()
    assert keys.sent == [(13, True, 0), (13, False, 0)]
