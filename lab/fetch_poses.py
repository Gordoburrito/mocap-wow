"""Download a small set of real, freely licensed photos of people in poses from Wikimedia
Commons (for testing the pose recognizer). Polite: one request at a time, with backoff."""
import json, os, random, time, urllib.error, urllib.parse, urllib.request

UA = {"User-Agent": "mocap-pose-test/0.1 (open-source gesture research)"}
CLASSES = {"arms_up": "Raised both hands", "crossed_arms": "People standing with crossed arms",
           "hands_on_hips": "Both hands on hips", "archer": "Archers", "t_pose": "T-pose",
           "neutral": "People standing"}
WANT = {"arms_up": 40, "crossed_arms": 40, "hands_on_hips": 40, "archer": 40, "t_pose": 10, "neutral": 50}


def get(url, binary=False):
    for attempt in range(8):
        try:
            data = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40).read()
            return data if binary else json.loads(data)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(min(float(e.headers.get("Retry-After") or 0) or 10 * (attempt + 1), 90))
                continue
            return None
        except Exception:
            time.sleep(3 * (attempt + 1))
    return None


def api(params):
    return get("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({**params, "format": "json"}))


def main():
    time.sleep(30)   # let the earlier rate limit cool off
    path = "data/poses/manifest.json"
    manifest = json.load(open(path)) if os.path.exists(path) else {}
    random.seed(4)
    for cls, cat in CLASSES.items():
        if len(manifest.get(cls, [])) >= WANT[cls] * 0.8:
            continue
        r = api({"action": "query", "list": "categorymembers", "cmtitle": "Category:" + cat, "cmtype": "file", "cmlimit": "500"})
        if not r:
            print(cls, "listing failed", flush=True)
            continue
        files = [m["title"] for m in r["query"]["categorymembers"] if m["title"].lower().endswith((".jpg", ".jpeg", ".png"))]
        random.shuffle(files)
        files = files[:WANT[cls]]
        os.makedirs(f"data/poses/img/{cls}", exist_ok=True)
        manifest[cls] = []
        time.sleep(2)
        for i in range(0, len(files), 25):
            r = api({"action": "query", "titles": "|".join(files[i:i + 25]), "prop": "imageinfo",
                     "iiprop": "url", "iiurlwidth": "640"})
            time.sleep(2)
            if not r:
                continue
            for p in r["query"]["pages"].values():
                ii = (p.get("imageinfo") or [{}])[0]
                url = ii.get("thumburl") or ii.get("url")
                if not url:
                    continue
                name = f"data/poses/img/{cls}/{abs(hash(p['title'])) % 10**8}.jpg"
                if not os.path.exists(name):
                    data = get(url, binary=True)
                    if data:
                        open(name, "wb").write(data)
                    time.sleep(1.0)
                if os.path.exists(name):
                    manifest[cls].append({"file": name, "title": p["title"], "source": ii.get("descriptionurl", "")})
        json.dump(manifest, open(path, "w"), indent=1)
        print(cls, len(manifest[cls]), flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
