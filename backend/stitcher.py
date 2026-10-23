"""
stitcher.py — Pinhole unprojection, temporal point-cloud fusion and surface
reconstruction (AGENTS.md § 3.3, colour routing § 6).

World frame: x right, y up, z forward (camera at the origin looking down +z, then
transformed by ``hardware.camera_pose``). The Blender exporter remaps this to
Blender's Z-up convention.

``ingest`` is called from the vision thread (rate limited); ``output_frame`` and
``latest_points`` return cached results so they are cheap to call from anywhere.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree

from config import BlenderStyleConfig, SystemConfig
from depth_ai import VisionFrame, _hex_bgr

STRIDE = 4            # pixel subsampling before unprojection (640x480 → 160x120)
MIN_INTERVAL_S = 0.1  # stitch at most 10 Hz
MESH_INTERVAL_S = 2.0  # surface re-meshing rate limit


def camera_rotation(pose) -> np.ndarray:
    """R = Ry(yaw) · Rx(−pitch) · Rz(roll) for camera axes x-right, y-up, z-forward."""
    p, y, r = math.radians(-pose.pitch_deg), math.radians(pose.yaw_deg), math.radians(pose.roll_deg)
    rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rz = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    return ry @ rx @ rz


def route_colors(
    mode: str,
    style: BlenderStyleConfig,
    bgr: np.ndarray,       # (N,3) uint8 camera colour per point
    i_fog: np.ndarray,     # (N,) uint8
    z: np.ndarray,         # (N,) metres
    zmin: float,
    zmax: float,
    peak_amplitude: float,
) -> np.ndarray:
    """Per-vertex RGB float32 in [0,1] for the selected ``color_mode`` (AGENTS.md § 6)."""
    near = _hex_bgr(style.custom_near_hex)[::-1] / 255.0  # → RGB
    far = _hex_bgr(style.custom_far_hex)[::-1] / 255.0
    if mode == "optical_rgb":
        return (bgr[:, ::-1].astype(np.float32) / 255.0)
    if mode == "fog_gradient":
        t = 1.0 - i_fog.astype(np.float32) / 255.0  # bright fog = near
    elif mode == "custom_palette":
        t = np.clip((z - zmin) / max(zmax - zmin, 1e-6), 0.0, 1.0)
    else:  # acoustic_heatmap: echo amplitude, modulated by proximity so the cloud has relief
        val = np.clip(peak_amplitude, 0.0, 1.0) * (0.4 + 0.6 * i_fog.astype(np.float32) / 255.0)
        heat = cv2.applyColorMap((val * 255).astype(np.uint8).reshape(-1, 1), cv2.COLORMAP_JET)
        return heat.reshape(-1, 3)[:, ::-1].astype(np.float32) / 255.0
    t = t[:, None].astype(np.float32)
    return (near + (far - near) * t).astype(np.float32)


class Stitcher:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buffer: deque[tuple[np.ndarray, np.ndarray]] = deque(maxlen=5)
        self._last_ingest = 0.0
        self._prev_pcd: o3d.geometry.PointCloud | None = None
        self._latest: tuple[np.ndarray, np.ndarray] | None = None  # newest single-frame cloud
        self._pcd: o3d.geometry.PointCloud | None = None
        self._last_mesh = 0.0
        self._V = np.zeros((0, 3), np.float32)
        self._F = np.zeros((0, 3), np.int32)
        self._C = np.zeros((0, 3), np.float32)
        self.last_ms = 0.0
        self.color_mode = "optical_rgb"

    # -- ingest ------------------------------------------------------------

    def ingest(self, vf: VisionFrame, cfg: SystemConfig, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_ingest < MIN_INTERVAL_S:
            return
        self._last_ingest = now
        t0 = time.perf_counter()

        pts, cols = self._unproject(vf, cfg)
        with self._lock:
            self._latest = (pts, cols) if len(pts) else None
        if len(pts) == 0:
            return
        if self._buffer.maxlen != cfg.vision_and_fog.temporal_stitch_frames:
            self._buffer = deque(self._buffer, maxlen=cfg.vision_and_fog.temporal_stitch_frames)
        if cfg.blender_style.acoustic_wave_distortion > 0.0:
            pts = self._icp_align(pts, cfg)
        self._buffer.append((pts, cols))
        self.color_mode = cfg.blender_style.color_mode
        self._rebuild(cfg)
        self.last_ms = (time.perf_counter() - t0) * 1000.0

    def _unproject(self, vf: VisionFrame, cfg: SystemConfig) -> tuple[np.ndarray, np.ndarray]:
        h, w = vf.z_metric.shape
        fx = (w / 2.0) / math.tan(math.radians(cfg.hardware.camera_fov_deg) / 2.0)
        cx, cy = w / 2.0, h / 2.0
        vs, us = np.mgrid[0:h:STRIDE, 0:w:STRIDE]
        z = vf.z_metric[::STRIDE, ::STRIDE]
        valid = np.isfinite(z)
        u, v, z = us[valid].astype(np.float32), vs[valid].astype(np.float32), z[valid]
        if z.size == 0:
            return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32)

        cam = np.stack([(u - cx) / fx * z, -(v - cy) / fx * z, z], axis=1)  # y flipped → up
        pose = cfg.hardware.camera_pose
        pts = cam @ camera_rotation(pose).T + np.array([pose.x_cm, pose.y_cm, pose.z_cm]) / 100.0

        vf_cfg = cfg.vision_and_fog
        cols = route_colors(
            cfg.blender_style.color_mode, cfg.blender_style,
            vf.bgr[::STRIDE, ::STRIDE][valid], vf.i_fog[::STRIDE, ::STRIDE][valid], z,
            vf_cfg.min_scan_distance_m, vf_cfg.max_scan_distance_m, vf.peak_amplitude,
        )
        return pts.astype(np.float32), cols

    def _icp_align(self, pts: np.ndarray, cfg: SystemConfig) -> np.ndarray:
        """Register the new frame onto the previous one (point-to-plane ICP)."""
        voxel = max(cfg.vision_and_fog.voxel_downsample_m, 0.02)
        pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(pts.astype(np.float64)))
        pcd = pcd.voxel_down_sample(voxel)
        prev = self._prev_pcd
        if prev is not None and len(pcd.points) > 30 and len(prev.points) > 30:
            pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=voxel * 4, max_nn=30))
            res = o3d.pipelines.registration.registration_icp(
                pcd, prev, max_correspondence_distance=voxel * 5,
                estimation_method=o3d.pipelines.registration.TransformationEstimationPointToPlane(),
                criteria=o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=15),
            )
            if res.fitness > 0.3:
                # Bound the correction so a bad match can't fling the cloud away.
                T = res.transformation
                if np.linalg.norm(T[:3, 3]) < 0.1:
                    pts = (pts.astype(np.float64) @ T[:3, :3].T + T[:3, 3]).astype(np.float32)
        self._prev_pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(pts.astype(np.float64)))
        self._prev_pcd = self._prev_pcd.voxel_down_sample(voxel)
        self._prev_pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=voxel * 4, max_nn=30))
        return pts

    # -- fusion / meshing ---------------------------------------------------

    def _rebuild(self, cfg: SystemConfig) -> None:
        """Fuse the ring buffer into one filtered cloud (cheap, every ingest) and, if a
        surface mode is selected, re-mesh it at most every ``MESH_INTERVAL_S`` (Poisson
        costs ~400 ms and Open3D holds the GIL, so it must not run at the stitch rate)."""
        pts = np.concatenate([p for p, _ in self._buffer])
        cols = np.concatenate([c for _, c in self._buffer])
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(pts.astype(np.float64))
        pcd.colors = o3d.utility.Vector3dVector(cols.astype(np.float64))

        pcd = pcd.voxel_down_sample(cfg.vision_and_fog.voxel_downsample_m)
        if len(pcd.points) > 40:
            pcd, _ = pcd.remove_statistical_outlier(nb_neighbors=20, std_ratio=2.0)
        self._pcd = pcd

        mode = cfg.blender_style.surface_reconstruction_mode
        now = time.monotonic()
        if mode != "point_cloud" and now - self._last_mesh >= MESH_INTERVAL_S:
            self._last_mesh = now
            if self._mesh_from_cloud(cfg):
                return
        elif mode != "point_cloud" and len(self._F):
            return  # keep the previous mesh until the next re-mesh

        with self._lock:
            self._V = np.asarray(pcd.points, np.float32)
            self._C = np.asarray(pcd.colors, np.float32)
            self._F = np.zeros((0, 3), np.int32)

    def _mesh_from_cloud(self, cfg: SystemConfig) -> bool:
        """Surface-reconstruct ``self._pcd``; returns True if a mesh was stored."""
        pcd = o3d.geometry.PointCloud(self._pcd)  # copy: normals are added in place
        if len(pcd.points) <= 50:
            return False
        voxel = cfg.vision_and_fog.voxel_downsample_m
        pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=voxel * 5, max_nn=30))
        pcd.orient_normals_towards_camera_location(np.zeros(3))
        try:
            mesh = self._reconstruct(pcd, cfg.blender_style.surface_reconstruction_mode)
        except Exception as exc:
            print(f"[stitcher] Surface reconstruction failed ({exc}); keeping point cloud.")
            return False
        if not len(mesh.triangles):
            return False
        V = np.asarray(mesh.vertices, np.float32)
        _, idx = cKDTree(np.asarray(pcd.points)).query(V)
        with self._lock:
            self._V, self._F = V, np.asarray(mesh.triangles, np.int32)
            self._C = np.asarray(pcd.colors, np.float32)[idx]
        return True

    @staticmethod
    def _reconstruct(pcd, mode: str):
        if mode == "poisson":
            mesh, dens = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(pcd, depth=7)
            mesh.remove_vertices_by_mask(np.asarray(dens) < np.quantile(np.asarray(dens), 0.05))
            return mesh
        d = float(np.mean(pcd.compute_nearest_neighbor_distance()))
        return o3d.geometry.TriangleMesh.create_from_point_cloud_ball_pivoting(
            pcd, o3d.utility.DoubleVector([d, 2 * d, 4 * d])
        )

    # -- outputs -----------------------------------------------------------

    def output_frame(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Cached ``(V (N,3), F (M,3), C (N,3))``; ``F`` is empty for a point cloud."""
        with self._lock:
            return self._V, self._F, self._C

    @property
    def vertex_count(self) -> int:
        with self._lock:
            return len(self._V)

    def sample(self, max_points: int, stitched: bool = False) -> tuple[list[list[float]], list[list[float]]]:
        """Uniform subsample (rounded) for the UDP bridge and the dashboard preview.

        By default this is the newest *single frame*: it keeps the true front-to-back relief of the
        subject. ``stitched=True`` returns the temporally fused cloud, which smears if the subject
        (and therefore Z_sonar) moves between the buffered frames.
        """
        if not stitched:
            with self._lock:
                latest = self._latest
            V, C = latest if latest is not None else (np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32))
        else:
            V, _, C = self.output_frame()
        if len(V) == 0:
            return [], []
        idx = np.linspace(0, len(V) - 1, min(max_points, len(V))).astype(int)
        return np.round(V[idx].astype(np.float64), 3).tolist(), np.round(C[idx].astype(np.float64), 2).tolist()

    def export_ply(self, path: Path, cfg: SystemConfig | None = None) -> int:
        """Write the current cloud/mesh (with vertex colours) to ``path``. Returns vertex count."""
        if cfg is not None and cfg.blender_style.surface_reconstruction_mode != "point_cloud" \
                and self._pcd is not None:
            self._mesh_from_cloud(cfg)  # export always gets a fresh surface
        V, F, C = self.output_frame()
        if len(V) == 0:
            raise RuntimeError("Stitcher has no points yet — nothing to export")
        path.parent.mkdir(parents=True, exist_ok=True)
        if len(F):
            mesh = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(V.astype(np.float64)),
                                             o3d.utility.Vector3iVector(F))
            mesh.vertex_colors = o3d.utility.Vector3dVector(C.astype(np.float64))
            ok = o3d.io.write_triangle_mesh(str(path), mesh, write_ascii=False)
        else:
            pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(V.astype(np.float64)))
            pcd.colors = o3d.utility.Vector3dVector(C.astype(np.float64))
            ok = o3d.io.write_point_cloud(str(path), pcd, write_ascii=False)
        if not ok:
            raise RuntimeError(f"Open3D failed to write {path}")
        return len(V)
