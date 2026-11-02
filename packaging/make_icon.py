"""Draws the app icon (orange tile with the M mark, as in the app's sidebar) and builds
packaging/Mocap.icns with macOS's iconutil."""
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent


def draw(size):
    s = size / 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = 100 * s
    d.rounded_rectangle([pad, pad, size - pad, size - pad], radius=190 * s, fill=(255, 107, 61, 255))
    pts = [(300 * s, 690 * s), (300 * s, 330 * s), (512 * s, 545 * s), (724 * s, 330 * s), (724 * s, 690 * s)]
    d.line(pts, fill=(26, 13, 7, 255), width=max(2, int(86 * s)), joint="curve")
    r = 43 * s
    for x, y in (pts[0], pts[-1]):
        d.ellipse([x - r, y - r, x + r, y + r], fill=(26, 13, 7, 255))
    return img


def main():
    tmp = Path(tempfile.mkdtemp()) / "Mocap.iconset"
    tmp.mkdir()
    for px in (16, 32, 64, 128, 256, 512, 1024):
        img = draw(px)
        if px <= 512:
            img.save(tmp / f"icon_{px}x{px}.png")
        if px >= 32:
            img.save(tmp / f"icon_{px // 2}x{px // 2}@2x.png")
    subprocess.run(["iconutil", "-c", "icns", str(tmp), "-o", str(HERE / "Mocap.icns")], check=True)
    draw(512).save(HERE / "icon.png")
    shutil.rmtree(tmp.parent)
    print("wrote", HERE / "Mocap.icns")


if __name__ == "__main__":
    main()
