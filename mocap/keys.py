"""Key output for macOS.

A binding is {"code": <browser KeyboardEvent.code>, "mods": ["shift", ...]}. The UI captures
bindings by having you press the key, so whatever you bound in WoW's keybinding menu is what
gets sent here. Every trigger becomes exactly one key press (tap) or one held key (hold).
"""

import queue
import threading
import time

# Browser KeyboardEvent.code -> macOS virtual keycode (ANSI layout).
KEYCODES = {
    "KeyA": 0, "KeyS": 1, "KeyD": 2, "KeyF": 3, "KeyH": 4, "KeyG": 5, "KeyZ": 6, "KeyX": 7,
    "KeyC": 8, "KeyV": 9, "KeyB": 11, "KeyQ": 12, "KeyW": 13, "KeyE": 14, "KeyR": 15,
    "KeyY": 16, "KeyT": 17, "Digit1": 18, "Digit2": 19, "Digit3": 20, "Digit4": 21,
    "Digit6": 22, "Digit5": 23, "Equal": 24, "Digit9": 25, "Digit7": 26, "Minus": 27,
    "Digit8": 28, "Digit0": 29, "BracketRight": 30, "KeyO": 31, "KeyU": 32, "BracketLeft": 33,
    "KeyI": 34, "KeyP": 35, "Enter": 36, "KeyL": 37, "KeyJ": 38, "Quote": 39, "KeyK": 40,
    "Semicolon": 41, "Backslash": 42, "Comma": 43, "Slash": 44, "KeyN": 45, "KeyM": 46,
    "Period": 47, "Tab": 48, "Space": 49, "Backquote": 50, "Backspace": 51, "Escape": 53,
    "IntlBackslash": 10,
    "F1": 122, "F2": 120, "F3": 99, "F4": 118, "F5": 96, "F6": 97, "F7": 98, "F8": 100,
    "F9": 101, "F10": 109, "F11": 103, "F12": 111, "F13": 105, "F14": 107, "F15": 113,
    "F16": 106, "F17": 64, "F18": 79, "F19": 80, "F20": 90,
    "Numpad0": 82, "Numpad1": 83, "Numpad2": 84, "Numpad3": 85, "Numpad4": 86, "Numpad5": 87,
    "Numpad6": 88, "Numpad7": 89, "Numpad8": 91, "Numpad9": 92, "NumpadDecimal": 65,
    "NumpadMultiply": 67, "NumpadAdd": 69, "NumpadSubtract": 78, "NumpadDivide": 75,
    "NumpadEnter": 76, "NumpadEqual": 81, "NumLock": 71,
    "Insert": 114, "Home": 115, "PageUp": 116, "Delete": 117, "End": 119, "PageDown": 121,
    "ArrowLeft": 123, "ArrowRight": 124, "ArrowDown": 125, "ArrowUp": 126,
}

CODES_BY_KEYCODE = {v: k for k, v in KEYCODES.items()}
SYNTHETIC_MARK = 0x6D6F6361   # "moca": marks key events this app posted itself

MOD_ORDER = ("ctrl", "alt", "shift", "cmd")
MOD_KEYCODES = {"shift": 56, "ctrl": 59, "alt": 58, "cmd": 55}
MOD_FLAGS = {"shift": 0x20000, "ctrl": 0x40000, "alt": 0x80000, "cmd": 0x100000}

_LABELS = {
    "Space": "Space", "Enter": "Enter", "Tab": "Tab", "Escape": "Esc", "Backspace": "Backspace",
    "Backquote": "`", "Minus": "-", "Equal": "=", "BracketLeft": "[", "BracketRight": "]",
    "Backslash": "\\", "Semicolon": ";", "Quote": "'", "Comma": ",", "Period": ".", "Slash": "/",
    "ArrowLeft": "←", "ArrowRight": "→", "ArrowUp": "↑", "ArrowDown": "↓", "NumLock": "Clear",
    "PageUp": "PgUp", "PageDown": "PgDn", "Delete": "Del", "IntlBackslash": "§",
}
_MOD_LABELS = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Cmd"}


def valid_binding(binding):
    return bool(binding) and binding.get("code") in KEYCODES and all(
        m in MOD_FLAGS for m in binding.get("mods", ()))


