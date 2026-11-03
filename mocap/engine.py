"""The hub: takes webcam poses and weapon motion, recognizes your gestures, and sends exactly
one key press (or one held key) per recognized gesture.

- Poses, motions, jog and hop only send keys while armed. A pose bound to "Toggle armed"
  (e.g. weapons crossed overhead) works armed or not.
- After a motion fires, that weapon is locked out briefly so the return stroke can't fire.
- Tapping the weapon shafts together opens quick capture; nothing fires while capturing.
- By default nothing is sent unless WoW is the front window.
- Body moves (a kick, a duck, a two-handed shove) come from the webcam alone
  (camera_moves.py); one that happens while a weapon motion fires is left to the weapon.
- When the webcam can see your weapon hand, motions are matched on the gyro AND the hand's
  path (fusion.py): the gyro decides alone when it's sure, otherwise the engine waits a
  moment for the camera. The camera also keeps the gyro calibrated (bias when you're still,
  mount slip from moves it agrees on) and works out which hand holds which weapon.
"""

import queue
import re
from pathlib import Path
import threading
import time
from collections import deque

import numpy as np

from . import profile as profiles
from .body import HopDetector, JogDetector
from . import camera_moves, permission_name
from .calibration import DeviceCalibration, StillCapture, pose_quality, trigger_from_noise
from .fusion import CameraTrack, ClockMap, HandFinder, LagFinder
from .metrics import Drill, Metrics
from .photos import PhotoBook, file_names
from .keys import binding_label, frontmost_app_name, valid_binding
from .motion import (DEFAULT_START, MIRROR_WINDOW, ClashDetector, GravityTracker, Segmenter,
                     SwingClassifier, held_back, peak, prepare)
from .pose import DEFAULT_THRESHOLD as POSE_DEFAULT, PoseClassifier, PoseTrigger, distances, features
from .pose import SCOPES as POSE_SCOPES

MAX_POSE_SAMPLES = 240
EDITABLE = {"name", "key", "mode", "action", "sensitivity", "hold_ms", "enabled", "casts",
            "tooltip", "source", "scope"}
MOVE_KEYFRAMES = 12          # skeleton frames kept per body-move take, to show what you recorded
TRAIL_JOINTS = {"left_arm": [15], "right_arm": [16], "arms": [15, 16], "left_leg": [27],
                "right_leg": [28], "legs": [27, 28], "body": [0]}
CAPTURE_READY_S = 1.2           # ignore everything right after a capture starts (the clash itself)
CAPTURE_TIMEOUT_S = 40.0
POSE_CAPTURE_S = 2.0
STILL_WINDOW_S = 0.5         # the camera must see the weapon hand still this long to re-zero its gyro
SETTLE_S = 1.0               # ...starting at least this long after a weapon motion (the slow end of a swing back)
STILL_DELAY_S = 0.3          # ...and judged this much later (the gentle start of a swing looks still at first)
POSE_STEADY_S = 0.8
CALIBRATION_STEPS = [
    ("still", "Rest the weapon on something and let go", 2.0),
    ("forward", "Point it at the screen and hold still", 2.0),
    ("up", "Point it straight up and hold still", 2.0),
    ("guard", "Stand in your guard stance and breathe normally", 4.0),
]
DEFAULT_HAND = {"melee": "right", "bow": "left"}


class Device:
    def __init__(self, name, info, role, cal_data, start):
        self.name = name
        self.info = info or {}
        self.role = role
        self.cal = DeviceCalibration(cal_data)
        self.gravity = GravityTracker()
        self.segmenter = Segmenter(start=start)
        self.energy_peak = 0.0
        self.last_emit = 0.0
        self.samples = 0
        self.rate = 0.0
        self.rate_t0 = time.monotonic()
        self.locked_until = -np.inf
        self.settle_until = -np.inf       # sensor time: no gyro re-zeroing before this
        self.mirror_block = (-np.inf, set(), "")
        self.clock = ClockMap()                 # this Joy-Con's sensor clock -> host clock
        self.finder = HandFinder()              # which wrist (on camera) holds it
        self.hand = "auto"                      # or "left" / "right", set on the Engine page
        self.queue = deque()                    # motions waiting for camera frames
        self.auto_logged = 0.0

    @property
    def side(self):
        """The camera wrist that holds this weapon."""
        if self.hand in ("left", "right"):
            return self.hand
        return self.finder.side or DEFAULT_HAND.get(self.role)


