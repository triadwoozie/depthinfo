"""
depth_ai.py — Depth-Anything-V2-Small inference, Fog Map (Z-depth mist pass) and
the vision pipeline thread (AGENTS.md § 3.2).

Pipeline per frame:
    camera / mock frame → relative inverse depth D_raw ∈ [0,1] (near = 1)
    → metric anchoring to Z_sonar → scan-range clipping → fog falloff → colormap

Threading (Rule 1): capture, ONNX inference and encoding all run in the
``VisionPipeline`` daemon thread; the asyncio loop only reads the latest JPEGs.

MOCK / OFFLINE (Rule 3): with no camera (or ``SONAR_MOCK_MODE=true``) procedural
640×480 frames with a moving ellipse are produced. If the ONNX file is missing
(or onnxruntime is unavailable) a deterministic procedural depth map (concentric
distance rings + luminance relief) replaces the network.
"""

from __future__ import annotations

import math
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from config import BACKEND_DIR, SystemConfig, get_config, settings

try:
    import onnxruntime as ort  # type: ignore
except Exception:
    ort = None

MODEL_URL = (
    "https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model.onnx"
)
MODEL_INPUT = 518
BG_MASK_PATH = BACKEND_DIR / "calibration_bg_mask.npy"
FRAME_W, FRAME_H = 640, 480
_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


# ---------------------------------------------------------------------------
# Depth estimation
# ---------------------------------------------------------------------------

def download_model(dest: Path | None = None) -> Path:
    """Fetch the ONNX weights from the Hugging Face Hub (~100 MB for the fp32 export)."""
    dest = dest or settings.depth_model_file
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    print(f"[depth_ai] Downloading {MODEL_URL} → {dest}")
    urllib.request.urlretrieve(MODEL_URL, tmp)
    tmp.replace(dest)
    return dest


def procedural_depth(frame_bgr: np.ndarray) -> np.ndarray:
    """Deterministic fallback relative inverse depth (near = 1.0), float32 in [0,1].

    Concentric distance rings centred on the frame midpoint, plus a luminance
    relief term so bright subjects protrude from the background.
    """
    h, w = frame_bgr.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.hypot((xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2)) / math.sqrt(2.0)
    rings = 0.55 * (1.0 - r) + 0.04 * np.cos(2 * math.pi * 8.0 * r)
    gray = cv2.GaussianBlur(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY), (0, 0), 6)
    d = rings + 0.45 * (gray.astype(np.float32) / 255.0)
    d -= d.min()
    return (d / max(float(d.max()), 1e-6)).astype(np.float32)


class DepthEstimator:
    """ONNX Depth-Anything-V2-Small with procedural fallback."""

    def __init__(self) -> None:
        self.session = None
        self.backend = "procedural"
        self._input_name = ""
        path = settings.depth_model_file
        if settings.mock_mode:  # Rule 3: mock runs are deterministic and fast, never network-model backed
            print("[depth_ai] Mock mode — using procedural depth map.")
            return
        if not path.exists() and settings.depth_auto_download and ort is not None:
            try:
                download_model(path)
            except Exception as exc:
                print(f"[depth_ai] Model download failed: {exc}")
        if ort is not None and path.exists():
            try:
                self.session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
                self._input_name = self.session.get_inputs()[0].name
                self.backend = "onnx"
            except Exception as exc:
                print(f"[depth_ai] Could not load {path.name}: {exc}; using procedural depth.")
        if self.backend == "procedural":
            print("[depth_ai] No ONNX model loaded — using procedural fallback depth map.")

    def infer(self, frame_bgr: np.ndarray) -> np.ndarray:
        """Return D_raw (float32, frame-sized, [0,1], near = 1)."""
        if self.session is None:
            return procedural_depth(frame_bgr)
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(cv2.resize(frame_bgr, (MODEL_INPUT, MODEL_INPUT)), cv2.COLOR_BGR2RGB)
        x = (rgb.astype(np.float32) / 255.0 - _IMAGENET_MEAN) / _IMAGENET_STD
        x = np.ascontiguousarray(x.transpose(2, 0, 1)[None])
        out = np.asarray(self.session.run(None, {self._input_name: x})[0], dtype=np.float32)
        d = out.reshape(out.shape[-2], out.shape[-1])
        d = cv2.resize(d, (w, h), interpolation=cv2.INTER_LINEAR)
        d -= d.min()
        return d / max(float(d.max()), 1e-6)


# ---------------------------------------------------------------------------
# Fog Map
# ---------------------------------------------------------------------------

def foreground_depth(d_raw: np.ndarray) -> float:
    """D̄_fg: median relative depth of the central foreground ROI."""
    h, w = d_raw.shape
    roi = d_raw[int(h * 0.3) : int(h * 0.7), int(w * 0.35) : int(w * 0.65)]
    return float(np.median(roi))


def metric_depth(d_raw: np.ndarray, z_sonar: float, relief_span_m: float) -> np.ndarray:
    """Z_metric = Z_sonar + (D̄_fg − D_raw) · S_relief."""
    return z_sonar + (foreground_depth(d_raw) - d_raw) * relief_span_m


