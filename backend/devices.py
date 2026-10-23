"""Audio and camera device enumeration for the dashboard."""

from __future__ import annotations

from config import settings


def enumerate_devices() -> dict:
    if settings.mock_mode:
        return {
            "audio_inputs": [
                {"id": 0, "name": "Virtual Studio Microphone", "channels": 2}
            ],
            "audio_outputs": [
                {"id": 0, "name": "Virtual Studio Speaker", "channels": 2}
            ],
            "cameras": [{"id": 0, "name": "Procedural Camera"}],
        }

    audio_inputs: list[dict] = []
    audio_outputs: list[dict] = []
    cameras: list[dict] = []

    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("Audio device enumeration requires sounddevice") from exc
    try:
        for device_id, device in enumerate(sd.query_devices()):
            if device["max_input_channels"] > 0:
                audio_inputs.append(
                    {
                        "id": device_id,
                        "name": device["name"],
                        "channels": device["max_input_channels"],
                    }
                )
            if device["max_output_channels"] > 0:
                audio_outputs.append(
                    {
                        "id": device_id,
                        "name": device["name"],
                        "channels": device["max_output_channels"],
                    }
                )
    except sd.PortAudioError as exc:
        raise RuntimeError(f"Audio device enumeration failed: {exc}") from exc

    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("Camera device enumeration requires OpenCV") from exc
    try:
        for device_id in range(10):
            capture = cv2.VideoCapture(device_id)
            try:
                if capture.isOpened():
                    cameras.append({"id": device_id, "name": f"Camera {device_id}"})
            finally:
                capture.release()
    except cv2.error as exc:
        raise RuntimeError(f"Camera device enumeration failed: {exc}") from exc

    return {
        "audio_inputs": audio_inputs,
        "audio_outputs": audio_outputs,
        "cameras": cameras,
    }
