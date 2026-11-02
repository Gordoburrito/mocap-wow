"""Gyro motion recognition for IMU weapons (Joy-Cons on the end of PVC pipes).

A motion is cut out of the sensor stream automatically: it starts when the weapon moves
faster than the trigger strength and ends once it settles. Each motion is compared against
your recorded takes with dynamic time warping (so fast and slow versions match) after
scaling to unit size (so soft and hard versions match). Direction is what tells motions apart.

Calibration (calibrate()) sets each gesture's threshold from two things: how much your own
takes vary, and how close the nearest *other* gesture or ignored move is. It also records a
speed floor per gesture so a slow return stroke can't fire a fast gesture.
"""

import numpy as np

RESAMPLE_N = 24
ACCEL_WEIGHT = 0.15        # m/s^2 -> roughly rad/s scale so thrusts register alongside twists
TRIGGER_ACCEL_WEIGHT = 0.2
DEFAULT_START = 3.0        # rad/s-equivalent energy that starts a motion
DEFAULT_THRESHOLD = 0.6
MIN_THRESHOLD, MAX_THRESHOLD = 0.3, 0.9
SPEED_FLOOR = 0.5          # a motion must reach this fraction of your slowest take's peak
SIZE_FLOOR = 0.45          # ...and be at least this fraction of your smallest take's size
G = 9.81


class GravityTracker:
    """Estimates gravity from the accelerometer while the weapon is still.

    It only learns when the weapon is both not rotating AND reading about 1 g, so a straight
    punch or thrust (no rotation, lots of acceleration) isn't mistaken for gravity.
    """

    def __init__(self):
        self.g = None
        self.t = None

    def linear(self, gyro, accel, t=None):
        accel = np.asarray(accel, dtype=float)
        gyro = np.asarray(gyro, dtype=float)
        if self.g is None:
            self.g, self.t = accel.copy(), t
            return np.zeros(3)
        dt = 1 / 200 if t is None or self.t is None else float(np.clip(t - self.t, 0, 0.05))
        self.t = t
        # Gravity is fixed in the world, so in the weapon's frame it turns opposite to the
        # weapon's own rotation. Without this, the weapon's new angle after a chop reads as motion.
        w = gyro * dt
        angle = float(np.linalg.norm(w))
        if angle > 1e-9:
            k = w / angle
            g = self.g
            self.g = (g * np.cos(angle) - np.cross(k, g) * np.sin(angle)
                      + k * np.dot(k, g) * (1 - np.cos(angle)))
        if np.linalg.norm(gyro) < 0.5 and abs(np.linalg.norm(accel) - G) < 0.8:
            self.g += 0.03 * (accel - self.g)
        norm = np.linalg.norm(self.g)
        if norm > 1e-6:
            self.g *= G / norm
        return accel - self.g


class Segmenter:
    """Cuts motions out of a continuous stream of (t, gyro, linear accel) samples."""

    def __init__(self, start=DEFAULT_START, end_ratio=0.45, quiet_s=0.05, min_s=0.08, max_s=1.6,
                 preroll_s=0.03):
        self.start = start
        self.end_ratio = end_ratio
        self.quiet_s = quiet_s
        self.min_s = min_s
        self.max_s = max_s
        self.preroll_s = preroll_s
        self._recent = []
        self._active = None
        self._quiet_since = None
        self._cooldown_until = -np.inf

    @staticmethod
    def energy(gyro, lin):
        # Straight-line moves (punch, raise) barely rotate, so acceleration counts a bit more
        # here than in the shape comparison.
        return float(np.linalg.norm(gyro) + TRIGGER_ACCEL_WEIGHT * np.linalg.norm(lin))

    @property
    def active(self):
        return self._active is not None

    def feed(self, t, gyro, lin):
        """Returns a finished segment as an (n, 7) array [t, gx, gy, gz, ax, ay, az], or None."""
        row = np.concatenate([[t], gyro, lin])
        e = self.energy(gyro, lin)
        if self._active is None:
            self._recent.append(row)
            while self._recent and t - self._recent[0][0] > self.preroll_s:
                self._recent.pop(0)
            if e >= self.start and t >= self._cooldown_until:
                self._active = list(self._recent)
                self._quiet_since = None
            return None
        self._active.append(row)
        duration = t - self._active[0][0]
        if e < self.start * self.end_ratio:
            if self._quiet_since is None:
                self._quiet_since = t
        else:
            self._quiet_since = None
        if duration > self.max_s:
            # Continuous waving isn't a gesture; drop it and wait for things to settle.
            self._active = None
            self._recent = []
            self._cooldown_until = t + 0.3
            return None
        if self._quiet_since is not None and t - self._quiet_since >= self.quiet_s:
            seg = np.array(self._active)
            self._active = None
            self._recent = []
            if seg[-1, 0] - seg[0, 0] >= self.min_s:
                return seg
        return None


