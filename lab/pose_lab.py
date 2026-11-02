"""Pose recognition on REAL photos of people (Wikimedia Commons, freely licensed), through the
same MediaPipe model and pose classifier the app uses.

    .venv/bin/python lab/fetch_poses.py      # once: downloads ~250 photos into data/poses/
    .venv/bin/python -m lab.pose_lab

This is a harsh test: every photo is a different person, camera, distance and angle, while in
the app you record yourself from one fixed webcam. So these numbers are a floor, not a ceiling.
It also writes data/poses/sim_library.json, real skeletons for the simulated camera (--sim-poses).
"""

import json
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import vision
from mediapipe.tasks.python.core.base_options import BaseOptions

from mocap.pose import DEFAULT_THRESHOLD, PoseClassifier, features

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "poses"
MODEL = ROOT / "models" / "pose_landmarker_full.task"
POSES = ["arms_up", "crossed_arms", "hands_on_hips", "archer", "t_pose"]


def extract():
    manifest = json.loads((DATA / "manifest.json").read_text())
    opts = vision.PoseLandmarkerOptions(base_options=BaseOptions(model_asset_path=str(MODEL)),
                                        running_mode=vision.RunningMode.IMAGE, num_poses=1,
                                        min_pose_detection_confidence=0.5)
    out, stats = {}, {}
    with vision.PoseLandmarker.create_from_options(opts) as lm:
        for cls, items in manifest.items():
            out[cls], found = [], 0
            for item in items:
                img = cv2.imread(str(ROOT / item["file"]))
                if img is None:
                    continue
                h, w = img.shape[:2]
                res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
                if not res.pose_landmarks:
                    continue
                arr = np.array([[p.x, p.y, p.z, 1.0 if p.visibility is None else p.visibility]
                                for p in res.pose_landmarks[0]])
                if features(arr, w / h) is None:
                    continue
                found += 1
                out[cls].append({"lm": np.round(arr, 4).tolist(), "aspect": round(w / h, 4),
                                 "file": item["file"], "source": item.get("source", "")})
            stats[cls] = (found, len(items))
    (DATA / "landmarks.json").write_text(json.dumps(out))
    return out, stats


def evaluate(data, n_enroll=5, n_neutral=15, trials=40, sensitivity=1.0, seed=0):
    rng = np.random.default_rng(seed)
    classes = [c for c in POSES if len(data.get(c, [])) > n_enroll]
    hit = {c: [0, 0] for c in classes}
    wrong = {c: 0 for c in classes}
    neutral_fp = [0, 0]
    for _ in range(trials):
        enroll, test = {}, {}
        for c in classes + ["neutral"]:
            items = list(data[c])
            idx = rng.permutation(len(items))
            k = n_neutral if c == "neutral" else n_enroll
            enroll[c] = [items[i] for i in idx[:k]]
            test[c] = [items[i] for i in idx[k:]]
        clf = PoseClassifier()
        clf.set_data([(c, enroll[c], None) for c in classes], enroll["neutral"],
                     {c: sensitivity for c in classes})
        for c in classes:
            for s in test[c]:
                best, _ = clf.classify(features(s["lm"], s["aspect"]))
                hit[c][1] += 1
                hit[c][0] += best == c
                wrong[c] += best is not None and best != c
        for s in test["neutral"]:
            best, _ = clf.classify(features(s["lm"], s["aspect"]))
            neutral_fp[1] += 1
            neutral_fp[0] += best is not None
    return {"recall": {c: hit[c][0] / hit[c][1] for c in classes},
            "wrong": {c: wrong[c] / hit[c][1] for c in classes},
            "neutral_false_trigger": neutral_fp[0] / max(neutral_fp[1], 1)}


def same_person(data, seed=0, trials=200):
    """Closer to real use: enroll from one person's photo with small natural variation (how
    you'd hold a pose for 2 s in front of your own webcam) and test new variations of the
    same pose against variations of the other poses and of neutral."""
    rng = np.random.default_rng(seed)

    def vary(s, scale=0.012):
        lm = np.array(s["lm"])
        lm[:, :2] += rng.normal(0, scale, (33, 2))
        lm[:, :2] = 0.5 + (lm[:, :2] - 0.5) * rng.uniform(0.85, 1.15) + rng.normal(0, 0.03, 2)
        return {"lm": lm.tolist(), "aspect": s["aspect"]}

    ok = total = fp = fp_total = 0
    classes = [c for c in POSES if data.get(c)]
    for _ in range(trials):
        picks = {c: data[c][rng.integers(len(data[c]))] for c in classes}
        neutral = data["neutral"][rng.integers(len(data["neutral"]))]
        clf = PoseClassifier()
        clf.set_data([(c, [vary(picks[c]) for _ in range(30)], None) for c in classes],
                     [vary(neutral) for _ in range(60)])
        for c in classes:
            v = vary(picks[c])
            best, _ = clf.classify(features(v["lm"], v["aspect"]))
            ok += best == c
            total += 1
        v = vary(neutral)
        best, _ = clf.classify(features(v["lm"], v["aspect"]))
        fp += best is not None
        fp_total += 1
    return {"accuracy": ok / total, "neutral_false_trigger": fp / fp_total}


def main():
    data, stats = extract()
    print("skeletons found (usable / photos):", {k: f"{a}/{b}" for k, (a, b) in stats.items()})
    report = {"found": stats}
    for sens in (1.0, 1.5):
        r = evaluate(data, sensitivity=sens)
        report[f"cross_person_sensitivity_{sens}"] = r
        print(f"\ncross-person, sensitivity {sens}: neutral photos wrongly firing a pose: {r['neutral_false_trigger']*100:.1f}%")
        for c in r["recall"]:
            print(f"  {c:14s} recognized {r['recall'][c]*100:5.1f}%   wrong pose {r['wrong'][c]*100:4.1f}%")
    r = same_person(data)
    report["same_person_variation"] = r
    print(f"\nsame person, natural variation: accuracy {r['accuracy']*100:.1f}%, neutral false trigger {r['neutral_false_trigger']*100:.1f}%")
    library = {c: [s["lm"] for s in data[c][:12]] for c in data if data[c]}
    (DATA / "sim_library.json").write_text(json.dumps(library))
    (ROOT / "lab" / "pose_results.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
