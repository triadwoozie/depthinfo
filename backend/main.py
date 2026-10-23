"""
main.py — FastAPI server: REST config, calibration rounds, MJPEG streams,
WebSocket telemetry and static .blend export.

Endpoints (AGENTS.md § 4.3)
---------------------------
  GET    /api/devices                    PortAudio + OpenCV enumeration
  GET    /api/config   PATCH /api/config Full SystemConfig / partial merge
  POST   /api/calibration/round/{1..4}   Calibration rounds
  GET    /api/stream/rgb | /api/stream/fog   MJPEG @ 30 FPS  (/video_feed = rgb alias)
  POST   /api/export/blend               Freeze + export .blend / .glb / .ply
  GET    /healthz
  WS     /ws/telemetry                   20 Hz telemetry; accepts config patches
  GET/POST /api/calibration              Legacy flat CalibrationState (old dashboard)

Usage
-----
  python main.py [--mock]      # or SONAR_MOCK_MODE=true python main.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import numpy as np
import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError


def _apply_cli_flags() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--mock", action="store_true", default=False)
    args, _ = parser.parse_known_args()
    if args.mock:
        os.environ["SONAR_MOCK_MODE"] = "true"


_apply_cli_flags()  # must run before `config` reads the environment

from blender_exporter import ExportError, export_scene, find_blender  # noqa: E402
from blender_udp import BlenderBridge  # noqa: E402
from config import (  # noqa: E402
    LEGACY_KEYS, SYSTEM_KEYS, apply_calibration_update, config_store, get_config,
    legacy_calibration, settings,
)
from depth_ai import VisionPipeline  # noqa: E402
from devices import list_devices  # noqa: E402
from sonar_dsp import SonarDSP  # noqa: E402
from stitcher import Stitcher  # noqa: E402

sonar = SonarDSP()
stitcher = Stitcher()
vision = VisionPipeline(lambda: sonar.latest_reading, on_frame=lambda vf, cfg: stitcher.ingest(vf, cfg))
bridge = BlenderBridge(sonar_provider=lambda: sonar.latest_reading, points_provider=stitcher.sample)

settings.exports_path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------

class ConnectionManager:
    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._connections = [c for c in self._connections if c is not ws]

    async def broadcast(self, data: str) -> None:
        for ws in list(self._connections):
            try:
                await ws.send_text(data)
            except Exception:
                self.disconnect(ws)

    @property
    def active_count(self) -> int:
        return len(self._connections)


manager = ConnectionManager()


def _config_message() -> str:
    return json.dumps({"type": "config_update", "config": get_config().model_dump()})


async def _telemetry_task() -> None:
    interval = 1.0 / max(settings.ws_hz, 1.0)
    last = time.monotonic()
    next_tick = last
    while True:
        start = time.monotonic()
        if manager.active_count:
            r = sonar.latest_reading
            cfg = get_config()
            payload = {
                "timestamp": r.timestamp,
                "status": r.status,
                "mock_mode": sonar.mock,
                "sonar_depth_m": r.sonar_depth_m,
                "peak_amplitude": r.peak_amplitude,
                "fps_vision": round(vision.fps, 1),
                "fps_sonar_hz": cfg.acoustics.ping_rate_hz,
                "active_vertex_count": stitcher.vertex_count,
                "doa_azimuth_deg": None,
                "system_fps": round(1.0 / max(start - last, 1e-6), 1),
                "ping_rate_hz": round(settings.ws_hz, 1),  # legacy dashboard field
                "correlation_curve": r.correlation_curve,
            }
            await manager.broadcast(json.dumps(payload))
        last = start
        # Absolute deadlines: per-tick work and wake-up latency must not accumulate as drift.
        next_tick += interval
        now = time.monotonic()
        if next_tick < now - interval:  # fell far behind (e.g. a long GIL stall): resync
            next_tick = now
        await asyncio.sleep(max(0.0, next_tick - now))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    print(f"[Sonar Imaging] Starting — mock_mode={settings.mock_mode}, blender={find_blender()}")
    sonar.start()
    vision.start()
    bridge.start()
    task = asyncio.create_task(_telemetry_task())
    yield
    task.cancel()
    bridge.stop()
    vision.stop()
    sonar.stop()
    print("[Sonar Imaging] Shutdown complete.")


app = FastAPI(title="Sonar Imaging API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/exports", StaticFiles(directory=settings.exports_path), name="exports")


# ---------------------------------------------------------------------------
# Config & devices
# ---------------------------------------------------------------------------

@app.get("/api/devices")
async def api_devices() -> JSONResponse:
    # PortAudio / OpenCV probing blocks (camera open can take seconds) — keep it off the loop.
    return JSONResponse(await asyncio.get_running_loop().run_in_executor(None, list_devices))


@app.get("/api/config")
async def get_config_route() -> JSONResponse:
    return JSONResponse(get_config().model_dump())


async def _patch_config(partial: dict) -> JSONResponse:
    try:
        cfg = config_store.patch(partial)
    except ValidationError as exc:
        return JSONResponse(status_code=422, content={"detail": json.loads(exc.json())})
    await manager.broadcast(_config_message())
    return JSONResponse(cfg.model_dump())


@app.patch("/api/config")
async def patch_config_route(body: dict = Body(...)) -> JSONResponse:
    return await _patch_config(body)


# Legacy flat calibration API used by the original dashboard.
@app.get("/api/calibration")
async def get_calibration() -> JSONResponse:
    return JSONResponse(legacy_calibration().model_dump())


@app.post("/api/calibration")
async def post_calibration(body: dict = Body(...)) -> JSONResponse:
    try:
        updated = apply_calibration_update(body)
    except ValidationError as exc:
        return JSONResponse(status_code=422, content={"detail": json.loads(exc.json())})
    await manager.broadcast(_config_message())
    return JSONResponse(updated.model_dump())


# ---------------------------------------------------------------------------
# Calibration rounds
# ---------------------------------------------------------------------------

_round4_samples: list[dict] = []
_ROUND4_TARGETS_M = (0.5, 1.5, 2.5)


def _mark_round(n: int) -> dict:
    return {"calibration_round_completed": max(get_config().calibration.calibration_round_completed, n)}


@app.post("/api/calibration/round/{n}")
async def calibration_round(n: int, request: Request) -> JSONResponse:
    loop = asyncio.get_running_loop()
    if n not in (1, 2, 3, 4):
        raise HTTPException(404, "Calibration round must be 1, 2, 3 or 4")
    try:
        if n == 1:
            res = await loop.run_in_executor(None, sonar.calibrate_round_1)
            await _patch_config({"calibration": {"blanking_distance_cm": res["blanking_distance_cm"], **_mark_round(1)}})
            return JSONResponse({"status": "ok", **res})
        if n == 2:
            res = await loop.run_in_executor(None, sonar.calibrate_round_2)
            await _patch_config({"calibration": {
                "ambient_noise_floor_rms": res["ambient_noise_floor_rms"],
                "detection_threshold": res["detection_threshold"], **_mark_round(2)}})
            return JSONResponse({"status": "ok", **res})
        if n == 3:
            bg = await loop.run_in_executor(None, vision.capture_background)
            await _patch_config({"calibration": {"background_fog_captured": True, **_mark_round(3)}})
            return JSONResponse({"status": "ok", "frames": 30, "bg_shape": list(bg.shape)})
        return await _round_4(request)
    except (RuntimeError, ValidationError) as exc:
        return JSONResponse(status_code=503, content={"status": "error", "detail": str(exc)})


async def _round_4(request: Request) -> JSONResponse:
    """Three-point metric anchor. POST ``{"distance_m": 0.5}`` with a target at that distance;
    after all three of 0.5 / 1.5 / 2.5 m are captured the fit is applied.
    Fits ``true_distance = α · Z_sonar_raw + β`` (the sonar's own α/β, see config)."""
    loop = asyncio.get_running_loop()
    cfg = get_config().calibration
    try:
        body = await request.json()
    except Exception:
        body = {}

    if sonar.mock:  # no physical target in mock mode: synthesise a clean 3-point sweep
        _round4_samples[:] = [
            {"distance_m": d, "z_raw": d + float(np.random.default_rng(int(d * 10)).normal(0, 0.005)),
             "d_raw": 1.0 - d / 3.0}
            for d in _ROUND4_TARGETS_M
        ]
    else:
        d = body.get("distance_m")
        if d not in _ROUND4_TARGETS_M:
            raise HTTPException(400, f'Body must be {{"distance_m": one of {list(_ROUND4_TARGETS_M)}}}')
        await asyncio.sleep(1.0)  # let the EMA settle on the placed target
        z_cal = sonar.latest_reading.sonar_depth_m
        z_raw = (z_cal - cfg.metric_offset_m) / cfg.metric_scale_factor  # undo current α/β
        d_raw = await loop.run_in_executor(None, vision.sample_foreground_d_raw)
        _round4_samples[:] = [s for s in _round4_samples if s["distance_m"] != d]
        _round4_samples.append({"distance_m": d, "z_raw": z_raw, "d_raw": d_raw})

    captured = sorted(s["distance_m"] for s in _round4_samples)
    if len(captured) < len(_ROUND4_TARGETS_M):
        return JSONResponse({"status": "pending", "captured_m": captured,
                             "remaining_m": [t for t in _ROUND4_TARGETS_M if t not in captured]})

    truth = np.array([s["distance_m"] for s in _round4_samples])
    raw = np.array([s["z_raw"] for s in _round4_samples])
    alpha, beta = (float(v) for v in np.polyfit(raw, truth, 1))
    rms = float(np.sqrt(np.mean((alpha * raw + beta - truth) ** 2)))
    samples = list(_round4_samples)
    _round4_samples.clear()
    await _patch_config({"calibration": {
        "metric_scale_factor": float(np.clip(alpha, 0.1, 5.0)),
        "metric_offset_m": float(np.clip(beta, -2.0, 2.0)), **_mark_round(4)}})
    return JSONResponse({"status": "ok", "metric_scale_factor": alpha, "metric_offset_m": beta,
                         "fit_rms_m": rms, "samples": samples})


# ---------------------------------------------------------------------------
# MJPEG streams
# ---------------------------------------------------------------------------

async def _mjpeg(get_jpg) -> AsyncGenerator[bytes, None]:
    while True:
        frame = get_jpg()
        if frame:
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n"
                   + f"Content-Length: {len(frame)}\r\n\r\n".encode() + frame + b"\r\n")
        await asyncio.sleep(1 / 30)