def _energies(seg):
    return np.linalg.norm(seg[:, 1:4], axis=1) + ACCEL_WEIGHT * np.linalg.norm(seg[:, 4:7], axis=1)


def peak(segment):
    """Peak motion energy of a raw segment, used for the speed floor."""
    return float(np.max(_energies(np.asarray(segment, dtype=float))))


def size(segment):
    """How much motion there was in total (energy integrated over time), for the size floor.
    A wrist fidget can have the same *shape* as a real move, but it's far smaller."""
    seg = np.asarray(segment, dtype=float)
    return float(np.trapezoid(_energies(seg), seg[:, 0])) if len(seg) > 1 else 0.0


def prepare(segment, n=RESAMPLE_N):
    """Raw segment (n, 7) -> (n, 6) resampled, unit-size sequence, plus its peak energy."""
    seg = np.asarray(segment, dtype=float)
    t = seg[:, 0] - seg[0, 0]
    data = np.concatenate([seg[:, 1:4], seg[:, 4:7] * ACCEL_WEIGHT], axis=1)
    if t[-1] <= 0:
        t = np.linspace(0, 1, len(seg))
    grid = np.linspace(0, t[-1], n)
    out = np.stack([np.interp(grid, t, data[:, i]) for i in range(data.shape[1])], axis=1)
    rms = np.sqrt((out ** 2).sum(axis=1).mean())
    return out / max(rms, 1e-6), peak(seg)


def dtw_batch(seq, bank, band=0.25):
    """DTW distance from seq (n, d) to every sequence in bank (T, n, d). Returns (T,)."""
    n = seq.shape[0]
    if len(bank) == 0:
        return np.empty(0)
    cost = np.linalg.norm(bank[:, :, None, :] - seq[None, None, :, :], axis=3)  # (T, n, n)
    r = max(1, int(np.ceil(band * n)))
    acc = np.full((len(bank), n + 1, n + 1), np.inf)
    acc[:, 0, 0] = 0.0
    for i in range(1, n + 1):
        for j in range(max(1, i - r), min(n, i + r) + 1):
            best = np.minimum(np.minimum(acc[:, i - 1, j], acc[:, i - 1, j - 1]), acc[:, i, j - 1])
            acc[:, i, j] = cost[:, i - 1, j - 1] + best
    return acc[:, n, n] / n


SIZE_WEIGHT = 0.25         # adds |log(size ratio)|: a 330-degree spin isn't a 110-degree slash
TWIN_FACTOR = 0.7          # moves whose takes come this close (x threshold) are gyro twins: true
                           # look-alikes such as a high vs low slash sit at 0.2-0.4; real but
                           # different moves often within 1.25, and the gyro still tells those apart


def distances(seq, size_, bank, bank_sizes):
    """Shape distance (DTW) plus a penalty for very different overall size."""
    d = dtw_batch(seq, bank)
    if len(d) and size_ > 0:
        ratio = np.log(np.maximum(size_, 1e-6) / np.maximum(np.asarray(bank_sizes), 1e-6))
        d = d + SIZE_WEIGHT * np.abs(ratio)
    return d


def auto_threshold(templates):
    """Threshold from how consistent one gesture's takes are (no knowledge of other gestures)."""
    seqs = [prepare(t)[0] for t in templates]
    if len(seqs) < 2:
        return DEFAULT_THRESHOLD
    bank = np.stack(seqs)
    nearest = [dtw_batch(s, np.delete(bank, i, axis=0)).min() for i, s in enumerate(seqs)]
    return float(np.clip(2.0 * np.median(nearest), 0.35, 0.85))


