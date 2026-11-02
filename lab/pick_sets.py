"""Rank candidate gesture sets by realistic fights (moves + their return strokes) over several
seeds. Used to pick the recommended starter sets in docs/gesture-guide.md."""
import json
from pathlib import Path

import numpy as np
from lab.stick_lab import combo

CANDIDATES = {
    "melee": [
        ["slash_left", "slash_right", "overhead_chop", "rising_slash", "thrust"],
        ["slash_left", "slash_right", "overhead_chop", "thrust", "circle"],
        ["slash_left", "slash_right", "overhead_chop", "spin", "thrust"],
        ["slash_left", "overhead_chop", "thrust", "circle", "wrist_twist"],
        ["slash_left", "slash_right", "overhead_chop", "circle", "wrist_twist"],
        ["slash_left", "slash_right", "overhead_chop", "thrust", "wrist_twist"],
        ["slash_left", "diagonal_slash", "overhead_chop", "thrust", "circle"],
    ],
    "bow": [
        ["point_forward", "punch", "yank_back", "cant_left"],
        ["point_forward", "punch", "yank_back", "cant_left", "cant_right"],
        ["point_forward", "punch", "yank_back", "twist"],
        ["point_forward", "punch", "cant_left", "twist"],
        ["point_forward", "punch", "yank_back", "cant_left", "twist"],
    ],
}

out = {}
for device, sets in CANDIDATES.items():
    print(f"== {device}")
    rows = []
    for names in sets:
        res = [combo(device, names, seed=s, n=60) for s in (11, 12, 13)]
        correct = sum(r["correct"] for r in res); extra = sum(r["extra"] for r in res); total = sum(r["expected"] for r in res)
        rows.append((correct / total - extra / total, correct, extra, total, names))
    for score, correct, extra, total, names in sorted(rows, reverse=True):
        print(f"  {correct}/{total} correct, {extra} extra   {names}")
    out[device] = [{"moves": names, "correct": c, "extra": e, "total": t} for _, c, e, t, names in sorted(rows, reverse=True)]
Path(__file__).with_name("pick_sets_results.json").write_text(json.dumps(out, indent=1))
