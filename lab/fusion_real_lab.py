"""Camera + gyro fusion on REAL synchronized data: UTD-MHAD (8 people x 27 actions x 4 trials,
a wearable gyro/accelerometer on the right wrist recorded together with a Kinect camera).

The app's own code does all the work: the gravity tracker and recognizer from motion.py, the
camera track / hand finder / lag finder from fusion.py, camera-gated bias from calibration.py.
The camera side is MediaPipe run on the real RGB video (the same model as the app), or the
Kinect skeleton with --kinect.

    .venv/bin/python -m lab.utd_landmarks          # once: MediaPipe on the videos (~5 min)
    .venv/bin/python -m lab.fusion_real_lab        # writes lab/fusion_real_results.json

Protocol (like real use, where you record yourself): per person, record 3 trials of each move,
test on the 4th, rotate. Rejection is on, so a rejected move is a miss, not a wrong key.
"""

import argparse
import json
from itertools import combinations
from pathlib import Path

import numpy as np
import scipy.io as sio

from mocap.calibration import DeviceCalibration, STILL_GYRO
from mocap.fusion import CameraTrack, HandFinder, LagFinder
from mocap.motion import GravityTracker, SwingClassifier, _thresholds, path_matrix

ROOT = Path(__file__).resolve().parent.parent
UTD = ROOT / "data" / "utd"
OUT = Path(__file__).resolve().parent / "fusion_real_results.json"

ACTIONS = {1: "swipe left", 2: "swipe right", 3: "wave", 4: "clap", 5: "throw", 6: "arm cross",
           7: "basketball shoot", 8: "draw X", 9: "draw circle (cw)", 10: "draw circle (ccw)",
           11: "draw triangle", 12: "bowling", 13: "boxing", 14: "baseball swing",
           15: "tennis swing", 16: "arm curl", 17: "tennis serve", 18: "push", 19: "knock",
           20: "catch", 21: "pickup and throw"}
SUBJECTS = range(1, 9)
TRIALS = range(1, 5)
# Kinect joint (0-based) -> MediaPipe landmark index, for --kinect.
KINECT_TO_MP = {4: 11, 8: 12, 5: 13, 9: 14, 6: 15, 10: 16, 0: 0, 12: 23, 16: 24}


def load_imu(a, s, t):
    path = UTD / "Inertial" / f"a{a}_s{s}_t{t}_inertial.mat"
    if not path.exists():
        return None
    x = sio.loadmat(path)["d_iner"].astype(float)
    accel = x[:, :3] * 9.81
    gyro = np.radians(x[:, 3:6])
    t = np.arange(len(x)) / 50.0
    return t, gyro, accel


def load_camera(a, s, t, duration, kinect=False):
    """Camera frames as (times, [(33, 4) landmarks or None], aspect). The video spans the
    same stretch as the IMU recording (checked by cross-correlation: see results)."""
    if kinect:
        path = UTD / "Skeleton" / f"a{a}_s{s}_t{t}_skeleton.mat"
        if not path.exists():
            return None
        sk = sio.loadmat(path)["d_skel"].astype(float)      # (20, 3, frames), metres
        frames = []
        for k in range(sk.shape[2]):
            lm = np.zeros((33, 4))
            for j, i in KINECT_TO_MP.items():
                x, y, _ = sk[j, :, k]
                lm[i] = [0.5 + 0.25 * x, 0.5 - 0.25 * y, 0.0, 1.0]
            frames.append(lm)
        aspect = 1.0
    else:
        path = UTD / "mediapipe" / f"a{a}_s{s}_t{t}.npz"
        if not path.exists():
            return None
        z = np.load(path)
        lms, seen = z["lm"], z["seen"]
        frames = [lms[k] if seen[k] else None for k in range(len(lms))]
        aspect = float(z["aspect"])
    n = len(frames)
    times = (np.arange(n) + 0.5) * duration / n
    return times, frames, aspect


