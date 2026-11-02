"""Collect the numbers behind docs/verification.md into web/lab-summary.json, which the app's
Metrics page draws ("How it was tested"). Run after the labs:

    .venv/bin/python -m lab.summarize
"""

import json
import time
from pathlib import Path

LAB = Path(__file__).resolve().parent
OUT = LAB.parent / "web" / "lab-summary.json"


def load(name):
    p = LAB / name
    return json.loads(p.read_text()) if p.exists() else None


def pct(x):
    return round(100 * x, 1) if x is not None else None


def main():
    out = {"generated": time.strftime("%Y-%m-%d")}

    imu = load("real_imu_results.json")
    if imu:
        keep = ["4 cardinal swipes", "stick set: 4 swipes + 2 twists", "4 pokes (thrust and back)", "4 circles",
                "8 swipes (with diagonals)", "all 20"]
        out["real_gyro"] = {
            "source": "6DMG: 28 people, handheld gyro + accelerometer (CC BY 4.0)",
            "sets": [{"label": k, "correct": pct(imu[k]["accuracy"]), "wrong": pct(imu[k]["wrong"]),
                      "rejected": pct(imu[k]["rejected"]), "tests": imu[k]["tests"]} for k in keep if k in imu],
            "takes": [{"takes": n, "correct": pct(imu[f"stick set, {n} takes"]["accuracy"])}
                      for n in (1, 3, 5) if f"stick set, {n} takes" in imu],
            "open_set_false_accept": pct(imu.get("open set: stick set vs 14 unknown gestures", {}).get("false_accept")),
        }

    fr = load("fusion_real_results.json")
    if fr:
        rows = []
        for key, label in (("all_21", "All 21 arm actions"), ("swing_like_10", "10 swing-like actions")):
            if key in fr:
                rows.append({"label": label, **{m: {k: fr[key][m][k + "_pct"] for k in ("correct", "wrong", "rejected")}
                                                for m in ("gyro", "camera", "fused")}})
        out["real_fusion"] = {
            "source": "UTD-MHAD: 8 people x 21 arm actions x 4 trials, wrist gyro + RGB video (MediaPipe)",
            "rows": rows, "curve": fr.get("curve"),
            "largest": {m: fr.get(f"largest_set_{m}", {}).get("count") for m in ("gyro", "fused")},
            "largest_sets": {m: fr.get(f"largest_set_{m}", {}).get("actions") for m in ("gyro", "fused")},
            "hand": fr.get("hand_finding"), "lag": fr.get("lag_finding"), "still": fr.get("stillness"),
            "bias": fr.get("bias_recovery"), "camera_lag_s": fr.get("camera_lag_s"),
            "wrist_label": fr.get("sensor_wrist_label"),
        }

    fs = load("fusion_sim_results.json")
    if fs:
        out["sim_fusion"] = {
            "sets": [{"label": k, "gyro": v["gyro"], "fused": v["fused"]} for k, v in fs.get("sets", {}).items()],
            "camera": [{"label": k, **v} for k, v in fs.get("camera", {}).items()],
            "auto_align": fs.get("auto_align"), "bias": fs.get("bias"),
        }

    picks = load("pick_sets_results.json")
    stick = load("results.json")
    if picks or stick:
        out["sim_fights"] = {"sets": picks}
        if stick:
            out["sim_fights"]["safeguards"] = {
                dev: {k: {kk: v[kk] for kk in ("correct", "expected", "extra")} for k, v in
                      {**stick[dev]["combo"], **stick[dev]["robustness"]}.items()} for dev in stick}

    poses = load("pose_results.json")
    if poses:
        same = poses.get("same_person_variation", {})
        out["poses"] = {"same_person": pct(same.get("accuracy")),
                        "neutral_false": pct(same.get("neutral_false_trigger"))}

    body = load("body_results.json")
    if body:
        jog = body.get("jog in place (should jog)", [])
        quiet = body.get("sit to stand (should do nothing)", []) + body.get("squats (should do nothing)", [])
        out["body"] = {"jog_found": sum(1 for r in jog if r["jog_starts"] > 0), "jog_clips": len(jog),
                       "false_jogs": sum(r["jog_starts"] for r in quiet),
                       "false_hops": sum(r["hops"] for r in quiet), "quiet_clips": len(quiet)}
    OUT.write_text(json.dumps(out, indent=1))
    print("wrote", OUT.relative_to(OUT.parent.parent), f"({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
