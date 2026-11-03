"""Run the controller: python -m mocap (or ./run.sh), or as the Mac app (mocap.app)."""

import argparse
import json
import os
import signal
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

from . import permission_name
from .camera import Camera
from .engine import Engine
from .keys import KeyOutput

ROOT = Path(__file__).resolve().parent.parent
MODEL_NAME = "pose_landmarker_full.task"


def resource_dir():
    """Where web/ and models/ live: next to the package, or inside the app bundle."""
    bundled = getattr(sys, "_MEIPASS", None)
    return Path(bundled) if bundled else ROOT


def default_data_dir():
    legacy = ROOT / "profiles"
    if legacy.is_dir() and not getattr(sys, "_MEIPASS", None):
        return ROOT                 # an older checkout kept profiles next to the code: keep using them
    return Path.home() / "Library" / "Application Support" / "Mocap"


def model_path(data_dir):
    for candidate in (resource_dir() / "models" / MODEL_NAME, Path(data_dir) / "models" / MODEL_NAME,
                      ROOT / "models" / MODEL_NAME):
        if candidate.exists():
            return candidate
    return Path(data_dir) / "models" / MODEL_NAME


def parser():
    p = argparse.ArgumentParser(description="Gesture engine: webcam poses + Joy-Con weapons -> WoW keys")
    p.add_argument("--data-dir", default=None,
                   help="where profiles and metrics live (default: ~/Library/Application Support/Mocap)")
    p.add_argument("--profile", default=None, help="profile file (default: <data>/profiles/default.json)")
    p.add_argument("--profiles-dir", default=None)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--camera", type=int, default=0, help="camera index (0 = built-in)")
    p.add_argument("--no-camera", action="store_true")
    p.add_argument("--no-controllers", action="store_true")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--no-hotkeys", action="store_true", help="skip the global ⌥C / key-capture tap")
    p.add_argument("--overlay", action="store_true", help="show the Live HUD as a panel over the game")
    p.add_argument("--dry-run", action="store_true", help="recognize but never send keys")
    p.add_argument("--sim", action="store_true",
                   help="simulated Joy-Cons (and a simulated camera that sees them with --sim-camera)")
    p.add_argument("--sim-camera", action="store_true", help="simulated webcam that sees the simulated weapons")
    p.add_argument("--sim-poses", default=None, help="JSON pose library for the simulated camera")
    p.add_argument("--demo", action="store_true",
                   help="the app with sample data on simulated hardware, in its own folder (never your real data)")
    p.add_argument("--demo-reset", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--snapshot", default=None, help=argparse.SUPPRESS)   # app: save the window as PNG, for tests
    p.add_argument("--selftest", action="store_true",
                   help="check the pose model, OpenCV, SDL (Joy-Cons) and permissions, then exit")
    return p


def selftest(data_dir):
    """Loads every native piece without needing the camera: handy after installing."""
    import numpy as np
    out = {}
    try:
        import cv2
        import mediapipe as mp
        from mediapipe.tasks.python import vision
        from mediapipe.tasks.python.core.base_options import BaseOptions
        model = model_path(data_dir)
        opts = vision.PoseLandmarkerOptions(base_options=BaseOptions(model_asset_path=str(model)),
                                            running_mode=vision.RunningMode.VIDEO, num_poses=1)
        with vision.PoseLandmarker.create_from_options(opts) as lm:
            img = np.zeros((360, 640, 3), np.uint8)
            res = lm.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=img), 0)
        ok, _ = cv2.imencode(".jpg", img)
        out["pose_model"] = f"ok ({model.name}, people found in a blank frame: {len(res.pose_landmarks)})"
        out["opencv"] = f"ok {cv2.__version__}" if ok else "jpeg encode failed"
    except Exception as exc:
        out["pose_model"] = f"FAILED: {exc}"
    try:
        from .demo import PHOTOS_DIR
        credits = json.loads((PHOTOS_DIR / "credits.json").read_text())
        found = sum((PHOTOS_DIR / c["file"]).is_file() for c in credits.values())
        out["demo_photos"] = f"ok, {found} of {len(credits)}" if found == len(credits) else f"MISSING: {found} of {len(credits)}"
    except Exception as exc:
        out["demo_photos"] = f"FAILED: {exc}"
    try:
        from .controllers import Controllers
        import sdl2
        Controllers(None).init()
        out["joycons_sdl"] = f"ok, {sdl2.SDL_NumJoysticks()} controller(s) connected now"
        sdl2.SDL_Quit()
    except Exception as exc:
        out["joycons_sdl"] = f"FAILED: {exc}"
    try:
        out["key_output"] = "allowed" if KeyOutput(dry_run=False).accessibility_ok() else \
            "needs Accessibility (System Settings → Privacy & Security → Accessibility)"
    except Exception as exc:
        out["key_output"] = f"FAILED: {exc}"
    out["data_folder"] = str(data_dir)
    return out