def _thresholds(D, owners, ign=None, lo=None, hi=None, default=None):
    """Per-gesture thresholds from a full take-to-take distance matrix.

    D: (T, T) distances between every pair of takes (NaN = not comparable); owners: the
    gesture of each take; ign: (T, I) distances from each take to each ignored move;
    lo/hi/default: threshold limits in the distance's own units (gyro units by default).
    """
    lo = MIN_THRESHOLD if lo is None else lo
    hi = MAX_THRESHOLD if hi is None else hi
    default = DEFAULT_THRESHOLD if default is None else default
    out = {}
    gids = list(dict.fromkeys(owners))
    own = {gid: [i for i, o in enumerate(owners) if o == gid] for gid in gids}
    for gid, idx in own.items():
        loo = []
        for i in idx:
            rest = [j for j in idx if j != i and np.isfinite(D[i, j])]
            if rest:
                loo.append(min(D[i, j] for j in rest))
        spread = float(np.percentile(loo, 90)) if loo else default / 2
        nearest, nearest_gid = np.inf, None
        for other, oidx in own.items():
            if other == gid:
                continue
            block = D[np.ix_(idx, oidx)]
            if np.isfinite(block).any():
                d = float(np.nanmin(block))
                if d < nearest:
                    nearest, nearest_gid = d, other
        if ign is not None and ign.size and np.isfinite(ign[idx]).any():
            d = float(np.nanmin(ign[idx]))
            if d < nearest:
                nearest, nearest_gid = d, "(ignored moves)"
        threshold = 1.8 * spread
        if np.isfinite(nearest):
            threshold = min(threshold, 0.6 * nearest)
        threshold = float(np.clip(max(threshold, 1.15 * spread), lo, hi))
        out[gid] = {
            "threshold": threshold, "spread": spread,
            "nearest": nearest if np.isfinite(nearest) else None, "nearest_gid": nearest_gid,
            "margin": (nearest / threshold) if np.isfinite(nearest) else None,
        }
    return out


def _gyro_matrix(seqs, sizes, ign=None):
    bank, sizes = np.stack(seqs), np.asarray(sizes)
    D = np.stack([distances(s, sizes[i], bank, sizes) for i, s in enumerate(bank)])
    I = None
    if ign is not None:
        I = np.stack([distances(s, sizes[i], ign[0], ign[1]) for i, s in enumerate(bank)])
    return D, I


def calibrate(gestures, ignore=(), matrix=None):
    """gestures: {gid: [raw segments]}; ignore: [raw segments that must never fire].

    Returns {gid: {"threshold", "spread", "nearest", "nearest_gid", "margin", "floor", ...}}.
    spread  = typical distance between your own takes (lower = more consistent)
    nearest = distance from this gesture's takes to the closest other gesture/ignored move
    margin  = nearest / threshold (>1.25 is comfortable, <1 means they overlap)
    """
    owners, seqs, sizes = [], [], []
    for gid, temps in gestures.items():
        for t in temps or ():
            owners.append(gid)
            seqs.append(prepare(t)[0])
            sizes.append(size(t))
    if not owners:
        return {}
    ign = None
    if len(ignore):
        ign = (np.stack([prepare(t)[0] for t in ignore]), np.array([size(t) for t in ignore]))
    if matrix is not None:
        # The caller already has take-to-take distances (same take order): only the ignored
        # moves are left to compare.
        D = matrix
        I = None if ign is None else np.stack(
            [distances(s, sizes[i], ign[0], ign[1]) for i, s in enumerate(seqs)])
    else:
        D, I = _gyro_matrix(seqs, sizes, ign)
    out = _thresholds(D, owners, I)
    for gid, info in out.items():
        temps = gestures[gid]
        info["floor"] = SPEED_FLOOR * float(min(peak(t) for t in temps))
        info["size_floor"] = SIZE_FLOOR * float(min(size(t) for t in temps))
    return out


VISION_WEIGHT = 0.5        # share of a fused distance that comes from the camera's hand path


