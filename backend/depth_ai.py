"""
depth_ai.py — camera acquisition, relative-depth inference, the hybrid sonar-anchored metric
depth model and the Fog Map (AGENTS.md § 3.2).

Threads (Rule 1: nothing here ever runs on the asyncio loop)
    CameraGrabber       reads the camera (or generates mock frames) and keeps only the newest frame
    NeuralDepthWorker   optional ONNX inference; updates a shared depth cache asynchronously
    VisionPipeline      per-frame: relative depth → hybrid metric depth → fog map → JPEGs

Relative depth convention: ``D_rel ∈ [0, 1]``, **0 = nearest, 1 = farthest** (the inverse of what
Depth-Anything outputs, which is converted on the way in).

Hybrid metric depth (the sonar is the physical anchor, vision supplies the relief):

    Z(u, v) = Z_sonar + (D_rel(u, v) − D_anchor) · S_relief

``D_anchor`` is the median ``D_rel`` inside the acoustic beam cone around the optical axis and
``S_relief`` is ``vision_and_fog.relief_depth_span_m``.

Depth modes (``vision_and_fog.depth_inference_mode``)
    disabled          flat plane: D_rel is constant, so Z = Z_sonar everywhere (no depth compute)
    lightweight_fast  CPU cue heuristic (frame difference against the calibrated empty background
                      when available, else edge energy + brightness + a ground-plane prior),
                      bilateral-smoothed at 160×120. A heuristic, not true depth.
    neural_onnx       Depth-Anything-V2-Small via onnxruntime in a worker thread

If a mode cannot run (e.g. the ONNX file is missing) the pipeline reports the reason in
``depth_error`` and runs ``lightweight_fast`` instead; ``depth_mode_active`` always says what is
really running.
"""

from __future__ import annotations

import functools
import logging
import math
import os
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

import devices
from config import BACKEND_DIR, SystemConfig, get_config, settings

try:
    import onnxruntime as ort  # type: ignore
except Exception:
    ort = None

logger = logging.getLogger("sonar.depth_ai")

