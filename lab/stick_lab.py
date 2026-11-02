"""Which stick gestures can the engine actually tell apart? Runs simulated takes through the real
recognizer (same segmenter, gravity tracker and classifier the app uses).

    .venv/bin/python -m lab.stick_lab            # full report
    .venv/bin/python -m lab.stick_lab --quick    # fewer takes
"""

import argparse
import json
from itertools import combinations
from pathlib import Path

import numpy as np

from mocap import motion
from mocap.sim.stick import CATALOGUE, non_gestures, take

ROOT = Path(__file__).resolve().parent.parent


def segments(stream, start=motion.DEFAULT_START, align=None):
    t, gyro, accel = stream
    if align is not None:
        gyro, accel = gyro @ align.T, accel @ align.T
    seg = motion.Segmenter(start=start)
    grav = motion.GravityTracker()
    out = []
    for i in range(len(t)):
        lin = grav.linear(gyro[i], accel[i], t[i])
        s = seg.feed(t[i], gyro[i], lin)
        if s is not None:
            out.append(s)
    return out


def record(stick, strokes, rng, n):
    """Like the capture screen: keep doing takes until n clean single segments."""
    got, attempts = [], 0
    while len(got) < n and attempts < n * 4:
        attempts += 1
        segs = segments(take(stick, strokes, rng))
        if len(segs) == 1:
            got.append(segs[0])
    return got, attempts


def build(device, names, rng, n_templates=5):
    stick, catalogue = CATALOGUE[device]
    templates, capture_attempts = {}, {}
    for name in names:
        templates[name], capture_attempts[name] = record(stick, catalogue[name], rng, n_templates)
    clf = motion.SwingClassifier()
    clf.set_data([(n, device, templates[n], None) for n in names if templates[n]], {})
    return clf, templates, capture_attempts


def evaluate(device, names, seed=0, n_templates=5, n_test=40, n_negative=60, spread=1.0):
    rng = np.random.default_rng(seed)
    stick, catalogue = CATALOGUE[device]
    clf, templates, attempts = build(device, names, rng, n_templates)
    per = {}
    confusion = {a: {b: 0 for b in names + ["(none)"]} for a in names}
    for name in names:
        hits = 0
        for _ in range(n_test):
            segs = segments(take(stick, catalogue[name], rng, spread=spread))
            fired = [clf.classify(device, s)["gid"] for s in segs]
            fired = [f for f in fired if f]
            if not fired:
                confusion[name]["(none)"] += 1
                continue
            confusion[name][fired[0]] += 1
            hits += fired[0] == name and len(fired) == 1
        per[name] = {"recall": hits / n_test, "capture_attempts": attempts[name]}
    false_fires, neg_seconds, by_kind = 0, 0.0, {}
    for kind, stream in non_gestures(stick, {n: catalogue[n] for n in names}, rng, n_negative):
        neg_seconds += stream[0][-1]
        fired = [f for f in (clf.classify(device, s)["gid"] for s in segments(stream)) if f]
        false_fires += len(fired)
        by_kind[kind] = by_kind.get(kind, 0) + len(fired)
    return {
        "device": device, "gestures": names, "per_gesture": per, "confusion": confusion,
        "false_fires": false_fires, "false_fires_per_min": false_fires / max(neg_seconds, 1e-9) * 60,
        "false_by_kind": by_kind,
        "mean_recall": float(np.mean([p["recall"] for p in per.values()])),
    }


def pairwise(device, seed=0, n_test=25):
    """Confusability of every pair of gestures when they're the only two recorded."""
    stick, catalogue = CATALOGUE[device]
    names = list(catalogue)
    out = {}
    for a, b in combinations(names, 2):
        res = evaluate(device, [a, b], seed=seed, n_test=n_test, n_negative=0)
        out[f"{a}|{b}"] = 1 - res["mean_recall"]
    return out


def greedy_pick(device, scores, k):
    """Pick k gestures whose worst pairwise confusion is as small as possible."""
    names = list(CATALOGUE[device][1])
    conf = {}
    for key, v in scores.items():
        a, b = key.split("|")
        conf[(a, b)] = conf[(b, a)] = v
    chosen = [min(names, key=lambda n: sum(conf.get((n, m), 0) for m in names if m != n))]
    while len(chosen) < k:
        best = min((n for n in names if n not in chosen),
                   key=lambda n: (max(conf[(n, c)] for c in chosen), sum(conf[(n, c)] for c in chosen)))
        chosen.append(best)
    return chosen