class Running:
    """Everything that's running, so the CLI and the Mac app can share the start-up code."""

    def __init__(self, args):
        self.args = args
        self.demo_profile = None
        if args.demo:
            # Sample data on simulated hardware, in its own folder; keys are never sent and
            # the real app's hotkeys and port are left alone.
            from . import demo
            args.sim = args.sim_camera = args.dry_run = args.no_hotkeys = True
            args.data_dir = args.data_dir or str(demo.demo_dir())
            if args.port == 8765:
                args.port = 8766
            self.demo_profile = demo.prepare(args.data_dir, reset=args.demo_reset)
            args.profile = args.profile or self.demo_profile
        if args.data_dir:
            data = Path(args.data_dir)
        elif args.sim and not args.profile and not args.profiles_dir:
            # Simulated sessions never touch your real recordings or play log.
            data = Path(tempfile.mkdtemp(prefix="mocap-sim-"))
        else:
            data = default_data_dir()
        self.data_dir = data
        profiles_dir = Path(args.profiles_dir) if args.profiles_dir else data / "profiles"
        self.profile_path = args.profile or str(profiles_dir / "default.json")
        self.keys = KeyOutput(dry_run=args.dry_run)
        self.engine = Engine(self.profile_path, self.keys, profiles_dir=str(profiles_dir),
                             metrics_dir=str(data / "metrics"), photos_dir=str(data / "photos"))
        self.engine.demo = bool(args.demo)
        self.engine.on_open_demo = launch_demo
        self.stop = threading.Event()
        self.url = f"http://127.0.0.1:{args.port}/"
        self.controllers = None
        self.sim = self.sim_camera = None

    def start(self):
        args, engine = self.args, self.engine
        if args.sim:
            from .sim.devices import SimCamera, SimRig
            self.sim = SimRig(engine, self.stop)
            if args.sim_camera or args.sim_poses:
                library = json.loads(Path(args.sim_poses).read_text()) if args.sim_poses else None
                self.sim_camera = SimCamera(engine, self.stop, library, rig=self.sim)
        from .server import Server
        Server(engine, resource_dir() / "web", port=args.port, sim=self.sim,
               sim_camera=self.sim_camera).start()

        def ticker():
            while not self.stop.is_set():
                engine.tick()
                time.sleep(0.05)

        threading.Thread(target=ticker, name="ticker", daemon=True).start()
        if self.sim_camera is not None:
            self.sim_camera.start()
        elif args.no_camera or args.sim:
            engine.set_camera(ok=False, message="Camera off")
        else:
            model = model_path(self.data_dir)
            if not model.exists():
                _download_model(model, engine)
            Camera(engine, args.camera, str(model), self.stop).start()
        if self.sim is not None:
            self.sim.start()
        if args.demo and self.sim is not None and self.sim_camera is not None:
            from .demo import DemoPlayer
            DemoPlayer(self.engine, self.sim, self.sim_camera, self.stop).start()
        if not args.no_hotkeys and not args.dry_run:
            from .hotkeys import Hotkeys
            Hotkeys(engine).start()
        if not args.no_controllers and not args.sim:
            from .controllers import Controllers
            self.controllers = Controllers(engine)
            try:
                self.controllers.init()
            except Exception as exc:
                print(f"  ! Controllers unavailable: {exc}")
                self.controllers = None
        return self

    def shutdown(self):
        self.stop.set()
        self.engine.set_armed(False)
        self.keys.release_all()
        self.keys.flush()
        self.engine.save()


def launch_demo():
    """Open the demo (sample data, simulated hardware) as a second, separate window."""
    import subprocess
    if getattr(sys, "frozen", False) or getattr(sys, "_MEIPASS", None):
        bundle = Path(sys.executable).resolve().parents[2]        # .../Mocap.app/Contents/MacOS/Mocap
        subprocess.Popen(["open", "-n", str(bundle), "--args", "--demo"])
    else:
        subprocess.Popen([sys.executable, "-m", "mocap.app", "--demo"], cwd=str(ROOT))


def _download_model(path, engine):
    """First run of the app outside a checkout: fetch the pose model (9 MB, Apache-2.0)."""
    from .camera import MODEL_URL
    import urllib.request
    engine.set_camera(ok=False, message="Downloading the pose model (9 MB)…")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(MODEL_URL, tmp)
        os.replace(tmp, path)
    except OSError as exc:
        engine.set_camera(ok=False, message=f"Couldn't download the pose model: {exc}")


def main():
    args = parser().parse_args()
    if args.selftest:
        print(json.dumps(selftest(Path(args.data_dir) if args.data_dir else default_data_dir()), indent=1))
        return
    run = Running(args)
    # Closing the terminal or a kill should still release held keys (a stuck W is no fun).
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: run.stop.set())
    run.start()
    print(f"\n  Gesture engine running → {run.url}")
    print(f"  Profile: {run.profile_path}")
    print(f"  Data:    {run.data_dir}")
    if args.dry_run:
        print("  Dry run: keys are recognized but never sent.")
    elif not run.keys.accessibility_ok():
        print(f"  ! Key output needs Accessibility permission for {permission_name()}:")
        print("    System Settings → Privacy & Security → Accessibility, then restart.")
    if args.sim:
        print("  Simulated weapons: POST /api/sim {\"role\": \"melee\", \"gesture\": \"slash_left\"}")
    print("  Ctrl+C to quit.\n")
    if not args.no_browser:
        webbrowser.open(run.url)
    try:
        if args.overlay:
            from .overlay import Overlay
            Overlay(run.engine, run.url, run.stop, run.controllers).run()
        elif run.controllers is not None:
            run.controllers.run(run.stop)
        else:
            while not run.stop.is_set():
                time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        run.shutdown()
        print("\n  Saved. Bye.")


if __name__ == "__main__":
    main()