MODEL_URL = (
    "https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model.onnx"
)
MODEL_INPUT = 518
BG_MASK_PATH = BACKEND_DIR / "calibration_bg_mask.npy"   # median D_rel of the empty scene (Round 3)
BG_GRAY_PATH = BACKEND_DIR / "calibration_bg_gray.npy"   # median 160x120 grey frame (Round 3)
FRAME_W, FRAME_H = 640, 480
SMALL_W, SMALL_H = 160, 120
_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def log(msg: str) -> None:
    print(f"[vision] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Frame sources
# ---------------------------------------------------------------------------

def mock_frame(t: float, rng: np.random.Generator) -> np.ndarray:
    """Procedural 640×480 BGR frame with a moving ellipse silhouette (AGENTS.md Rule 3)."""
    yy = np.linspace(12, 40, FRAME_H, dtype=np.float32)[:, None]
    frame = np.dstack([yy * 1.2, yy, yy * 0.8]).repeat(FRAME_W, axis=1)
    frame += rng.integers(0, 6, (FRAME_H, FRAME_W, 3))
    frame = np.clip(frame, 0, 255).astype(np.uint8)
    cx = int(FRAME_W * (0.5 + 0.2 * math.sin(t * 0.4)))
    cy = int(FRAME_H * (0.5 + 0.1 * math.cos(t * 0.3)))
    cv2.ellipse(frame, (cx, cy), (80, 140), 0, 0, 360, (150, 190, 235), -1)
    cv2.ellipse(frame, (cx, cy - 60), (35, 40), 0, 0, 360, (190, 215, 245), -1)
    return cv2.GaussianBlur(frame, (15, 15), 5)


class CameraGrabber:
    """Dedicated frame-grabber thread: camera I/O never blocks inference, WebSocket or UDP work.

    * ``use_real=False`` → procedural mock frames.
    * ``use_real=True``  → ``cv2.VideoCapture(index)``. If the device cannot be opened, stops
      delivering frames, or the configured index changes, a warning is logged once per episode,
      the grabber falls back to mock frames (``source == "mock-fallback"``) and keeps retrying
      the real camera every ``RETRY_S`` seconds. It never raises into the pipeline.
    """

    RETRY_S = 5.0
    MAX_READ_FAILS = 10

    def __init__(self, index_provider: Callable[[], int], use_real: bool) -> None:
        self._index_provider = index_provider
        self.use_real = use_real
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._seq = 0
        self._source = "mock" if not use_real else "mock-fallback"
        self._running = False
        self._thread: threading.Thread | None = None
        self.error: str | None = None
        self.active_index: int | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="camera-grabber")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)

    def snapshot(self) -> tuple[np.ndarray | None, int, str]:
        """``(newest frame, sequence number, source)``; the frame must be treated as read-only."""
        with self._lock:
            return self._frame, self._seq, self._source

    @property
    def source(self) -> str:
        return self.snapshot()[2]

    # -- internals ---------------------------------------------------------

    def _publish(self, frame: np.ndarray, source: str) -> None:
        with self._lock:
            self._frame, self._source = frame, source
            self._seq += 1

    @staticmethod
    def _open(index: int):
        """Open ``index`` and confirm it really delivers frames (some devices open but are dead)."""
        cap = cv2.VideoCapture(index)
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
        for _ in range(5):
            ok, frame = cap.read()
            if ok and frame is not None:
                return cap
            time.sleep(0.05)
        cap.release()
        return None

    def _run(self) -> None:
        rng = np.random.default_rng(7)
        cap, cap_index, cap_requested = None, None, None  # actual device vs the index the operator asked for
        fails, next_retry, warned = 0, 0.0, False
        while self._running:
            now = time.monotonic()
            if not self.use_real:
                self._publish(mock_frame(now, rng), "mock")
                time.sleep(1 / 30)
                continue

            index = self._index_provider()
            if cap is not None and index != cap_requested:  # operator picked another camera
                cap.release()
                cap, warned = None, False
                next_retry = 0.0

            if cap is None and now >= next_retry:
                requested = index
                cap = self._open(index)
                if cap is None and not warned:
                    # Try whatever devices.py can actually detect before giving up on real video.
                    alt = [c["index"] for c in devices.enumerate_cameras()[0]
                           if not c.get("mock") and c["index"] != index]
                    for candidate in alt:
                        cap = self._open(candidate)
                        if cap is not None:
                            log(f"WARNING: camera {index} unavailable; using detected camera {candidate}")
                            index = candidate
                            break
                if cap is not None:
                    cap_index, cap_requested, fails, warned = index, requested, 0, False
                    self.active_index, self.error = index, None
                    log(f"camera {index} opened")
                else:
                    next_retry = now + self.RETRY_S
                    self.active_index = None
                    self.error = f"camera {requested} could not be opened"
                    if not warned:
                        log(f"WARNING: {self.error}; using mock frames and retrying every {self.RETRY_S:.0f}s")
                        warned = True

            if cap is not None:
                ok, frame = cap.read()
                if ok and frame is not None:
                    fails = 0
                    if frame.shape[:2] != (FRAME_H, FRAME_W):
                        frame = cv2.resize(frame, (FRAME_W, FRAME_H))
                    self._publish(frame, "camera")
                else:
                    fails += 1
                    time.sleep(0.02)
                    if fails >= self.MAX_READ_FAILS:
                        log(f"WARNING: camera {cap_index} stopped delivering frames (disconnected?); "
                            "falling back to mock frames")
                        cap.release()
                        cap, fails, warned = None, 0, True
                        self.active_index = None
                        self.error = f"camera {cap_index} disconnected"
                        next_retry = time.monotonic() + self.RETRY_S
            else:
                self._publish(mock_frame(now, rng), "mock-fallback")
                time.sleep(1 / 30)
        if cap is not None:
            cap.release()


# ---------------------------------------------------------------------------
# Relative depth: disabled / lightweight heuristic / neural
# ---------------------------------------------------------------------------

def flat_relative_depth(shape: tuple[int, int]) -> np.ndarray:
    """``disabled`` mode: a constant map, so the hybrid equation yields Z = Z_sonar everywhere."""
    return np.full(shape, 0.5, dtype=np.float32)


def small_gray(frame_bgr: np.ndarray) -> np.ndarray:
    small = cv2.resize(frame_bgr, (SMALL_W, SMALL_H), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)


def foreground_mask(frame_bgr: np.ndarray, bg_gray: np.ndarray, threshold: float = 22.0) -> np.ndarray:
    """Boolean frame-sized mask of pixels that differ from the calibrated empty background."""
    diff = cv2.absdiff(small_gray(frame_bgr), bg_gray)
    diff = cv2.GaussianBlur(diff, (0, 0), 1.5)
    mask = (diff > threshold).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    h, w = frame_bgr.shape[:2]
    return cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)


