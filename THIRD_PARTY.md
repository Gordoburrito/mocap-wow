# Third-party software, models and data

Mocap's own code is MIT licensed (see LICENSE). The built app bundles the packages below.

| Component | Licence | Notes |
|---|---|---|
| Python | PSF | bundled interpreter |
| MediaPipe + Pose Landmarker (full) model | Apache-2.0 | `pose_landmarker_full.task`, downloaded from Google's model storage |
| OpenCV (opencv-contrib-python) | Apache-2.0 | its wheels include FFmpeg (LGPL-2.1+) and other libraries, dynamically linked; see the wheel's licence files |
| SDL2 (pysdl2-dll) | zlib | Joy-Con access |
| PySDL2 | public domain (CC0) | |
| NumPy | BSD-3-Clause | |
| aiohttp | Apache-2.0 | |
| PyObjC | MIT | |
| Matplotlib, sounddevice | PSF-based / MIT | imported by MediaPipe |
| PyInstaller bootloader | GPL-2.0 with the bootloader exception | used to build the app; the exception allows distributing bundled apps |

## Data used for testing (not redistributed)

The labs download these at run time; nothing from them is in this repository except
aggregate numbers (accuracy percentages).

- **6DMG** (Chen, Ji, AlRegib; MMSys 2012), handheld gyro + accelerometer gestures. CC BY 4.0.
  https://zenodo.org/records/10471794
- **UTD-MHAD** (Chen, Jafari, Kehtarnavaz; ICIP 2015), wrist inertial sensor + Kinect + RGB
  video. No licence stated; cite the paper. https://personal.utdallas.edu/~kehtar/UTD-MHAD.html
- **Wikimedia Commons** photos and two jumping-jack videos, each under its own Creative Commons
  licence (recorded per file by `lab/fetch_poses.py` in `data/poses/manifest.json`).

"World of Warcraft" is a trademark of Blizzard Entertainment. "Joy-Con" is a trademark of
Nintendo. This project is not affiliated with either.
