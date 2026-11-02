from mocap.hotkeys import CAPTURE_HOTKEY, Hotkeys
from mocap.keys import KEYCODES, MOD_FLAGS, binding_from_event


class FakeEngine:
    def __init__(self):
        self.wants_key = False
        self.capture = None
        self.calls = []

    def capture_key(self, b):
        self.calls.append(("key", b))

    def cancel_capture(self):
        self.calls.append(("cancel",))

    def start_capture(self, trigger):
        self.calls.append(("start", trigger))


def test_option_c_opens_capture_and_is_swallowed():
    e = FakeEngine()
    h = Hotkeys(e)
    assert h.handle(CAPTURE_HOTKEY) is False
    assert e.calls == [("start", "hotkey")]
    assert h.handle({"code": "Digit1", "mods": []}) is True      # normal keys pass through


def test_bind_step_captures_the_next_key_and_escape_cancels():
    e = FakeEngine()
    e.wants_key = True
    h = Hotkeys(e)
    assert h.handle({"code": "Digit2", "mods": ["shift"]}) is False
    assert h.handle({"code": "Escape", "mods": []}) is False
    assert e.calls == [("key", {"code": "Digit2", "mods": ["shift"]}), ("cancel",)]


def test_binding_from_macos_event():
    b = binding_from_event(KEYCODES["Digit2"], MOD_FLAGS["shift"] | MOD_FLAGS["ctrl"])
    assert b == {"code": "Digit2", "mods": ["ctrl", "shift"]}
    assert binding_from_event(999, 0) is None