def path_matrix(paths):
    """(T, T) DTW distances between hand paths; NaN where either take has no path."""
    T = len(paths)
    D = np.full((T, T), np.nan)
    have = [i for i, p in enumerate(paths) if p is not None]
    if len(have) < 2:
        return D
    bank = np.stack([np.asarray(paths[i], dtype=float) for i in have])
    for a, i in enumerate(have):
        D[i, have] = dtw_batch(bank[a], bank)
    return D


def vision_scale(Dg, Dv, owners):
    """How many gyro units one unit of hand-path distance is worth: the ratio of how much your
    own takes typically vary in each. Puts both on the same footing without hand tuning."""
    g, v = [], []
    for i, gid in enumerate(owners):
        same = [j for j, o in enumerate(owners) if o == gid and j != i and np.isfinite(Dv[i, j])]
        if same:
            g.append(min(Dg[i, j] for j in same))
            v.append(min(Dv[i, j] for j in same))
    if len(v) < 3 or np.median(v) <= 1e-6:
        return None
    return float(np.clip(np.median(g) / np.median(v), 0.02, 50.0))


def fuse(dg, dv, kappa, weight=VISION_WEIGHT):
    return (1 - weight) * dg + weight * kappa * dv


def mirror(seq):
    """What the swing back looks like: the same path run backwards. Rotation flips sign and
    order; acceleration only flips order."""
    out = seq[::-1].copy()
    out[:, :3] *= -1
    return out


MIRROR_WINDOW = 1.0        # seconds after a hit during which its mirror move counts as the return