def segment(t, gyro, accel):
    """Gravity removal exactly as the app does it, then the active stretch of the trial."""
    grav = GravityTracker()
    lin = np.array([grav.linear(g, a, tt) for tt, g, a in zip(t, gyro, accel)])
    energy = np.linalg.norm(gyro, axis=1) + 0.2 * np.linalg.norm(lin, axis=1)
    on = np.flatnonzero(energy > max(0.8, 0.2 * energy.max()))
    i0, i1 = max(0, on[0] - 2), min(len(t), on[-1] + 3)
    seg = np.concatenate([t[:, None], gyro, lin], axis=1)[i0:i1]
    return seg, (i0, i1)


def build(kinect=False, lag=0.0):
    trials = {}
    for a in ACTIONS:
        for s in SUBJECTS:
            for tr in TRIALS:
                imu = load_imu(a, s, tr)
                if imu is None:
                    continue
                t, gyro, accel = imu
                cam = load_camera(a, s, tr, t[-1] + 1 / 50, kinect)
                if cam is None:
                    continue
                seg, (i0, i1) = segment(t, gyro, accel)
                track = CameraTrack(seconds=30)
                track.lag = lag
                times, frames, aspect = cam
                for ft, lm in zip(times, frames):
                    track.add(ft, lm, aspect)
                trials[(a, s, tr)] = {"seg": seg, "track": track, "imu": imu, "span": (i0, i1),
                                      "path": track.path("right", seg[0, 0], seg[-1, 0])}
    return trials


def decide_vision(path, bank, owners, info):
    d = {}
    for p, o in zip(bank, owners):
        if p is not None:
            d[o] = min(d.get(o, np.inf), float(np.mean(path_matrix([path, p])[0, 1])))
    if not d:
        return None
    ranked = sorted(d.items(), key=lambda kv: kv[1])
    gid, dist = ranked[0]
    if dist > info[gid]["threshold"]:
        return None
    if len(ranked) > 1 and ranked[1][1] <= info[ranked[1][0]]["threshold"] and dist > 0.85 * ranked[1][1]:
        return None
    return gid


def evaluate(trials, actions, subjects=SUBJECTS):
    """Within-person, leave-one-trial-out. Returns counts per mode and per-action confusions."""
    modes = ("gyro", "camera", "fused", "blocked")
    counts = {m: {"correct": 0, "wrong": 0, "rejected": 0} for m in modes}
    confusion = {m: {} for m in modes}
    for s in subjects:
        for test_tr in TRIALS:
            train = {a: [k for k in trials if k[0] == a and k[1] == s and k[2] != test_tr] for a in actions}
            if any(len(v) < 2 for v in train.values()):
                continue
            data = [(a, "w", [trials[k]["seg"] for k in train[a]], None) for a in actions]
            # "gyro": recorded and played without a camera at all. "fused": recorded and played with
            # it. "blocked": recorded with the camera, played without (twins are held back).
            gyro_clf = SwingClassifier()
            gyro_clf.set_data(data, {})
            clf = SwingClassifier()
            clf.set_data(data, {}, paths={a: [trials[k]["path"] for k in train[a]] for a in actions})
            owners = [a for a in actions for _ in train[a]]
            vbank = [trials[k]["path"] for a in actions for k in train[a]]
            vinfo = _thresholds(path_matrix(vbank), owners)
            for a in actions:
                key = (a, s, test_tr)
                if key not in trials:
                    continue
                tr = trials[key]
                g = gyro_clf.classify("w", tr["seg"])
                b = clf.classify("w", tr["seg"])
                f = clf.classify("w", tr["seg"], tr["path"]) if tr["path"] is not None else b
                v = decide_vision(tr["path"], vbank, owners, vinfo) if tr["path"] is not None else None
                for m, got in (("gyro", g["gid"]), ("fused", f["gid"]), ("camera", v), ("blocked", b["gid"])):
                    outcome = "rejected" if got is None else "correct" if got == a else "wrong"
                    counts[m][outcome] += 1
                    confusion[m].setdefault(a, {}).setdefault(str(got), 0)
                    confusion[m][a][str(got)] += 1
    for m in modes:
        n = sum(counts[m].values())
        counts[m]["n"] = n
        for k in ("correct", "wrong", "rejected"):
            counts[m][k + "_pct"] = round(100 * counts[m][k] / max(n, 1), 1)
    return counts, confusion