def heuristic_relative_depth(frame_bgr: np.ndarray, bg_gray: np.ndarray | None = None) -> np.ndarray:
    """``lightweight_fast``: cue-based relative depth at 160×120, bilateral-smoothed, upsampled.

    Nearness cue ("saliency") in [0, 1]:
      * with a calibrated empty background: how much each pixel differs from it (a subject that
        entered the scene is nearer than the wall behind it);
      * otherwise: local edge energy (near surfaces carry more resolved detail) + brightness
        + a weak ground-plane prior (lower image rows are nearer).
    Returns D_rel with 0 = nearest.  Typically ~1 ms.
    """
    h, w = frame_bgr.shape[:2]
    gray = small_gray(frame_bgr)
    if bg_gray is not None and bg_gray.shape == gray.shape:
        cue = cv2.GaussianBlur(cv2.absdiff(gray, bg_gray).astype(np.float32), (0, 0), 2.0)
        sal = np.clip(cue / max(float(np.percentile(cue, 99)), 8.0), 0.0, 1.0)
    else:
        g = gray.astype(np.float32)
        gx, gy = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
        detail = cv2.GaussianBlur(cv2.magnitude(gx, gy), (0, 0), 3.0)
        lum = cv2.GaussianBlur(g, (0, 0), 3.0)
        detail = detail / max(float(np.percentile(detail, 99)), 1e-6)
        lum = (lum - float(lum.min())) / max(float(np.ptp(lum)), 1.0)
        ground = np.linspace(0.0, 1.0, SMALL_H, dtype=np.float32)[:, None]
        sal = np.clip(0.45 * detail + 0.35 * lum + 0.20 * ground, 0.0, 1.0)
    sal8 = cv2.bilateralFilter((sal * 255.0).astype(np.uint8), 5, 40, 5)  # edge-preserving smooth
    d_rel = 1.0 - sal8.astype(np.float32) / 255.0
    return cv2.resize(d_rel, (w, h), interpolation=cv2.INTER_LINEAR)


def download_model(dest: Path | None = None) -> Path:
    """Fetch the ONNX weights from the Hugging Face Hub (~99 MB)."""
    dest = dest or settings.depth_model_file
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    log(f"downloading depth model → {dest}")
    urllib.request.urlretrieve(MODEL_URL, tmp)
    tmp.replace(dest)
    return dest


