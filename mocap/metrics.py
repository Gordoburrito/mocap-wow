"""How often does it get it right? Two ways to find out, both local to your machine.

1. Play log: every motion decision, pose fire, near miss and correction you make is counted for
   this session and appended to a daily file (metrics/YYYY-MM-DD.jsonl in your data folder),
   so the Metrics page can show trends across sessions. Nothing leaves your computer.
2. Accuracy test: the app asks for your moves in random order ("Do: Slash left") and records
   what it recognized, with no keys sent. That gives real accuracy, confusions and latency on
   your own hardware, which the play log can only estimate.
"""

import json
import os
import random
import time
from collections import Counter, defaultdict, deque

DRILL_READY_S = 1.2       # show the move's name this long before "go"
DRILL_WINDOW_S = 4.0      # time to do it
DRILL_SETTLE_S = 0.9      # quiet time after a result before the next prompt


class Metrics:
    def __init__(self, directory=None, enabled=True, clock=time.time):
        self.directory = directory
        self.enabled = enabled and directory is not None
        self.clock = clock
        self.started = clock()
        self.decisions = Counter()          # outcome -> count (motions)
        self.reasons = Counter()            # why a motion didn't fire
        self.fires = Counter()              # gid -> keys sent
        self.held = Counter()               # why a recognized gesture didn't send a key
        self.fusion = Counter()             # how the camera changed motion decisions
        self.body_moves = Counter()         # webcam-only move decisions by outcome
        self.corrections = Counter()        # loosen / ignore / wrong
        self.calibration = Counter()        # camera_bias / auto_align steps
        self.latency = deque(maxlen=500)    # ms from motion end to decision
        self.waits = deque(maxlen=500)      # ms spent waiting for the camera
        self.recent = deque(maxlen=300)     # recent decisions for the page's timeline
        self.names = {}
        self._last_cal = {}

    # ------------------------------------------------------------------ logging

    def _write(self, record):
        if not self.enabled:
            return
        try:
            os.makedirs(self.directory, exist_ok=True)
            day = time.strftime("%Y-%m-%d", time.localtime(record["t"]))
            with open(os.path.join(self.directory, f"{day}.jsonl"), "a") as f:
                f.write(json.dumps(record, separators=(",", ":")) + "\n")
        except OSError:
            self.enabled = False            # a read-only disk shouldn't stop you playing

    def motion(self, role, result, outcome, latency_ms=None, waited_ms=None, gesture_name=None,
               kind="motion"):
        """outcome: fired / held / rejected / near / skipped (mirror) / drill.
        kind: "motion" (a weapon) or "move" (a body move from the webcam alone)."""
        self.decisions[outcome] += 1
        if outcome in ("rejected", "near", "held"):
            self.reasons[result.get("reason") or outcome] += 1
        if kind == "move":
            self.body_moves[outcome] += 1
        elif result.get("fused"):
            gyro, fused = result.get("gyro_gid"), result.get("gid")
            effect = ("agreed" if gyro == fused else "rescued" if gyro is None
                      else "vetoed" if fused is None else "corrected")
            self.fusion[effect] += 1
        elif waited_ms is not None:
            self.fusion["no_view"] += 1
        else:
            self.fusion["gyro_only"] += 1
        if latency_ms is not None:
            self.latency.append(latency_ms)
        if waited_ms is not None:
            self.waits.append(waited_ms)
        rec = {"t": self.clock(), "k": kind, "role": role, "o": outcome,
               "g": result.get("gid") or result.get("best"), "n": gesture_name,
               "r": result.get("reason"), "fused": bool(result.get("fused")),
               "gyro": result.get("gyro_gid"), "lat": latency_ms, "wait": waited_ms}
        self.recent.append(rec)
        self._write(rec)

    def fire(self, gid, name, source, sent, why=""):
        if sent:
            self.fires[gid] += 1
            self.names[gid] = name
        else:
            self.held[why or "not sent"] += 1
        rec = {"t": self.clock(), "k": "fire", "g": gid, "n": name, "src": source, "sent": sent,
               "why": why}
        self._write(rec)

    def correction(self, kind, gid, name):
        self.corrections[kind] += 1
        self._write({"t": self.clock(), "k": "correction", "c": kind, "g": gid, "n": name})

    def calibrated(self, kind, device, amount):
        self.calibration[kind] += 1
        now = self.clock()
        if now - self._last_cal.get((kind, device), -1e9) >= 10:      # keep the log small
            self._last_cal[(kind, device)] = now
            self._write({"t": now, "k": "calibration", "c": kind, "d": device, "v": amount})

    def drill_result(self, summary):
        self._write({"t": self.clock(), "k": "drill", **summary})

    # ------------------------------------------------------------------ views

    def session(self):
        lat = sorted(self.latency)
        waits = sorted(self.waits)

        def pct(xs, p):
            return round(xs[min(len(xs) - 1, int(p * len(xs)))], 1) if xs else None

        decided = sum(self.decisions.values())
        return {
            "since": self.started, "motions": decided, "decisions": dict(self.decisions),
            "reasons": dict(self.reasons.most_common(8)), "held": dict(self.held),
            "fires": {self.names.get(g, g): n for g, n in self.fires.most_common()},
            "fusion": dict(self.fusion), "body_moves": dict(self.body_moves),
            "corrections": dict(self.corrections),
            "calibration": dict(self.calibration),
            "latency_ms": {"median": pct(lat, 0.5), "p90": pct(lat, 0.9)},
            "camera_wait_ms": {"median": pct(waits, 0.5), "p90": pct(waits, 0.9),
                               "share": round(len(waits) / max(len(lat), 1), 2) if lat else None},
            "recent": list(self.recent)[-60:],
        }

    def history(self, days=30):
        """Per-day totals from the log files: fires, near misses, corrections, drills."""
        if not self.directory or not os.path.isdir(self.directory):
            return []
        out = []
        for name in sorted(os.listdir(self.directory))[-days:]:
            if not name.endswith(".jsonl"):
                continue
            day = {"day": name[:-6], "motions": 0, "fired": 0, "near": 0, "rejected": 0, "held": 0,
                   "fused": 0, "poses": 0, "corrections": Counter(), "drills": []}
            try:
                with open(os.path.join(self.directory, name)) as f:
                    for line in f:
                        try:
                            r = json.loads(line)
                        except ValueError:
                            continue
                        if r.get("k") in ("motion", "move"):
                            day["motions"] += 1
                            o = r.get("o")
                            if o in ("fired", "near", "rejected", "held"):
                                day[o] += 1
                            day["fused"] += bool(r.get("fused"))
                        elif r.get("k") == "fire" and r.get("src") == "webcam" and r.get("sent"):
                            day["poses"] += 1
                        elif r.get("k") == "correction":
                            day["corrections"][r.get("c")] += 1
                        elif r.get("k") == "drill":
                            day["drills"].append({k: v for k, v in r.items() if k != "k"})
            except OSError:
                continue
            day["corrections"] = dict(day["corrections"])
            out.append(day)
        return out