class Engine:
    def __init__(self, profile_path, keys, clock=time.monotonic, front_app=frontmost_app_name,
                 profiles_dir=None, metrics_dir=None, photos_dir=None):
        self.lock = threading.RLock()
        self.path = profile_path
        self.profiles_dir = profiles_dir
        self.keys = keys
        self.clock = clock
        self.front_app = front_app
        self.profile = profiles.load(profile_path)
        self.pose_clf = PoseClassifier()
        self.swing_clf = SwingClassifier()
        self.pose_trigger = PoseTrigger()
        self.jog = JogDetector()
        self.hop = HopDetector()
        self.clash = ClashDetector()
        self.devices = {}
        self.armed = False
        self.capture = None
        self.calib = None
        self.near = None
        self.neutral_rec = None
        self.last_captured = None
        self.listeners = []
        self.binary_listeners = []
        self.rumble_q = queue.Queue()
        self.preview_wanted = False
        self.camera = {"ok": False, "fps": 0.0, "message": "Starting camera…"}
        self.hotkeys = {"ok": False, "message": "Global hotkeys off"}
        self.activity = deque(maxlen=200)
        self.stats = {}
        self.history = {}                       # gid -> deque of (distance/threshold, outcome)
        self.pose_matches = {}
        self.motion_block_until = -np.inf       # host time; set by a clash
        self.overlay_height = None
        self.overlay_hide_requested = False     # the HUD panel's own "hide" button (the app acts on it)
        self.demo = False                       # running the demo (sample data, simulated hardware)
        self.on_open_demo = None                # how to open the demo window (set at start-up)
        self.track = CameraTrack()
        self.move_seg = camera_moves.MoveSegmenter()    # whole body: capture
        self.move_scopes = {}                           # scope -> MoveScope (live recognition)
        self.move_fires = deque(maxlen=12)              # (start, end, body points) of moves that fired
        self.move_frames = deque(maxlen=240)            # recent landmarks, for take keyframes
        # Webcam photos of each take (start, peak, end) with the skeleton on each.
        self.photos = PhotoBook(photos_dir or Path(profile_path).resolve().parent / "photos")
        self.weapon_spans = deque(maxlen=20)            # (start, end, hand) of weapon motions that fired
        self.lag_finder = LagFinder()
        self.metrics = Metrics(metrics_dir, enabled=self.profile["settings"].get("metrics", True))
        self.drill = None
        self.last_fire = None
        self._last_still = 0.0
        self._front = {"t": -1.0, "name": None}
        self._last_status = 0.0
        self._public_cache = {}
        self.rebuild()

    # ----------------------------------------------------------------- plumbing

    @property
    def settings(self):
        return self.profile["settings"]

    def gesture(self, gid):
        return next((g for g in self.profile["gestures"] if g["id"] == gid), None)

    def emit(self, msg):
        for fn in list(self.listeners):
            fn(msg)

    def emit_binary(self, data):
        for fn in list(self.binary_listeners):
            fn(data)

    @property
    def photos_wanted(self):
        """The camera keeps JPEG frames for us while a capture is recording takes."""
        c = self.capture
        return c is not None and c["phase"] == "perform"

    def on_photo(self, t, jpeg):
        """A webcam frame as JPEG, stamped like the landmarks found on it."""
        self.photos.add(t, jpeg)

    def _take_photos(self, t0, t1, joints=None, peak_t=None, trail_joints=()):
        """Photos for a take (camera time), remembered on the capture so a cancel can drop them."""
        try:
            rec = self.photos.take(list(self.move_frames), t0, t1, joints, peak_t, trail_joints)
        except OSError:
            return None
        if rec is not None and self.capture is not None:
            self.capture.setdefault("photo_files", set()).update(f["file"] for f in rec["frames"])
        return rec

    def _move_photos(self, take):
        scope = camera_moves.auto_scope([take])
        pts = [i for i in camera_moves.SCOPES[scope] if i < len(camera_moves.JOINTS)]
        joints = [camera_moves.JOINTS[i] for i in pts] or [23, 24]
        return self._take_photos(take[0, 0], take[-1, 0], joints=joints,
                                 trail_joints=TRAIL_JOINTS.get(scope) or [15, 16])

    def _photo_refs(self):
        """Photo files any profile still uses (profiles can share them)."""
        refs = file_names(self.profile["gestures"])
        if self.capture is not None:
            refs |= file_names(self.capture.get("photos") or [])
        if self.profiles_dir:
            for other in Path(self.profiles_dir).glob("*.json"):
                if Path(other).resolve() == Path(self.path).resolve():
                    continue
                try:
                    refs |= set(re.findall(r"[0-9a-f]{16}\.jpg", other.read_text()))
                except OSError:
                    pass
        return refs

    def _drop_photos(self, names):
        if names:
            self.photos.forget(names, self._photo_refs())

    def save(self):
        with self.lock:
            for name, dev in self.devices.items():
                entry = self.profile["devices"].setdefault(name, {})
                entry["role"] = dev.role
                entry["hand"] = dev.hand
                entry["calibration"] = dev.cal.to_dict()
            profiles.save(self.path, self.profile)

    def rebuild(self):
        with self.lock:
            enabled = [g for g in self.profile["gestures"] if g.get("enabled", True)]
            poses = [(g["id"], g["samples"], None, g.get("scope") if g.get("scope") in POSE_SCOPES else "whole")
                     for g in enabled if g["kind"] == "pose" and g.get("samples")]
            self.pose_clf.set_data(poses, self.profile["neutral"],
                                   {g["id"]: g.get("sensitivity", 1.0) for g in enabled})
            motions = [(g["id"], g["source"], g["templates"], None)
                       for g in enabled if g["kind"] == "motion" and g.get("templates")]
            sens = {g["id"]: g.get("sensitivity", 1.0) for g in enabled}
            # "Weapon only" motions ignore the camera: no hand paths, judged on the gyro alone.
            paths = {g["id"]: (None if g.get("scope") == "weapon" else g.get("paths"))
                     for g in enabled if g["kind"] == "motion"}
            ignore = self.profile.get("ignore", {})
            self.swing_clf.set_data(motions, {r: v for r, v in ignore.items() if r != "webcam"}, sens, paths)
            start = camera_moves.calibrate_start(self.profile["neutral"]) if self.profile["neutral"] \
                else camera_moves.START
            self.move_seg.start = start
            by_scope = {}
            for g in enabled:
                if g["kind"] == "move" and g.get("templates"):
                    by_scope.setdefault(move_scope(g), []).append((g["id"], g["templates"]))
            self.move_scopes = {}
            for scope, moves in by_scope.items():
                sc = MoveScope(scope, start)
                sc.clf.set_data(moves, ignore.get("webcam", []), sens)
                self.move_scopes[scope] = sc
            self.jog.reset()
            self._public_cache = {}

    def _wow_front(self):
        now = self.clock()
        if now - self._front["t"] > 0.25:
            self._front = {"t": now, "name": self.front_app()}
        name = self._front["name"]
        if name is None:
            return True, None
        return self.settings["wow_app"].lower() in name.lower(), name

    def _role_device(self, role):
        return next((d for d in self.devices.values() if d.role == role), None)

    # ----------------------------------------------------------------- public views

    def _visuals(self, g):
        """Data the UI draws tiles from: a trace for motions, a skeleton for poses."""
        cached = self._public_cache.get(g["id"])
        if cached is not None:
            return cached
        out = {}
        if g["kind"] == "motion" and g.get("templates"):
            seqs = [prepare(t)[0] for t in g["templates"]]
            mean = np.mean(seqs, axis=0)
            # Draw the three channels that carry this move: gyro for swings, accel for punches.
            channels = np.argsort(-mean.std(axis=0))[:3]
            out["trace"] = np.round(mean[:, np.sort(channels)], 2).tolist()
            out["takes"] = [np.round(np.linalg.norm(s[:, :3], axis=1), 2).tolist() for s in seqs]
            bank = np.stack(seqs)
            from .motion import dtw_batch
            thr = self.swing_clf.calibration.get(g["id"], {}).get("threshold", 0.6)
            out["take_match"] = [round(float(np.clip(1 - dtw_batch(s, np.delete(bank, i, 0)).min()
                                                     / (2 * thr), 0, 1)), 2) if len(bank) > 1 else 1.0
                                 for i, s in enumerate(seqs)]
        elif g["kind"] == "move" and g.get("templates"):
            seqs = np.stack([camera_moves.prepare(t) for t in g["templates"]])
            mean = seqs.mean(axis=0).reshape(len(seqs[0]), -1, 2)
            travel = np.linalg.norm(np.diff(mean, axis=0), axis=2).sum(axis=0)
            scope = move_scope(g)
            out["move"] = {"points": np.round(mean, 3).tolist(),
                           "active": [int(i) for i in np.argsort(-travel)[:3] if travel[i] > 0.15]}
            out["takes"] = [np.round(camera_moves._speeds(t), 2).tolist() for t in g["templates"]]
            skels = g.get("skeletons") or []
            if any(skels):
                # Draw what you actually recorded: the take closest to all the others.
                d = [float(np.mean([np.abs(a - b).mean() for b in seqs])) for a in seqs]
                order = [i for i in np.argsort(d) if i < len(skels) and skels[i]]
                if order:
                    out["real"] = move_keyframes(skels[order[0]], scope)
                out["take_real"] = [move_keyframes(sk, scope, compact=True) if sk else None for sk in skels]
        elif g["kind"] == "pose" and g.get("samples"):
            mid = g["samples"][len(g["samples"]) // 2]
            out["skeleton"] = [[round(p[0], 3), round(p[1], 3), round(p[3], 2)] for p in mid["lm"]]
            out["aspect"] = mid["aspect"]
        photos = g.get("photos") or []
        if any(photos):
            # Photos of you doing it: the most typical take in full (the tile plays it), and the
            # peak of every take (the contact sheet).
            if g["kind"] == "pose":
                best = len(photos) - 1
            else:
                rank = out.get("take_match")
                if g["kind"] == "move" and g.get("templates"):
                    seqs = np.stack([camera_moves.prepare(t) for t in g["templates"]])
                    rank = [-float(np.mean([np.abs(a - b).mean() for b in seqs])) for a in seqs]
                rank = list(rank or [0.0] * len(photos)) + [-np.inf] * len(photos)
                best = max((i for i, r in enumerate(photos) if r), key=lambda i: rank[i])
            out["photo"] = photos[best]
            # A sample photo (the demo's) shows what the move looks like, not one of the takes.
            out["take_photos"] = [_peak_photo(r) if r and not r.get("credit") else None for r in photos]
        self._public_cache[g["id"]] = out
        return out

    def _ready(self, g):
        if g.get("action", "key") == "key" and not valid_binding(g.get("key")):
            return False
        if g["kind"] == "pose":
            return bool(g.get("samples"))
        if g["kind"] in ("motion", "move"):
            return bool(g.get("templates"))
        return True

    def public_gesture(self, g):
        out = {k: v for k, v in g.items() if k not in ("samples", "templates", "paths", "skeletons", "photos")}
        out["scope"] = g.get("scope") or ("auto" if g["kind"] == "move" else "whole")
        if g["kind"] == "move":
            out["scope_effective"] = move_scope(g)
        if g["kind"] == "motion":
            out["camera_takes"] = sum(1 for p in g.get("paths") or [] if p is not None)
            out["uses_camera"] = g["id"] in self.swing_clf.fused
            out["twins"] = [self.gesture(t)["name"] for t in self.swing_clf.twin_of.get(g["id"], ())
                            if self.gesture(t) and g["id"] in self.swing_clf.fused]
        out["key_label"] = binding_label(g.get("key"))
        out["ready"] = self._ready(g)
        out["count"] = len(g.get("samples") or g.get("templates") or [])
        out["stats"] = self.stats.get(g["id"], {"fired": 0, "near": 0})
        cal = self.swing_clf.calibration.get(g["id"])
        pcal = self.pose_clf.calibration.get(g["id"])
        mcal = next((sc.clf.calibration[g["id"]] for sc in self.move_scopes.values()
                     if g["id"] in sc.clf.calibration), None)
        if mcal:
            nearest = mcal.get("nearest_gid")
            other = self.gesture(nearest) if nearest and nearest.startswith("g") else None
            out["calibration"] = {"threshold": round(mcal["threshold"], 3), "spread": round(mcal["spread"], 3),
                                  "margin": round(mcal["margin"], 2) if mcal.get("margin") else None,
                                  "nearest": other["name"] if other else nearest}
        if pcal and pcal.get("nearest"):
            nearest = pcal["nearest_gid"]
            other = self.gesture(nearest) if nearest and nearest.startswith("g") else None
            out["calibration"] = {"threshold": round(pcal["threshold"], 3), "spread": 0.0,
                                  "margin": round(pcal["nearest"] / pcal["threshold"], 2),
                                  "nearest": other["name"] if other else nearest}
        if cal:
            # A move matched with the camera is judged on gyro + hand path, so show that margin.
            fused = self.swing_clf.fused.get(g["id"])
            shown = fused or cal
            nearest = shown.get("nearest_gid")
            other = self.gesture(nearest) if nearest and nearest.startswith("g") else None
            out["calibration"] = {
                "threshold": round(shown["threshold"], 3), "spread": round(cal["spread"], 3),
                "margin": round(shown["margin"], 2) if shown.get("margin") else None,
                "nearest": other["name"] if other else nearest, "with_camera": bool(fused),
                "gyro_margin": round(cal["margin"], 2) if cal.get("margin") else None,
            }
        hist = self.history.get(g["id"])
        if hist:
            out["history"] = [[round(r, 2), o] for r, o in hist]
        out.update(self._visuals(g))
        return out

    def snapshot(self):
        with self.lock:
            front_ok, front_name = self._wow_front()
            return {
                "type": "state",
                "profile": {"name": self.profile["name"], "path": self.path,
                            "all": profiles.list_profiles(self.profiles_dir) if self.profiles_dir else []},
                "armed": self.armed,
                "gestures": [self.public_gesture(g) for g in self.profile["gestures"]],
                "neutral_count": len(self.profile["neutral"]),
                "ignore_counts": {r: len(s) for r, s in self.profile.get("ignore", {}).items()},
                "devices": [self._public_device(d) for d in self.devices.values()],
                "settings": self.settings,
                "keys_ok": self.keys.dry_run or self.keys.accessibility_ok(),
                "permission_app": permission_name(),
                "demo": self.demo,
                "dry_run": self.keys.dry_run,
                "camera": self.camera,
                "hotkeys": self.hotkeys,
                "front_app": front_name,
                "wow_front": front_ok,
                "capture": self._capture_public(),
                "calibration": self._calib_public(),
                "near": self._near_public(),
                "last_captured": self.last_captured,
                "activity": list(self.activity)[-40:],
                "fusion": {"enabled": self.settings["fusion"], "lag_ms": round(self.track.lag * 1000),
                           "lag_learned": self.lag_finder.lag is not None,
                           "camera_recent": self.clock() - self.track.latest < 1.0},
                "drill": self.drill.public() if self.drill else None,
                "metrics_on": self.metrics.enabled,
                "last_fire": {k: self.last_fire[k] for k in ("gid", "name", "t")} if self.last_fire else None,
            }

    def _public_device(self, d):
        cal = d.cal
        return {"name": d.name, "role": d.role, "rate": round(d.rate), "info": d.info,
                "trigger": round(d.segmenter.start, 2),
                "calibrated": {"bias": bool(np.any(cal.bias)), "mount": cal.ref_forward is not None,
                               "trigger": cal.trigger is not None},
                "locked": float(d.locked_until) if np.isfinite(d.locked_until) else None,
                "hand": d.hand, "side": d.side, "hand_found": d.finder.side,
                "hand_scores": d.finder.public(),
                "bias_dps": round(float(np.degrees(np.linalg.norm(cal.bias))), 1),
                "auto_align_deg": round(cal.auto_angle, 1)}

    def _event(self, g, outcome, detail="", sent=False, source=None):
        ev = {"type": "event", "t": time.time(), "gid": g["id"] if g else None,
              "gesture": g["name"] if g else "", "key": binding_label(g.get("key")) if g else "",
              "casts": g.get("casts", "") if g else "", "outcome": outcome, "detail": detail,
              "sent": sent, "source": source or (g["source"] if g else "")}
        self.activity.append(ev)
        self.emit(ev)

    def _stat(self, gid, field):
        s = self.stats.setdefault(gid, {"fired": 0, "near": 0})
        s[field] += 1

    # ----------------------------------------------------------------- output

    def _send(self, g, phase, needs_arm=True, match=None):
        """phase: 'tap', 'down' or 'up'. Returns True if a key went out."""
        if phase == "up":
            self.keys.release(g["id"])
            return False
        if self.capture is not None or self.calib is not None:
            return False
        if self.drill is not None:
            if phase != "up" and g.get("action") != "arm_toggle":
                self.drill.observe(g["id"], g["source"])
            return False
        if g.get("action") == "arm_toggle":
            self.set_armed(not self.armed)
            self._event(g, "armed" if self.armed else "disarmed", sent=False)
            return False
        if needs_arm and not self.armed:
            self._event(g, "not sent — disarmed")
            self.metrics.fire(g["id"], g["name"], g["source"], False, "disarmed")
            return False
        if not valid_binding(g.get("key")):
            self._event(g, "no key bound")
            self.metrics.fire(g["id"], g["name"], g["source"], False, "no key")
            return False
        if self.settings["require_wow_front"]:
            front_ok, name = self._wow_front()
            if not front_ok:
                self._event(g, "held back", f"{name or 'another app'} is in front, not WoW")
                self.metrics.fire(g["id"], g["name"], g["source"], False, "WoW not in front")
                return False
        if phase == "tap":
            self.keys.tap(g["key"])
        else:
            self.keys.press(g["id"], g["key"])
        self._stat(g["id"], "fired")
        self.metrics.fire(g["id"], g["name"], g["source"], True)
        detail = f"match {match:.2f}" if match is not None else ("held" if phase == "down" else "")
        self._event(g, "sent", detail, sent=True)
        return True

    def set_armed(self, value):
        with self.lock:
            value = bool(value)
            if value == self.armed:
                return
            self.armed = value
            if not value:
                # Let go of held keys, but remember a pose that's still being held, or a
                # "toggle armed" pose would fire again and re-arm straight away.
                for g in self.profile["gestures"]:
                    if g.get("mode") == "hold" or g["kind"] == "jog":
                        self.keys.release(g["id"])
                self.jog.reset()
        self.emit({"type": "armed", "armed": self.armed})

    def _release_all_holds(self):
        for _, gid in self.pose_trigger.reset():
            self.keys.release(gid)
        for g in self.profile["gestures"]:
            if g["kind"] == "jog":
                self.keys.release(g["id"])
        self.jog.reset()

    def set_camera(self, **status):
        with self.lock:
            changed = status.get("ok") != self.camera.get("ok") or \
                status.get("message") != self.camera.get("message")
            self.camera = {**self.camera, **status}
        if changed:
            self.emit(self.snapshot())

    def rumble(self, role_or_device, strength, ms):
        dev = self.devices.get(role_or_device) or self._role_device(role_or_device)
        if dev is not None and self.settings["rumble"]:
            self.rumble_q.put((dev.name, strength, ms))

    # ----------------------------------------------------------------- webcam

    def on_pose(self, landmarks, aspect, t):
        with self.lock:
            self.track.add(t, landmarks, aspect)
            self._camera_frame(t)
            feat = features(landmarks, aspect) if landmarks is not None else None
            n = self.neutral_rec
            if n is not None and feat is not None and n["start"] <= self.clock() < n["until"]:
                n["frames"].append({"lm": np.round(landmarks, 4).tolist(),
                                    "aspect": round(float(aspect), 4)})
            if self.capture is not None:
                self._capture_pose(feat, landmarks, aspect, t)
            best, matches = self.pose_clf.classify(feat)
            self.pose_matches = matches
            if self.capture is None and self.calib is None and self.neutral_rec is None:
                self._pose_triggers(best, t)
                if self.drill is None:
                    self._body_triggers(landmarks, aspect, t)
            self._body_moves(landmarks, aspect, t)
        lm = None
        if landmarks is not None:
            lm = np.round(np.asarray(landmarks)[:, [0, 1, 3]], 3).tolist()
        self.emit({"type": "pose", "lm": lm, "best": best,
                   "matches": {k: round(v, 3) for k, v in matches.items()},
                   "latched": self.pose_trigger.latched, "jogging": self.jog.active})

    # ----------------------------------------------------------------- body moves (webcam only)

    def _body_moves(self, landmarks, aspect, t):
        if self.calib is not None or self.neutral_rec is not None:
            return
        if landmarks is not None:
            lm = np.asarray(landmarks, dtype=float)
            self.move_frames.append((t, np.round(lm[:, [0, 1, 3]], 3), round(float(aspect), 4)))
        seg = self.move_seg.feed(t, landmarks, aspect)
        if self.capture is not None:
            self._capture_move_tick(t)
            if seg is not None:
                self._capture_move(seg, t)
            return
        live = self.settings.get("body_moves", True) and not self.jog.active
        for sc in self.move_scopes.values():
            sc.joiner.expire(t)
            piece = sc.seg.feed(t, landmarks, aspect)
            if piece is not None and live:
                self._decide_move(sc, piece)

    def _decide_move(self, sc, seg):
        """A body move cut out on one scope's body parts (whole body, right leg...)."""
        lockout = self.settings["lockout_ms"] / 1000.0
        for f0, f1, pts in self.move_fires:
            if pts & sc.points and seg[0, 0] < f1 + lockout and seg[-1, 0] > f0 - 0.1:
                return      # the way back from a move, or the same move seen by another scope
        lag = self.track.lag
        sides = camera_moves.SCOPE_SIDES[sc.name]
        if any(side in sides and s0 < seg[-1, 0] - lag + 0.1 and s1 > seg[0, 0] - lag - 0.1
               for s0, s1, side in self.weapon_spans):
            return          # that hand's weapon motion fired during it: that's what you meant
        result, used = sc.joiner.offer(seg, sc.clf.classify)
        latency = round((self.clock() - used[-1, 0]) * 1000 + sc.seg.quiet_s * 1000, 1)
        gid = result["gid"]
        until, blocked, blocker = sc.block
        if gid in blocked and used[0, 0] < until and result["matches"].get(gid, 0) < 0.75:
            self._event(self.gesture(gid), "skipped", f"looked like the way back from {blocker}")
            return
        g = self.gesture(gid) if gid else None
        if g is None:
            held = camera_moves_held(result)
            if held:
                self.move_fires.append((used[0, 0], used[-1, 0], sc.points))
            if result.get("near"):
                ng = self.gesture(result["near"])
                self._stat(ng["id"], "near")
                self.near = {"gid": ng["id"], "role": "webcam", "segment": used,
                             "match": result["matches"].get(ng["id"], 0), "t": time.time()}
                self._event(ng, "near miss", f"match {self.near['match']:.2f} · not sent")
                self.emit({"type": "near", **self._near_public()})
                self.metrics.motion("webcam", result, "near", latency, None, ng["name"], kind="move")
                if self.drill is not None:
                    self.drill.observe(None, "webcam", "near miss: " + ng["name"])
            else:
                self.metrics.motion("webcam", result, "held" if held else "rejected", latency, None, kind="move")
            return
        self.move_fires.append((used[0, 0], used[-1, 0], sc.points))
        sc.block = (used[-1, 0] + camera_moves.MIRROR_WINDOW, sc.clf.mirrors.get(gid, set()), g["name"])
        self.metrics.motion("webcam", result, "drill" if self.drill else "fired", latency, None, g["name"], kind="move")
        self.last_fire = {"gid": g["id"], "name": g["name"], "role": "webcam", "segment": used, "t": time.time()}
        self._send(g, "tap", match=result["matches"].get(gid))

    def _capture_move(self, seg, t):
        """During capture: a body move with the weapons still. Pieces with short pauses between
        them are one take; the take is kept once you've paused for a moment."""
        c = self.capture
        if c.get("style") == "countdown":
            if c["phase"] == "perform" and c["kind"] in (None, "move") and \
                    self._in_window(seg[0, 0] - self.track.lag, end=seg[-1, 0] - self.track.lag):
                c["window"]["move"].append(seg)
            return
        if c["phase"] != "perform" or self.clock() < c["ready_at"] or c["kind"] in ("pose", "motion"):
            return
        lag = self.track.lag
        if any(s0 < seg[-1, 0] - lag + 0.1 and s1 > seg[0, 0] - lag - 0.1 for s0, s1, _ in c.get("weapon_spans", [])):
            return
        held = c.get("move_hold")
        if held is not None and seg[0, 0] - held[-1, 0] <= camera_moves.JOIN_GAP:
            c["move_hold"] = np.concatenate([held, seg])
        else:
            self._commit_move_take()
            c["move_hold"] = seg

    def _keyframes(self, t0, t1):
        """The skeleton you actually recorded for a take: up to MOVE_KEYFRAMES frames."""
        frames = [(t, lm, a) for t, lm, a in self.move_frames if t0 - 0.05 <= t <= t1 + 0.05]
        if not frames:
            return None
        pick = np.linspace(0, len(frames) - 1, min(MOVE_KEYFRAMES, len(frames))).round().astype(int)
        return {"aspect": frames[0][2], "frames": [frames[i][1].tolist() for i in pick]}

    def _capture_move_tick(self, t):
        c = self.capture
        if c is not None and c.get("style") == "countdown":
            return
        held = c.get("move_hold") if c else None
        if held is not None and t - held[-1, 0] > camera_moves.JOIN_GAP:
            self._commit_move_take()

    def _commit_move_take(self):
        c = self.capture
        held, c["move_hold"] = c.get("move_hold"), None
        if held is None or c["phase"] != "perform" or c["kind"] in ("pose", "motion"):
            return
        if c["kind"] is None:
            # A move with no weapon motion and no pose held at the end of it: a body move.
            c["kind"], c["source"] = "move", "webcam"
        c["takes"].append(np.round(held, 4).tolist())
        c.setdefault("skeletons", []).append(self._keyframes(held[0, 0], held[-1, 0]))
        c.setdefault("photos", []).append(self._move_photos(held))
        c["message"] = ""
        if len(c["takes"]) >= c["target"]:
            self._finish_perform()
        else:
            self.emit({"type": "capture", **self._capture_public()})

    def _pose_triggers(self, best, t):
        default_hold = self.settings["pose_hold_ms"]

        def hold_s(gid):
            g = self.gesture(gid)
            if g is None:
                return 9e9
            ms = g.get("hold_ms") or (max(700, default_hold) if g.get("action") == "arm_toggle"
                                      else default_hold)
            return ms / 1000.0

        for kind, gid in self.pose_trigger.update(best, t, hold_s):
            g = self.gesture(gid)
            if g is None:
                continue
            if kind == "latch":
                self._send(g, "down" if g.get("mode") == "hold" else "tap")
            elif g.get("mode") == "hold":
                self._send(g, "up")

    def _body_triggers(self, landmarks, aspect, t):
        jog_gs = [g for g in self.profile["gestures"] if g["kind"] == "jog" and g.get("enabled", True)]
        hop_gs = [g for g in self.profile["gestures"] if g["kind"] == "hop" and g.get("enabled", True)]
        lm = np.asarray(landmarks) if landmarks is not None else None
        if jog_gs and self.settings["jog_enabled"]:
            ev = self.jog.update(lm, aspect, t)
            for g in jog_gs:
                if ev == "start":
                    self._send(g, "down")
                elif ev == "stop":
                    self._send(g, "up")
        hopped = self.hop.update(lm, aspect, t) == "hop" if hop_gs and self.settings["hop_enabled"] else False
        if hopped and not self.jog.active:
            for g in hop_gs:
                self._send(g, "tap")

    # ----------------------------------------------------------------- weapons

    def on_device(self, name, connected, info=None):
        with self.lock:
            if connected:
                stored = self.profile["devices"].get(name, {})
                taken = {d.role for d in self.devices.values()}
                role = stored.get("role") if stored.get("role") not in taken else None
                role = role or profiles.default_role(name, taken)
                cal = stored.get("calibration")
                start = (cal or {}).get("trigger") or DEFAULT_START
                self.devices[name] = Device(name, info, role, cal, start)
                self.devices[name].hand = stored.get("hand", "auto")
                self.profile["devices"].setdefault(name, {})["role"] = role
            else:
                self.devices.pop(name, None)
        self.emit(self.snapshot())

    def on_imu(self, name, t, gyro, accel):
        with self.lock:
            dev = self.devices.get(name)
            if dev is None:
                return
            host = self.clock()
            dev.clock.feed(t, host)
            raw_accel = np.asarray(accel, dtype=float)
            if self.calib is not None and self.calib["device"] == name:
                self._calib_sample(dev, t, gyro, raw_accel)
            gyro, accel = dev.cal.apply(t, gyro, raw_accel)
            if self.settings["clash_capture"] and dev.role and self.clash.feed(dev.role, host, raw_accel):
                self._on_clash(host)
            lin = dev.gravity.linear(gyro, accel, t)
            energy = Segmenter.energy(gyro, lin)
            dev.samples += 1
            dev.energy_peak = max(dev.energy_peak, energy)
            if self.calib is not None and self.calib["device"] == name and self.calib["step"] == "guard" \
                    and self.calib["phase"] == "capture":
                self.calib["energies"].append(energy)
            segment = dev.segmenter.feed(t, gyro, lin)
            if dev.segmenter.active or segment is not None:
                dev.settle_until = t + SETTLE_S
            if host - dev.last_emit >= 1 / 30:
                self.emit({"type": "motion", "device": name, "role": dev.role,
                           "energy": round(dev.energy_peak, 2), "start": dev.segmenter.start})
                dev.energy_peak = 0.0
                dev.last_emit = host
            if host - dev.rate_t0 >= 1.0:
                dev.rate = dev.samples / (host - dev.rate_t0)
                dev.samples, dev.rate_t0 = 0, host
            if segment is not None and self.calib is None:
                self._on_segment(dev, segment, host)
            if dev.queue:
                self._drain(dev)

    def _on_clash(self, host):
        self.motion_block_until = host + 0.8
        if self.capture is None and self.calib is None:
            self.start_capture("clash")

    def _on_segment(self, dev, segment, host):
        if host < self.motion_block_until or dev.role is None:
            return
        if self.capture is not None:
            self._capture_motion(dev, segment, host)
            return
        dev.queue.append({"seg": segment, "arrived": host, "result": None})
        self._drain(dev)

    def _fusion_ready(self, dev):
        return (self.settings["fusion"] and dev.side is not None
                and self.clock() - self.track.latest < 0.5)

    def _drain(self, dev):
        """Decide queued motions in order. A motion the gyro is sure about goes straight away;
        one that needs the camera waits (briefly) for frames covering it."""
        now = self.clock()
        while dev.queue:
            item = dev.queue[0]
            seg = item["seg"]
            if seg[0, 0] < dev.locked_until:
                dev.queue.popleft()         # the swing back after a hit
                continue
            if item["result"] is None:
                item["result"] = self.swing_clf.classify(dev.role, seg)
            r = item["result"]
            waited = None
            if not r["confident"] and r.get("needs_camera") and self._fusion_ready(dev):
                t0, t1 = dev.clock.host(seg[0, 0]), dev.clock.host(seg[-1, 0])
                if self.track.covers(t1):
                    path = self.track.path(dev.side, t0, t1)
                    if path is not None:
                        r = self.swing_clf.classify(dev.role, seg, path)
                    waited = round((now - item["arrived"]) * 1000, 1)
                elif now < item["arrived"] + self.settings["fusion_wait_ms"] / 1000.0:
                    return                  # wait for the camera
                else:
                    waited = round((now - item["arrived"]) * 1000, 1)
            dev.queue.popleft()
            self._finalize(dev, seg, r, item["arrived"], waited)

    def _learn_from(self, dev, seg):
        """Every motion: which wrist moves with this weapon, and how late the camera runs."""
        if not self.settings["fusion"] or self.clock() - self.track.latest > 0.5:
            return
        host_t = dev.clock.host(seg[:, 0])
        mag = np.linalg.norm(seg[:, 1:4], axis=1)
        dev.finder.observe(host_t, mag, self.track)
        if dev.side:
            self.lag_finder.observe(host_t, mag, self.track, dev.side)
            lag = self.lag_finder.lag
            if lag is not None:
                self.track.lag = lag

    def _finalize(self, dev, segment, result, arrived, waited_ms):
        now = self.clock()
        latency = round((now - arrived) * 1000 + 50, 1)    # + the segmenter's quiet tail
        block_until, blocked, blocker = dev.mirror_block
        if result["gid"] in blocked and segment[0, 0] < block_until \
                and result["matches"].get(result["gid"], 0) < 0.75:
            # Shaped like the swing back from the move that just fired, and not a crisp
            # match: treat it as the return, not a new strike.
            g = self.gesture(result["gid"])
            self._event(g, "skipped", f"looked like the swing back from {blocker}")
            self.metrics.motion(dev.role, result, "skipped", latency, waited_ms, g["name"] if g else None)
            return
        self._learn_from(dev, segment)
        for gid, m in result["matches"].items():
            hist = self.history.setdefault(gid, deque(maxlen=60))
            outcome = "fired" if result["gid"] == gid else "near" if result["near"] == gid \
                else "other" if result["gid"] else "none"
            hist.append((2 * (1 - m), outcome))
        g = self.gesture(result["gid"]) if result["gid"] else None
        self.emit({"type": "swing", "device": dev.name, "role": dev.role, "gid": result["gid"],
                   "reason": result["reason"], "peak": round(result["peak"], 1),
                   "fused": result.get("fused", False), "gyro_gid": result.get("gyro_gid"),
                   "matches": {k: round(v, 3) for k, v in result["matches"].items()}})
        if g is None:
            held = held_back(result)
            if held:
                # A real move we chose not to send; its swing back mustn't fire either.
                dev.locked_until = segment[-1, 0] + self.settings["lockout_ms"] / 1000.0
                dev.mirror_block = (segment[-1, 0] + MIRROR_WINDOW, self.swing_clf.mirrors.get(held, set()),
                                    (self.gesture(held) or {}).get("name", ""))
                hg = self.gesture(held)
                if hg is not None and result.get("twin"):
                    self._event(hg, "held back", "needs the camera to tell it apart from "
                                + (self.gesture(result["twin"]) or {}).get("name", "another move"))
                self.metrics.motion(dev.role, result, "held", latency, waited_ms, hg["name"] if hg else None)
                if self.drill is not None:
                    self.drill.observe(None, dev.role, result["reason"])
                return
            if result["near"]:
                ng = self.gesture(result["near"])
                self._stat(ng["id"], "near")
                self.near = {"gid": ng["id"], "role": dev.role, "segment": segment,
                             "match": result["matches"].get(ng["id"], 0), "t": time.time()}
                self._event(ng, "near miss", f"match {self.near['match']:.2f} · not sent")
                self.emit({"type": "near", **self._near_public()})
                self.metrics.motion(dev.role, result, "near", latency, waited_ms, ng["name"])
                if self.drill is not None:
                    self.drill.observe(None, dev.role, "near miss: " + ng["name"])
            else:
                self.metrics.motion(dev.role, result, "rejected", latency, waited_ms)
            return
        dev.locked_until = segment[-1, 0] + self.settings["lockout_ms"] / 1000.0
        dev.mirror_block = (segment[-1, 0] + MIRROR_WINDOW, self.swing_clf.mirrors.get(g["id"], set()), g["name"])
        self.weapon_spans.append((dev.clock.host(segment[0, 0]), dev.clock.host(segment[-1, 0]), dev.side))
        self.rumble(dev.name, 0.6 if self.armed else 0.25, 70)
        self.metrics.motion(dev.role, result, "drill" if self.drill else "fired", latency, waited_ms, g["name"])
        self._learn_alignment(dev, segment, result, g)
        self.last_fire = {"gid": g["id"], "name": g["name"], "role": dev.role, "segment": segment,
                          "t": time.time()}
        self._send(g, "tap", match=result["matches"].get(g["id"]))

    def _learn_alignment(self, dev, segment, result, g):
        """Moves recognized with confidence (the camera agreeing, or a very clean gyro match)
        teach the engine whether the Joy-Con has slipped on the pipe."""
        if not self.settings["auto_align"] or result.get("template") is None:
            return
        thr = self.swing_clf.calibration.get(g["id"], {}).get("threshold", 0)
        trusted = (result.get("fused") and g["id"] in result.get("vision", {})) or \
            (result.get("distance") is not None and result["distance"] < 0.6 * thr)
        if not trusted:
            return
        step = dev.cal.learn_alignment(prepare(segment)[0],
                                       self.swing_clf.template_seq(dev.role, result["template"]))
        if step:
            self.metrics.calibrated("auto_align", dev.name, round(step, 2))
            if time.time() - dev.auto_logged > 30:
                dev.auto_logged = time.time()
                self._event(None, "mount corrected", f"{dev.role}: {dev.cal.auto_angle:.0f}° from your last calibration",
                            source=dev.role)

    def _camera_frame(self, t):
        """Each webcam frame: finish motions waiting for it, fill capture paths, and check for
        a still weapon hand (which tells us the gyro's bias)."""
        for dev in self.devices.values():
            if dev.queue:
                self._drain(dev)
        if self.capture is not None and self.capture.get("path_jobs"):
            self._fill_paths()
        if not self.settings["camera_bias"] or t - self._last_still < 0.25:
            return
        self._last_still = t
        lag = self.track.lag
        for dev in self.devices.values():
            if dev.side is None or dev.clock.offset is None:
                continue
            w1 = t - lag - STILL_DELAY_S
            w0 = w1 - STILL_WINDOW_S
            if dev.clock.sensor(w0) < dev.settle_until:
                continue        # a motion started during or since: it wasn't really still
            if self.track.still(dev.side, w0, w1):
                change = dev.cal.camera_still(dev.clock.sensor(w0), dev.clock.sensor(w1))
                if change is not None and np.degrees(change) >= 1.0:
                    # Only real corrections count (it re-checks every quarter second you're still).
                    self.metrics.calibrated("camera_bias", dev.name, round(float(np.degrees(change)), 2))

    # ----------------------------------------------------------------- quick capture

    def _capture_public(self):
        c = self.capture
        if c is None:
            return None
        now = self.clock()
        return {k: c[k] for k in ("phase", "trigger", "kind", "source", "target", "message",
                                  "name", "casts", "mode", "tooltip_type", "tooltip_text",
                                  "editing")} | {
            "count": len(c["takes"]), "ready": now >= c["ready_at"],
            "ready_in": round(max(0.0, c["ready_at"] - now), 2),
            "key": c["key"], "key_label": binding_label(c["key"]) if c["key"] else None,
            "conflict": self._key_conflict(c["key"]),
            "auto_scope": c.get("auto_scope"),
            "style": c.get("style"), "take_phase": c.get("take_phase"), "take_message": c.get("take_message", ""),
            "take_remaining": round(max(0.0, c.get("take_until", now) - now), 2),
            "take_length": c.get("take_length", 0.0), "misses": c.get("misses", 0),
            "take_photos": [_peak_photo(r) for r in (c.get("photos") or [])],
            "pose_progress": round(min(1.0, (now - c["pose_since"]) / POSE_CAPTURE_S), 2)
            if c.get("pose_since") else 0.0,
            "traces": [np.round(np.linalg.norm(prepare(s)[0][:, :3], axis=1), 2).tolist()
                       for s in c["takes"]] if c["kind"] == "motion"
            else [np.round(camera_moves._speeds(s), 2).tolist() for s in c["takes"]] if c["kind"] == "move" else [],
        }

    def _key_conflict(self, binding):
        if not binding:
            return None
        for g in self.profile["gestures"]:
            if g.get("key") == binding and (self.capture is None or g["id"] != self.capture.get("editing")):
                return g["name"]
        return None

    def start_capture(self, trigger="ui", gid=None, kind=None):
        with self.lock:
            if self.calib is not None:
                return
            self._release_all_holds()
            editing = self.gesture(gid) if gid else None
            now = self.clock()
            old, self.capture = self.capture, None
            if old is not None:
                self._drop_photos(old.get("photo_files"))
            self.capture = {
                "phase": "perform", "trigger": trigger, "started": now,
                "ready_at": now + (CAPTURE_READY_S if trigger == "clash" else 0.6),
                "kind": editing["kind"] if editing else kind,
                "source": editing["source"] if editing else ("webcam" if kind in ("pose", "move") else None),
                "takes": [], "target": int(self.settings["capture_takes"]),
                "message": "", "key": None, "name": "", "casts": "", "mode": "tap",
                "tooltip_type": None, "tooltip_text": "", "editing": gid,
                "pose_since": None, "steady_since": None, "move_hold": None, "weapon_spans": [],
                "style": self.settings.get("capture_style", "countdown"), "misses": 0,
            }
            if self.capture["style"] == "countdown":
                # Like recording software: 3-2-1, then a few seconds of recording, per take.
                self.capture["ready_at"] = now
                self._next_take(now, first=True)
            self.move_seg.reset()
        self.emit({"type": "capture", **self._capture_public()})

    def _capture_motion(self, dev, segment, host):
        c = self.capture
        if c.get("style") == "countdown":
            if c["phase"] == "perform" and c["kind"] in (None, "motion") and \
                    self._in_window(dev.clock.host(segment[0, 0])):
                c["window"]["motion"].append((dev, segment))
            return
        if c["phase"] != "perform" or host < c["ready_at"] or c["kind"] in ("pose", "move"):
            return
        if c["source"] is None:
            c["source"], c["kind"] = dev.role, "motion"
            c["move_hold"] = None
        if dev.role != c["source"]:
            c["message"] = f"That was the {dev.role}; this move is on the {c['source']}"
            return
        if segment[0, 0] < c.get("locked_until", -np.inf):
            return      # the swing back to guard after a take, not a take
        c["locked_until"] = segment[-1, 0] + self.settings["lockout_ms"] / 1000.0
        pk = peak(segment)
        peaks = c.setdefault("peaks", [])
        if peaks and pk < 0.6 * max(peaks):
            c["message"] = "That looked like a swing back to guard, so it didn't count"
            self.emit({"type": "capture", **self._capture_public()})
            return
        if peaks and pk > 1.8 * max(peaks) and len(peaks) == 1:
            # The first "take" was the slow swing back; start over from this one.
            c["takes"], peaks[:], c["paths"], c["path_jobs"], c["photos"] = [], [], [], [], []
        peaks.append(pk)
        c.setdefault("weapon_spans", []).append((dev.clock.host(segment[0, 0]), dev.clock.host(segment[-1, 0]),
                                                 dev.side))
        c["takes"].append(np.round(segment, 4).tolist())
        c.setdefault("paths", []).append(None)
        c.setdefault("photos", []).append(None)
        c.setdefault("path_jobs", []).append((len(c["takes"]) - 1, dev.name, dev.clock.host(segment[0, 0]),
                                             dev.clock.host(segment[-1, 0]), _peak_host(dev, segment)))
        self._learn_from(dev, segment)
        c["message"] = ""
        self.rumble(dev.name, 0.35, 60)
        if len(c["takes"]) >= c["target"]:
            self._finish_perform()
        else:
            self.emit({"type": "capture", **self._capture_public()})

    def _capture_pose(self, feat, landmarks, aspect, t):
        c = self.capture
        if c.get("style") == "countdown":
            if c["phase"] == "perform" and c["kind"] in (None, "pose") and feat is not None and \
                    self._in_window(t - self.track.lag, pose=True):
                c["window"]["pose"].append(({"lm": np.round(landmarks, 4).tolist(), "aspect": round(float(aspect), 4)},
                                            bool(len(self.pose_clf.neutral[0])) and self.pose_clf.away_from_neutral(feat), t))
            return
        if c["phase"] != "perform" or self.clock() < c["ready_at"] or c["kind"] in ("motion", "move"):
            return
        if feat is None:
            c["steady_since"] = None
            return
        now = self.clock()
        if c["kind"] is None:
            # Auto-detect: a clearly non-neutral pose held steady while the weapons are still.
            if not len(self.pose_clf.neutral[0]):
                c["message"] = "Record Neutral first (Engine page), or press \"It's a pose\""
                return
            if self.pose_clf.away_from_neutral(feat):
                c["steady_since"] = c["steady_since"] or now
                if now - c["steady_since"] >= POSE_STEADY_S:
                    c["kind"], c["source"] = "pose", "webcam"
                    c["move_hold"] = None
            else:
                c["steady_since"] = None
            if c["kind"] is None:
                return
        if c["pose_since"] is None:
            c["pose_since"] = now
        c["takes"].append({"lm": np.round(landmarks, 4).tolist(), "aspect": round(float(aspect), 4)})
        if now - c["pose_since"] >= POSE_CAPTURE_S:
            mid = t - POSE_CAPTURE_S / 2
            c["photos"] = [self._take_photos(mid, mid, peak_t=mid)]
            self._finish_perform()

    # ----------------------------------------------------------------- countdown takes

    def _next_take(self, now, first=False):
        c = self.capture
        c["take_phase"] = "countdown"
        c["take_length"] = float(self.settings.get("capture_countdown_s", 3))
        c["take_until"] = now + c["take_length"] + (CAPTURE_READY_S if first and c["trigger"] == "clash" else 0.0)
        c["window"] = {"motion": [], "move": [], "pose": []}

    def _in_window(self, t, end=None, pose=False):
        """Did something that started at host time t happen while this take was recording?"""
        c = self.capture
        start, stop = c.get("rec_start"), c.get("rec_stop")
        if start is None or c.get("take_phase") not in ("recording", "checking"):
            return False
        stop = stop if stop is not None else np.inf
        if pose:
            return start + 0.4 <= t <= stop          # a held pose: once you've got into it
        return start - 0.25 <= t <= stop and (end is None or end >= start)

    def _take_tick(self, now):
        c = self.capture
        phase = c.get("take_phase")
        if phase == "countdown" and now >= c["take_until"]:
            c["take_phase"], c["rec_start"], c["rec_stop"] = "recording", now, None
            c["take_length"] = float(self.settings.get("capture_window_s", 2.5))
            c["take_until"] = now + c["take_length"]
            c["window"] = {"motion": [], "move": [], "pose": []}
        elif phase == "recording" and now >= c["take_until"]:
            # Give a move that ended at the last moment time to finish arriving (the camera lags).
            c["take_phase"], c["rec_stop"] = "checking", now
            c["take_length"] = 0.6 + self.track.lag
            c["take_until"] = now + c["take_length"]
        elif phase == "checking" and now >= c["take_until"]:
            if c["kind"] in (None, "move") and self.move_seg.active and now < c["take_until"] + 2.0:
                return                                  # a body move is still finishing
            self._judge_take(now)
        elif phase == "review" and now >= c["take_until"]:
            if c["kind"] == "pose" and c["takes"] or len(c["takes"]) >= c["target"]:
                self._finish_perform()
            else:
                self._next_take(now)

    def _judge_take(self, now):
        """Keep what was done during the recording window as this take, or ask for it again."""
        c, w = self.capture, self.capture["window"]
        kept, msg = False, ""
        if c["kind"] in (None, "motion") and w["motion"]:
            dev, seg = max(w["motion"], key=lambda x: peak(x[1]))
            if c["source"] is None:
                c["kind"], c["source"] = "motion", dev.role
            if dev.role != c["source"]:
                msg = f"That was the {dev.role}; this move is on the {c['source']}. Again:"
            else:
                c["takes"].append(np.round(seg, 4).tolist())
                c.setdefault("paths", []).append(None)
                c.setdefault("photos", []).append(None)
                c.setdefault("path_jobs", []).append((len(c["takes"]) - 1, dev.name, dev.clock.host(seg[0, 0]),
                                                     dev.clock.host(seg[-1, 0]), _peak_host(dev, seg)))
                c.setdefault("peaks", []).append(peak(seg))
                self._learn_from(dev, seg)
                self.rumble(dev.name, 0.35, 60)
                self._fill_paths()          # the camera has caught up by now: paths and photos
                kept = True
        elif c["kind"] in (None, "move") and w["move"]:
            take = np.concatenate(sorted(w["move"], key=lambda s: s[0, 0]))
            c["kind"], c["source"] = "move", "webcam"
            c["takes"].append(np.round(take, 4).tolist())
            c.setdefault("skeletons", []).append(self._keyframes(take[0, 0], take[-1, 0]))
            c.setdefault("photos", []).append(self._move_photos(take))
            kept = True
        elif c["kind"] in (None, "pose") and w["pose"]:
            away = sum(a for _, a, _ in w["pose"]) / len(w["pose"])
            if c["kind"] == "pose" or away >= 0.6:
                c["kind"], c["source"] = "pose", "webcam"
                c["takes"] = [f for f, _, _ in w["pose"]]
                mid = w["pose"][len(w["pose"]) // 2][2]
                c["photos"] = [self._take_photos(mid, mid, peak_t=mid)]
                kept = True
            elif not len(self.pose_clf.neutral[0]):
                msg = "Didn't see a move. For a pose, press \"It's a pose\" (or record Neutral first). Again:"
        if kept:
            c["misses"] = 0
            c["started"] = now                          # the capture timeout counts from the last take
            n = len(c["takes"]) if c["kind"] != "pose" else 1
            c["take_message"] = "✓ Got the pose" if c["kind"] == "pose" else f"✓ Take {n} of {c['target']}"
        else:
            c["misses"] += 1
            c["take_message"] = msg or ("Didn't catch a move that time. Again:" if c["misses"] < 3 else
                                        "Still nothing. Move bigger, check you're in view, or pick the kind below.")
        c["take_phase"] = "review"
        c["take_length"] = 0.9 if kept else 1.4
        c["take_until"] = now + c["take_length"]
        self.emit({"type": "capture", **self._capture_public()})

    def _fill_paths(self, final=False):
        """Attach the camera's view of the hand to each take once frames covering it arrive."""
        c = self.capture
        left = []
        for job in c.get("path_jobs", []):
            i, name, t0, t1, tp = job
            dev = self.devices.get(name)
            if dev is None or i >= len(c["paths"]):
                continue
            if self.track.covers(t1) or final:
                side = dev.side
                path = self.track.path(side, t0, t1) if side and self.settings["fusion"] else None
                c["paths"][i] = np.round(path, 4).tolist() if path is not None else None
                photos = c.setdefault("photos", [])
                photos += [None] * (len(c["takes"]) - len(photos))
                if photos[i] is None:
                    lag = self.track.lag        # camera frames are stamped this long after the moment
                    photos[i] = self._take_photos(t0 + lag, t1 + lag, peak_t=tp + lag,
                                                  trail_joints=[{"left": 15, "right": 16}[side]] if side else [15, 16])
            else:
                left.append(job)
        c["path_jobs"] = left

    def finish_perform(self):
        with self.lock:
            c = self.capture
            if c and c.get("move_hold") is not None:
                self._commit_move_take()
            if c and c["phase"] == "perform" and (len(c["takes"]) >= 3 or c["kind"] == "pose" and c["takes"]):
                self._finish_perform()

    def _finish_perform(self):
        c = self.capture
        c["take_phase"] = None
        if c["kind"] == "move" and c["takes"]:
            c["auto_scope"] = camera_moves.auto_scope(c["takes"])
        if c["kind"] == "motion":
            self._fill_paths(final=True)
            paths = c.get("paths") or [None] * len(c["takes"])
            photos = ((c.get("photos") or []) + [None] * len(c["takes"]))[:len(c["takes"])]
            if len(c["takes"]) >= 3:
                keep = prune_indices(c["takes"])
                c["pruned"] = len(c["takes"]) - len(keep)
                c["takes"] = [c["takes"][i] for i in keep]
                paths = [paths[i] for i in keep]
                photos = [photos[i] for i in keep]
            c["paths"], c["photos"] = paths, photos
        if c["editing"]:
            g = self.gesture(c["editing"])
            if g is not None:
                if g["kind"] == "pose":
                    g["samples"] = _cap(g["samples"] + c["takes"], MAX_POSE_SAMPLES)
                    c.setdefault("photo_files", set()).update(file_names(g.get("photos") or []))
                    g["photos"] = ((g.get("photos") or []) + [r for r in c.get("photos") or [] if r])[-4:]
                elif g["kind"] == "move":
                    old = g.get("skeletons") or []
                    old = old + [None] * (len(g["templates"]) - len(old))
                    g["templates"] = g["templates"] + c["takes"]
                    g["skeletons"] = old + (c.get("skeletons") or [None] * len(c["takes"]))
                    g["auto_scope"] = camera_moves.auto_scope(g["templates"])
                else:
                    old = g.get("paths") or []
                    old = old + [None] * (len(g["templates"]) - len(old))
                    g["templates"] = g["templates"] + c["takes"]
                    g["paths"] = old + c["paths"]
                if g["kind"] in ("move", "motion"):
                    n_old = len(g["templates"]) - len(c["takes"])
                    old = (g.get("photos") or [])[:n_old]
                    new = (c.get("photos") or [])[:len(c["takes"])]
                    g["photos"] = old + [None] * (n_old - len(old)) + new + [None] * (len(c["takes"]) - len(new))
                self.save()
                self.rebuild()
                self._event(g, "takes added", f"{len(c['takes'])} more"
                            + (f", dropped {c['pruned']} stray" if c.get("pruned") else ""))
            self.capture = None
            self._drop_photos(c.get("photo_files"))
            self.emit({"type": "capture", "phase": "done", "gid": c["editing"]})
            self.emit(self.snapshot())
            return
        c["phase"] = "bind"
        n = sum(1 for g in self.profile["gestures"] if g["source"] == c["source"] and g["kind"] == c["kind"]) + 1
        c["name"] = {"bow": f"Bow move {n}", "melee": f"Melee move {n}"}.get(
            c["source"], f"Body move {n}" if c["kind"] == "move" else f"Pose {n}")
        self.emit({"type": "capture", **self._capture_public()})

    def capture_key(self, binding):
        """Called with the next key pressed during the bind step (from the UI or a global tap)."""
        with self.lock:
            if self.capture and self.capture["phase"] == "bind" and valid_binding(binding):
                self.capture["key"] = binding
                self.emit({"type": "capture", **self._capture_public()})
                return True
        return False

    @property
    def wants_key(self):
        return bool(self.capture and self.capture["phase"] == "bind")

    def save_capture(self):
        with self.lock:
            c = self.capture
            if not c or c["phase"] != "bind" or not c["takes"]:
                return None
            g = profiles.new_gesture(c["kind"], c["source"], c["name"].strip() or None)
            g.update(key=c["key"], casts=c["casts"], mode=c["mode"],
                     tooltip={"type": c["tooltip_type"],
                              "steps": [s for s in [c["tooltip_text"].strip()] if s]})
            photos = c.get("photos") or []
            if c["kind"] == "pose":
                g["samples"] = _cap(c["takes"], MAX_POSE_SAMPLES)
                g["photos"] = [r for r in photos if r]
            elif c["kind"] == "move":
                g["templates"] = c["takes"]
                g["skeletons"] = c.get("skeletons") or [None] * len(c["takes"])
                g["auto_scope"] = c.get("auto_scope") or camera_moves.auto_scope(c["takes"])
                g["scope"] = "auto"
            else:
                g["templates"] = c["takes"]
                g["paths"] = c.get("paths") or [None] * len(c["takes"])
            if c["kind"] in ("move", "motion"):
                g["photos"] = (photos + [None] * len(c["takes"]))[:len(c["takes"])]
            self.profile["gestures"].append(g)
            self.capture = None
            self._drop_photos(c.get("photo_files"))      # takes you didn't keep
            self.last_captured = g["id"]
            self.save()
            self.rebuild()
            self._event(g, "captured", f"{len(g.get('templates') or g.get('samples'))} takes"
                        + (f", dropped {c['pruned']} stray" if c.get("pruned") else ""))
        self.emit({"type": "capture", "phase": "done", "gid": g["id"]})
        self.emit({"type": "captured", "gid": g["id"], "name": g["name"],
                   "key": binding_label(g["key"])})
        self.emit(self.snapshot())
        return g["id"]

    def cancel_capture(self):
        with self.lock:
            c, self.capture = self.capture, None
            if c is not None:
                self._drop_photos(c.get("photo_files"))
        self.emit({"type": "capture", "phase": "cancelled"})
        self.emit(self.snapshot())

    def undo_capture(self):
        with self.lock:
            gid, self.last_captured = self.last_captured, None
            if gid:
                gone = file_names(self.gesture(gid) or {})
                self.profile["gestures"] = [g for g in self.profile["gestures"] if g["id"] != gid]
                self._drop_photos(gone)
                self.save()
                self.rebuild()
        self.emit(self.snapshot())

    # ----------------------------------------------------------------- calibration wizard

    def _calib_public(self):
        c = self.calib
        if c is None:
            return None
        now = self.clock()
        step = CALIBRATION_STEPS[c["index"]]
        return {"device": c["device"], "step": c["step"], "index": c["index"],
                "total": len(CALIBRATION_STEPS), "instruction": step[1], "phase": c["phase"],
                "remaining": round(max(0.0, c["until"] - now), 2), "message": c["message"],
                "results": c["results"]}

    def start_calibration(self, device):
        with self.lock:
            if device not in self.devices or self.capture is not None:
                return
            self._release_all_holds()
            self.calib = {"device": device, "index": 0, "step": CALIBRATION_STEPS[0][0],
                          "phase": "countdown", "until": self.clock() + 2.5, "message": "",
                          "results": {}, "still": None, "energies": []}
        self.emit({"type": "calibration", **self._calib_public()})

    def cancel_calibration(self):
        with self.lock:
            self.calib = None
        self.emit(self.snapshot())

    def _calib_sample(self, dev, t, gyro, accel):
        c = self.calib
        if c["phase"] == "capture" and c["step"] != "guard" and c["still"] is not None:
            c["still"].feed(t, gyro, accel)

    def _calib_advance(self):
        c = self.calib
        now = self.clock()
        name, _, seconds = CALIBRATION_STEPS[c["index"]]
        if c["phase"] == "countdown" and now >= c["until"]:
            c["phase"], c["until"] = "capture", now + seconds
            c["still"] = StillCapture(seconds) if name != "guard" else None
            c["energies"] = []
            return
        if c["phase"] != "capture" or now < c["until"]:
            return
        dev = self.devices.get(c["device"])
        if dev is None:
            self.calib = None
            return
        if name == "guard":
            trigger = trigger_from_noise(c["energies"])
            if trigger:
                dev.cal.trigger = trigger
                dev.segmenter.start = trigger
            c["results"]["trigger"] = trigger
        else:
            res = c["still"].result()
            if not res["ok"]:
                c["message"] = "Moved too much. Let's do that step again."
                c["phase"], c["until"] = "countdown", now + 2.0
                return
            if name == "still":
                dev.cal.bias = np.asarray(res["gyro_bias"])
                c["results"]["bias"] = [round(x, 3) for x in res["gyro_bias"]]
            else:
                # Gravity seen in the Joy-Con's own frame, before any alignment.
                c["results"][name] = res["gravity"]
        c["message"] = ""
        if name == "up" and "forward" in c["results"]:
            quality = pose_quality(c["results"]["forward"], c["results"]["up"])
            c["results"]["pose_angle"] = round(quality, 1)
            if quality < 30:
                c["message"] = "Those two poses were too similar. Point clearly forward, then up."
                c["index"] = 1
                c["step"], c["phase"], c["until"] = "forward", "countdown", now + 2.0
                return
            had_reference = dev.cal.ref_forward is not None
            has_takes = any(g["kind"] == "motion" and g["source"] == dev.role and g.get("templates")
                            for g in self.profile["gestures"])
            if had_reference and has_takes:
                dev.cal.realign(c["results"]["forward"], c["results"]["up"])
                c["results"]["realigned"] = True
            else:
                dev.cal.set_reference(c["results"]["forward"], c["results"]["up"])
        c["index"] += 1
        if c["index"] >= len(CALIBRATION_STEPS):
            results = c["results"]
            self.calib = None
            self.save()
            self.emit({"type": "calibration", "phase": "done", "device": dev.name, "results": results})
            self.emit(self.snapshot())
            return
        c["step"] = CALIBRATION_STEPS[c["index"]][0]
        c["phase"], c["until"] = "countdown", now + 2.0

    # ----------------------------------------------------------------- clock

    def tick(self):
        with self.lock:
            now = self.clock()
            if self.capture is not None and self.capture.get("style") == "countdown" \
                    and self.capture["phase"] == "perform":
                self._take_tick(now)
            if self.capture is not None:
                if now - self.capture["started"] > CAPTURE_TIMEOUT_S and self.capture["phase"] == "perform":
                    c, self.capture = self.capture, None
                    self._drop_photos(c.get("photo_files"))
                    self.emit({"type": "capture", "phase": "cancelled", "message": "Timed out"})
                else:
                    self.emit({"type": "capture", **self._capture_public()})
            self._neutral_tick(now)
            if self.drill is not None:
                self._drill_tick()
            for dev in self.devices.values():
                if dev.queue:
                    self._drain(dev)
            if self.calib is not None:
                self._calib_advance()
                if self.calib is not None:
                    self.emit({"type": "calibration", **self._calib_public()})
            if now - self._last_status >= 1.0:
                self._last_status = now
                self.emit(self.snapshot())

    # ----------------------------------------------------------------- near misses

    def _near_public(self):
        n = self.near
        if n is None:
            return None
        g = self.gesture(n["gid"])
        return {"gid": n["gid"], "name": g["name"] if g else "", "match": round(n["match"], 2),
                "t": n["t"]}

    def resolve_near(self, choice):
        with self.lock:
            n, self.near = self.near, None
            if n is None:
                return
            if choice == "dismiss":         # "Not now": no answer, nothing changes
                self.emit(self.snapshot())
                return
            g = self.gesture(n["gid"])
            if g is not None:
                self.metrics.correction(choice, g["id"], g["name"])
            if choice == "loosen" and g is not None:
                g["sensitivity"] = float(min(2.0, g.get("sensitivity", 1.0) * 1.15))
                self._event(g, "loosened", f"sensitivity {g['sensitivity']:.2f}")
            elif choice == "ignore":
                self.profile.setdefault("ignore", {}).setdefault(n["role"], []).append(
                    np.round(n["segment"], 4).tolist())
                self._event(g, "move ignored", "won't fire on that move again")
            self.save()
            self.rebuild()
        self.emit(self.snapshot())

    # ----------------------------------------------------------------- profiles

    def switch_profile(self, path):
        with self.lock:
            self._release_all_holds()
            self.save()
            self.path = path
            self.profile = profiles.load(path)
            self.stats, self.history = {}, {}
            for name, dev in list(self.devices.items()):
                stored = self.profile["devices"].get(name, {})
                if stored.get("calibration"):
                    dev.cal = DeviceCalibration(stored["calibration"])
                    dev.segmenter.start = dev.cal.trigger or DEFAULT_START
                if stored.get("role"):
                    dev.role = stored["role"]
            self.rebuild()
        self.emit(self.snapshot())

    def new_profile(self, name, copy=False):
        import copy as _copy
        import os
        directory = self.profiles_dir or os.path.dirname(self.path)
        path = os.path.join(directory, profiles.slug(name) + ".json")
        n = 2
        while os.path.exists(path):
            path = os.path.join(directory, f"{profiles.slug(name)}-{n}.json")
            n += 1
        data = _copy.deepcopy(self.profile) if copy else profiles.empty_profile(name)
        data["name"] = name
        data["devices"] = _copy.deepcopy(self.profile["devices"])   # keep weapon calibration
        profiles.save(path, data)
        self.switch_profile(path)

    # ----------------------------------------------------------------- UI ops

    def handle(self, op):
        name = op.get("op")
        reply = None
        with self.lock:
            if name == "add_gesture":
                g = profiles.new_gesture(op.get("kind", "pose"), op.get("source"), op.get("name"))
                self.profile["gestures"].append(g)
                reply = {"type": "added", "id": g["id"]}
            elif name == "update_gesture":
                g = self.gesture(op.get("id"))
                if g is None:
                    return None
                for k, v in op.get("fields", {}).items():
                    if k in EDITABLE:
                        g[k] = v
                if self.keys.is_held(g["id"]):
                    self.keys.release(g["id"])
            elif name == "delete_gesture":
                self.keys.release(op.get("id"))
                gone = file_names(self.gesture(op.get("id")) or {})
                self.profile["gestures"] = [g for g in self.profile["gestures"]
                                            if g["id"] != op.get("id")]
                self._drop_photos(gone)
            elif name == "move_gesture":
                gs = self.profile["gestures"]
                g = self.gesture(op.get("id"))
                if g is not None:
                    i = gs.index(g)
                    j = max(0, min(len(gs) - 1, i + int(op.get("by", 0))))
                    gs.insert(j, gs.pop(i))
            elif name == "clear_samples":
                g = self.gesture(op.get("id"))
                if g is not None:
                    gone = file_names(g.pop("photos", None) or [])
                    self._drop_photos(gone)
                    if g["kind"] == "pose":
                        g["samples"] = []
                    elif g["kind"] == "motion":
                        g["templates"] = []
                        g["paths"] = []
                    elif g["kind"] == "move":
                        g["templates"] = []
                        g["skeletons"] = []
            elif name == "remove_take":
                g = self.gesture(op.get("id"))
                i = int(op.get("index", -1))
                if g is not None and g["kind"] in ("motion", "move") and 0 <= i < len(g["templates"]):
                    g["templates"].pop(i)
                    gone = set()
                    for k in ("paths", "skeletons", "photos"):
                        if i < len(g.get(k) or []):
                            gone |= file_names(g[k].pop(i) or {}) if k == "photos" else set()
                    self._drop_photos(gone)
                    if g["kind"] == "move" and g["templates"]:
                        g["auto_scope"] = camera_moves.auto_scope(g["templates"])
            elif name == "clear_neutral":
                self.profile["neutral"] = []
            elif name == "clear_ignore":
                self.profile.get("ignore", {}).pop(op.get("role"), None)
            elif name == "record_neutral":
                self._start_neutral()
                return None
            elif name == "neutral_cancel":
                if self.neutral_rec is not None:
                    self.neutral_rec = None
                    self.emit({"type": "neutral", "phase": "cancelled"})
                return None
            elif name == "open_demo":
                if self.on_open_demo is not None and not self.demo:
                    self.on_open_demo()
                return None
            elif name == "overlay_hide":
                self.overlay_hide_requested = True
                return None
            elif name == "capture_start":
                self.start_capture(op.get("trigger", "ui"), op.get("id"), op.get("kind"))
                return None
            elif name == "capture_finish":
                self.finish_perform()
                return None
            elif name == "capture_key":
                self.capture_key(op.get("key"))
                return None
            elif name == "capture_update":
                if self.capture and self.capture["phase"] == "bind":
                    for k in ("name", "casts", "mode", "tooltip_type", "tooltip_text"):
                        if k in op.get("fields", {}):
                            self.capture[k] = op["fields"][k]
                    self.emit({"type": "capture", **self._capture_public()})
                return None
            elif name == "capture_save":
                gid = self.save_capture()
                return {"type": "added", "id": gid} if gid else None
            elif name == "capture_cancel":
                self.cancel_capture()
                return None
            elif name == "capture_undo":
                self.undo_capture()
                return None
            elif name == "near":
                self.resolve_near(op.get("choice"))
                return None
            elif name == "calibrate":
                self.start_calibration(op.get("device"))
                return None
            elif name == "calibrate_cancel":
                self.cancel_calibration()
                return None
            elif name == "device_role":
                dev = self.devices.get(op.get("device"))
                role = op.get("role")
                if dev is not None and role in profiles.ROLES + (None,):
                    other = self._role_device(role)
                    if other is not None and other is not dev:
                        other.role = dev.role
                    dev.role = role
            elif name == "device_hand":
                dev = self.devices.get(op.get("device"))
                if dev is not None and op.get("hand") in ("auto", "left", "right"):
                    dev.hand = op["hand"]
                    self.profile["devices"].setdefault(dev.name, {})["hand"] = dev.hand
            elif name == "drill_start":
                self.start_drill(op.get("ids"), int(op.get("reps", 3)))
                return None
            elif name == "drill_stop":
                self.stop_drill()
                return None
            elif name == "report_wrong":
                self.report_wrong(bool(op.get("ignore", True)))
                return None
            elif name == "set_armed":
                self.set_armed(op.get("value"))
                self.emit(self.snapshot())
                return None
            elif name == "settings":
                for k, v in op.get("fields", {}).items():
                    if k in profiles.DEFAULT_SETTINGS:
                        self.settings[k] = v
            elif name == "test_key":
                g = self.gesture(op.get("id"))
                if g is not None:
                    self._event(g, "test in 3 s", "switch to WoW now")
                    threading.Timer(3.0, self._test_fire, args=(g["id"],)).start()
                return None
            elif name == "profile_switch":
                path = op.get("path")
                if path and any(p["path"] == path for p in profiles.list_profiles(self.profiles_dir)):
                    self.switch_profile(path)
                return None
            elif name == "profile_new":
                self.new_profile(op.get("name") or "New profile", bool(op.get("copy")))
                return None
            elif name == "profile_rename":
                self.profile["name"] = op.get("name") or self.profile["name"]
            elif name == "preview":
                self.preview_wanted = bool(op.get("on"))
                return None
            elif name == "overlay_size":
                self.overlay_height = op.get("height")
                return None
            else:
                return None
            self.save()
            self.rebuild()
        self.emit(self.snapshot())
        return reply

    # ----------------------------------------------------------------- accuracy test

    def start_drill(self, ids=None, reps=3):
        with self.lock:
            if self.capture is not None or self.calib is not None:
                return
            gs = [g for g in self.profile["gestures"] if g.get("enabled", True) and self._ready(g)
                  and g["kind"] in ("pose", "motion", "move") and g.get("action") != "arm_toggle"
                  and (not ids or g["id"] in ids)]
            if not gs:
                self.emit({"type": "drill", "phase": "empty"})
                return
            self._release_all_holds()
            self.drill = Drill(gs, reps=max(1, min(reps, 10)), clock=self.clock)
        self.emit({"type": "drill", **self.drill.public()})

    def stop_drill(self):
        with self.lock:
            d, self.drill = self.drill, None
            if d is not None and d.results:
                self.metrics.drill_result(d.summary())
        self.emit({"type": "drill", "phase": "stopped"})
        self.emit(self.snapshot())

    def _drill_tick(self):
        d = self.drill
        d.tick()
        if d.done:
            summary = d.summary()
            self.metrics.drill_result(summary)
            self.drill = None
            self.emit({"type": "drill", "phase": "done", "summary": summary})
            self.emit(self.snapshot())
            return
        self.emit({"type": "drill", **d.public()})

    def report_wrong(self, ignore=True):
        """The HUD's "that wasn't me" on the last motion that fired."""
        with self.lock:
            f, self.last_fire = self.last_fire, None
            if f is None:
                return
            self.metrics.correction("wrong", f["gid"], f["name"])
            g = self.gesture(f["gid"])
            if ignore:
                self.profile.setdefault("ignore", {}).setdefault(f["role"], []).append(
                    np.round(f["segment"], 4).tolist())
                self.save()
                self.rebuild()
            self._event(g, "marked wrong", "won't fire on that move again" if ignore else "")
        self.emit(self.snapshot())

    def _start_neutral(self):
        """Neutral = a few seconds of you just standing around: the 'not a pose' bank."""
        now = self.clock()
        self.neutral_rec = {"start": now + 3.0, "until": now + 9.0, "frames": []}
        self.emit({"type": "neutral", "phase": "countdown", "remaining": 3.0})

    def _neutral_tick(self, now):
        n = self.neutral_rec
        if n is None:
            return
        if now < n["start"]:
            self.emit({"type": "neutral", "phase": "countdown", "remaining": round(n["start"] - now, 2)})
        elif now < n["until"]:
            self.emit({"type": "neutral", "phase": "capture", "remaining": round(n["until"] - now, 2),
                       "count": len(n["frames"])})
        else:
            self.neutral_rec = None
            if n["frames"]:
                self.profile["neutral"] = _cap(self.profile["neutral"] + n["frames"], MAX_POSE_SAMPLES * 2)
                self.save()
                self.rebuild()
            self.emit({"type": "neutral", "phase": "done", "count": len(n["frames"])})
            self.emit(self.snapshot())

    def _test_fire(self, gid):
        with self.lock:
            g = self.gesture(gid)
            if g is not None:
                self._send(g, "tap", needs_arm=False)


class MoveScope:
    """Live recognition of the body moves judged on one scope: its own segmenter (so the rest of
    your body moving doesn't start or spoil a move), recognizer and piece joiner."""

    def __init__(self, scope, start):
        pts = camera_moves.SCOPES[scope]
        self.name = scope
        self.points = set(pts)
        grounded = scope in camera_moves.GROUNDED
        self.seg = camera_moves.MoveSegmenter(start=start, points=pts, grounded=grounded)
        self.clf = camera_moves.MoveClassifier(points=pts, grounded=grounded)
        self.joiner = camera_moves.MoveJoiner()
        self.block = (-np.inf, set(), "")


def _peak_host(dev, segment):
    """Host time of the fastest moment of a weapon motion (where its photo is taken)."""
    seg = np.asarray(segment)
    return dev.clock.host(seg[int(np.argmax(np.linalg.norm(seg[:, 1:4], axis=1))), 0])


def _peak_photo(rec):
    """One frame of a take's photos (the peak) for small pictures: file, skeleton, aspect."""
    if not rec or not rec.get("frames"):
        return None
    f = rec["frames"][min(rec.get("peak", 0), len(rec["frames"]) - 1)]
    return {"file": f["file"], "lm": f.get("lm"), "aspect": rec.get("aspect"), "credit": rec.get("credit")}


def move_scope(g):
    """The scope a body move is judged on: what you chose, or (auto) the parts that moved."""
    scope = g.get("scope") or "auto"
    if scope == "auto":
        scope = g.get("auto_scope") or "whole"
    return scope if scope in camera_moves.SCOPES else "whole"


def move_keyframes(skel, scope, compact=False):
    """Recorded skeleton frames -> what the UI draws: first, furthest and last frames plus
    trails for the joints that matter for this scope."""
    frames = np.asarray(skel["frames"], dtype=float)          # (k, 33, 3): x, y, visibility
    joints = TRAIL_JOINTS.get(scope)
    if joints is None:      # whole body: the two joints (of hands, feet, head) that travelled most
        cand = [15, 16, 27, 28, 0]
        travel = [float(np.linalg.norm(np.diff(frames[:, j, :2], axis=0), axis=1).sum()) for j in cand]
        joints = [cand[i] for i in np.argsort(travel)[::-1][:2]]
    far = int(np.argmax([np.linalg.norm(f[joints, :2] - frames[0][joints, :2]) for f in frames]))
    pick = [far] if compact else [0, far, len(frames) - 1]
    return {"aspect": skel["aspect"], "frames": [np.round(frames[i], 3).tolist() for i in pick],
            "trails": {str(j): np.round(frames[:, j, :2], 3).tolist() for j in joints}}


def camera_moves_held(result):
    """A body move recognized but not sent (a tie between two moves)."""
    return result["ambiguous"][0] if not result.get("gid") and result.get("ambiguous") else None


def prune_takes(takes):
    """Drop takes that are clearly not the move: much slower than the rest (a swing back to
    guard) or shaped unlike all the others. Keeps at least 3."""
    return [takes[i] for i in prune_indices(takes)]


def prune_indices(takes):
    """Which takes prune_takes keeps (so their camera paths can be kept alongside)."""
    from .motion import dtw_batch, peak
    if len(takes) < 4:
        return list(range(len(takes)))
    segs = [np.asarray(t) for t in takes]
    peaks = np.array([peak(s) for s in segs])
    keep = [i for i in range(len(segs)) if peaks[i] >= 0.6 * np.median(peaks)]
    if len(keep) >= 3:
        seqs = np.stack([prepare(segs[i])[0] for i in keep])
        dist = np.array([[dtw_batch(a, b[None])[0] for b in seqs] for a in seqs])
        medoid = int(np.argmin(dist.sum(axis=1)))
        d = dist[medoid]
        limit = max(0.5, 2.5 * float(np.median(d[d > 0])) if np.any(d > 0) else 0.5)
        keep2 = [keep[j] for j in range(len(keep)) if d[j] <= limit]
        if len(keep2) >= 3:
            keep = keep2
    if len(keep) < 3:
        return list(range(len(takes)))
    return keep


def _cap(items, limit):
    if len(items) <= limit:
        return items
    keep = np.linspace(0, len(items) - 1, limit).round().astype(int)
    return [items[i] for i in keep]