def _stream(get_jpg) -> StreamingResponse:
    return StreamingResponse(_mjpeg(get_jpg), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/api/stream/rgb")
async def stream_rgb() -> StreamingResponse:
    return _stream(lambda: vision.rgb_jpg)


@app.get("/video_feed")  # legacy dashboard path
async def video_feed() -> StreamingResponse:
    return _stream(lambda: vision.rgb_jpg)


@app.get("/api/stream/fog")
async def stream_fog() -> StreamingResponse:
    return _stream(lambda: vision.fog_jpg)


# ---------------------------------------------------------------------------
# Point cloud preview (polled by the dashboard's 3D tab)
# ---------------------------------------------------------------------------

@app.get("/api/pointcloud")
async def pointcloud(max_points: int = 4000) -> JSONResponse:
    points, colors = stitcher.sample(min(max(max_points, 1), 20000))
    return JSONResponse({"count": len(points), "total": stitcher.vertex_count,
                         "points": points, "colors": colors})


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

_export_lock = asyncio.Lock()


@app.post("/api/export/blend")
async def export_blend() -> JSONResponse:
    if _export_lock.locked():
        return JSONResponse(status_code=409, content={"status": "busy", "detail": "An export is already running"})
    async with _export_lock:
        try:
            return JSONResponse(await export_scene(stitcher, get_config()))
        except ExportError as exc:
            return JSONResponse(status_code=500, content={"status": "error", "detail": str(exc)})


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws/telemetry")
async def ws_telemetry(websocket: WebSocket) -> None:
    """Server → client: telemetry @ ws_hz. Client → server: partial config patches
    (nested SystemConfig sections, or the legacy flat CalibrationState keys)."""
    await manager.connect(websocket)
    await websocket.send_text(_config_message())
    try:
        while True:
            try:
                data = json.loads(await websocket.receive_text())
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict) or not data:
                continue
            try:
                if set(data) <= LEGACY_KEYS:
                    updated = apply_calibration_update(data)
                    await websocket.send_text(json.dumps(
                        {"type": "calibration_ack", "calibration": updated.model_dump()}))
                    await manager.broadcast(_config_message())
                elif set(data) <= SYSTEM_KEYS:
                    config_store.patch(data)
                    await manager.broadcast(_config_message())  # reaches clients within one tick
            except ValidationError as exc:
                await websocket.send_text(json.dumps(
                    {"type": "error", "detail": json.loads(exc.json())}))
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({
        "ok": True,
        "mock": sonar.mock,
        "ws_clients": manager.active_count,
        "sonar_status": sonar.latest_reading.status,
        "vision_fps": round(vision.fps, 1),
        "depth_backend": vision.depth_backend,
        "camera_mock": vision.camera_mock,
        "vertex_count": stitcher.vertex_count,
        "blender": find_blender(),
        "udp_sent": bridge.sent_count,
    })


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False, log_level="info")
