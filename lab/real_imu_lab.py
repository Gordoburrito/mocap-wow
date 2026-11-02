"""Run the engine's motion recognizer on REAL handheld gyro gestures: the 6DMG dataset
(Wii Remote + MotionPlus, 28 people x 20 gestures x 10 trials, CC BY 4.0).

    .venv/bin/python -m lab.real_imu_lab

Data: data/6dmg/MotionGesture.db (see lab/README or research notes for the download line).
Every number here uses the same prepare / DTW / calibrate / classify code the app uses, with
thresholds and rejection on, i.e. a rejected move counts as a miss.
"""

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np

from mocap import motion

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "6dmg" / "MotionGesture.db"


def load():
    con = sqlite3.connect(DB)
    data = defaultdict(lambda: defaultdict(dict))
    for name, tester, trial, length, blob in con.execute(
            "select name, tester, trial, length, data from GestureTable"):
        a = np.frombuffer(blob, np.float32).reshape(length, 14).astype(float)
        t = (a[:, 0] - a[0, 0]) / 1000.0
        gyro = a[:, 11:14]
        accel = a[:, 8:11] * 9.81
        grav = motion.GravityTracker()
        lin = np.array([grav.linear(gyro[i], accel[i], t[i]) for i in range(len(t))])
        data[tester][name][trial] = np.column_stack([t, gyro, lin])
    return data


def run(data, gestures, n_templates=5, open_set=None):
    """Per tester: enroll `gestures` with n templates, test on the remaining trials.
    open_set: other gesture names whose trials should all be rejected."""
    counts = {"correct": 0, "wrong": 0, "rejected": 0, "tests": 0, "false_accept": 0, "open_tests": 0}
    confusions = defaultdict(int)
    for tester, by_name in data.items():
        if not all(g in by_name for g in gestures):
            continue
        for split in (range(1, 1 + n_templates), range(11 - n_templates, 11)):
            train = {g: [by_name[g][i] for i in split if i in by_name[g]] for g in gestures}
            clf = motion.SwingClassifier()
            clf.set_data([(g, "stick", train[g], None) for g in gestures], {})
            for g in gestures:
                for trial, seg in by_name[g].items():
                    if trial in split:
                        continue
                    r = clf.classify("stick", seg)
                    counts["tests"] += 1
                    if r["gid"] == g:
                        counts["correct"] += 1
                    elif r["gid"] is None:
                        counts["rejected"] += 1
                    else:
                        counts["wrong"] += 1
                        confusions[(g, r["gid"])] += 1
            for other in open_set or ():
                for seg in by_name.get(other, {}).values():
                    counts["open_tests"] += 1
                    counts["false_accept"] += clf.classify("stick", seg)["gid"] is not None
    n = max(counts["tests"], 1)
    out = {"accuracy": counts["correct"] / n, "wrong": counts["wrong"] / n,
           "rejected": counts["rejected"] / n, "tests": counts["tests"],
           "top_confusions": sorted(((f"{a} -> {b}", c) for (a, b), c in confusions.items()),
                                    key=lambda kv: -kv[1])[:6]}
    if open_set:
        out["false_accept"] = counts["false_accept"] / max(counts["open_tests"], 1)
        out["open_tests"] = counts["open_tests"]
    return out


SETS = {
    "4 cardinal swipes": ["swipe_left", "swipe_right", "swipe_up", "swipe_down"],
    "8 swipes (with diagonals)": ["swipe_left", "swipe_right", "swipe_up", "swipe_down",
                                  "swipe_upleft", "swipe_upright", "swipe_downleft", "swipe_downright"],
    "stick set: 4 swipes + 2 twists": ["swipe_left", "swipe_right", "swipe_up", "swipe_down",
                                       "twist_clk", "twist_cclk"],
    "4 pokes (thrust and back)": ["poke_left", "poke_right", "poke_up", "poke_down"],
    "4 circles": ["cir_hor_clk", "cir_hor_cclk", "cir_ver_clk", "cir_ver_cclk"],
    "all 20": None,
}


def main():
    data = load()
    all_names = sorted({g for t in data.values() for g in t})
    report = {}
    for label, gestures in SETS.items():
        gestures = gestures or all_names
        report[label] = run(data, gestures)
        r = report[label]
        print(f"{label:34s} accuracy {r['accuracy']*100:5.1f}%  wrong {r['wrong']*100:4.1f}%  "
              f"rejected {r['rejected']*100:4.1f}%   ({r['tests']} tests)  {r['top_confusions'][:3]}")
    stick = SETS["stick set: 4 swipes + 2 twists"]
    others = [g for g in all_names if g not in stick]
    r = run(data, stick, open_set=others)
    report["open set: stick set vs 14 unknown gestures"] = r
    print(f"{'open set (14 gestures never recorded)':34s} accuracy {r['accuracy']*100:5.1f}%  "
          f"unknown moves wrongly fired: {r['false_accept']*100:4.1f}% of {r['open_tests']}")
    for n in (1, 3, 5):
        r = run(data, stick, n_templates=n)
        report[f"stick set, {n} takes"] = r
        print(f"{'stick set with ' + str(n) + ' take(s)':34s} accuracy {r['accuracy']*100:5.1f}%  wrong {r['wrong']*100:4.1f}%  rejected {r['rejected']*100:4.1f}%")
    saved = motion.ACCEL_WEIGHT
    motion.ACCEL_WEIGHT = 0.0
    r = run(data, all_names)
    motion.ACCEL_WEIGHT = saved
    report["all 20, gyro only"] = r
    print(f"{'all 20, gyro only':34s} accuracy {r['accuracy']*100:5.1f}%  wrong {r['wrong']*100:4.1f}%  rejected {r['rejected']*100:4.1f}%")
    (ROOT / "lab" / "real_imu_results.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