def fog_intensity(z: np.ndarray, zmin: float, zmax: float, curve: str, rho: float) -> np.ndarray:
    """I_fog ∈ [0,255] (uint8), near = bright; 0 outside [zmin, zmax]."""
    span = max(zmax - zmin, 1e-6)
    s = (z - zmin) / span
    if curve == "linear":
        f = np.clip((zmax - z) / span, 0.0, 1.0)
    elif curve == "squared":
        f = np.exp(-((rho * s) ** 2))
    else:  # exponential
        f = np.exp(-rho * s)
    out = np.floor(np.clip(f, 0.0, 1.0) * 255.0).astype(np.uint8)
    out[(z < zmin) | (z > zmax)] = 0
    return out


def _hex_bgr(h: str) -> np.ndarray:
    h = h.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return np.array([b, g, r], dtype=np.float32)


def colorize_fog(i_fog: np.ndarray, cfg: SystemConfig) -> np.ndarray:
    """Apply the configured colormap; clipped (zero-intensity) pixels stay black."""
    cm = cfg.vision_and_fog.fog_colormap
    if cm == "mist_grayscale":
        img = cv2.cvtColor(i_fog, cv2.COLOR_GRAY2BGR)
    elif cm == "thermal":
        img = cv2.applyColorMap(i_fog, cv2.COLORMAP_INFERNO)
    elif cm == "viridis":
        img = cv2.applyColorMap(i_fog, cv2.COLORMAP_VIRIDIS)
    else:  # custom_hex: far → near gradient from blender_style palette
        t = (i_fog.astype(np.float32) / 255.0)[..., None]
        near, far = _hex_bgr(cfg.blender_style.custom_near_hex), _hex_bgr(cfg.blender_style.custom_far_hex)
        img = (far + (near - far) * t).astype(np.uint8)
    img[i_fog == 0] = 0
    return img


# ---------------------------------------------------------------------------
# Frame sources
# ---------------------------------------------------------------------------

def mock_frame(t: float, rng: np.random.Generator) -> np.ndarray:
    """Procedural 640×480 BGR frame with a moving ellipse silhouette."""
    yy = np.linspace(12, 40, FRAME_H, dtype=np.float32)[:, None]
    frame = np.dstack([yy * 1.2, yy, yy * 0.8]).repeat(FRAME_W, axis=1)
    frame += rng.integers(0, 6, (FRAME_H, FRAME_W, 3))
    frame = np.clip(frame, 0, 255).astype(np.uint8)
    cx = int(FRAME_W * (0.5 + 0.2 * math.sin(t * 0.4)))
    cy = int(FRAME_H * (0.5 + 0.1 * math.cos(t * 0.3)))
    cv2.ellipse(frame, (cx, cy), (80, 140), 0, 0, 360, (150, 190, 235), -1)
    cv2.ellipse(frame, (cx, cy - 60), (35, 40), 0, 0, 360, (190, 215, 245), -1)
    return cv2.GaussianBlur(frame, (15, 15), 5)


# ---------------------------------------------------------------------------
# Pipeline thread
# ---------------------------------------------------------------------------

@dataclass
class VisionFrame:
    """One processed frame, shared with the stitcher."""
    bgr: np.ndarray
    d_raw: np.ndarray
    z_metric: np.ndarray  # metres, NaN where clipped / background
    i_fog: np.ndarray     # uint8, 0 where clipped
    z_sonar: float
    peak_amplitude: float


def cfg_camera_index(cfg: SystemConfig) -> int:
    return cfg.hardware.camera_device_index