def binding_label(binding):
    if not valid_binding(binding):
        return "—"
    code = binding["code"]
    if code.startswith("Key"):
        name = code[3:]
    elif code.startswith("Digit"):
        name = code[5:]
    elif code.startswith("Numpad"):
        name = "Num " + {"Decimal": ".", "Multiply": "*", "Add": "+", "Subtract": "-",
                         "Divide": "/", "Enter": "Enter", "Equal": "="}.get(code[6:], code[6:])
    else:
        name = _LABELS.get(code, code)
    mods = [_MOD_LABELS[m] for m in MOD_ORDER if m in binding.get("mods", ())]
    return " + ".join(mods + [name])


class KeyOutput:
    """Posts key events from one worker thread so taps never block the recognizers.

    dry_run records what would have been sent in `self.sent` instead of posting events.
    """

    def __init__(self, dry_run=False, tap_ms=35):
        self.dry_run = dry_run
        self.tap_s = tap_ms / 1000.0
        self.sent = []
        self._held = {}
        self._lock = threading.Lock()
        self._q = queue.Queue()
        self._source = None
        if not dry_run:
            import Quartz
            self._quartz = Quartz
            self._source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
        threading.Thread(target=self._worker, name="keys", daemon=True).start()

    @staticmethod
    def accessibility_ok():
        try:
            from ApplicationServices import AXIsProcessTrusted
            return bool(AXIsProcessTrusted())
        except Exception:
            return False

    def tap(self, binding):
        if valid_binding(binding):
            self._q.put(("tap", binding))

    def press(self, token, binding):
        """Hold `binding` down until release(token)."""
        if not valid_binding(binding):
            return
        with self._lock:
            if token in self._held:
                return
            self._held[token] = binding
        self._q.put(("down", binding))

    def release(self, token):
        with self._lock:
            binding = self._held.pop(token, None)
        if binding:
            self._q.put(("up", binding))

    def is_held(self, token):
        with self._lock:
            return token in self._held

    def release_all(self):
        with self._lock:
            held = list(self._held.values())
            self._held.clear()
        for binding in held:
            self._q.put(("up", binding))

    def flush(self, timeout=2.0):
        """Wait for queued events to go out (used by tests and shutdown)."""
        done = threading.Event()
        self._q.put(("sync", done))
        done.wait(timeout)

    def _worker(self):
        while True:
            op, arg = self._q.get()
            if op == "sync":
                arg.set()
            elif op == "tap":
                self._chord(arg, True)
                time.sleep(self.tap_s)
                self._chord(arg, False)
            elif op == "down":
                self._chord(arg, True)
            elif op == "up":
                self._chord(arg, False)

    def _chord(self, binding, down):
        mods = [m for m in MOD_ORDER if m in binding.get("mods", ())]
        flags = 0
        for m in mods:
            flags |= MOD_FLAGS[m]
        if down:
            running = 0
            for m in mods:
                running |= MOD_FLAGS[m]
                self._post(MOD_KEYCODES[m], True, running)
            self._post(KEYCODES[binding["code"]], True, flags)
        else:
            self._post(KEYCODES[binding["code"]], False, flags)
            running = flags
            for m in reversed(mods):
                running &= ~MOD_FLAGS[m]
                self._post(MOD_KEYCODES[m], False, running)

    def _post(self, keycode, down, flags):
        if self.dry_run:
            self.sent.append((keycode, down, flags))
            return
        q = self._quartz
        event = q.CGEventCreateKeyboardEvent(self._source, keycode, down)
        q.CGEventSetFlags(event, flags)
        # Tag our own key presses so the global hotkey tap lets them straight through.
        q.CGEventSetIntegerValueField(event, q.kCGEventSourceUserData, SYNTHETIC_MARK)
        q.CGEventPost(q.kCGHIDEventTap, event)


_front_cache = {"t": 0.0, "name": None}


def frontmost_app_name(max_age=0.25):
    """Owner name of the frontmost on-screen window. Works without an AppKit run loop."""
    now = time.monotonic()
    if now - _front_cache["t"] < max_age:
        return _front_cache["name"]
    name = None
    try:
        import Quartz
        opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
        for window in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or ():
            if window.get("kCGWindowLayer") == 0:
                name = window.get("kCGWindowOwnerName")
                break
    except Exception:
        name = None
    _front_cache.update(t=now, name=name)
    return name


def binding_from_event(keycode, flags):
    """macOS keycode + event flags -> binding, or None for a key we don't support."""
    code = CODES_BY_KEYCODE.get(int(keycode))
    if code is None:
        return None
    mods = [m for m in MOD_ORDER if flags & MOD_FLAGS[m]]
    return {"code": code, "mods": mods}
