"""Fetch the demo's gesture-tile photos from Wikimedia Commons and landmark them.

Each of the 16 gestures in the demo profile gets one real, freely licensed photo of a person
doing something like that move. For every slug in PICKS this script:

  1. asks the Commons API for the file's license, author and an 800 px wide thumbnail
     (prop=imageinfo, iiprop=url|extmetadata|size|mime, iiurlwidth=800);
  2. refuses the file unless its license is CC0, Public Domain, CC BY or CC BY-SA
     (anything NC / ND / unclear is rejected);
  3. downloads the thumbnail, converts it to sRGB, resizes it so the long side is 720 px and
     saves it as mocap/demo_photos/<slug>.jpg (JPEG quality 82);
  4. runs MediaPipe Pose (models/pose_landmarker_full.task, IMAGE mode, one pose) on the
     saved file, and keeps it only if shoulders, elbows, wrists and hips are all visible (> 0.5);
  5. writes mocap/demo_photos/credits.json: title, source page, author, license, license URL,
     aspect ratio, and the 33 normalized landmarks [x, y, visibility].

Network etiquette: one request at a time, at least 1 s apart, a plain project User-Agent with
no personal info, and back-off on HTTP 429 (Retry-After is honored).

Run from the repo root:   .venv/bin/python lab/fetch_demo_photos.py [slug ...]
With slugs given, only those are refreshed; the other entries in credits.json are kept.
"""
import html
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "mocap" / "demo_photos"
MODEL = ROOT / "models" / "pose_landmarker_full.task"
API = "https://commons.wikimedia.org/w/api.php"
UA = "mocap-pose-test/0.1 (open-source gesture research)"
LONG_SIDE, QUALITY = 720, 82
KEY_JOINTS = (11, 12, 13, 14, 15, 16, 23, 24)   # shoulders, elbows, wrists, hips
MIN_GAP = 1.2                                   # seconds between requests

# slug -> Commons file title. Chosen by hand (see the report in the commit that added this).
PICKS = {
    # bow
    "draw_aim": "File:Archery at the 2017 Warrior Games 34896116493 31aee156df b.jpg",
    "snap_shot": "File:Zanshin Kimono Standform.jpg",
    "sky_shot": "File:FlightArchery Jack.jpg",
    "pull_string": "File:Archer Doreen Roberts (14845626809).jpg",
    "turn_bow": "File:Archer japonais.jpg",
    # melee
    "slash_left": "File:Entrenamiento de Iaido La Paz - Bolivia 7.jpg",
    "slash_right": "File:Entrenamiento de IAIDO, La Paz Bolivia, mayo 2026.png",
    "low_slash": "File:Entrenamiento de Iaido La Paz - Bolivia 4.jpg",
    "thrust": "File:Advanced lunge performed by an Epee fencer at Athenaikos Fencing Club.jpg",
    "circle": "File:Entrenamiento de Iaido La Paz - Bolivia 3.jpg",
    # body moves
    "duck": "File:Marine Corps officer candidate participate in physical training.jpg",
    "kick": "File:Taekwondo side kick.jpg",
    "stomp": "File:USMC-100518-M-4569K-033.jpg",
    "shove": "File:Dimostrazione di Tui Shou.jpg",
    # poses
    "arms_up": "File:On The Top (69175155).jpeg",
    "right_up": "File:1985 Friendly Chinese man in Gelugor, Penang Island. Spielvogel Archiv.jpg",
}

# Commons' Artist field is sometimes empty or a sentence rather than a name; these were read by hand.
AUTHORS = {
    "File:USMC-100518-M-4569K-033.jpg": "U.S. Marine Corps",
    "File:1985 Friendly Chinese man in Gelugor, Penang Island. Spielvogel Archiv.jpg": "Spielvogel",
}

_last = [0.0]


def get(url, binary=False):
    """One polite GET: spaced out, project User-Agent, backs off on 429 / transient errors."""
    for attempt in range(6):
        wait = _last[0] + MIN_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            data = urllib.request.urlopen(req, timeout=40).read()
            return data if binary else json.loads(data)
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                retry = e.headers.get("Retry-After")
                delay = float(retry) if retry and retry.isdigit() else 10 * (attempt + 1)
                time.sleep(min(delay, 120))
                continue
            return None
        except Exception:
            time.sleep(3 * (attempt + 1))
    return None


