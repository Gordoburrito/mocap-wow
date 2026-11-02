"""Body moves from the webcam alone (no gyro) on REAL video: UTD-MHAD, 8 people x 27 actions x
4 trials, MediaPipe on the RGB video (the app's model), through the app's own move segmenter
and classifier (mocap/camera_moves.py). Compared with the gyro and camera + gyro numbers from
lab/fusion_real_lab.py on the same people and trials.

    .venv/bin/python -m lab.utd_landmarks       # once
    .venv/bin/python -m lab.camera_moves_lab    # writes lab/camera_moves_results.json

Per person: 3 trials of each action recorded, the 4th tested, rotated. Rejection on.
"""

import json
from pathlib import Path

import numpy as np

from mocap.camera_moves import MoveClassifier, MoveJoiner, MoveSegmenter

ROOT = Path(__file__).resolve().parent.parent
MP = ROOT / "data" / "utd" / "mediapipe"
OUT = Path(__file__).resolve().parent / "camera_moves_results.json"
ACTIONS = {1: "swipe left", 2: "swipe right", 3: "wave", 4: "clap", 5: "throw", 6: "arm cross",
           7: "basketball shoot", 8: "draw X", 9: "draw circle (cw)", 10: "draw circle (ccw)",
           11: "draw triangle", 12: "bowling", 13: "boxing", 14: "baseball swing",
           15: "tennis swing", 16: "arm curl", 17: "tennis serve", 18: "push", 19: "knock",
           20: "catch", 21: "pickup and throw", 22: "jog in place", 23: "walk in place",
           24: "sit to stand", 25: "stand to sit", 26: "lunge", 27: "squat"}
ARMS = list(range(1, 22))
LEGS = list(range(22, 28))
SUBJECTS = range(1, 9)
TRIALS = range(1, 5)


def segments(a, s, t):
    """The live segmenter over one clip; the stream is flushed at the end (the clip stops)."""
    path = MP / f"a{a}_s{s}_t{t}.npz"
    if not path.exists():
        return None
    z = np.load(path)
    lm, seen, aspect, fps = z["lm"], z["seen"], float(z["aspect"]), float(z["fps"])
    seg = MoveSegmenter(max_s=5.0)      # UTD's actions are long (up to ~5 s with wind-ups)
    out = []
    for k in range(len(lm)):
        r = seg.feed(k / fps, lm[k] if seen[k] else None, aspect)
        if r is not None:
            out.append(r)
    r = seg.flush(len(lm) / fps)
    if r is not None:
        out.append(r)
    return out


def build():
    trials = {}
    for a in ACTIONS:
        for s in SUBJECTS:
            for t in TRIALS:
                segs = segments(a, s, t)
                if segs is None:
                    continue
                merged = np.concatenate(segs) if segs else None
                trials[(a, s, t)] = {"segs": segs, "merged": merged}
    return trials


def evaluate(trials, actions, subjects=SUBJECTS, live=False):
    """live=False: each clip's move cut as one piece. live=True: every segment the live
    segmenter produced is classified on its own; the clip counts as right only if the right
    move fired and nothing else did."""
    counts = {"correct": 0, "wrong": 0, "rejected": 0}
    confusion = {}
    for s in subjects:
        for test in TRIALS:
            train = {a: [trials[k]["merged"] for k in trials if k[0] == a and k[1] == s and k[2] != test
                         and trials[k]["merged"] is not None] for a in actions}
            # An action whose takes the segmenter missed isn't recorded; its test still counts.
            train = {a: v for a, v in train.items() if v}
            if len(train) < 2:
                continue
            clf = MoveClassifier()
            clf.set_data(list(train.items()))
            for a in actions:
                tr = trials.get((a, s, test))
                if tr is None:
                    continue
                if live:
                    joiner, fired = MoveJoiner(), []
                    for p in tr["segs"]:
                        r, _ = joiner.offer(p, clf.classify)
                        if r["gid"] is not None:
                            fired.append(r["gid"])
                else:
                    pieces = [tr["merged"]] if tr["merged"] is not None else []
                    fired = [g for g in (clf.classify(p)["gid"] for p in pieces) if g is not None]
                outcome = "rejected" if not fired else "correct" if fired == [a] or (not live and fired[0] == a) \
                    else "wrong"
                counts[outcome] += 1
                confusion.setdefault(a, {}).setdefault(str(fired[0] if fired else None), 0)
                confusion[a][str(fired[0] if fired else None)] += 1
    n = sum(counts.values())
    for k in ("correct", "wrong", "rejected"):
        counts[k + "_pct"] = round(100 * counts[k] / max(n, 1), 1)
    counts["n"] = n
    return counts, confusion


