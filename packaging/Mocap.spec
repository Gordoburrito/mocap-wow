# PyInstaller spec for Mocap.app. Build with: packaging/build_app.sh
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
datas, binaries, hidden = [], [], []
d, b, h = collect_all("mediapipe")
datas += d
binaries += b
hidden += h

import sdl2dll  # noqa: E402  (pysdl2-dll ships SDL2.framework; the app only needs SDL2 itself)
datas += [(os.path.join(os.path.dirname(sdl2dll.__file__), "dll", "SDL2.framework"), "sdl2dll/dll/SDL2.framework")]
datas += [(os.path.join(ROOT, "web"), "web"),
          (os.path.join(ROOT, "models", "pose_landmarker_full.task"), "models")]
hidden += collect_submodules("mocap") + ["AppKit", "Foundation", "WebKit", "Quartz", "ApplicationServices",
                                         "objc", "sdl2", "sdl2dll", "aiohttp"]

a = Analysis([os.path.join(SPECPATH, "entry.py")], pathex=[ROOT], datas=datas, binaries=binaries,
             hiddenimports=hidden, excludes=["tkinter", "scipy", "IPython", "pytest", "PyInstaller", "lab"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Mocap", console=False, argv_emulation=False,
          target_arch=None, codesign_identity=None)
coll = COLLECT(exe, a.binaries, a.datas, name="Mocap")
app = BUNDLE(
    coll, name="Mocap.app", icon=os.path.join(SPECPATH, "Mocap.icns"),
    bundle_identifier="io.github.mocap-wow.mocap", version="0.3.0",
    info_plist={
        "CFBundleName": "Mocap",
        "CFBundleDisplayName": "Mocap",
        "CFBundleShortVersionString": "0.3.0",
        "LSMinimumSystemVersion": "13.0",
        "LSApplicationCategoryType": "public.app-category.games",
        "NSHighResolutionCapable": True,
        "NSCameraUsageDescription": "Mocap watches your poses and where your weapon hands are. "
                                    "Video is processed on your Mac and never saved or sent anywhere.",
        "NSBluetoothAlwaysUsageDescription": "Mocap reads the Joy-Cons strapped to your weapons.",
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        "NSHumanReadableCopyright": "Open source, MIT licence.",
    },
)
