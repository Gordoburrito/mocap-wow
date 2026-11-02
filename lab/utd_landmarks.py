"""Run MediaPipe pose (the model the app uses) on the UTD-MHAD RGB videos, actions 1-21.

    curl -o data/utd/RGB.zip https://personal.utdallas.edu/~kehtar/UTD-MAD/RGB.zip   # 1.1 GB
    .venv/bin/python -m lab.utd_landmarks

Writes data/utd/mediapipe/a{action}_s{subject}_t{trial}.npz (landmarks, seen mask, aspect).
"""

import os
import re
import tempfile
import zipfile
from multiprocessing import Pool
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
ZIP = ROOT / "data" / "utd" / "RGB.zip"
OUT = ROOT / "data" / "utd" / "mediapipe"
MODEL = ROOT / "models" / "pose_landmarker_full.task"


def work(name):
    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import vision
    from mediapipe.tasks.python.core.base_options import BaseOptions

    m = re.search(r"a(\d+)_s(\d+)_t(\d+)_color", name)
    a, s, t = map(int, m.groups())
    dest = OUT / f"a{a}_s{s}_t{t}.npz"
    if dest.exists():
        return name, "cached"
    with zipfile.ZipFile(ZIP) as z, tempfile.NamedTemporaryFile(suffix=".avi") as tmp:
        tmp.write(z.read(name))
        tmp.flush()
        cap = cv2.VideoCapture(tmp.name)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        opts = vision.PoseLandmarkerOptions(base_options=BaseOptions(model_asset_path=str(MODEL)),
                                            running_mode=vision.RunningMode.VIDEO, num_poses=1)
        lms, seen, aspect, i = [], [], 4 / 3, 0
        with vision.PoseLandmarker.create_from_options(opts) as lm:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                h, w = frame.shape[:2]
                aspect = w / h
                res = lm.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB,
                                                   data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)),
                                          int(i * 1000 / fps))
                if res.pose_landmarks:
                    lms.append([[p.x, p.y, p.z, 1.0 if p.visibility is None else p.visibility]
                                for p in res.pose_landmarks[0]])
                    seen.append(True)
                else:
                    lms.append(np.zeros((33, 4)).tolist())
                    seen.append(False)
                i += 1
        cap.release()
    np.savez_compressed(dest, lm=np.array(lms, dtype=np.float32), seen=np.array(seen),
                        aspect=aspect, fps=fps)
    return name, len(lms)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ZIP) as z:
        names = [n for n in z.namelist() if n.endswith(".avi")
                 and (m := re.search(r"a(\d+)_s\d+_t\d+_color", n)) and int(m.group(1)) <= 21]
    print(len(names), "videos")
    os.environ.setdefault("GLOG_minloglevel", "2")
    with Pool(6) as pool:
        for k, (name, n) in enumerate(pool.imap_unordered(work, names), 1):
            if k % 50 == 0:
                print(k, name, n, flush=True)
    print("done")


if __name__ == "__main__":
    main()
