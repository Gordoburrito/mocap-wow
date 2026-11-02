"""Profiles: your gestures, their recordings, key bindings, weapon setup and calibration.

One JSON file per profile (e.g. one per spec) in the profiles folder.
"""

import json
import os
import re
import secrets
import tempfile
import time

VERSION = 2

DEFAULT_SETTINGS = {
    "require_wow_front": True,    # only send keys while WoW is the front window
    "wow_app": "World of Warcraft",
    "rumble": True,               # buzz the weapon when it recognizes a motion
    "pose_hold_ms": 250,          # default time a pose must be held before it fires
    "lockout_ms": 300,            # after a motion fires, ignore that weapon briefly (return stroke)
    "tooltip_type": "How to do it",
    "flash_on_fire": True,        # show what fired in the overlay
    "clash_capture": True,        # tapping the weapon shafts together opens quick capture
    "capture_takes": 5,
    "jog_enabled": True,
    "hop_enabled": False,
    "fusion": True,               # use the webcam's view of your weapon hand with motions
    "fusion_wait_ms": 150,        # longest a motion waits for camera frames before deciding
    "camera_bias": True,          # re-measure gyro drift whenever the camera sees you hold still
    "auto_align": True,           # correct a Joy-Con that slips on the pipe, from moves you make
    "metrics": True,              # keep a local log of what fired (for the Metrics page)
}

KINDS = ("pose", "motion", "jog", "hop")
SOURCES = ("webcam", "bow", "melee")
ACTIONS = ("key", "arm_toggle")
TOOLTIP_TYPES = ("How to do it", "Spell card", "Recording tips", "Off")
ROLES = ("bow", "melee")


def empty_profile(name="Default"):
    return {"version": VERSION, "name": name, "settings": dict(DEFAULT_SETTINGS),
            "devices": {}, "gestures": [], "neutral": [], "ignore": {}}


def default_role(device_name, taken):
    lowered = device_name.lower()
    if "(l)" in lowered and "bow" not in taken:
        return "bow"
    if "(r)" in lowered and "melee" not in taken:
        return "melee"
    for role in ("melee", "bow"):
        if role not in taken:
            return role
    return None


def migrate(data):
    """Bring a v1 profile (pose/swing/button) up to v2."""
    if data.get("version", 1) >= VERSION:
        return data
    out = empty_profile(data.get("name", "Default"))
    out["settings"].update({k: v for k, v in data.get("settings", {}).items()
                            if k in DEFAULT_SETTINGS})
    out["neutral"] = data.get("neutral", [])
    for g in data.get("gestures", []):
        if g.get("kind") == "pose":
            g = dict(g, source="webcam", action=g.get("action", "key"))
        elif g.get("kind") == "swing":
            role = "bow" if "(l)" in (g.get("device") or "").lower() else "melee"
            g = dict(g, kind="motion", source=role, mode="tap", action="key")
            g.pop("device", None)
        else:
            continue   # controller buttons are gone: the Joy-Cons live on PVC weapons
        out["gestures"].append(_fill(g))
    for dev, segs in data.get("ignore", {}).items():
        role = "bow" if "(l)" in dev.lower() else "melee"
        out["ignore"].setdefault(role, []).extend(segs)
    return out


def _fill(g):
    base = new_gesture(g.get("kind", "pose"), g.get("source"))
    base.update(g)
    return base


def load(path):
    if not os.path.exists(path):
        return empty_profile(_name_from_path(path))
    with open(path) as f:
        data = migrate(json.load(f))
    profile = empty_profile(data.get("name") or _name_from_path(path))
    profile.update(data)
    profile["settings"] = {**DEFAULT_SETTINGS, **data.get("settings", {})}
    profile["gestures"] = [_fill(g) for g in profile["gestures"]]
    return profile


def save(path, profile):
    """Atomic write so a crash mid-save never corrupts your recordings."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".profile-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(profile, f, separators=(",", ":"))
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def list_profiles(directory):
    if not os.path.isdir(directory):
        return []
    out = []
    for name in sorted(os.listdir(directory)):
        if name.endswith(".json") and not name.startswith("."):
            path = os.path.join(directory, name)
            try:
                with open(path) as f:
                    data = json.load(f)
                out.append({"path": path, "name": data.get("name") or name[:-5],
                            "gestures": len(data.get("gestures", []))})
            except (OSError, ValueError):
                continue
    return out


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "profile"


def _name_from_path(path):
    base = os.path.splitext(os.path.basename(path))[0]
    return base.replace("-", " ").title() if base != "default" else "Default"


def new_gesture(kind, source=None, name=None):
    if kind not in KINDS:
        raise ValueError(f"unknown gesture kind {kind!r}")
    if source is None:
        source = "webcam" if kind in ("pose", "jog", "hop") else "melee"
    if kind == "motion" and source not in ROLES:
        raise ValueError(f"motion gestures come from a weapon, not {source!r}")
    default_names = {"pose": "New pose", "motion": f"New {source} move", "jog": "Jog in place",
                     "hop": "Hop"}
    gesture = {
        "id": "g" + secrets.token_hex(4), "kind": kind, "source": source,
        "name": name or default_names[kind], "key": None, "enabled": True,
        "action": "key", "mode": "hold" if kind == "jog" else "tap",
        "casts": "", "tooltip": {"type": None, "steps": []},
        "sensitivity": 1.0, "created": time.time(),
    }
    if kind == "pose":
        gesture.update(hold_ms=None, samples=[])
    elif kind == "motion":
        gesture.update(templates=[], paths=[])
    return gesture