class SwingClassifier:
    """Recognizes weapon motions. Gyro alone always works; when the camera saw the weapon hand
    and the takes have hand paths too, a fused decision is made from both (see fusion.py)."""

    def __init__(self):
        self.banks = {}         # device -> (bank (T, n, 6), [gid per template], {gid: info}, sizes)
        self.paths = {}         # device -> (path bank (T, PATH_N, 2) with NaN rows, has-path mask, kappa)
        self.fused = {}         # gid -> fused calibration info (only gestures whose takes have paths)
        self.ignore = {}        # device -> bank of moves that should never fire
        self.calibration = {}   # gid -> calibrate() info (gyro only)
        self.mirrors = {}       # gid -> set of gids whose shape is this move's swing back
        self.twins = set()      # gids the gyro alone can't keep apart from another move
        self.twin_of = {}       # gid -> the moves it's a gyro twin of (told apart by the camera)

    def set_data(self, gestures, ignore, sensitivity=None, paths=None):
        """gestures: iterable of (gid, device, templates, threshold-or-None);
        ignore: {device: [segments]}; sensitivity: {gid: multiplier on the threshold};
        paths: {gid: [hand path or None per template]} from the camera, if any."""
        sensitivity = sensitivity or {}
        paths = paths or {}
        by_device = {}
        for gid, device, templates, threshold in gestures:
            if device and templates:
                by_device.setdefault(device, []).append((gid, templates, threshold))
        self.banks, self.calibration, self.mirrors, self.paths, self.fused = {}, {}, {}, {}, {}
        self.twins, self.twin_of = set(), {}
        for device, items in by_device.items():
            prepared = [prepare(t)[0] for _, temps, _ in items for t in temps]
            all_sizes = [size(t) for _, temps, _ in items for t in temps]
            Dg, _ = _gyro_matrix(prepared, all_sizes)
            cal = calibrate({gid: temps for gid, temps, _ in items}, ignore.get(device, ()), matrix=Dg)
            seqs, owners, sizes, info, tpaths = [], [], [], {}, []
            for gid, temps, threshold in items:
                c = dict(cal[gid])
                if threshold is not None:
                    c["threshold"] = float(threshold)
                c["threshold"] *= sensitivity.get(gid, 1.0)
                info[gid] = c
                self.calibration[gid] = c
                gp = list(paths.get(gid) or [])
                for k, t in enumerate(temps):
                    seqs.append(prepared[len(owners)])
                    sizes.append(all_sizes[len(owners)])
                    owners.append(gid)
                    tpaths.append(gp[k] if k < len(gp) else None)
            self.banks[device] = (np.stack(seqs), owners, info, np.array(sizes))
            bank = self.banks[device][0]
            for gid, temps, _ in items:
                back = mirror(np.mean([prepare(t)[0] for t in temps], axis=0))
                d = dtw_batch(back, bank)
                close = {o for o, dist in zip(owners, d) if o != gid and dist <= 1.3 * info[o]["threshold"]}
                self.mirrors[gid] = close
            # Gyro twins: moves whose takes come within TWIN_FACTOR x each other's threshold. Without
            # the camera they're held back rather than guessed (see classify).
            idx = {gid: [i for i, o in enumerate(owners) if o == gid] for gid in info}
            for a in info:
                for b in info:
                    if a != b and Dg[np.ix_(idx[a], idx[b])].min() < TWIN_FACTOR * info[a]["threshold"]:
                        self.twin_of.setdefault(a, set()).add(b)
            self.twins |= set(self.twin_of)
            self._set_paths(device, seqs, sizes, owners, tpaths, info, sensitivity, Dg)
        self.ignore = {dev: (np.stack([prepare(t)[0] for t in segs]), np.array([size(t) for t in segs]))
                       for dev, segs in ignore.items() if len(segs)}

    def _set_paths(self, device, seqs, sizes, owners, tpaths, info, sensitivity, Dg):
        has = np.array([p is not None for p in tpaths])
        if has.sum() < 3:
            return
        Dv = path_matrix(tpaths)
        kappa = vision_scale(Dg, Dv, owners)
        if kappa is None:
            return
        Df = fuse(Dg, Dv, kappa)            # NaN wherever a take has no path
        fused = _thresholds(Df, owners)
        dims = next(np.asarray(p).shape for p in tpaths if p is not None)
        pbank = np.stack([np.asarray(p, dtype=float) if p is not None else np.full(dims, np.nan)
                          for p in tpaths])
        self.paths[device] = (pbank, has, kappa)
        for gid, c in fused.items():
            n_paths = sum(1 for o, h in zip(owners, has) if o == gid and h)
            if n_paths >= 2:
                c = dict(c, floor=info[gid]["floor"], size_floor=info[gid]["size_floor"],
                         kappa=kappa)
                c["threshold"] *= sensitivity.get(gid, 1.0)
                self.fused[gid] = c

    def _decide(self, result, per, info, pk, seg_size, seq, device, gyro_per):
        result["matches"] = {gid: float(np.clip(1 - dist / (2 * info[gid]["threshold"]), 0, 1))
                             for gid, dist in per.items()}
        # Nearest match wins; thresholds only decide whether it's close enough to count.
        ranked = sorted(per.items(), key=lambda kv: kv[1])
        gid, dist = ranked[0]
        thr = info[gid]["threshold"]
        result.update(best=gid, distance=dist)
        if dist > thr:
            result["reason"] = "didn't match any motion closely enough"
            if dist < 1.35 * thr:
                result["near"] = gid
            return result
        if pk < info[gid]["floor"]:
            result["reason"] = "too slow (looks like a return or a wind-up)"
            return result
        if seg_size < info[gid].get("size_floor", 0):
            result["reason"] = "too small (looks like a fidget)"
            return result
        if device in self.ignore and distances(seq, seg_size, *self.ignore[device]).min() < gyro_per[gid]:
            result["reason"] = "looked like one of your ignored moves"
            return result
        if len(ranked) > 1:
            other_gid, other = ranked[1]
            if other <= info[other_gid]["threshold"] and dist > 0.85 * other:
                result["reason"] = "too close between two motions"
                result["ambiguous"] = [gid, other_gid]
                return result
        result["gid"] = gid
        result["reason"] = "matched"
        return result

    def classify(self, device, segment, path=None):
        """Returns dict: gid (or None), reason, peak, best guess, per-gesture match 0..1, and
        `near` when the best gesture missed its threshold by less than 35%.

        With a hand path from the camera (and takes that have paths), the decision uses both;
        `gyro_gid` is what the gyro alone would have said. `confident` means the gyro alone is
        sure enough that waiting for the camera can't change the answer."""
        seq, pk = prepare(segment)
        result = {"gid": None, "reason": "no motions recorded for this weapon", "peak": pk,
                  "matches": {}, "near": None, "best": None, "distance": None, "fused": False,
                  "confident": False, "template": None}
        if device not in self.banks:
            return result
        bank, owners, info, bank_sizes = self.banks[device]
        seg_size = size(segment)
        d = distances(seq, seg_size, bank, bank_sizes)
        per, best_t = {}, {}
        for i, (gid, dist) in enumerate(zip(owners, d)):
            if dist < per.get(gid, np.inf):
                per[gid], best_t[gid] = float(dist), i
        self._decide(result, per, info, pk, seg_size, seq, device, per)
        g = result["gid"]
        # Only moves recorded with the camera: those are the ones it's meant to tell apart.
        twins = [t for t in self.twin_of.get(g, ()) if g in self.fused and t in self.fused
                 and per.get(t, np.inf) <= 1.5 * info[t]["threshold"]]
        will_fuse = path is not None and device in self.paths
        if g and twins and not will_fuse:
            # The gyro alone can't tell this from its twin (e.g. a high and a low slash). With no
            # view of the hand, hold it back instead of guessing.
            result.update(gid=None, reason=f"needs the camera to tell it apart from another move",
                          twin=twins[0], twin_of=g)
        result["gyro_gid"] = result["gid"] or result.get("twin_of")
        g = result["gyro_gid"]
        # Sure enough to skip the camera: well inside its own threshold, not a gyro twin, and no
        # other move anywhere near its threshold on this very swing.
        result["confident"] = bool(result["gid"] and per[g] <= 0.8 * info[g]["threshold"] and g not in self.twins
                                   and all(d > 1.25 * info[o]["threshold"] for o, d in per.items() if o != g))
        result["template"] = best_t.get(result["best"])
        result["needs_camera"] = device in self.paths and any(gid in self.fused for gid in per)
        if not will_fuse:
            return result
        pbank, has, kappa = self.paths[device]
        dv = np.full(len(owners), np.nan)
        dv[has] = dtw_batch(np.asarray(path, dtype=float), pbank[has])
        fper, finfo, vis, fbest = {}, {}, {}, {}
        for i, gid in enumerate(owners):
            if gid in self.fused:
                if not has[i]:
                    continue
                f = float(fuse(d[i], dv[i], kappa))
                if f < fper.get(gid, np.inf):
                    fper[gid], fbest[gid] = f, i
                    vis[gid] = float(dv[i])
                finfo[gid] = self.fused[gid]
            else:
                fper[gid], finfo[gid], fbest[gid] = per[gid], info[gid], best_t[gid]
        fused = {"gid": None, "reason": "", "peak": pk, "matches": {}, "near": None, "best": None,
                 "distance": None}
        self._decide(fused, fper, finfo, pk, seg_size, seq, device, per)
        result.update(fused, fused=True, template=fbest.get(fused["best"]),
                      vision={k: round(v, 3) for k, v in vis.items()})
        return result

    def template_seq(self, device, index):
        return self.banks[device][0][index]


