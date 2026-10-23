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


def audio_device_name(device_id: int | None, kind: str) -> str | None:
    """Return the current device name only when it supports the requested direction."""
    if sd is None or device_id is None:
        return None
    try:
        device = sd.query_devices(device_id)
        if device[f"max_{kind}_channels"] <= 0:
            return None
        return str(device["name"])
    except Exception:
        return None


def _select_audio_device(
    devices: list[dict[str, Any]],
    kind: str,
    preferred_name: str | None,
    preferred_id: int | None,
    default_id: int | None,
) -> dict[str, Any]:
    channel_key = f"max_{kind}_channels"
    candidates = [device for device in devices if device[channel_key] > 0]
    if preferred_name:
        exact = [device for device in candidates if device["name"].casefold() == preferred_name.casefold()]
        matched = exact or [
            device for device in candidates
            if preferred_name.casefold() in device["name"].casefold()
        ]
        if matched:
            return matched[0]
        print(
            f"[devices] Configured {kind} name {preferred_name!r} is unavailable; "
            "trying the system default."
        )
    default = next((device for device in candidates if device["id"] == default_id), None)
    if default:
        return default
    if preferred_id is not None:
        selected = next((device for device in candidates if device["id"] == preferred_id), None)
        if selected:
            print(f"[devices] No system-default {kind}; using legacy device ID {preferred_id}.")
            return selected
        print(f"[devices] Configured {kind} device ID {preferred_id} is stale.")
    if not candidates:
        raise RuntimeError(f"No PortAudio {kind} devices with available channels")
    raise RuntimeError(
        f"No system-default PortAudio {kind} device; select a device by name in the dashboard"
    )


def resolve_audio_device_id(hardware, kind: str) -> int:
    """Resolve one input/output device using its stable name before its legacy ID/default."""
    if sd is None:
        raise RuntimeError("sounddevice / PortAudio is unavailable")
    devices = [{"id": i, **device} for i, device in enumerate(sd.query_devices())]
    defaults = sd.default.device
    default_id = defaults[0 if kind == "input" else 1] if defaults else None
    if default_id is not None and default_id < 0:
        default_id = None
    return _select_audio_device(
        devices,
        kind,
        getattr(hardware, f"{kind}_audio_device_name"),
        getattr(hardware, f"{kind}_audio_device_id"),
        default_id,
    )["id"]


def resolve_audio_device_pair(hardware) -> tuple[int, int]:
    """Resolve stable name hints to current IDs and ensure PortAudio can open both together."""
    if sd is None:
        raise RuntimeError("sounddevice / PortAudio is unavailable")
    devices = [
        {"id": i, **device}
        for i, device in enumerate(sd.query_devices())
    ]
    defaults = sd.default.device
    default_input = defaults[0] if defaults and defaults[0] is not None else None
    default_output = defaults[1] if defaults and defaults[1] is not None else None
    if default_input is not None and default_input < 0:
        default_input = None
    if default_output is not None and default_output < 0:
        default_output = None
    input_device = _select_audio_device(
        devices, "input", hardware.input_audio_device_name,
        hardware.input_audio_device_id, default_input,
    )
    output_device = _select_audio_device(
        devices, "output", hardware.output_audio_device_name,
        hardware.output_audio_device_id, default_output,
    )
    if input_device["hostapi"] != output_device["hostapi"]:
        default_pair = (
            next((d for d in devices if d["id"] == default_input and d["max_input_channels"] > 0), None),
            next((d for d in devices if d["id"] == default_output and d["max_output_channels"] > 0), None),
        )
        if all(default_pair) and default_pair[0]["hostapi"] == default_pair[1]["hostapi"]:
            print("[devices] Selected devices use different PortAudio APIs; using the compatible system-default pair.")
            input_device, output_device = default_pair
        else:
            raise RuntimeError(
                f"Selected input {input_device['name']!r} and output {output_device['name']!r} "
                "belong to different PortAudio host APIs; choose a compatible device pair."
            )
    return input_device["id"], output_device["id"]


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
    if (settings.mock_mode and not settings.real_camera) or cv2 is None:
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


def list_devices(active_camera: int | None = None) -> dict[str, Any]:
    """``active_camera``: index of a camera already held open by the vision pipeline. A busy
    camera cannot be re-opened for probing, so without this it would be reported as missing."""
    audio, audio_mock = enumerate_audio()
    cameras, cam_mock = enumerate_cameras()
    if active_camera is not None and not any(c["index"] == active_camera and not c.get("mock") for c in cameras):
        cameras = [c for c in cameras if not c.get("mock") and c["index"] != active_camera]
        cameras.append({"index": active_camera, "name": f"Camera {active_camera} (in use)",
                        "width": 640, "height": 480, "mock": False})
        cameras.sort(key=lambda c: c["index"])
        cam_mock = False
    return {
        "mock": settings.mock_mode or audio_mock or cam_mock,
        "audio_mock": audio_mock,
        "camera_mock": cam_mock,
        "audio_inputs": [d for d in audio if d["max_input_channels"] > 0],
        "audio_outputs": [d for d in audio if d["max_output_channels"] > 0],
        "cameras": cameras,
    }
