"""
blender_udp.py — High-frequency non-blocking UDP sender to Blender.

Sends JSON-encoded packets to 127.0.0.1:5005 (Blender "Sonar Imaging Live Receiver" extension) at
the rate defined by settings.udp_hz (default 30 Hz).

Design rules (from AGENTS.md Rule 1):
  - Runs in a dedicated daemon thread — never blocks the FastAPI event loop.
  - Uses a non-blocking UDP socket (fire-and-forget, no ACK).
  - Keeps payload < 8192 bytes to prevent IP fragmentation.
  - Truncates points[] to fit within the size budget if necessary.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Callable

from config import get_config, settings


# Maximum UDP payload in bytes (well below 8192 to leave header room)
_MAX_PAYLOAD = 7680

# UDP packet counter (wraps at 2^32)
_ping_counter: int = 0


class BlenderBridge:
    """
    Sends fused sonar+vision data to the Blender receiver extension via UDP.

    Usage::

        bridge = BlenderBridge(
            sonar_provider=lambda: engine.latest_reading,
            points_provider=lambda: vision.latest_points,
        )
        bridge.start()
    """

    def __init__(
        self,
        sonar_provider: Callable,   # returns SonarReading
        points_provider: Callable,  # (max_points) -> (points, colors)
    ) -> None:
        self._sonar_provider = sonar_provider
        self._points_provider = points_provider
        self._running = False
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self._sent_count: int = 0
        self._max_points: int = 120
        self._last_reading_ts: float = 0.0
        self._last_sent: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self._running = True
        self._thread = threading.Thread(
            target=self._send_loop, daemon=True, name="blender-bridge"
        )
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
        return self._sent_count

    @property
    def last_sent_timestamp(self) -> float:
        return self._last_sent

    # ------------------------------------------------------------------
    # Internal send loop
    # ------------------------------------------------------------------

    def _send_loop(self) -> None:
        global _ping_counter
        interval = 1.0 / max(settings.udp_hz, 1.0)
        target = (settings.udp_host, settings.udp_port)

        while self._running:
            loop_start = time.monotonic()

            try:
                reading = self._sonar_provider()
                points, colors = self._points_provider(self._max_points)

                _ping_counter = (_ping_counter + 1) % (2 ** 32)
                # 1 only on the first packet after a fresh sonar reading (a chirp echo
                # arrived); 0 while the same reading is repeated at the UDP rate.
                ping_pulse = 1 if reading.timestamp != self._last_reading_ts else 0
                self._last_reading_ts = reading.timestamp

                packet = {
                    "state": reading.status,
                    "sonar_z": reading.sonar_depth_m,
                    "ping_pulse": ping_pulse,
                    "audio_energy": reading.peak_amplitude,
                    "color_mode": get_config().blender_style.color_mode,
                    "points": points,
                    "colors": colors,
                }

                payload = json.dumps(packet, separators=(",", ":"))

                # Adapt the point budget so the packet stays under _MAX_PAYLOAD.
                if len(payload) > _MAX_PAYLOAD and points:
                    self._max_points = max(1, int(len(points) * _MAX_PAYLOAD / len(payload) * 0.9))
                    packet["points"], packet["colors"] = points[: self._max_points], colors[: self._max_points]
                    payload = json.dumps(packet, separators=(",", ":"))
                elif len(payload) < _MAX_PAYLOAD * 0.6 and len(points) >= self._max_points:
                    self._max_points = int(self._max_points * 1.2) + 1

                if self._sock:
                    self._sock.sendto(payload.encode("utf-8"), target)
                    self._sent_count += 1
                    self._last_sent = time.time()

            except BlockingIOError:
                # Non-blocking socket; buffer full — skip this frame
                pass
            except Exception as exc:
                # Never crash the bridge thread
                print(f"[BlenderBridge] Send error: {exc}")

            # Precise sleep to maintain target Hz
            elapsed = time.monotonic() - loop_start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