def held_back(result):
    """The move a decision recognized but chose not to send (a gyro twin with no camera view,
    or a tie between two moves), or None. Its swing back still needs the lockout."""
    if result.get("gid"):
        return None
    if result.get("twin_of"):
        return result["twin_of"]
    if result.get("ambiguous"):
        return result["ambiguous"][0]
    return None


class ClashDetector:
    """Both weapons jolt at the same instant: the shafts were tapped together.

    A swing builds acceleration smoothly over ~0.1 s; an impact jumps it by tens of m/s^2 from
    one sample to the next. We look for that jump on both weapons within `window` seconds of
    each other (host clock, since each Joy-Con has its own sensor clock).
    """

    def __init__(self, jolt=25.0, window=0.06, cooldown=1.5):
        self.jolt = jolt
        self.window = window
        self.cooldown = cooldown
        self.prev = {}
        self.spikes = {}
        self.cooldown_until = -np.inf

    def feed(self, role, host_t, accel):
        accel = np.asarray(accel, dtype=float)
        prev = self.prev.get(role)
        self.prev[role] = accel
        if prev is None or float(np.linalg.norm(accel - prev)) < self.jolt:
            return False
        self.spikes[role] = host_t
        if host_t < self.cooldown_until:
            return False
        for other, t in self.spikes.items():
            if other != role and abs(host_t - t) <= self.window:
                self.cooldown_until = host_t + self.cooldown
                return True
        return False