def api(**params):
    return get(API + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": "2"}))


def plain(s):
    """Strip HTML tags/entities from an extmetadata value."""
    s = re.sub(r"<[^>]+>", " ", str(s) if s is not None else "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def file_info(titles, width=800):
    """{title: info} for up to ~20 File: titles, with license fields pulled out of extmetadata."""
    r = api(action="query", prop="imageinfo", iiprop="url|extmetadata|size|mime",
            iiurlwidth=str(width), titles="|".join(titles))
    out = {}
    for p in (r or {}).get("query", {}).get("pages", []):
        ii = (p.get("imageinfo") or [None])[0]
        if not ii:
            continue
        em = {k: plain(v.get("value", "")) if isinstance(v, dict) else "" for k, v in ii.get("extmetadata", {}).items()}
        out[p["title"]] = {
            "title": p["title"], "page": ii.get("descriptionurl", ""),
            "thumb": ii.get("thumburl") or ii.get("url"), "width": ii.get("width"), "height": ii.get("height"),
            "mime": ii.get("mime"), "license": em.get("LicenseShortName", ""), "license_url": em.get("LicenseUrl", ""),
            "license_code": em.get("License", ""), "usage": em.get("UsageTerms", ""),
            "author": em.get("Artist", ""), "credit": em.get("Credit", ""),
            "description": em.get("ImageDescription", "")[:300], "restrictions": em.get("Restrictions", ""),
            "nonfree": em.get("NonFree", ""),
        }
    return out


_FREE = re.compile(r"^(cc0( 1\.0)?|public domain( mark)?( 1\.0)?|pd([- ].*)?|cc[- ]by(-sa)?[- ]\d(\.\d)?( [a-z-]+)?)$", re.I)


def license_ok(info):
    """True only for CC0, Public Domain, CC BY and CC BY-SA. Everything else (NC, ND, custom) is refused."""
    lic = info["license"].strip()
    text = " ".join([lic, info["usage"], info["license_code"]]).lower()
    if not lic or info["nonfree"].lower() == "true":
        return False
    if re.search(r"\b(nc|nd)\b|noncommercial|non-commercial|noderiv|no deriv|fair use", text):
        return False
    return bool(_FREE.match(lic))


def license_url(info):
    if info["license_url"]:
        return info["license_url"].replace("http://", "https://")
    if re.match(r"^(public domain|pd)", info["license"], re.I):
        return "https://commons.wikimedia.org/wiki/Commons:Public_domain"
    return ""


def author(info):
    if info["title"] in AUTHORS:
        return AUTHORS[info["title"]]
    a = info["author"] or info["credit"] or "Unknown author"
    return re.sub(r"\s*\(talk\)\s*$", "", a)[:200]


def to_jpeg(data):
    """Bytes of any image -> (jpeg bytes, width, height): sRGB, long side 720 px, quality 82."""
    from PIL import Image, ImageCms, ImageOps
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im)
    icc = im.info.get("icc_profile")
    if im.mode not in ("RGB", "L", "CMYK"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            im = ImageCms.profileToProfile(im, src, ImageCms.createProfile("sRGB"), outputMode="RGB")
        except Exception:
            im = im.convert("RGB")
    im = im.convert("RGB")
    scale = LONG_SIDE / max(im.size)
    size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
    im = im.resize(size, Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=QUALITY, optimize=True)
    return buf.getvalue(), im.width, im.height


_landmarker = [None]


def landmarks(jpeg_bytes):
    """33 [x, y, visibility] for the one pose MediaPipe finds in the image, or None."""
    import cv2
    import mediapipe as mp
    import numpy as np
    from mediapipe.tasks.python import vision
    from mediapipe.tasks.python.core.base_options import BaseOptions
    if _landmarker[0] is None:
        opts = vision.PoseLandmarkerOptions(base_options=BaseOptions(model_asset_path=str(MODEL)),
                                            running_mode=vision.RunningMode.IMAGE, num_poses=1,
                                            min_pose_detection_confidence=0.5)
        _landmarker[0] = vision.PoseLandmarker.create_from_options(opts)
    bgr = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
    res = _landmarker[0].detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)))
    if not res.pose_landmarks:
        return None
    return [[p.x, p.y, 0.0 if p.visibility is None else p.visibility] for p in res.pose_landmarks[0]]


def pose_ok(lm):
    return lm is not None and all(lm[i][2] > 0.5 for i in KEY_JOINTS)


def build(slug, title):
    """Fetch, check, save and landmark one pick. Returns its credits entry, or None with a reason printed."""
    info = file_info([title]).get(title)
    if not info:
        print(f"{slug}: {title} not found", flush=True)
        return None
    if not license_ok(info):
        print(f"{slug}: license refused ({info['license']!r}, {info['usage']!r})", flush=True)
        return None
    data = get(info["thumb"], binary=True)
    if not data:
        print(f"{slug}: download failed", flush=True)
        return None
    jpg, w, h = to_jpeg(data)
    lm = landmarks(jpg)
    if not pose_ok(lm):
        print(f"{slug}: pose not found / key joints not visible", flush=True)
        return None
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{slug}.jpg").write_bytes(jpg)
    print(f"{slug}: ok  {w}x{h}  {info['license']}  {title}", flush=True)
    return {
        "file": f"{slug}.jpg", "title": title,
        "source": "https://commons.wikimedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"), safe=":()_,-.'"),
        "author": author(info), "license": info["license"], "license_url": license_url(info),
        "aspect": round(w / h, 4),
        "lm": [[round(x, 3), round(y, 3), round(v, 3)] for x, y, v in lm],
    }


def main(slugs):
    path = OUT / "credits.json"
    credits = json.loads(path.read_text()) if path.exists() else {}
    for slug in slugs or list(PICKS):
        entry = build(slug, PICKS[slug])
        if entry:
            credits[slug] = entry
        else:
            credits.pop(slug, None)
    order = [s for s in PICKS if s in credits]
    path.write_text(json.dumps({s: credits[s] for s in order}, indent=1, ensure_ascii=False) + "\n")
    missing = [s for s in PICKS if s not in credits]
    print(f"wrote {len(order)} photos to {OUT.relative_to(ROOT)}" + (f"; missing: {', '.join(missing)}" if missing else ""))


if __name__ == "__main__":
    main(sys.argv[1:])