def largest_set(trials, mode, target=95.0, max_wrong=1.0):
    """Drop the most-confused move until the rest hits the target. How many moves survive?"""
    actions = list(ACTIONS)
    while len(actions) > 2:
        counts, conf = evaluate(trials, actions)
        c = counts[mode]
        if c["correct_pct"] >= target and c["wrong_pct"] <= max_wrong:
            return actions, c
        worst = min(actions, key=lambda a: conf[mode][a].get(str(a), 0) / max(sum(conf[mode][a].values()), 1)
                    - 0.5 * sum(conf[mode][b].get(str(a), 0) for b in actions if b != a) / 32)
        actions.remove(worst)
    return actions, evaluate(trials, actions)[0][mode]


def curve(trials, sizes=(4, 6, 8, 10, 12, 15, 18, 21), samples=12, seed=0):
    rng = np.random.default_rng(seed)
    out = {}
    for k in sizes:
        acc = {m: [] for m in ("gyro", "camera", "fused")}
        wrong = {m: [] for m in acc}
        draws = [list(ACTIONS)] if k == len(ACTIONS) else \
            [sorted(rng.choice(list(ACTIONS), k, replace=False).tolist()) for _ in range(samples)]
        for actions in draws:
            counts, _ = evaluate(trials, actions)
            for m in acc:
                acc[m].append(counts[m]["correct_pct"])
                wrong[m].append(counts[m]["wrong_pct"])
        out[k] = {m: {"correct": round(float(np.mean(acc[m])), 1), "wrong": round(float(np.mean(wrong[m])), 1)}
                  for m in acc}
        print(f"  {k:2d} moves: " + "  ".join(f"{m} {out[k][m]['correct']:5.1f}% ({out[k][m]['wrong']:.1f}% wrong)"
                                             for m in acc))
    return out


ONE_HANDED = [1, 2, 3, 5, 8, 9, 10, 11, 12, 15, 16, 17, 19, 21]
SIDE = {"side": "right"}     # which camera wrist carries the sensor; set by the hand finder


def hand_finding(trials, truth):
    """Which wrist is the sensor on? Scored on one-handed moves, like a weapon in each hand;
    two-handed moves (clap, catch...) can't tell. `truth` is the camera label of the sensor
    wrist (the person's right; the label flips if the video is mirrored)."""
    per_trial, groups, majority = [], 0, 0
    other = "left" if truth == "right" else "right"
    for s in SUBJECTS:
        keys = [k for k in sorted(trials, key=lambda k: (k[2], k[0])) if k[1] == s and k[0] in ONE_HANDED]
        finder = HandFinder()
        for i, k in enumerate(keys):
            seg = trials[k]["seg"]
            scores = finder.observe(seg[:, 0], np.linalg.norm(seg[:, 1:4], axis=1), trials[k]["track"])
            if "left" in scores and "right" in scores:
                per_trial.append(scores[truth] > scores[other])
            if i % 3 == 2:
                means = {sd: np.mean(v) for sd, v in finder.scores.items() if v}
                groups += 1
                majority += len(means) == 2 and means[truth] > means[other]
                finder = HandFinder()
    return {"single_move_correct_pct": round(100 * float(np.mean(per_trial)), 1),
            "three_moves_correct_pct": round(100 * majority / max(groups, 1), 1),
            "moves": len(per_trial), "groups_of_3": groups}


def which_wrist(trials):
    """The hand finder over everything: which camera wrist label moves with the sensor?"""
    finder = HandFinder()
    votes = {"left": 0, "right": 0}
    for k in sorted(trials):
        if k[0] not in ONE_HANDED:
            continue
        seg = trials[k]["seg"]
        sc = HandFinder().observe(seg[:, 0], np.linalg.norm(seg[:, 1:4], axis=1), trials[k]["track"])
        if len(sc) == 2:
            votes[max(sc, key=sc.get)] += 1
    return max(votes, key=votes.get), votes


