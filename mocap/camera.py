"""Webcam capture + MediaPipe pose tracking on a background thread."""

import os
import threading
import time

import numpy as np

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
             "pose_landmarker_full/float16/latest/pose_landmarker_full.task")
PHOTO_WIDTH = 640
PHOTO_FPS = 15


class Camera(threading.Thread):
    def __init__(self, engine, index, model_path, stop):
        super().__init__(name="camera", daemon=True)
        self.engine = engine
        self.index = index
        self.model_path = model_path
        self.stop = stop

    def run(self):
        try:
            self._run()
        except Exception as exc:  # keep the rest of the app alive if the camera dies
            self.engine.set_camera(ok=False, fps=0.0, message=f"Camera error: {exc}")

    def _run(self):
        import cv2
        import mediapipe as mp
        from mediapipe.tasks.python import vision
        from mediapipe.tasks.python.core.base_options import BaseOptions

        if not os.path.exists(self.model_path):
            self.engine.set_camera(ok=False, message="Pose model missing — run ./run.sh to download it")
            return
        from . import permission_name
        # The first time, macOS asks for camera access and OpenCV gives up at once rather than
        # wait for the answer, so keep trying: the camera starts as soon as you click Allow.
        while True:
            cap = cv2.VideoCapture(self.index, cv2.CAP_AVFOUNDATION)
            if cap.isOpened():
                break
            cap.release()
            self.engine.set_camera(ok=False, message=(
                f"Waiting for the camera: click Allow on the macOS prompt, or turn on {permission_name()} in "
                "System Settings → Privacy & Security → Camera."))
            if self.stop.wait(2.0):
                return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        cap.set(cv2.CAP_PROP_FPS, 60)
        options = vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=self.model_path),
            running_mode=vision.RunningMode.VIDEO, num_poses=1,
            min_pose_detection_confidence=0.5, min_pose_presence_confidence=0.5,
            min_tracking_confidence=0.5)
        landmarker = vision.PoseLandmarker.create_from_options(options)
        t0 = time.monotonic()
        last_ts = -1
        frames, fps_t0, last_preview, last_photo, failures = 0, t0, 0.0, 0.0, 0
        try:
            while not self.stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    failures += 1
                    if failures == 30:
                        self.engine.set_camera(ok=False, message="Camera stopped sending frames")
                    time.sleep(0.01)
                    continue
                failures = 0
                h, w = frame.shape[:2]
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                ts = max(int((time.monotonic() - t0) * 1000), last_ts + 1)
                last_ts = ts
                result = landmarker.detect_for_video(
                    mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)
                landmarks = None
                if result.pose_landmarks:
                    landmarks = np.array([[p.x, p.y, p.z, 1.0 if p.visibility is None else p.visibility]
                                          for p in result.pose_landmarks[0]])
                now = time.monotonic()
                self.engine.on_pose(landmarks, w / h, now)
                frames += 1
                if now - fps_t0 >= 1.0:
                    self.engine.set_camera(ok=True, fps=round(frames / (now - fps_t0), 1),
                                           message="")
                    frames, fps_t0 = 0, now
                eng = self.engine
                photos = eng.photos_wanted
                if eng.preview_wanted and now - last_preview >= 1 / eng.preview_fps:
                    last_preview = now
                    data = _jpeg(cv2, frame, eng.preview_width, 72)
                    if data:
                        eng.emit_binary(data)
                        if photos and eng.preview_width == PHOTO_WIDTH:
                            eng.on_photo(now, data)
                            last_photo = now
                # While capturing, frames are also kept as the photos of your takes.
                if photos and now - last_photo >= 1 / PHOTO_FPS:
                    last_photo = now
                    data = _jpeg(cv2, frame, PHOTO_WIDTH, 78)
                    if data:
                        eng.on_photo(now, data)
        finally:
            landmarker.close()
            cap.release()


def _jpeg(cv2, frame, width, quality):
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (width, int(width * h / w)), interpolation=cv2.INTER_AREA)
    ok, jpeg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return jpeg.tobytes() if ok else None