class VisionPipeline:
    def __init__(
        self,
        sonar_provider: Callable,
        on_frame: Callable[[VisionFrame, SystemConfig], None] | None = None,
    ) -> None:
        self._sonar_provider = sonar_provider
        self._on_frame = on_frame
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._rgb_jpg = b""
        self._fog_jpg = b""
        self._fps = 0.0
        self._times: list[float] = []
        self._latest: VisionFrame | None = None
        self.estimator: DepthEstimator | None = None
        self.bg_depth: np.ndarray | None = None
        self.camera_mock = True
        self.active_camera: int | None = None  # index of the camera this pipeline holds open
        self.error: str | None = None
        if BG_MASK_PATH.exists():
            try:
                self.bg_depth = np.load(BG_MASK_PATH)
            except Exception:
                self.bg_depth = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="vision-pipeline")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)

    # -- accessors ---------------------------------------------------------

    @property
    def rgb_jpg(self) -> bytes:
        with self._lock:
            return self._rgb_jpg

    @property
    def fog_jpg(self) -> bytes:
        with self._lock:
            return self._fog_jpg

    @property
    def fps(self) -> float:
        with self._lock:
            return self._fps

    @property
    def latest(self) -> VisionFrame | None:
        with self._lock:
            return self._latest

    @property
    def depth_backend(self) -> str:
        return self.estimator.backend if self.estimator else "loading"

    # -- calibration helpers ----------------------------------------------

    def capture_background(self, n_frames: int = 30, timeout: float = 30.0) -> np.ndarray:
        """Round 3: median of ``n_frames`` consecutive empty-room D_raw maps."""
        frames: list[np.ndarray] = []
        last_ts, deadline = None, time.monotonic() + timeout
        while len(frames) < n_frames and time.monotonic() < deadline:
            f = self.latest
            if f is not None and id(f) != last_ts:
                last_ts = id(f)
                frames.append(f.d_raw)
            time.sleep(0.01)
        if not frames:
            raise RuntimeError("No vision frames available for background capture")
        bg = np.median(np.stack(frames), axis=0).astype(np.float32)
        np.save(BG_MASK_PATH, bg)
        self.bg_depth = bg
        return bg

    def sample_foreground_d_raw(self, n: int = 5) -> float:
        vals: list[float] = []
        last = None
        deadline = time.monotonic() + 10.0
        while len(vals) < n and time.monotonic() < deadline:
            f = self.latest
            if f is not None and id(f) != last:
                last = id(f)
                vals.append(foreground_depth(f.d_raw))
            time.sleep(0.01)
        return float(np.median(vals)) if vals else 0.0

    # -- main loop ---------------------------------------------------------

    def _open_camera(self, cfg: SystemConfig):
        if settings.mock_mode:
            return None
        cap = cv2.VideoCapture(cfg.hardware.camera_device_index)
        if not cap.isOpened():
            print(f"[vision] Cannot open camera {cfg.hardware.camera_device_index}; using mock frames.")
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
        return cap

    def _loop(self) -> None:
        try:
            self.estimator = DepthEstimator()
            cap = self._open_camera(get_config())
            self.camera_mock = cap is None
            self.active_camera = None if cap is None else cfg_camera_index(get_config())
            rng = np.random.default_rng(7)
            while self._running:
                t0 = time.monotonic()
                cfg = get_config()
                if cap is not None:
                    ok, frame = cap.read()
                    if not ok:
                        time.sleep(0.03)
                        continue
                    if frame.shape[:2] != (FRAME_H, FRAME_W):
                        frame = cv2.resize(frame, (FRAME_W, FRAME_H))
                else:
                    frame = mock_frame(t0, rng)
                self._process(frame, cfg)
                time.sleep(max(0.0, 1 / 30 - (time.monotonic() - t0)))
            if cap is not None:
                cap.release()
        except Exception as exc:
            self.error = str(exc)
            print(f"[vision] Pipeline crashed: {exc}")
            raise

    def _process(self, frame: np.ndarray, cfg: SystemConfig) -> None:
        reading = self._sonar_provider()
        vf = cfg.vision_and_fog
        z_sonar = reading.sonar_depth_m
        if z_sonar <= 0.0:  # sonar not locked yet: anchor mid-range so the map is still sensible
            z_sonar = 0.5 * (vf.min_scan_distance_m + vf.max_scan_distance_m)

        d_raw = self.estimator.infer(frame)
        z = metric_depth(d_raw, z_sonar, vf.relief_depth_span_m)

        if cfg.calibration.background_fog_captured and self.bg_depth is not None:
            bg = self.bg_depth
            if bg.shape != d_raw.shape:
                bg = cv2.resize(bg, (d_raw.shape[1], d_raw.shape[0]))
            z = np.where(d_raw - bg > 0.05, z, np.inf)  # empty-room pixels → background

        i_fog = fog_intensity(z, vf.min_scan_distance_m, vf.max_scan_distance_m,
                              vf.fog_falloff_curve, vf.fog_density)
        z_clipped = np.where(i_fog > 0, z, np.nan).astype(np.float32)

        fog_img = colorize_fog(i_fog, cfg)
        rgb_img = frame.copy()
        contours, _ = cv2.findContours((i_fog > 0).astype(np.uint8), cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(rgb_img, [c for c in contours if cv2.contourArea(c) > 500], -1, (128, 255, 0), 2)
        label = f"Sonar Z: {reading.sonar_depth_m:.2f} m  [{reading.status}]"
        cv2.putText(rgb_img, label, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 255, 0), 2)
        cv2.putText(fog_img, f"{vf.min_scan_distance_m:.2f}-{vf.max_scan_distance_m:.2f} m {vf.fog_falloff_curve}",
                    (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

        q = [cv2.IMWRITE_JPEG_QUALITY, 80]
        rgb_jpg = cv2.imencode(".jpg", rgb_img, q)[1].tobytes()
        fog_jpg = cv2.imencode(".jpg", fog_img, q)[1].tobytes()

        vfr = VisionFrame(frame, d_raw, z_clipped, i_fog, float(z_sonar), reading.peak_amplitude)
        now = time.monotonic()
        self._times = (self._times + [now])[-30:]
        fps = (len(self._times) - 1) / (self._times[-1] - self._times[0]) if len(self._times) > 1 else 0.0
        with self._lock:
            self._rgb_jpg, self._fog_jpg, self._latest, self._fps = rgb_jpg, fog_jpg, vfr, fps

        if self._on_frame is not None:
            try:
                self._on_frame(vfr, cfg)
            except Exception as exc:
                print(f"[vision] on_frame error: {exc}")
