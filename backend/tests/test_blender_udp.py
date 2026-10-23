"""Depth-frame stream: codec agreement with the extension, loss tolerance, real UDP round trip."""
import importlib.util
import random
import socket
import time
from pathlib import Path

import numpy as np
import pytest

import blender_udp
import depth_ai
from config import SystemConfig, settings

PROTOCOL = Path(__file__).resolve().parents[2] / "blender" / "sonar_imaging" / "protocol.py"
spec = importlib.util.spec_from_file_location("sonar_protocol", PROTOCOL)
protocol = importlib.util.module_from_spec(spec)
spec.loader.exec_module(protocol)

H, W = depth_ai.BLENDER_H, depth_ai.BLENDER_W


def sample_frame(frame_id=1, seed=0):
    rng = np.random.default_rng(seed)
    return depth_ai.BlenderFrame(
        frame_id=frame_id,
        depth=rng.integers(0, 256, (H, W), dtype=np.uint8),
        rgb=rng.integers(0, 256, (H // 2, W // 2, 3), dtype=np.uint8),
        z_sonar=1.37, relief_m=0.75, fov_deg=65.0, plane_m=1.67,
    )


def test_sender_and_receiver_agree_on_the_format():
    assert blender_udp.HEADER.format == protocol.HEADER.format and blender_udp.MAGIC == protocol.MAGIC
    assert blender_udp.CHUNK_PAYLOAD == protocol.CHUNK_PAYLOAD
    assert protocol.HEADER.size == 32


def test_one_frame_needs_several_datagrams_that_each_fit_in_udp():
    datagrams = blender_udp.pack_frame(sample_frame())
    assert len(datagrams) == 5                       # 76,800 depth + 57,600 half-res RGB = 134,400 bytes
    assert max(len(d) for d in datagrams) <= protocol.MAX_DATAGRAM
    assert sum(len(d) - protocol.HEADER.size for d in datagrams) == 134_400
    assert 134_400 > protocol.MAX_DATAGRAM           # a single datagram could never carry it


def test_round_trip_is_bit_exact():
    f = sample_frame(frame_id=41)
    asm, out = protocol.FrameAssembler(), None
    for d in blender_udp.pack_frame(f):
        out = asm.feed(d) or out
    assert out is not None and out.frame_id == 41 and (out.width, out.height) == (W, H)
    assert np.array_equal(out.depth, f.depth) and np.array_equal(out.rgb, f.rgb)
    assert (out.z_sonar, out.relief, out.fov_deg, out.plane_m) == pytest.approx((1.37, 0.75, 65.0, 1.67), rel=1e-6)


def test_reordered_and_duplicated_chunks_still_assemble():
    f = sample_frame()
    datagrams = blender_udp.pack_frame(f)
    random.Random(3).shuffle(datagrams)
    asm, out = protocol.FrameAssembler(), []
    for d in datagrams + datagrams[:3]:              # shuffled, then three duplicates
        got = asm.feed(d)
        if got:
            out.append(got)
    assert len(out) == 1 and np.array_equal(out[0].depth, f.depth)


def test_lost_chunk_drops_that_frame_only_and_the_next_one_arrives():
    asm, delivered = protocol.FrameAssembler(), []
    a, b = blender_udp.pack_frame(sample_frame(1, 1)), blender_udp.pack_frame(sample_frame(2, 2))
    for d in a[:4] + a[5:]:                          # chunk 4 of frame 1 lost
        delivered.append(asm.feed(d))
    assert not any(delivered)
    for d in b:
        got = asm.feed(d)
    assert got is not None and got.frame_id == 2 and asm.dropped_frames == 1


def test_stale_frames_and_garbage_are_ignored():
    asm = protocol.FrameAssembler()
    new = blender_udp.pack_frame(sample_frame(10, 5))
    for d in new:
        asm.feed(d)
    assert asm.feed(blender_udp.pack_frame(sample_frame(9, 6))[0]) is None   # older frame
    assert asm.feed(b"{\"state\": \"legacy json\"}") is None
    assert asm.feed(b"") is None and asm.feed(b"SDF1") is None


def test_frame_id_wraparound_is_treated_as_newer():
    asm = protocol.FrameAssembler()
    for d in blender_udp.pack_frame(sample_frame(0xFFFFFFFF, 1)):
        asm.feed(d)
    out = None
    for d in blender_udp.pack_frame(sample_frame(0, 2)):
        out = asm.feed(d) or out
    assert out is not None and out.frame_id == 0


def test_bridge_delivers_frames_over_real_udp(monkeypatch):
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)   # what the extension does too
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(2.0)
    monkeypatch.setattr(settings, "udp_host", "127.0.0.1")
    monkeypatch.setattr(settings, "udp_port", rx.getsockname()[1])
    monkeypatch.setattr(settings, "udp_hz", 100.0)
    current = {"frame": sample_frame(1, 11)}
    bridge = blender_udp.BlenderBridge(lambda: current["frame"])
    bridge.start()
    asm, got = protocol.FrameAssembler(), []
    try:
        deadline = time.time() + 5
        while len(got) < 2 and time.time() < deadline:
            if len(got) == 1 and current["frame"].frame_id == 1:
                current["frame"] = sample_frame(2, 12)   # a new vision frame
            try:
                frame = asm.feed(rx.recv(70000))
            except socket.timeout:
                break
            if frame:
                got.append(frame)
    finally:
        bridge.stop()
        rx.close()
    assert [f.frame_id for f in got] == [1, 2]
    assert np.array_equal(got[1].depth, sample_frame(2, 12).depth)
    assert bridge.sent_count == 2 and bridge.dropped_count == 0   # a repeated frame is not resent


# ---------------------------------------------------------------------------
# What the numbers mean
# ---------------------------------------------------------------------------

def test_depth_values_are_metric_nearness_with_background_cut():
    cfg = SystemConfig()
    v = cfg.vision_and_fog
    z_sonar = 1.5
    h, w = 480, 640
    z = np.full((h, w), z_sonar + 0.8, np.float32)                  # a wall far behind
    yy, xx = np.ogrid[:h, :w]
    head = (xx - 320) ** 2 + (yy - 240) ** 2 < 110**2
    z[head] = z_sonar - 0.20                                         # nose tip, 0.2 m in front of the anchor
    z[(xx - 320) ** 2 + (yy - 240) ** 2 < 40**2] = z_sonar + 0.15    # a recess further back
    i_fog = np.full((h, w), 255, np.uint8)
    frame = depth_ai.make_blender_frame(np.full((h, w, 3), 90, np.uint8), z, i_fog, z_sonar, cfg, 7)
    assert frame.depth.shape == (H, W) and frame.depth.dtype == np.uint8
    assert frame.rgb.shape == (H // 2, W // 2, 3)
    assert frame.plane_m == pytest.approx(z_sonar + v.subject_thickness_m)
    assert frame.relief_m == pytest.approx(v.subject_thickness_m + v.relief_depth_span_m)
    assert frame.depth[0, 0] == 0                                    # the wall is cut away
    nose = int(frame.depth[H // 2, W // 2 + 40])                    # on the head (80 px out), outside the recess
    expected = (frame.plane_m - (z_sonar - 0.20)) / frame.relief_m * 255
    assert nose == pytest.approx(expected, abs=2)                    # metric: displacement = depth/255 * relief
    assert 0 < frame.depth[H // 2, W // 2] < nose                    # the recess is further back, so lower
    assert (frame.depth > 0).mean() < 0.35                           # only the subject remains
