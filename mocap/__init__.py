"""Mocap: a gesture engine that turns webcam poses and Joy-Con weapon motions into WoW keys."""

# OpenCV's wheels carry their own copy of SDL2 (pulled in by ffmpeg). When two copies load,
# macOS uses the Objective-C classes (rumble, controller glue) of whichever loaded first, so the
# Joy-Con SDL must load before anything imports cv2.
try:
    import warnings as _w
    _w.filterwarnings("ignore", message="Using SDL2 binaries from pysdl2-dll")
    import sdl2 as _sdl2  # noqa: F401
except Exception:          # no SDL installed: everything except the Joy-Cons still works
    pass

__version__ = "0.3.0"


def permission_name():
    """Who macOS asks permissions for: the app itself, or the terminal running it from source."""
    import sys
    return "Mocap" if getattr(sys, "frozen", False) or getattr(sys, "_MEIPASS", None) else "your terminal app"
