"""
main.py — FastAPI Server: WebSocket telemetry, MJPEG video feed, calibration REST.

Endpoints
---------
  WS   /ws/telemetry     Streams SonarTelemetry JSON at settings.ws_hz.
                         Accepts CalibrationState JSON updates from the client.
  GET  /video_feed       MJPEG stream of the vision engine's annotated frame.
  GET  /api/calibration  Returns the current CalibrationState as JSON.
  POST /api/calibration  Merges a partial CalibrationState update.
  GET  /healthz          Health check (returns {"ok": true, "mock": bool}).

Usage
-----
  # With hardware:
  python main.py

  # Mock mode (no mic / camera):
  SONAR_MOCK_MODE=true python main.py
  python main.py --mock

"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from blender_bridge import BlenderBridge
from config import (
    apply_calibration_update,
    apply_dashboard_config_update,
    get_calibration_state,
    get_dashboard_config,
    settings,
)
from devices import enumerate_devices
from pydantic import ValidationError
from sonar_engine import SonarEngine
from vision_engine import VisionEngine


# ---------------------------------------------------------------------------
# Parse --mock CLI flag before anything else
# ---------------------------------------------------------------------------

def _apply_cli_flags() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--mock", action="store_true", default=False)
    args, _ = parser.parse_known_args()
    if args.mock:
        os.environ["SONAR_MOCK_MODE"] = "true"
        # Patch the already-loaded settings object in-place
        object.__setattr__(settings, "mock_mode", True)


_apply_cli_flags()

# ---------------------------------------------------------------------------
# Engine singletons (created after CLI flags are processed)
# ---------------------------------------------------------------------------

sonar = SonarEngine()
vision = VisionEngine(depth_provider=lambda: sonar.latest_reading.sonar_depth_m)
bridge = BlenderBridge(
    sonar_provider=lambda: sonar.latest_reading,
    points_provider=lambda: vision.latest_points,
)


# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------

class ConnectionManager:
    """Tracks active WebSocket connections and broadcasts to all."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.append(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections = [c for c in self._connections if c is not ws]

    async def broadcast(self, data: str) -> None:
        dead: list[WebSocket] = []
        async with self._lock:
            conns = list(self._connections)
        for ws in conns:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)

    @property
    def active_count(self) -> int:
        return len(self._connections)


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Telemetry broadcast task
# ---------------------------------------------------------------------------

async def _telemetry_broadcast_task() -> None:
    """
    Coroutine that fires at settings.ws_hz and pushes SonarTelemetry JSON
    to all connected WebSocket clients.
    """
    interval = 1.0 / max(settings.ws_hz, 1.0)

    while True:
        start = asyncio.get_event_loop().time()

        if manager.active_count > 0:
            reading = sonar.latest_reading
            payload = {
                "timestamp": reading.timestamp,
                "status": reading.status,
                "mock_mode": settings.mock_mode,
                "sonar_depth_m": reading.sonar_depth_m,
                "peak_amplitude": reading.peak_amplitude,
                "fps_vision": round(vision.fps, 1),
                "ping_rate_hz": round(settings.ws_hz, 1),
                "correlation_curve": reading.correlation_curve,
            }
            await manager.broadcast(json.dumps(payload))

        elapsed = asyncio.get_event_loop().time() - start
        await asyncio.sleep(max(0.0, interval - elapsed))


# ---------------------------------------------------------------------------
# Lifespan: start/stop background workers
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup
    print(f"[Sonar Imaging] Starting — mock_mode={settings.mock_mode}")
    sonar.start()
    vision.start()
    bridge.start()
    broadcast_task = asyncio.create_task(_telemetry_broadcast_task())

    yield

    # Shutdown
    broadcast_task.cancel()
    bridge.stop()
    vision.stop()
    sonar.stop()
    print("[Sonar Imaging] Shutdown complete.")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Sonar Imaging API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# WebSocket endpoint: /ws/telemetry
# ---------------------------------------------------------------------------

@app.websocket("/ws/telemetry")
async def ws_telemetry(websocket: WebSocket) -> None:
    """
    Bi-directional WebSocket.

    Server → Client: SonarTelemetry JSON at settings.ws_hz.
    Client → Server: Partial CalibrationState JSON to update calibration.
    """
    await manager.connect(websocket)
    try:
        while True:
            # Listen for incoming calibration updates (non-blocking with timeout)
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=0.05)
                data = json.loads(raw)
                if not isinstance(data, dict):
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "config_error",
                                "message": "WebSocket updates must be JSON objects",
                            }
                        )
                    )
                    continue
                if data.get("type") == "operator_config_patch":
                    updated_config = apply_dashboard_config_update(data.get("config", {}))
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "operator_config_ack",
                                "config": updated_config.model_dump(),
                            }
                        )
                    )
                else:
                    updated = apply_calibration_update(data)
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "calibration_ack",
                                "calibration": updated.model_dump(),
                            }
                        )
                    )
            except asyncio.TimeoutError:
                pass  # No incoming message — continue
            except json.JSONDecodeError:
                await websocket.send_text(
                    json.dumps({"type": "config_error", "message": "Invalid JSON message"})
                )
            except ValidationError as exc:
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "config_error",
                            "message": "Configuration validation failed",
                            "details": exc.errors(include_context=False),
                        }
                    )
                )
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# MJPEG video feed: /video_feed
# ---------------------------------------------------------------------------

async def _mjpeg_generator() -> AsyncGenerator[bytes, None]:
    """
    Async generator yielding MJPEG multipart frames from the vision engine.

    Separates video from the JSON WebSocket pipe to avoid congesting JSON
    telemetry with large binary blobs.
    """
    boundary = b"--frame"
    while True:
        frame = vision.latest_frame_jpg
        if frame:
            yield (
                boundary
                + b"\r\nContent-Type: image/jpeg\r\n"
                + f"Content-Length: {len(frame)}\r\n\r\n".encode()
                + frame
                + b"\r\n"
            )
        await asyncio.sleep(1 / 30)  # 30 FPS cap


@app.get("/video_feed")
async def video_feed() -> StreamingResponse:
    """MJPEG stream of the live annotated camera / mock frame."""
    return StreamingResponse(
        _mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


# ---------------------------------------------------------------------------
# Calibration REST endpoints
# ---------------------------------------------------------------------------

@app.get("/api/calibration")
async def get_calibration() -> JSONResponse:
    """Return the current calibration state."""
    return JSONResponse(content=get_calibration_state().model_dump())


@app.get("/api/config")
async def get_config() -> JSONResponse:
    return JSONResponse(content=get_dashboard_config().model_dump())


@app.patch("/api/config")
async def patch_config(body: dict) -> JSONResponse:
    try:
        updated = apply_dashboard_config_update(body)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_context=False),
        ) from exc
    return JSONResponse(content=updated.model_dump())


@app.get("/api/devices")
async def get_devices() -> JSONResponse:
    try:
        return JSONResponse(content=enumerate_devices())
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/calibration")
async def post_calibration(body: dict) -> JSONResponse:
    """
    Merge a partial calibration dict into the current state.
    Returns the full updated state.
    """
    try:
        updated = apply_calibration_update(body)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_context=False),
        ) from exc
    return JSONResponse(content=updated.model_dump())


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse(
        content={
            "ok": True,
            "mock": settings.mock_mode,
            "ws_clients": manager.active_count,
            "sonar_status": sonar.latest_reading.status,
            "vision_fps": round(vision.fps, 1),
            "udp_sent": bridge.sent_count,
        }
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
