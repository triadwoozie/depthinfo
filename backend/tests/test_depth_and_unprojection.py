"""Hybrid depth equation, depth modes, camera grabber fallback and 3D unprojection."""
import math
import time

import numpy as np
import pytest
from pydantic import ValidationError

import depth_ai
from config import Pose, SystemConfig, settings
from stitcher import Stitcher, camera_rotation

H, W = 480, 640


def make_frame(z_metric, bgr=None):
    bgr = np.full((H, W, 3), 120, np.uint8) if bgr is None else bgr
    return depth_ai.VisionFrame(
        bgr=bgr, d_rel=np.zeros((H, W), np.float32), z_metric=z_metric,
        i_fog=np.full((H, W), 255, np.uint8), z_sonar=1.0, peak_amplitude=0.5,
    )


# ---------------------------------------------------------------------------
# Pinhole unprojection and camera pose
# ---------------------------------------------------------------------------

def test_unprojection_matches_pinhole_equations():
    cfg = SystemConfig()
    fov_x = math.radians(cfg.hardware.camera_fov_deg)
    fov_y = 2 * math.atan(math.tan(fov_x / 2) * H / W)
    fx = (W / 2) / math.tan(fov_x / 2)
    fy = (H / 2) / math.tan(fov_y / 2)
    assert fy == pytest.approx(fx)
    z = np.full((H, W), np.nan, np.float32)
    u, v, depth = 400, 200, 2.0  # multiples of the stitcher's pixel stride
    z[v, u] = depth
    pts, _ = Stitcher()._unproject(make_frame(z), cfg)
    assert pts.shape == (1, 3)
    x, y, zz = pts[0]
    assert zz == pytest.approx(depth)
    assert x == pytest.approx((u - W / 2) * depth / fx, abs=1e-5)
    assert y == pytest.approx(-(v - H / 2) * depth / fy, abs=1e-5)  # +y is up, image v grows down


