"""
blender_udp.py — streams the subject-isolated depth frame to the Blender receiver extension.

Per new vision frame it sends a 320x240 uint8 nearness map (0 = culled background, 255 = nearest
subject surface) plus a half-resolution optical RGB plane, over UDP to
``SONAR_UDP_HOST:SONAR_UDP_PORT`` (5005). One frame is 134 KB, which does not fit in a single UDP
datagram (max 65,507 bytes), so it is split into 32 KB chunks that the extension reassembles. The wire format is documented (and parsed) in
``blender/sonar_imaging/protocol.py``; backend/tests/test_blender_udp.py checks both ends agree.

Rules (AGENTS.md Rule 1): runs in its own daemon thread, non-blocking socket, never touches the
asyncio loop. A frame that cannot be fully handed to the OS is dropped, never queued.
"""

from __future__ import annotations

import socket
import struct
import threading
import time
from typing import Callable

from config import settings

MAGIC = b"SDF1"
HEADER = struct.Struct("<4sIBBHHHffff")  # keep identical to blender/sonar_imaging/protocol.py
CHUNK_PAYLOAD = 32768


def pack_frame(frame) -> list[bytes]:
    """Split a ``depth_ai.BlenderFrame`` into datagrams (header + up to CHUNK_PAYLOAD bytes each)."""
    h, w = frame.depth.shape
    payload = frame.depth.tobytes() + (frame.rgb.tobytes() if frame.rgb is not None else b"")
    color_div = (w // frame.rgb.shape[1]) if frame.rgb is not None else 0
    chunks = [payload[i:i + CHUNK_PAYLOAD] for i in range(0, len(payload), CHUNK_PAYLOAD)]
    if len(chunks) > 255:
        raise ValueError("frame needs more than 255 chunks")
    return [
        HEADER.pack(MAGIC, frame.frame_id & 0xFFFFFFFF, i, len(chunks), w, h, color_div,
                    frame.z_sonar, frame.relief_m, frame.fov_deg, frame.plane_m) + chunk
        for i, chunk in enumerate(chunks)
    ]


class BlenderBridge:
    """Sends each new ``BlenderFrame`` from ``frame_provider`` to the Blender extension."""

    def __init__(self, frame_provider: Callable) -> None:
        self._frame_provider = frame_provider
        self._running = False
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self._sent_frames = 0
        self._dropped_frames = 0
        self._last_id = -1
        self._last_sent = 0.0

    def start(self) -> None:
        if self._running:
            return
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024)
        self._sock.setblocking(False)
        self._running = True
        self._thread = threading.Thread(target=self._send_loop, daemon=True, name="blender-bridge")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._sock:
            self._sock.close()
            self._sock = None

    @property
    def sent_count(self) -> int:
        """Frames fully handed to the network (kept under the old name for /healthz)."""
        return self._sent_frames

    @property
    def dropped_count(self) -> int:
        return self._dropped_frames

    @property
    def last_sent_timestamp(self) -> float:
        return self._last_sent

    def _send_loop(self) -> None:
        interval = 1.0 / max(settings.udp_hz, 1.0)
        target = (settings.udp_host, settings.udp_port)
        while self._running:
            started = time.monotonic()
            try:
                frame = self._frame_provider()
                if frame is not None and frame.frame_id != self._last_id and self._sock:
                    self._last_id = frame.frame_id
                    try:
                        for datagram in pack_frame(frame):
                            self._sock.sendto(datagram, target)
                        self._sent_frames += 1
                        self._last_sent = time.time()
                    except BlockingIOError:
                        self._dropped_frames += 1  # OS buffer full: drop the frame, never block
            except Exception as exc:  # never crash the bridge thread
                print(f"[BlenderBridge] send error: {exc}")
            time.sleep(max(0.0, interval - (time.monotonic() - started)))