def lag_finding(trials, shifts=(0.0, 0.06, 0.12)):
    """Delay the camera by a known amount and see if the lag finder notices. The dataset has a
    small built-in offset of its own, so what's scored is the found change from the 0 shift."""
    keys = sorted(trials)
    found = {}
    for shift in shifts:
        for s in SUBJECTS:
            finder = LagFinder()
            for k in [k for k in keys if k[1] == s and k[0] in ONE_HANDED][:12]:
                seg = trials[k]["seg"]
                finder.observe(seg[:, 0], np.linalg.norm(seg[:, 1:4], axis=1),
                               _shifted(trials[k]["track"], shift), SIDE["side"])
            if finder.lag is not None:
                found.setdefault(s, {})[shift] = finder.lag
    base = {s: v[0.0] for s, v in found.items() if 0.0 in v}
    out = {"dataset_offset_s": round(float(np.median(list(base.values()))), 3) if base else None}
    for shift in shifts[1:]:
        err = [found[s][shift] - base[s] - shift for s in base if shift in found[s]]
        out[f"shift_{shift}"] = {"median_found": round(float(np.median(err)) + shift, 3) if err else None,
                                 "within_20ms_pct": round(100 * float(np.mean(np.abs(err) <= 0.02)), 1)
                                 if err else None, "people": len(err)}
    return out


def _shifted(track, shift):
    out = CameraTrack(seconds=30)
    out._t.extend(t + shift for t in track._t)
    out._pts.extend(track._pts)
    out._raw.extend(track._raw)
    out._sw.extend(track._sw)
    out.lag = track.lag
    return out


def _windows(t, gm, length=0.5, step=0.1):
    for s0 in np.arange(0, t[-1] - length, step):
        m = (t >= s0) & (t < s0 + length)
        yield s0, s0 + length, float(gm[m].max())


def stillness(trials):
    """Does camera stillness agree with the IMU? Windows where the wrist sensor says still (all
    gyro under 0.3 rad/s) vs clearly moving (peak over 2 rad/s)."""
    still_seen, moving_seen, jitter = [], [], []
    for tr in trials.values():
        t, gyro, _ = tr["imu"]
        gm = np.linalg.norm(gyro, axis=1)
        for a, b, top in _windows(t, gm):
            if top < 0.3:
                still_seen.append(tr["track"].still(SIDE["side"], a, b))
                _, _, raw = tr["track"]._window(a + tr["track"].lag, b + tr["track"].lag)
                r = raw[:, 4 if SIDE["side"] == "left" else 5]
                r = r[~np.isnan(r).any(axis=1)]
                if len(r) >= 5:
                    jitter.append(float(np.linalg.norm(r - np.median(r, axis=0), axis=1).max()))
            elif top > 2.0:
                moving_seen.append(tr["track"].still(SIDE["side"], a, b))
    return {"imu_still_windows": len(still_seen),
            "camera_agrees_still_pct": round(100 * float(np.mean(still_seen)), 1) if still_seen else None,
            "imu_moving_windows": len(moving_seen),
            "camera_wrongly_still_pct": round(100 * float(np.mean(moving_seen)), 2) if moving_seen else None,
            "still_wrist_wander_sw": {"median": round(float(np.median(jitter)), 3),
                                      "p90": round(float(np.percentile(jitter, 90)), 3)} if jitter else None}