def open_set(trials, seed=0, known=10):
    """Record `known` random actions; how often do the OTHER actions fire one of them?"""
    rng = np.random.default_rng(seed)
    fired = total = 0
    for s in SUBJECTS:
        for test in TRIALS:
            ks = sorted(rng.choice(list(ACTIONS), known, replace=False).tolist())
            train = {a: [trials[k]["merged"] for k in trials if k[0] == a and k[1] == s and k[2] != test
                         and trials[k]["merged"] is not None] for a in ks}
            train = {a: v for a, v in train.items() if v}
            clf = MoveClassifier()
            clf.set_data(list(train.items()))
            for a in ACTIONS:
                if a in ks or (a, s, test) not in trials or trials[(a, s, test)]["merged"] is None:
                    continue
                total += 1
                fired += clf.classify(trials[(a, s, test)]["merged"])["gid"] is not None
    return round(100 * fired / max(total, 1), 1), total


def curve(trials, pool, sizes, samples=12, seed=0):
    rng = np.random.default_rng(seed)
    out = {}
    for k in sizes:
        draws = [pool] if k == len(pool) else [sorted(rng.choice(pool, k, replace=False).tolist())
                                               for _ in range(samples)]
        res = [evaluate(trials, d)[0] for d in draws]
        out[k] = {"correct": round(float(np.mean([r["correct_pct"] for r in res])), 1),
                  "wrong": round(float(np.mean([r["wrong_pct"] for r in res])), 1)}
        print(f"  {k:2d} moves: {out[k]['correct']}% right, {out[k]['wrong']}% wrong", flush=True)
    return out


def largest_set(trials, pool, target=95.0, max_wrong=1.0):
    actions = list(pool)
    while len(actions) > 2:
        c, conf = evaluate(trials, actions)
        if c["correct_pct"] >= target and c["wrong_pct"] <= max_wrong:
            return actions, c
        worst = min(actions, key=lambda a: conf[a].get(str(a), 0) / max(sum(conf[a].values()), 1))
        actions.remove(worst)
    return actions, evaluate(trials, actions)[0]


def main():
    trials = build()
    nseg = [len(tr["segs"]) for tr in trials.values()]
    seg_stats = {"clips": len(nseg), "one_piece_pct": round(100 * np.mean([n == 1 for n in nseg]), 1),
                 "split_pct": round(100 * np.mean([n > 1 for n in nseg]), 1),
                 "missed_pct": round(100 * np.mean([n == 0 for n in nseg]), 1)}
    print("segmenter:", seg_stats, flush=True)
    res = {"segmenter": seg_stats}
    for label, pool in (("all 27", list(ACTIONS)), ("21 arm actions", ARMS), ("6 leg and body moves", LEGS)):
        c, conf = evaluate(trials, pool)
        cl, _ = evaluate(trials, pool, live=True)
        res[label] = {"cut": c, "live": cl, "confusion": conf}
        print(f"  {label:22s} {c['correct_pct']}% right, {c['wrong_pct']}% wrong, {c['rejected_pct']}% missed"
              f"   (live segmenter: {cl['correct_pct']}% right, {cl['wrong_pct']}% wrong)", flush=True)
    res["open_set_false_fire_pct"], res["open_set_tests"] = open_set(trials)
    print("  moves never recorded that fired something:", res["open_set_false_fire_pct"], "%", flush=True)
    print("Moves vs accuracy, all 27 (random sets):", flush=True)
    res["curve"] = curve(trials, list(ACTIONS), (4, 6, 8, 10, 12, 15, 18, 21, 27))
    acts, c = largest_set(trials, list(ACTIONS))
    res["largest_set"] = {"count": len(acts), "actions": [ACTIONS[a] for a in acts], **c}
    print(f"  largest set at >=95% right, <=1% wrong: {len(acts)} moves: {[ACTIONS[a] for a in acts]}")
    OUT.write_text(json.dumps(res, indent=1))
    print("wrote", OUT.name)


if __name__ == "__main__":
    main()
