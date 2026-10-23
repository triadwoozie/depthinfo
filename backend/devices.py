"""
devices.py — PortAudio & OpenCV hardware enumeration.

Never raises: if SONAR_MOCK_MODE is on, or PortAudio / OpenCV are missing or fail,
virtual device descriptors are returned instead (AGENTS.md Rule 3).
"""

from __future__ import annotations

from typing import Any

from config import settings

try:
    import sounddevice as sd  # type: ignore
except Exception:  # ImportError, or OSError when libportaudio is absent
    sd = None

try:
    import cv2  # type: ignore
except Exception:
    cv2 = None


def _mock_audio() -> list[dict[str, Any]]:
    return [
        {"id": 1, "name": "Mock Studio Interface (in)", "max_input_channels": 2,
         "max_output_channels": 0, "default_samplerate": 48000.0, "hostapi": "mock"},
        {"id": 2, "name": "Mock Studio Interface (out)", "max_input_channels": 0,
         "max_output_channels": 2, "default_samplerate": 48000.0, "hostapi": "mock"},
    ]


def _mock_cameras() -> list[dict[str, Any]]:
    return [{"index": 0, "name": "Mock Camera 640x480", "width": 640, "height": 480, "mock": True}]


def enumerate_audio() -> tuple[list[dict[str, Any]], bool]:
    """Return ``(devices, is_mock)``."""
    if settings.mock_mode or sd is None:
        return _mock_audio(), True
    try:
        hostapis = sd.query_hostapis()
        devices = [
            {
                "id": i,
                "name": d["name"],
                "max_input_channels": d["max_input_channels"],
                "max_output_channels": d["max_output_channels"],
                "default_samplerate": d["default_samplerate"],
                "hostapi": hostapis[d["hostapi"]]["name"],
            }
            for i, d in enumerate(sd.query_devices())
        ]
        return (devices, False) if devices else (_mock_audio(), True)
    except Exception as exc:
        print(f"[devices] PortAudio enumeration failed ({exc}); using mock devices.")
        return _mock_audio(), True


def enumerate_cameras(max_index: int = 10) -> tuple[list[dict[str, Any]], bool]:
    """Probe camera indices ``0..max_index-1``. Returns ``(cameras, is_mock)``."""
    if settings.mock_mode or cv2 is None:
        return _mock_cameras(), True
    found: list[dict[str, Any]] = []
    try:
        for idx in range(max_index):
            cap = cv2.VideoCapture(idx)
            try:
                if cap.isOpened():
                    found.append({
                        "index": idx,
                        "name": f"Camera {idx}",
                        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                        "mock": False,
                    })
            finally:
                cap.release()
    except Exception as exc:
        print(f"[devices] OpenCV enumeration failed ({exc}); using mock camera.")
        return _mock_cameras(), True
    return (found, False) if found else (_mock_cameras(), True)


def list_devices() -> dict[str, Any]:
    audio, audio_mock = enumerate_audio()
    cameras, cam_mock = enumerate_cameras()
    return {
        "mock": settings.mock_mode or audio_mock or cam_mock,
        "audio_mock": audio_mock,
        "camera_mock": cam_mock,
        "audio_inputs": [d for d in audio if d["max_input_channels"] > 0],
        "audio_outputs": [d for d in audio if d["max_output_channels"] > 0],
        "cameras": cameras,
    }