def bias_recovery(trials, true_bias=(0.40, -0.25, 0.30)):
    """Add a large gyro offset (about 30 deg/s, beyond what the gyro-only tracker can notice)
    to the real recordings, play each person's trials back to back, and see who finds it."""
    true_bias = np.array(true_bias)
    gyro_err, cam_err, updates = [], [], []
    for s in SUBJECTS:
        cal_g, cal_c = DeviceCalibration(), DeviceCalibration()
        clock, n = 0.0, 0
        for k in sorted(k for k in trials if k[1] == s):
            tr = trials[k]
            t, gyro, accel = tr["imu"]
            for tt, g, a in zip(t, gyro, accel):
                cal_g.apply(clock + tt, g + true_bias, a)
                cal_c.apply(clock + tt, g + true_bias, a)
            for a, b, _ in _windows(t, np.zeros(len(t)), step=0.25):
                if tr["track"].still(SIDE["side"], a, b) and cal_c.camera_still(clock + a, clock + b) is not None:
                    n += 1
            clock += t[-1] + 1.0
        gyro_err.append(float(np.linalg.norm(cal_g.bias - true_bias)))
        cam_err.append(float(np.linalg.norm(cal_c.bias - true_bias)))
        updates.append(n)
    return {"true_offset_deg_s": round(float(np.degrees(np.linalg.norm(true_bias))), 1),
            "gyro_only_error_deg_s": round(float(np.degrees(np.median(gyro_err))), 1),
            "camera_error_deg_s": round(float(np.degrees(np.median(cam_err))), 1),
            "camera_updates_per_person": int(np.median(updates)),
            "gyro_only_still_limit_deg_s": round(float(np.degrees(STILL_GYRO)), 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kinect", action="store_true", help="use the Kinect skeleton, not MediaPipe")
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    source = "kinect" if args.kinect else "mediapipe"
    trials = build(kinect=args.kinect)
    # Like the app: find the weapon wrist and the camera's delay from the moves themselves.
    SIDE["side"], votes = which_wrist(trials)
    print(f"hand finder: the sensor moves with the camera's '{SIDE['side']}' wrist {votes}")
    lag = lag_finding(trials)["dataset_offset_s"] or 0.0
    for tr in trials.values():
        tr["track"].lag = lag
        tr["path"] = tr["track"].path(SIDE["side"], tr["seg"][0, 0], tr["seg"][-1, 0])
    print(f"camera delay found from the moves: {lag * 1000:.0f} ms")
    with_path = sum(1 for tr in trials.values() if tr["path"] is not None)
    print(f"{len(trials)} trials ({source}), {with_path} with a usable hand path")
    results = {"source": source, "trials": len(trials), "with_path": with_path, "camera_lag_s": lag,
               "sensor_wrist_label": SIDE["side"], "wrist_votes": votes}
    counts, conf = evaluate(trials, list(ACTIONS))
    results["all_21"] = counts
    results["confusion_21"] = conf
    for m, c in counts.items():
        print(f"  all 21 moves, {m:7s}: {c['correct_pct']}% correct, {c['wrong_pct']}% wrong, {c['rejected_pct']}% rejected")
    swing_like = [1, 2, 5, 12, 13, 14, 15, 17, 18, 20]
    counts_s, _ = evaluate(trials, swing_like)
    results["swing_like_10"] = {"actions": [ACTIONS[a] for a in swing_like], **counts_s}
    for m, c in counts_s.items():
        print(f"  10 swing-like moves, {m:7s}: {c['correct_pct']}% correct, {c['wrong_pct']}% wrong, {c['rejected_pct']}% rejected")
    if not args.quick:
        print("Moves vs accuracy (random sets):")
        results["curve"] = curve(trials)
        for m in ("gyro", "fused"):
            acts, c = largest_set(trials, m)
            results[f"largest_set_{m}"] = {"count": len(acts), "actions": [ACTIONS[a] for a in acts], **c}
            print(f"  largest set at >=95% correct, <=1% wrong, {m}: {len(acts)} moves")
    results["hand_finding"] = hand_finding(trials, SIDE["side"])
    print("  hand finding:", results["hand_finding"])
    results["lag_finding"] = lag_finding(trials)
    print("  lag finding:", results["lag_finding"])
    results["stillness"] = stillness(trials)
    print("  stillness:", results["stillness"])
    results["bias_recovery"] = bias_recovery(trials)
    print("  bias recovery:", results["bias_recovery"])
    out = OUT if not args.kinect else OUT.with_name("fusion_real_results_kinect.json")
    out.write_text(json.dumps(results, indent=1))
    print("wrote", out.name)


if __name__ == "__main__":
    main()
