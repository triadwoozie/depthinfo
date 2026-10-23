"""
vision_engine.py — OpenCV Camera Capture & Contour/Depth Extraction.

Runs in a dedicated background thread. Exposes:
  - latest_frame_jpg  : bytes  — latest JPEG frame for MJPEG streaming
  - latest_points     : list   — list of [x, y, z] 3-D point estimates
  - fps               : float  — measured capture FPS

MOCK MODE
---------
When settings.mock_mode is True, no camera is opened. Instead, the engine
generates synthetic 640×480 frames with:
  - A dark background gradient
  - A moving bright ellipse simulating a person silhouette
  - Extracted contour points mapped to 3D using the sonar_depth_m EMA value
"""

from __future__ import annotations

import math
import threading
import time
from typing import Callable

import numpy as np

from config import get_dashboard_config, settings

try:
    import cv2  # type: ignore
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False


class VisionEngine:
    """
    Background OpenCV capture and contour extraction thread.

    Pass a ``depth_provider`` callable that returns the current sonar depth (m).
    This is used to assign a Z coordinate to 2D image contour points.
    """

    def __init__(self, depth_provider: Callable[[], float]) -> None:
        self._depth_provider = depth_provider
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

        # Shared state (guarded by _lock)
        self._latest_frame_jpg: bytes = b""
        self._latest_points: list[list[float]] = []
        self._fps: float = 0.0

        # FPS tracking
        self._frame_times: list[float] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        target = self._mock_loop if settings.mock_mode else self._hardware_loop
        self._thread = threading.Thread(target=target, daemon=True, name="vision-engine")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    @property
    def latest_frame_jpg(self) -> bytes:
        with self._lock:
            return self._latest_frame_jpg

    @property
    def latest_points(self) -> list[list[float]]:
        with self._lock:
            return list(self._latest_points)

    @property
    def fps(self) -> float:
        with self._lock:
            return self._fps

    # ------------------------------------------------------------------
    # Shared processing helpers
    # ------------------------------------------------------------------

    def _process_frame(self, frame: np.ndarray) -> tuple[bytes, list[list[float]]]:
        """
        Extract contour points from a frame and encode it as JPEG.

        Returns (jpeg_bytes, list_of_xyz_points).
        """
        depth_m = self._depth_provider()

        # Convert to grayscale and threshold to get silhouette mask
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        _, mask = cv2.threshold(gray, 30, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # Map significant contour points to 3D
        h, w = frame.shape[:2]
        points: list[list[float]] = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < 500:  # ignore tiny noise blobs
                continue
            # Subsample contour to avoid huge point clouds
            step = max(1, len(cnt) // 20)
            for pt in cnt[::step]:
                px, py = pt[0]
                # Normalise to [-1, 1] centred on image
                x = (px / w - 0.5) * 2.0 * depth_m
                y = -(py / h - 0.5) * 2.0 * depth_m
                z = depth_m
                points.append([round(x, 4), round(y, 4), round(z, 4)])

        # Draw overlay: contours + depth text
        overlay = frame.copy()
        cv2.drawContours(overlay, contours, -1, (0, 255, 128), 2)
        cv2.putText(
            overlay,
            f"Depth: {depth_m:.2f}m",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 255, 200),
            2,
        )

        _, buf = cv2.imencode(".jpg", overlay, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return buf.tobytes(), points

    def _update_fps(self) -> None:
        now = time.monotonic()
        self._frame_times.append(now)
        # Keep only the last 30 frame timestamps
        self._frame_times = self._frame_times[-30:]
        if len(self._frame_times) >= 2:
            elapsed = self._frame_times[-1] - self._frame_times[0]
            self._fps = (len(self._frame_times) - 1) / elapsed if elapsed > 0 else 0.0

    # ------------------------------------------------------------------
    # Hardware loop
    # ------------------------------------------------------------------

    def _hardware_loop(self) -> None:
        if not _CV2_AVAILABLE:
            print("[VisionEngine] OpenCV not available — falling back to mock mode.")
            self._mock_loop()
            return

        configured_camera = get_dashboard_config().camera_device_index
        camera_index = (
            settings.camera_index if configured_camera is None else configured_camera
        )
        cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            print(
                f"[VisionEngine] Cannot open camera {camera_index} "
                "— falling back to mock mode."
            )
            self._mock_loop()
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        while self._running:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.033)
                continue

            jpg, points = self._process_frame(frame)
            self._update_fps()

            with self._lock:
                self._latest_frame_jpg = jpg
                self._latest_points = points
                self._fps = self._fps  # already updated above

        cap.release()

    # ------------------------------------------------------------------
    # Mock loop
    # ------------------------------------------------------------------

    def _mock_loop(self) -> None:
        """
        Generate synthetic frames without a real camera.

        Each frame contains a moving bright ellipse on a dark background,
        simulating a person silhouette in a studio space.
        """
        if not _CV2_AVAILABLE:
            # Ultra-minimal fallback: just send a blank JPEG placeholder
            placeholder = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # minimal JPEG header stub
            while self._running:
                with self._lock:
                    self._latest_frame_jpg = placeholder
                    self._latest_points = []
                    self._fps = 0.0
                time.sleep(0.033)
            return

        W, H = 640, 480
        rng = np.random.default_rng(7)

        while self._running:
            t = time.monotonic()

            # Dark background with subtle noise
            frame = rng.integers(5, 20, (H, W, 3), dtype=np.uint8)

            # Moving ellipse (simulated silhouette)
            cx = int(W * (0.5 + 0.25 * math.sin(t * 0.4)))
            cy = int(H * (0.5 + 0.15 * math.cos(t * 0.3)))
            cv2.ellipse(frame, (cx, cy), (80, 140), 0, 0, 360, (200, 200, 210), -1)

            # Gaussian blur to simulate soft silhouette edges
            frame = cv2.GaussianBlur(frame, (15, 15), 5)

            jpg, points = self._process_frame(frame)
            self._update_fps()

            with self._lock:
                self._latest_frame_jpg = jpg
                self._latest_points = points

            time.sleep(0.033)  # ~30 FPS