def test_every_point_keeps_its_own_depth():
    cfg = SystemConfig()
    z = np.full((H, W), np.nan, np.float32)
    z[::4, ::4] = np.linspace(1.0, 2.0, (H // 4) * (W // 4), dtype=np.float32).reshape(H // 4, W // 4)
    pts, _ = Stitcher()._unproject(make_frame(z), cfg)
    assert len(pts) == (H // 4) * (W // 4)
    assert pts[:, 2].min() == pytest.approx(1.0, abs=1e-4)
    assert pts[:, 2].max() == pytest.approx(2.0, abs=1e-4)
    assert np.unique(np.round(pts[:, 2], 4)).size > 1000


def test_camera_pose_rotation_and_translation():
    fwd = np.array([0.0, 0.0, 1.0])
    assert camera_rotation(Pose(yaw_deg=90)) @ fwd == pytest.approx([1, 0, 0], abs=1e-9)    # turn right
    assert camera_rotation(Pose(pitch_deg=90)) @ fwd == pytest.approx([0, 1, 0], abs=1e-9)  # look up
    cfg = SystemConfig()
    cfg.hardware.camera_pose = Pose(x_cm=100.0, y_cm=50.0, z_cm=-20.0)
    z = np.full((H, W), np.nan, np.float32)
    z[240, 320] = 2.0  # optical axis
    pts, _ = Stitcher()._unproject(make_frame(z), cfg)
    assert pts[0] == pytest.approx([1.0, 0.5, 2.0 - 0.2], abs=1e-5)


# ---------------------------------------------------------------------------
# Hybrid metric depth
# ---------------------------------------------------------------------------

def test_hybrid_equation_anchors_on_sonar_and_scales_relief():
    d = np.full((H, W), 0.30, np.float32)
    d[:, :100] = 0.80  # background far to the left, outside the beam cone (cone spans x 137..503)
    cone = depth_ai.beam_mask(H, W, 65.0, 20.0)
    assert not cone[:, :100].any()
    assert depth_ai.anchor_depth(d, cone) == pytest.approx(0.30)
    z = depth_ai.hybrid_metric_depth(d, z_sonar=1.7, relief_m=0.5, mask=cone)
    assert z[240, 320] == pytest.approx(1.7)                   # anchor pixel sits at Z_sonar
    assert z[0, 0] == pytest.approx(1.7 + (0.80 - 0.30) * 0.5)  # farther pixel is behind it


def test_beam_cone_radius_follows_fov_and_angle():
    cone = depth_ai.beam_mask(H, W, 65.0, 20.0)
    fx = (W / 2) / math.tan(math.radians(65.0) / 2)
    radius = fx * math.tan(math.radians(20.0))
    assert cone[240, 320 + int(radius) - 2] and not cone[240, 320 + int(radius) + 3]


def test_disabled_mode_is_a_flat_plane_at_sonar_depth():
    d = depth_ai.flat_relative_depth((H, W))
    z = depth_ai.hybrid_metric_depth(d, 1.234, 0.45, depth_ai.beam_mask(H, W, 65.0, 20.0))
    assert np.allclose(z, 1.234)


def scene_with_disk():
    img = np.full((H, W, 3), 25, np.uint8)
    rng = np.random.default_rng(3)
    img = np.clip(img + rng.integers(0, 12, img.shape), 0, 255).astype(np.uint8)
    yy, xx = np.ogrid[:H, :W]
    disk = (xx - 320) ** 2 + (yy - 240) ** 2 < 90**2
    img[disk] = (200, 210, 230)
    return img, disk


def test_lightweight_mode_gives_the_subject_real_front_to_back_relief():
    img, disk = scene_with_disk()
    d = depth_ai.heuristic_relative_depth(img)
    assert d.shape == (H, W) and d.dtype == np.float32 and 0.0 <= d.min() and d.max() <= 1.0
    z = depth_ai.hybrid_metric_depth(d, 1.5, 0.5, depth_ai.beam_mask(H, W, 65.0, 20.0))
    assert np.median(z[disk]) < np.median(z[~disk]) - 0.05  # subject is in front of its surroundings
    assert z.std() > 0.03                                    # not a flat card


def test_lightweight_mode_with_calibrated_background_isolates_the_subject():
    img, disk = scene_with_disk()
    empty = np.full((H, W, 3), 25, np.uint8)
    bg = depth_ai.small_gray(empty)
    d = depth_ai.heuristic_relative_depth(img, bg)
    assert np.median(d[disk]) < 0.4 and np.median(d[~disk]) > 0.9
    mask = depth_ai.foreground_mask(img, bg)
    assert mask[disk].mean() > 0.9 and mask[~disk].mean() < 0.05


def test_lightweight_mode_is_fast():
    img, _ = scene_with_disk()
    depth_ai.heuristic_relative_depth(img)  # warm up
    times = []
    for _ in range(60):
        t0 = time.perf_counter()
        depth_ai.heuristic_relative_depth(img)
        times.append((time.perf_counter() - t0) * 1000)
    assert np.median(times) < 5.0, f"median {np.median(times):.2f} ms"


# ---------------------------------------------------------------------------
# Config and mode fallbacks
# ---------------------------------------------------------------------------

def test_depth_mode_is_validated():
    cfg = SystemConfig()
    assert cfg.vision_and_fog.depth_inference_mode == "lightweight_fast"
    for mode in ("disabled", "lightweight_fast", "neural_onnx"):
        SystemConfig.model_validate({"vision_and_fog": {"depth_inference_mode": mode}})
    with pytest.raises(ValidationError):
        SystemConfig.model_validate({"vision_and_fog": {"depth_inference_mode": "magic"}})


def test_neural_mode_reports_a_missing_model_and_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "depth_model_path", str(tmp_path / "missing.onnx"))
    monkeypatch.setattr(settings, "depth_auto_download", False)
    pipe = depth_ai.VisionPipeline(lambda: None)
    img, _ = scene_with_disk()
    deadline = time.time() + 5
    while pipe.neural.state != "error" and time.time() < deadline:
        pipe._relative_depth(img, "neural_onnx")
        time.sleep(0.05)
    d = pipe._relative_depth(img, "neural_onnx")
    pipe.neural.stop()
    assert pipe.neural.state == "error"
    assert pipe.depth_mode_active == "lightweight_fast"
    assert "DEPTH_AUTO_DOWNLOAD" in pipe.depth_error
    assert d.shape == (H, W)


MODEL = settings.depth_model_file


@pytest.mark.skipif(not MODEL.exists(), reason="ONNX model not downloaded")
def test_neural_mode_runs_the_real_model():
    img, disk = scene_with_disk()
    worker = depth_ai.NeuralDepthWorker()
    worker.ensure_started()
    deadline = time.time() + 60
    while worker.latest() is None and time.time() < deadline:
        worker.submit(img)
        time.sleep(0.1)
    d = worker.latest()
    worker.stop()
    assert worker.state == "ready" and d is not None
    assert d.shape == (H, W) and 0.0 <= d.min() and d.max() <= 1.0


# ---------------------------------------------------------------------------
# Camera grabber
# ---------------------------------------------------------------------------

def test_missing_camera_falls_back_to_mock_frames_without_crashing():
    grabber = depth_ai.CameraGrabber(lambda: 99, use_real=True)  # no such device
    grabber.start()
    try:
        deadline = time.time() + 10
        frame = None
        while frame is None and time.time() < deadline:
            frame, _, source = grabber.snapshot()
            time.sleep(0.05)
        assert frame is not None and frame.shape == (H, W, 3)
        assert source == "mock-fallback"
        assert "could not be opened" in grabber.error
    finally:
        grabber.stop()


def test_mock_mode_grabber_never_touches_hardware():
    grabber = depth_ai.CameraGrabber(lambda: 0, use_real=False)
    grabber.start()
    time.sleep(0.3)
    frame, seq, source = grabber.snapshot()
    grabber.stop()
    assert source == "mock" and frame is not None and seq > 1


# ---------------------------------------------------------------------------
# Grabber against a simulated flaky camera (a webcam cannot be unplugged on demand)
# ---------------------------------------------------------------------------

class FlakyCapture:
    """Stand-in for cv2.VideoCapture that follows a script of up/down periods."""
    instances = 0
    plan = {"good_reads": 8, "recover": False}

    def __init__(self, index):
        FlakyCapture.instances += 1
        self.reads = 0
        self.open = index == 0

    def isOpened(self):
        return self.open

    def set(self, *_):
        return True

    def read(self):
        self.reads += 1
        if self.reads <= FlakyCapture.plan["good_reads"] or FlakyCapture.plan["recover"]:
            return True, np.full((H, W, 3), 90, np.uint8)
        return False, None

    def release(self):
        self.open = False


def wait_for(predicate, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_camera_disconnect_falls_back_then_recovers(monkeypatch):
    FlakyCapture.instances, FlakyCapture.plan = 0, {"good_reads": 8, "recover": False}
    monkeypatch.setattr(depth_ai.cv2, "VideoCapture", FlakyCapture)
    monkeypatch.setattr(depth_ai.CameraGrabber, "RETRY_S", 0.5)
    grabber = depth_ai.CameraGrabber(lambda: 0, use_real=True)
    grabber.start()
    try:
        assert wait_for(lambda: grabber.source == "camera")
        assert wait_for(lambda: grabber.source == "mock-fallback")  # reads start failing
        assert "disconnected" in grabber.error
        assert grabber.snapshot()[0].shape == (H, W, 3)             # frames keep flowing
        FlakyCapture.plan["recover"] = True                          # camera comes back
        assert wait_for(lambda: grabber.source == "camera")
        assert grabber.error is None
    finally:
        grabber.stop()


def test_substituted_camera_does_not_reopen_in_a_loop(monkeypatch):
    """Asking for an absent index adopts a detected camera once, instead of flapping."""
    FlakyCapture.instances, FlakyCapture.plan = 0, {"good_reads": 10**9, "recover": True}
    monkeypatch.setattr(depth_ai.cv2, "VideoCapture", FlakyCapture)
    monkeypatch.setattr(depth_ai.devices, "enumerate_cameras",
                        lambda *a, **k: ([{"index": 0, "name": "Camera 0", "mock": False}], False))
    grabber = depth_ai.CameraGrabber(lambda: 7, use_real=True)  # index 7 does not exist; 0 does
    grabber.start()
    try:
        assert wait_for(lambda: grabber.source == "camera")
        opened_after_settling = FlakyCapture.instances
        time.sleep(1.5)
        assert grabber.source == "camera" and grabber.active_index == 0
        assert FlakyCapture.instances == opened_after_settling  # no reopen churn
    finally:
        grabber.stop()
