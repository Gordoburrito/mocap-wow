"""Global keyboard hooks (macOS event tap), so quick capture works while WoW has focus.

- Option+C opens quick capture from anywhere.
- During the "bind key" step, the next key you press is captured and swallowed: it goes to
  the engine, not to WoW. Escape cancels the capture.
- Key presses this app posts itself are tagged and pass straight through.

Needs the same Accessibility permission as key output. Without it the tap can't be created and
everything still works from the setup page.
"""

import threading

from .keys import SYNTHETIC_MARK, binding_from_event
from . import permission_name

CAPTURE_HOTKEY = {"code": "KeyC", "mods": ["alt"]}


class Hotkeys(threading.Thread):
    def __init__(self, engine, capture_hotkey=CAPTURE_HOTKEY):
        super().__init__(name="hotkeys", daemon=True)
        self.engine = engine
        self.capture_hotkey = capture_hotkey
        self.tap = None

    def run(self):
        import Quartz
        q = Quartz
        mask = q.CGEventMaskBit(q.kCGEventKeyDown)
        self.tap = q.CGEventTapCreate(q.kCGSessionEventTap, q.kCGHeadInsertEventTap,
                                      q.kCGEventTapOptionDefault, mask, self._callback, None)
        if not self.tap:
            self.engine.hotkeys = {"ok": False, "message": (
                f"Global hotkeys need Accessibility permission for {permission_name()}")}
            return
        source = q.CFMachPortCreateRunLoopSource(None, self.tap, 0)
        q.CFRunLoopAddSource(q.CFRunLoopGetCurrent(), source, q.kCFRunLoopCommonModes)
        q.CGEventTapEnable(self.tap, True)
        self.engine.hotkeys = {"ok": True, "message": "⌥C opens quick capture"}
        q.CFRunLoopRun()

    def _callback(self, proxy, event_type, event, refcon):
        import Quartz as q
        if event_type in (q.kCGEventTapDisabledByTimeout, q.kCGEventTapDisabledByUserInput):
            q.CGEventTapEnable(self.tap, True)
            return event
        if event_type != q.kCGEventKeyDown:
            return event
        if q.CGEventGetIntegerValueField(event, q.kCGEventSourceUserData) == SYNTHETIC_MARK:
            return event
        keycode = q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventKeycode)
        binding = binding_from_event(keycode, q.CGEventGetFlags(event))
        return event if self.handle(binding) else None

    def handle(self, binding):
        """Returns True to let the key through, False to swallow it."""
        if binding is None:
            return True
        if self.engine.wants_key:
            if binding == {"code": "Escape", "mods": []}:
                self.engine.cancel_capture()
            else:
                self.engine.capture_key(binding)
            return False
        if binding == self.capture_hotkey and self.engine.capture is None:
            self.engine.start_capture("hotkey")
            return False
        return True