class Drill:
    """The accuracy test: prompts each move a few times in random order, scores what fired."""

    def __init__(self, gestures, reps=3, clock=time.monotonic, seed=None):
        self.clock = clock
        items = [g for g in gestures for _ in range(reps)]
        random.Random(seed).shuffle(items)
        # Avoid the same move twice in a row where possible: it hides return-stroke trouble.
        for i in range(1, len(items)):
            if items[i]["id"] == items[i - 1]["id"]:
                for j in range(i + 1, len(items)):
                    if items[j]["id"] != items[i]["id"]:
                        items[i], items[j] = items[j], items[i]
                        break
        self.queue = items
        self.index = 0
        self.results = []
        self.phase = "ready"
        self.phase_until = clock() + DRILL_READY_S
        self.got = []
        self.go_at = None

    @property
    def current(self):
        return self.queue[self.index] if self.index < len(self.queue) else None

    @property
    def done(self):
        return self.index >= len(self.queue)

    def observe(self, gid, source, detail=""):
        """Something was recognized (gid) or a near miss / hold-back happened (gid None)."""
        if self.phase != "go" or self.done:
            return
        self.got.append((gid, source, self.clock(), detail))
        if gid is not None:
            self._close()

    def _close(self):
        g = self.current
        first = next((x for x in self.got if x[0] is not None), None)
        got = first[0] if first else None
        latency = round((first[2] - self.go_at) * 1000) if first else None
        self.results.append({"want": g["id"], "want_name": g["name"], "source": g["source"],
                             "got": got, "latency_ms": latency,
                             "detail": next((x[3] for x in self.got if x[3]), ""),
                             "outcome": "correct" if got == g["id"] else "missed" if got is None else "wrong"})
        self.got = []
        self.index += 1
        self.phase = "settle"
        self.phase_until = self.clock() + DRILL_SETTLE_S

    def tick(self):
        now = self.clock()
        if self.done:
            return
        if self.phase == "ready" and now >= self.phase_until:
            self.phase, self.phase_until, self.go_at = "go", now + DRILL_WINDOW_S, now
        elif self.phase == "go" and now >= self.phase_until:
            self._close()
        elif self.phase == "settle" and now >= self.phase_until:
            self.phase, self.phase_until = "ready", now + DRILL_READY_S

    def public(self):
        now = self.clock()
        g = self.current
        return {"phase": "done" if self.done else self.phase, "index": self.index,
                "total": len(self.queue), "gid": g["id"] if g else None,
                "name": g["name"] if g else None, "source": g["source"] if g else None,
                "remaining": round(max(0.0, self.phase_until - now), 2),
                "last": self.results[-1] if self.results else None, "summary": self.summary()}

    def summary(self):
        res = self.results
        if not res:
            return None
        per = defaultdict(lambda: {"n": 0, "correct": 0, "wrong": 0, "missed": 0, "lat": []})
        confusion = defaultdict(Counter)
        for r in res:
            p = per[r["want"]]
            p["name"] = r["want_name"]
            p["n"] += 1
            p[r["outcome"]] += 1
            if r["latency_ms"] is not None and r["outcome"] == "correct":
                p["lat"].append(r["latency_ms"])
            confusion[r["want"]][r["got"] or "(nothing)"] += 1
        n = len(res)
        correct = sum(r["outcome"] == "correct" for r in res)
        wrong = sum(r["outcome"] == "wrong" for r in res)
        lat = sorted(r["latency_ms"] for r in res if r["latency_ms"] is not None and r["outcome"] == "correct")
        return {
            "n": n, "accuracy": round(100 * correct / n, 1), "wrong": round(100 * wrong / n, 1),
            "missed": round(100 * (n - correct - wrong) / n, 1),
            "latency_ms": lat[len(lat) // 2] if lat else None,
            "per": {gid: {"name": p["name"], "n": p["n"], "correct": p["correct"], "wrong": p["wrong"],
                          "missed": p["missed"],
                          "latency_ms": sorted(p["lat"])[len(p["lat"]) // 2] if p["lat"] else None}
                    for gid, p in per.items()},
            "confusion": {gid: dict(c) for gid, c in confusion.items()},
        }