def build_optimized_session(model_path: str | Path) -> "ort.InferenceSession":
    """Build an ONNX session with the best available accelerator and a reliable CPU fallback."""
    if ort is None:
        raise RuntimeError("onnxruntime is not installed")

    # pip-installed CUDA/cuDNN (the nvidia-* wheels behind onnxruntime-gpu[cuda,cudnn]) are not on
    # the dynamic loader's path, so the CUDA provider is listed yet fails to load
    # ("libcublasLt.so.13: cannot open shared object file"). preload_dlls() loads them first.
    preload = getattr(ort, "preload_dlls", None)
    if preload is not None:
        try:
            preload()
        except Exception as exc:  # no GPU libraries present: the CPU path below still works
            logger.info("onnxruntime.preload_dlls() skipped: %s", exc)

    available = ort.get_available_providers()
    logger.info("Available ONNX providers detected on system: %s", available)
    if "CPUExecutionProvider" not in available:
        raise RuntimeError("ONNX Runtime has no CPUExecutionProvider for fallback")

    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 2) // 2))

    provider_options = {
        "CUDAExecutionProvider": {
            "device_id": 0,
            "arena_extend_strategy": "kNextPowerOfTwo",
            "gpu_mem_limit": 2 * 1024 * 1024 * 1024,
            "cudnn_conv_algo_search": "EXHAUSTIVE",
            "do_copy_in_default_stream": True,
        },
        "VulkanExecutionProvider": {"device_id": 0},
        "ROCMExecutionProvider": {"device_id": 0},
        "DmlExecutionProvider": {"device_id": 0},
    }
    preference = (
        "CUDAExecutionProvider",
        "VulkanExecutionProvider",
        "ROCMExecutionProvider",
        "DmlExecutionProvider",
    )
    candidates = [provider for provider in preference if provider in available]
    logger.info(
        "Initializing ONNX session with negotiated providers: %s",
        candidates + ["CPUExecutionProvider"],
    )

    for provider in candidates:
        try:
            session = ort.InferenceSession(
                str(model_path),
                sess_options=options,
                providers=[provider, "CPUExecutionProvider"],
                provider_options=[provider_options[provider], {}],
            )
        except Exception as exc:
            logger.warning(
                "Could not initialize ONNX provider %s; trying the next provider: %s",
                provider,
                exc,
            )
            continue

        active = session.get_providers()
        if provider in active:
            logger.info("Active ONNX session initialized with providers: %s", active)
            return session
        logger.warning("ONNX provider %s was not activated; trying the next provider", provider)

    try:
        session = ort.InferenceSession(
            str(model_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
    except Exception as exc:
        raise RuntimeError(f"Could not initialize an ONNX session with any available provider: {exc}") from exc

    logger.info("Active ONNX session initialized with providers: %s", session.get_providers())
    return session


class NeuralDepthWorker:
    """Depth-Anything-V2-Small in its own thread, feeding a shared depth cache.

    ``submit`` never blocks; the worker always infers on the newest submitted frame and the
    pipeline reads whatever result is currently cached (so depth may lag the video by one
    inference). ``state`` is ``idle | loading | ready | error`` and ``error`` explains failures.
    """

    def __init__(self) -> None:
        self.state = "idle"
        self.error: str | None = None
        self.last_ms = 0.0
        self.active_provider = "none"
        self._fps = 0.0
        self._last_completed_at: float | None = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._pending: np.ndarray | None = None
        self._result: np.ndarray | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def ensure_started(self) -> None:
        if self._thread is None:
            self._running = True
            self.state = "loading"
            self._thread = threading.Thread(target=self._run, daemon=True, name="neural-depth")
            self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    def submit(self, frame: np.ndarray) -> None:
        with self._lock:
            self._pending = frame
        self._wake.set()

    def latest(self) -> np.ndarray | None:
        with self._lock:
            return self._result

    @property
    def fps(self) -> float:
        with self._lock:
            if (
                self._last_completed_at is None
                or time.monotonic() - self._last_completed_at > 2.0
            ):
                return 0.0
            return self._fps

    def _load(self):
        path = settings.depth_model_file
        if not path.exists():
            if not settings.depth_auto_download:
                raise FileNotFoundError(
                    f"depth model not found at {path} (place the file there, or start with "
                    "DEPTH_AUTO_DOWNLOAD=true)"
                )
            download_model(path)
        return build_optimized_session(path)

    @staticmethod
    def _infer(session, input_name: str, frame_bgr: np.ndarray) -> np.ndarray:
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(cv2.resize(frame_bgr, (MODEL_INPUT, MODEL_INPUT)), cv2.COLOR_BGR2RGB)
        x = (rgb.astype(np.float32) / 255.0 - _IMAGENET_MEAN) / _IMAGENET_STD
        out = np.asarray(
            session.run(None, {input_name: np.ascontiguousarray(x.transpose(2, 0, 1)[None])})[0],
            dtype=np.float32,
        )
        inv = cv2.resize(out.reshape(out.shape[-2], out.shape[-1]), (w, h), interpolation=cv2.INTER_LINEAR)
        inv -= inv.min()
        inv /= max(float(inv.max()), 1e-6)
        return 1.0 - inv  # model output is inverse depth (near = 1); D_rel has near = 0

    def _run(self) -> None:
        try:
            session = self._load()
            name = session.get_inputs()[0].name
            active_providers = session.get_providers()
            self.active_provider = active_providers[0] if active_providers else "none"
        except Exception as exc:
            self.error, self.state = str(exc), "error"
            log(f"WARNING: neural depth unavailable: {exc}")
            return
        self.state = "ready"
        log("neural depth model loaded")
        while self._running:
            self._wake.wait(timeout=0.5)
            self._wake.clear()
            with self._lock:
                frame, self._pending = self._pending, None
            if frame is None:
                continue
            t0 = time.perf_counter()
            try:
                d_rel = self._infer(session, name, frame)
            except Exception as exc:
                self.error, self.state = f"inference failed: {exc}", "error"
                log(f"WARNING: {self.error}")
                return
            self.last_ms = (time.perf_counter() - t0) * 1000.0
            completed_at = time.monotonic()
            with self._lock:
                if self._last_completed_at is not None:
                    measured_fps = 1.0 / max(completed_at - self._last_completed_at, 1e-6)
                    self._fps = measured_fps if self._fps == 0.0 else 0.2 * measured_fps + 0.8 * self._fps
                self._last_completed_at = completed_at
                self._result = d_rel


# ---------------------------------------------------------------------------
# Hybrid sonar + vision metric depth
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=8)
def beam_mask(h: int, w: int, fov_deg: float, beam_deg: float) -> np.ndarray:
    """Pixels inside the acoustic beam cone (half-angle ``beam_deg``) around the optical axis."""
    fx = (w / 2.0) / math.tan(math.radians(fov_deg) / 2.0)
    radius = fx * math.tan(math.radians(min(beam_deg, 89.0)))
    yy, xx = np.ogrid[0:h, 0:w]
    return (xx - w / 2.0) ** 2 + (yy - h / 2.0) ** 2 <= radius**2


def anchor_depth(d_rel: np.ndarray, mask: np.ndarray) -> float:
    """D_anchor: median relative depth of the subject inside the acoustic beam cone."""
    return float(np.median(d_rel[mask])) if mask.any() else float(np.median(d_rel))


def hybrid_metric_depth(d_rel: np.ndarray, z_sonar: float, relief_m: float, mask: np.ndarray) -> np.ndarray:
    """Z(u,v) = Z_sonar + (D_rel(u,v) − D_anchor) · S_relief   (metres)."""
    return z_sonar + (d_rel - anchor_depth(d_rel, mask)) * relief_m


# ---------------------------------------------------------------------------
# Fog Map
# ---------------------------------------------------------------------------

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
# Pipeline thread
# ---------------------------------------------------------------------------

@dataclass
class VisionFrame:
    """One processed frame, shared with the stitcher."""
    bgr: np.ndarray
    d_rel: np.ndarray     # relative depth, 0 = near
    z_metric: np.ndarray  # metres, NaN where clipped / background
    i_fog: np.ndarray     # uint8, 0 where clipped
    z_sonar: float
    peak_amplitude: float
    source: str = "mock"  # camera | mock | mock-fallback


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
        self.grabber = CameraGrabber(
            lambda: get_config().hardware.camera_device_index,
            use_real=(not settings.mock_mode) or settings.real_camera,
        )
        self.neural = NeuralDepthWorker()
        self.bg_depth: np.ndarray | None = None
        self.bg_gray: np.ndarray | None = None
        self.depth_mode_active = "lightweight_fast"
        self.depth_error: str | None = None
        self.depth_ms = 0.0
        self.error: str | None = None
        self._load_background()

    def _load_background(self) -> None:
        for attr, path in (("bg_depth", BG_MASK_PATH), ("bg_gray", BG_GRAY_PATH)):
            if path.exists():
                try:
                    setattr(self, attr, np.load(path))
                except Exception:
                    setattr(self, attr, None)

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.grabber.start()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="vision-pipeline")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
        self.neural.stop()
        self.grabber.stop()

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
    def camera_source(self) -> str:
        return self.grabber.source

    @property
    def camera_mock(self) -> bool:
        return self.grabber.source != "camera"

    @property
    def active_camera(self) -> int | None:
        return self.grabber.active_index

    @property
    def active_depth_provider(self) -> str:
        return self.neural.active_provider

    @property
    def depth_fps(self) -> float:
        return self.neural.fps

    # -- calibration helpers ----------------------------------------------

    def _collect(self, n: int, timeout: float) -> list[VisionFrame]:
        frames: list[VisionFrame] = []
        last, deadline = None, time.monotonic() + timeout
        while len(frames) < n and time.monotonic() < deadline:
            f = self.latest
            if f is not None and f is not last:
                last = f
                frames.append(f)
            time.sleep(0.01)
        return frames

    def capture_background(self, n_frames: int = 30, timeout: float = 30.0) -> np.ndarray:
        """Round 3: median of ``n_frames`` empty-scene frames → background depth and grey images."""
        frames = self._collect(n_frames, timeout)
        if not frames:
            raise RuntimeError("No vision frames available for background capture")
        self.bg_depth = np.median(np.stack([f.d_rel for f in frames]), axis=0).astype(np.float32)
        self.bg_gray = np.median(np.stack([small_gray(f.bgr) for f in frames]), axis=0).astype(np.uint8)
        np.save(BG_MASK_PATH, self.bg_depth)
        np.save(BG_GRAY_PATH, self.bg_gray)
        return self.bg_depth

    def sample_anchor_d_rel(self, n: int = 5) -> float:
        """Round 4: median D_anchor (relative depth inside the beam cone) over ``n`` frames."""
        cfg = get_config()
        vals = []
        for f in self._collect(n, 10.0):
            h, w = f.d_rel.shape
            mask = beam_mask(h, w, cfg.hardware.camera_fov_deg, cfg.vision_and_fog.acoustic_beam_deg)
            vals.append(anchor_depth(f.d_rel, mask))
        return float(np.median(vals)) if vals else 0.0

    # -- main loop ---------------------------------------------------------

    def _loop(self) -> None:
        last_seq = -1
        try:
            while self._running:
                frame, seq, source = self.grabber.snapshot()
                if frame is None or seq == last_seq:
                    time.sleep(0.002)
                    continue
                last_seq = seq
                self._process(frame, source, get_config())
        except Exception as exc:
            self.error = str(exc)
            log(f"pipeline crashed: {exc}")
            raise

    def _relative_depth(self, frame: np.ndarray, mode: str) -> np.ndarray:
        """Run the requested mode, falling back (and saying so) when it cannot run."""
        t0 = time.perf_counter()
        self.depth_error = None
        if mode == "disabled":
            d, self.depth_mode_active = flat_relative_depth(frame.shape[:2]), "disabled"
        elif mode == "neural_onnx":
            self.neural.ensure_started()
            self.neural.submit(frame)
            cached = self.neural.latest()
            if self.neural.state == "error":
                self.depth_error = f"{self.neural.error}; using lightweight_fast"
                d = heuristic_relative_depth(frame, self.bg_gray)
                self.depth_mode_active = "lightweight_fast"
            elif cached is None:
                self.depth_error = "neural model loading; using lightweight_fast until it is ready"
                d = heuristic_relative_depth(frame, self.bg_gray)
                self.depth_mode_active = "lightweight_fast"
            else:
                d, self.depth_mode_active = cached, "neural_onnx"
        else:
            d, self.depth_mode_active = heuristic_relative_depth(frame, self.bg_gray), "lightweight_fast"
        self.depth_ms = 0.9 * self.depth_ms + 0.1 * (time.perf_counter() - t0) * 1000.0
        return d

    def _process(self, frame: np.ndarray, source: str, cfg: SystemConfig) -> None:
        reading = self._sonar_provider()
        vf = cfg.vision_and_fog
        z_sonar = reading.sonar_depth_m
        if z_sonar <= 0.0:  # sonar not locked yet: anchor mid-range so the map is still sensible
            z_sonar = 0.5 * (vf.min_scan_distance_m + vf.max_scan_distance_m)

        d_rel = self._relative_depth(frame, vf.depth_inference_mode)
        h, w = d_rel.shape
        cone = beam_mask(h, w, cfg.hardware.camera_fov_deg, vf.acoustic_beam_deg)
        z = hybrid_metric_depth(d_rel, z_sonar, vf.relief_depth_span_m, cone)

        if cfg.calibration.background_fog_captured and self.bg_gray is not None:
            z = np.where(foreground_mask(frame, self.bg_gray), z, np.inf)  # empty-room pixels out

        i_fog = fog_intensity(z, vf.min_scan_distance_m, vf.max_scan_distance_m,
                              vf.fog_falloff_curve, vf.fog_density)
        z_clipped = np.where(i_fog > 0, z, np.nan).astype(np.float32)

        fog_img = colorize_fog(i_fog, cfg)
        rgb_img = frame.copy()
        contours, _ = cv2.findContours((i_fog > 0).astype(np.uint8), cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(rgb_img, [c for c in contours if cv2.contourArea(c) > 500], -1, (128, 255, 0), 2)
        label = f"Sonar Z: {reading.sonar_depth_m:.2f} m  [{reading.status}]  {source}"
        cv2.putText(rgb_img, label, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 255, 0), 2)
        cv2.putText(fog_img, f"{vf.min_scan_distance_m:.2f}-{vf.max_scan_distance_m:.2f} m "
                             f"{vf.fog_falloff_curve} | {self.depth_mode_active}",
                    (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

        q = [cv2.IMWRITE_JPEG_QUALITY, 80]
        rgb_jpg = cv2.imencode(".jpg", rgb_img, q)[1].tobytes()
        fog_jpg = cv2.imencode(".jpg", fog_img, q)[1].tobytes()

        vfr = VisionFrame(frame, d_rel, z_clipped, i_fog, float(z_sonar), reading.peak_amplitude, source)
        now = time.monotonic()
        self._times = (self._times + [now])[-30:]
        fps = (len(self._times) - 1) / (self._times[-1] - self._times[0]) if len(self._times) > 1 else 0.0
        with self._lock:
            self._rgb_jpg, self._fog_jpg, self._latest, self._fps = rgb_jpg, fog_jpg, vfr, fps

        if self._on_frame is not None:
            try:
                self._on_frame(vfr, cfg)
            except Exception as exc:
                log(f"on_frame error: {exc}")