def combo(device, names, seed=0, n=60, lockout=0.3, spread=1.0, sim_kw=None, clf=None, align=None,
          mirror_block=True):
    """Fight-like stream: gesture, short pause, its slower return stroke, pause, next gesture.
    Counts correct fires, misses and extra fires (mostly return strokes) with a lockout after
    every fire on the same weapon."""
    from mocap.sim.stick import Stroke, human_variation, reverse, simulate
    rng = np.random.default_rng(seed)
    stick, catalogue = CATALOGUE[device]
    if clf is None:
        clf, _, _ = build(device, names, rng)
    sim_kw = sim_kw or {}
    ts, gs, accs, expected = [], [], [], []
    offset = 0.0
    for _ in range(n):
        name = names[rng.integers(len(names))]
        strokes = (catalogue[name] + [Stroke(rng.uniform(0.08, 0.3))]
                   + reverse(catalogue[name], slow=rng.uniform(1.5, 2.5)))
        t, g, a = simulate(strokes, stick, rng, human_variation(rng, spread),
                           pre=rng.uniform(0.3, 0.8), post=0.3, **sim_kw)
        ts.append(t + offset)
        gs.append(g)
        accs.append(a)
        offset = ts[-1][-1] + 1 / 200
        expected.append(name)
    stream = (np.concatenate(ts), np.concatenate(gs), np.concatenate(accs))
    fired, locked_until, block = [], -np.inf, (-np.inf, set())
    for s in segments(stream, align=align):
        if s[0, 0] < locked_until:
            continue
        r = clf.classify(device, s)
        gid = r["gid"]
        if gid and mirror_block and gid in block[1] and s[0, 0] < block[0] and r["matches"].get(gid, 0) < 0.75:
            continue
        if gid:
            fired.append(gid)
            locked_until = s[-1, 0] + lockout
            block = (s[-1, 0] + motion.MIRROR_WINDOW, clf.mirrors.get(gid, set()))
    # Align fired against expected (in order).
    i = correct = 0
    for f in fired:
        while i < len(expected) and expected[i] != f:
            i += 1
        if i < len(expected):
            correct += 1
            i += 1
    return {"expected": len(expected), "fired": len(fired), "correct": correct,
            "missed": len(expected) - correct, "extra": len(fired) - correct}


def robustness(device, names, seed=3):
    """How sensitive is the recognizer to sloppier players, an uncalibrated gyro, and a
    Joy-Con remounted at a different angle after recording?"""
    from mocap.sim.stick import Stick, rot
    rng = np.random.default_rng(seed)
    stick, catalogue = CATALOGUE[device]
    clf, _, _ = build(device, names, rng)
    out = {}
    out["baseline"] = combo(device, names, seed=seed, clf=clf)
    out["sloppier (1.6x variation)"] = combo(device, names, seed=seed, clf=clf, spread=1.6)
    out["uncalibrated gyro bias 0.1 rad/s"] = combo(device, names, seed=seed, clf=clf,
                                                    sim_kw={"gyro_bias": [0.1, -0.06, 0.05]})
    from mocap.calibration import mount_alignment
    from mocap.sim.stick import pitch_up, simulate

    def gravity(st, pose):
        posed = Stick(st.name, st.length, pose, st.mount)
        return simulate([], posed, rng, pre=1.0, post=1.0)[2].mean(axis=0)

    ref = gravity(stick, pitch_up(0)), gravity(stick, pitch_up(90))
    remounted = Stick(stick.name, stick.length, stick.rest,
                      rot((1, 0, 0), np.radians(35)) @ rot((0, 0, 1), np.radians(20)))
    new = gravity(remounted, pitch_up(0)), gravity(remounted, pitch_up(90))
    align = mount_alignment(ref[0], ref[1], new[0], new[1])
    CATALOGUE[device] = (remounted, catalogue)
    try:
        out["Joy-Con remounted (not re-aligned)"] = combo(device, names, seed=seed, clf=clf)
        out["Joy-Con remounted + 2-pose re-align"] = combo(device, names, seed=seed, clf=clf, align=align)
    finally:
        CATALOGUE[device] = (stick, catalogue)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "lab" / "results.json"))
    args = ap.parse_args()
    n_test = 15 if args.quick else 40
    report = {}
    for device in CATALOGUE:
        names = list(CATALOGUE[device][1])
        full = evaluate(device, names, n_test=n_test)
        pairs = pairwise(device, n_test=10 if args.quick else 25)
        pick = greedy_pick(device, pairs, 5 if device == "melee" else 4)
        picked = evaluate(device, pick, seed=1, n_test=n_test)
        fights = {f"lockout {lo}s": combo(device, pick, seed=2, lockout=lo) for lo in (0.0, 0.3)}
        robust = robustness(device, pick)
        report[device] = {"all": full, "pairs": pairs, "recommended": pick, "recommended_eval": picked,
                          "combo": fights, "robustness": robust}
        print(f"\n== {device}: all {len(names)} gestures recorded at once ==")
        for n, p in full["per_gesture"].items():
            worst = max(((k, v) for k, v in full["confusion"][n].items() if k != n), key=lambda kv: kv[1])
            print(f"  {n:16s} recall {p['recall']*100:5.1f}%   most often mistaken for: {worst[0]} ({worst[1]})")
        print(f"  false fires: {full['false_fires_per_min']:.2f}/min {full['false_by_kind']}")
        worst_pairs = sorted(pairs.items(), key=lambda kv: -kv[1])[:6]
        print("  most confusable pairs:", ", ".join(f"{k} {v*100:.0f}%" for k, v in worst_pairs))
        print(f"  recommended set: {pick}")
        print(f"    recall {picked['mean_recall']*100:.1f}%, false fires {picked['false_fires_per_min']:.2f}/min {picked['false_by_kind']}")
        for k, v in fights.items():
            print(f"    fight test ({k}): {v['correct']}/{v['expected']} correct, {v['missed']} missed, {v['extra']} extra")
        for k, v in robust.items():
            print(f"    {k}: {v['correct']}/{v['expected']} correct, {v['missed']} missed, {v['extra']} extra")
    Path(args.out).write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
