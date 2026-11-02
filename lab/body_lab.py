"""Jog and hop detection on REAL video, through the same MediaPipe model and detectors the app uses.

Videos (see research notes): UTD-MHAD RGB clips (a22 jogging in place = should jog; a24 sit to
stand and a27 squat = should do nothing), and two freely licensed Wikimedia Commons clips of
jumping jacks (each jack is a hop).

    .venv/bin/python -m lab.body_lab
"""

import glob
import json
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import vision
from mediapipe.tasks.python.core.base_options import BaseOptions

from mocap.body import HopDetector, JogDetector

ROOT = Path(__file__).resolve().parent.parent
VIDEO = ROOT / "data" / "video"
MODEL = ROOT / "models" / "pose_landmarker_full.task"


def landmarks(path, max_seconds=60):
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    opts = vision.PoseLandmarkerOptions(base_options=BaseOptions(model_asset_path=str(MODEL)),
                                        running_mode=vision.RunningMode.VIDEO, num_poses=1)
    frames = []
    with vision.PoseLandmarker.create_from_options(opts) as lm:
        i = 0
        while i < fps * max_seconds:
            ok, frame = cap.read()
            if not ok:
                break
            h, w = frame.shape[:2]
            res = lm.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB,
                                               data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), int(i * 1000 / fps))
            arr = None
            if res.pose_landmarks:
                arr = np.array([[p.x, p.y, p.z, 1.0 if p.visibility is None else p.visibility]
                                for p in res.pose_landmarks[0]])
            frames.append((i / fps, arr, w / h))
            i += 1
    return frames


def run(frames):
    jog, hop = JogDetector(), HopDetector()
    events, jog_time, active_since = [], 0.0, None
    for t, lm, aspect in frames:
        e = jog.update(lm, aspect, t)
        if e == "start":
            active_since = t
            events.append(("jog start", round(t, 2)))
        elif e == "stop":
            jog_time += t - active_since
            active_since = None
            events.append(("jog stop", round(t, 2)))
        if hop.update(lm, aspect, t) == "hop":
            events.append(("hop", round(t, 2)))
    duration = frames[-1][0] if frames else 0
    if active_since is not None:
        jog_time += duration - active_since
    return events, jog_time, duration


def reference_jumps(frames):
    """Independent count of jumps: both ankles leave their usual height together."""
    ys = []
    for t, lm, aspect in frames:
        if lm is None or lm[27, 3] < 0.5 or lm[28, 3] < 0.5:
            ys.append(np.nan)
            continue
        sw = abs(lm[11, 0] - lm[12, 0]) * aspect or 1
        ys.append(-((lm[27, 1] + lm[28, 1]) / 2) / sw)
    y = np.array(ys)
    base = np.nanmedian(y)
    up = (y - base) > 0.2
    return int(np.sum(np.diff(up.astype(int)) == 1))


def main():
    groups = {
        "jog in place (should jog)": sorted(glob.glob(str(VIDEO / "utd" / "a22_*"))),
        "sit to stand (should do nothing)": sorted(glob.glob(str(VIDEO / "utd" / "a24_*"))),
        "squats (should do nothing)": sorted(glob.glob(str(VIDEO / "utd" / "a27_*"))),
        "jumping jacks (each is a hop)": [str(VIDEO / "jacks_burpees.webm"), str(VIDEO / "jumping_jack_slow.webm")],
    }
    report = {}
    for label, files in groups.items():
        print(f"\n== {label}")
        report[label] = []
        for f in files:
            frames = landmarks(f)
            seen = sum(1 for _, lm, _ in frames if lm is not None)
            events, jog_time, duration = run(frames)
            hops = sum(1 for e, _ in events if e == "hop")
            jogs = sum(1 for e, _ in events if e == "jog start")
            ref = reference_jumps(frames)
            first_jog = next((t for e, t in events if e == "jog start"), None)
            row = {"file": Path(f).name, "seconds": round(duration, 1), "body_seen": f"{seen}/{len(frames)}",
                   "jog_starts": jogs, "jogging_seconds": round(jog_time, 1), "first_jog_at": first_jog,
                   "hops": hops, "reference_jumps": ref}
            report[label].append(row)
            print(f"  {row['file']:28s} {row['seconds']:5.1f}s  body seen {row['body_seen']:9s} "
                  f"jog: {jogs} start(s), {row['jogging_seconds']}s active (first at {first_jog})   "
                  f"hops: {hops} (ankle reference: {ref})")
    (ROOT / "lab" / "body_results.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
