"""Background culling, percentile normalisation, CLAHE contrast and the new defaults."""
import numpy as np
import pytest

import depth_ai
from config import SystemConfig
from stitcher import Stitcher, subject_mask

H, W = 480, 640


def frame_with(z, fog=255, z_sonar=1.5):
    return depth_ai.VisionFrame(
        bgr=np.full((H, W, 3), 128, np.uint8), d_rel=np.zeros((H, W), np.float32), z_metric=z,
        i_fog=np.full((H, W), fog, np.uint8), z_sonar=z_sonar, peak_amplitude=0.5,
    )


# --- subject isolation -------------------------------------------------------

def test_subject_mask_rules():
    z = np.array([1.40, 1.55, 1.74, 1.95, 2.60, np.nan])      # sonar anchor 1.5, max scan 2.5
    fog = np.array([0.9, 0.9, 0.9, 0.9, 0.9, 0.9])
    keep = subject_mask(z, fog, z_sonar=1.5, max_scan_m=2.5, fog_threshold=0.12, thickness_m=0.30)
    assert keep.tolist() == [True, True, True, False, False, False]  # wall (+0.45 m), far and NaN dropped
    dim = subject_mask(np.array([1.5, 1.5]), np.array([0.12, 0.13]), 1.5, 2.5, 0.12, 0.30)
    assert dim.tolist() == [False, True]                              # fog must be *above* 0.12


def test_wall_is_stripped_and_subject_kept_in_the_cloud():
    cfg = SystemConfig()
    z = np.full((H, W), np.nan, np.float32)
    z[:, :] = 1.95                                   # wall about 0.45 m behind the subject
    yy, xx = np.ogrid[:H, :W]
    face = (xx - 320) ** 2 + (yy - 240) ** 2 < 90**2
    z[face] = 1.5 + np.linspace(-0.2, 0.2, int(face.sum()), dtype=np.float32)  # nose to back of head
    pts, _ = Stitcher()._unproject(frame_with(z), cfg)
    assert 0 < len(pts) < (H // 4) * (W // 4) * 0.2   # only the subject survives (~12% of the frame)
    assert pts[:, 2].max() <= 1.5 + 0.30 + 1e-4
    assert np.ptp(pts[:, 2]) > 0.3                    # and it keeps its front-to-back relief


def test_low_fog_and_beyond_max_scan_points_are_dropped():
    cfg = SystemConfig()
    cfg.vision_and_fog.max_scan_distance_m = 2.0
    z = np.full((H, W), 1.6, np.float32)
    z[:, 400:] = 2.2                                              # beyond max scan
    frame = frame_with(z, fog=255)
    frame.i_fog[:100] = 20                                        # 20/255 = 0.08 < 0.12
    pts, _ = Stitcher()._unproject(frame, cfg)
    assert pts[:, 2].max() < 2.0
    assert (pts[:, 1] > 0).sum() < len(pts)                       # top rows (y > 0) removed by fog


# --- depth dynamic range -----------------------------------------------------

def test_percentile_normalisation_resists_outliers_where_min_max_collapses():
    rng = np.random.default_rng(1)
    d = rng.uniform(0.40, 0.60, (200, 200)).astype(np.float32)
    d[0, 0] = 50.0                                                # one hot pixel
    minmax = (d - d.min()) / (d.max() - d.min())
    robust = depth_ai.percentile_normalize(d)
    assert np.ptp(minmax[1:, 1:]) < 0.01          # min-max squeezes the whole subject into 0.4%
    assert robust.min() == pytest.approx(0.0, abs=1e-3) and robust.max() == pytest.approx(1.0, abs=1e-3)
    assert robust[1:, 1:].std() > 0.25            # percentile keeps the useful range


def test_clahe_pulls_out_low_contrast_facial_topology():
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    base = 0.2 + 0.6 * xx / W                                     # broad head-scale slope
    nose = 0.012 * np.exp(-(((xx - 320) / 25) ** 2 + ((yy - 240) / 60) ** 2))
    disparity = np.clip(base + nose, 0, 1).astype(np.float32)
    out = depth_ai.enhance_local_contrast(disparity)
    region = (slice(180, 300), slice(270, 370))
    assert out.dtype == np.float32 and 0.0 <= out.min() and out.max() <= 1.0
    # local relief across the nose bridge is amplified, not erased
    before = np.ptp(disparity[region][:, 50] - base[region][:, 50])
    after = np.ptp(out[region][:, 50]) - np.ptp(out[region][:, 0])
    assert after > before


def test_defaults_match_the_facial_depth_spec():
    v = SystemConfig().vision_and_fog
    assert v.fog_falloff_curve == "linear"
    assert 0.35 <= v.relief_depth_span_m <= 0.50
    assert v.fog_discard_threshold == pytest.approx(0.12)
    assert v.depth_contrast_enhance is True
    assert v.anchor_percentile == pytest.approx(5.0)


def test_anchor_percentile_pins_a_minority_subject_instead_of_the_background():
    """A subject filling a minority of the beam cone must anchor at Z_sonar, with the wall behind it."""
    d = np.full((H, W), 1.0, np.float32)                  # background: farthest (full range after percentile normalisation)
    yy, xx = np.ogrid[:H, :W]
    subject = (xx - 320) ** 2 + (yy - 240) ** 2 < 60**2   # small disk, well under half the 183 px cone
    d[subject] = 0.2                                      # subject: near
    cone = depth_ai.beam_mask(H, W, 65.0, 20.0)
    assert subject[cone].mean() < 0.5
    median = depth_ai.hybrid_metric_depth(d, 1.5, 0.45, cone, 50.0)
    pinned = depth_ai.hybrid_metric_depth(d, 1.5, 0.45, cone, 5.0)
    assert median[subject].mean() < 1.5 - 0.2     # median anchoring drags the subject in front of the sonar distance
    quarter = depth_ai.hybrid_metric_depth(d, 1.5, 0.45, cone, 25.0)
    assert quarter[subject].mean() < 1.5 - 0.2    # ...and a 25th percentile still fails for a subject this small
    assert pinned[subject].mean() == pytest.approx(1.5, abs=1e-5)           # the 5th percentile keeps it at Z_sonar
    assert pinned[~subject].mean() == pytest.approx(1.5 + 0.8 * 0.45, abs=1e-4)  # and puts the wall 0.36 m behind it
    keep = subject_mask(pinned, np.ones_like(pinned), 1.5, 2.5, 0.12, 0.30)
    assert keep[subject].all() and not keep[~subject].any()
