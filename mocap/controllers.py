"""Joy-Cons through SDL2: gyro, accelerometer, battery and rumble. No buttons: the Joy-Cons
live on the end of PVC weapons, so only their motion is used.

SDL runs on the main thread (macOS wants controller frameworks there). Each Joy-Con is kept
as its own controller so one can be the bow and the other the melee weapon.
"""

import ctypes
import time
import warnings

warnings.filterwarnings("ignore", message="Using SDL2 binaries from pysdl2-dll")
import sdl2  # noqa: E402

POWER = {-1: "unknown", 0: "empty", 1: "low", 2: "medium", 3: "full", 4: "wired", 5: "full"}


def _display_name(raw, taken):
    name = raw.replace("Nintendo Switch ", "").strip() or "Controller"
    if name not in taken:
        return name
    n = 2
    while f"{name} {n}" in taken:
        n += 1
    return f"{name} {n}"


class Controllers:
    def __init__(self, engine):
        self.engine = engine
        self.by_instance = {}   # instance id -> dict(ctrl, name, accel)
        self._event = sdl2.SDL_Event()
        self._last_power = 0.0

    def init(self):
        for hint, value in ((sdl2.SDL_HINT_NO_SIGNAL_HANDLERS, b"1"),
                            (sdl2.SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, b"1"),
                            (sdl2.SDL_HINT_JOYSTICK_HIDAPI_COMBINE_JOY_CONS, b"0")):
            sdl2.SDL_SetHint(hint, value)
        if sdl2.SDL_Init(sdl2.SDL_INIT_GAMECONTROLLER | sdl2.SDL_INIT_SENSOR) != 0:
            raise RuntimeError(sdl2.SDL_GetError().decode())

    def run(self, stop):
        while not stop.is_set():
            self.step(wait_ms=4)
        self.close()

    def step(self, wait_ms=0):
        """Handle whatever SDL has queued. Safe to call from a timer on the main thread."""
        e = self._event
        got = sdl2.SDL_WaitEventTimeout(ctypes.byref(e), wait_ms) if wait_ms else sdl2.SDL_PollEvent(ctypes.byref(e))
        while got:
            self._handle(e)
            got = sdl2.SDL_PollEvent(ctypes.byref(e))
        self._rumble()
        now = time.monotonic()
        if now - self._last_power > 20:
            self._last_power = now
            self._update_power()

    def close(self):
        for dev in self.by_instance.values():
            sdl2.SDL_GameControllerClose(dev["ctrl"])
        sdl2.SDL_Quit()

    def _handle(self, e):
        t = e.type
        if t == sdl2.SDL_CONTROLLERSENSORUPDATE:
            dev = self.by_instance.get(e.csensor.which)
            if dev is None:
                return
            data = (e.csensor.data[0], e.csensor.data[1], e.csensor.data[2])
            if e.csensor.sensor == sdl2.SDL_SENSOR_ACCEL:
                dev["accel"] = data
            elif e.csensor.sensor == sdl2.SDL_SENSOR_GYRO:
                ts = e.csensor.timestamp_us
                stamp = ts / 1e6 if ts else time.monotonic()
                self.engine.on_imu(dev["name"], stamp, data, dev["accel"])
        elif t == sdl2.SDL_CONTROLLERDEVICEADDED:
            self._open(e.cdevice.which)
        elif t == sdl2.SDL_CONTROLLERDEVICEREMOVED:
            dev = self.by_instance.pop(e.cdevice.which, None)
            if dev is not None:
                sdl2.SDL_GameControllerClose(dev["ctrl"])
                self.engine.on_device(dev["name"], False)

    def _open(self, index):
        ctrl = sdl2.SDL_GameControllerOpen(index)
        if not ctrl:
            return
        joystick = sdl2.SDL_GameControllerGetJoystick(ctrl)
        instance = sdl2.SDL_JoystickInstanceID(joystick)
        if instance in self.by_instance:
            return
        raw = (sdl2.SDL_GameControllerName(ctrl) or b"Controller").decode(errors="replace")
        name = _display_name(raw, {d["name"] for d in self.by_instance.values()})
        sensors = []
        for sensor, label in ((sdl2.SDL_SENSOR_GYRO, "gyro"), (sdl2.SDL_SENSOR_ACCEL, "accel")):
            if sdl2.SDL_GameControllerHasSensor(ctrl, sensor):
                sdl2.SDL_GameControllerSetSensorEnabled(ctrl, sensor, sdl2.SDL_TRUE)
                sensors.append(label)
        power = POWER.get(sdl2.SDL_JoystickCurrentPowerLevel(joystick), "unknown")
        self.by_instance[instance] = {"ctrl": ctrl, "joystick": joystick, "name": name,
                                      "accel": (0.0, 0.0, 9.81)}
        self.engine.on_device(name, True, {"sensors": sensors, "raw_name": raw, "battery": power})
        self.engine.rumble(name, 0.4, 120)

    def _update_power(self):
        for dev in self.by_instance.values():
            d = self.engine.devices.get(dev["name"])
            if d is not None:
                d.info["battery"] = POWER.get(sdl2.SDL_JoystickCurrentPowerLevel(dev["joystick"]), "unknown")

    def _rumble(self):
        q = self.engine.rumble_q
        while not q.empty():
            name, strength, ms = q.get_nowait()
            for dev in self.by_instance.values():
                if dev["name"] == name:
                    level = int(max(0.0, min(1.0, strength)) * 0xFFFF)
                    sdl2.SDL_GameControllerRumble(dev["ctrl"], level, level, ms)
