"""Webcam capture + MediaPipe pose tracking on a background thread."""

import os
import threading
import time

import numpy as np

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
             "pose_landmarker_full/float16/latest/pose_landmarker_full.task")
PREVIEW_WIDTH = 480
PREVIEW_FPS = 15


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
        cap = cv2.VideoCapture(self.index, cv2.CAP_AVFOUNDATION)
        if not cap.isOpened():
            self.engine.set_camera(ok=False, message=(
                "Can't open the camera. Allow your terminal app in System Settings → "
                "Privacy & Security → Camera, then restart."))
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
        frames, fps_t0, last_preview, failures = 0, t0, 0.0, 0
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
                if self.engine.preview_wanted and now - last_preview >= 1 / PREVIEW_FPS:
                    last_preview = now
                    small = cv2.resize(frame, (PREVIEW_WIDTH, int(PREVIEW_WIDTH * h / w)))
                    ok, jpeg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    if ok:
                        self.engine.emit_binary(jpeg.tobytes())
        finally:
            landmarker.close()
            cap.release()
