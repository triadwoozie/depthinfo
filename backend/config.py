"""
config.py — Pydantic settings & mutable calibration state for Sonar Imaging.

All calibration fields are the single source of truth that must match
the TypeScript CalibrationState interface in dashboard/src/types/sonar.ts.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Immutable startup settings (read from environment / .env)
# ---------------------------------------------------------------------------

class SonarSettings(BaseSettings):
    """
    Startup configuration loaded from environment variables.
    These values are fixed at process start and cannot be changed at runtime.
    """

    # Hardware / mock
    mock_mode: bool = Field(
        default=False,
        alias="SONAR_MOCK_MODE",
        description="If True, synthesize audio and vision data (no hardware required).",
    )

    # Audio
    sample_rate: int = Field(
        default=44100,
        alias="SONAR_SAMPLE_RATE",
        description="Audio sample rate in Hz.",
    )
    audio_device_index: int | None = Field(
        default=None,
        alias="SONAR_AUDIO_DEVICE",
        description="sounddevice device index. None = system default.",
    )

    # UDP (Blender bridge)
    udp_host: str = Field(
        default="127.0.0.1",
        alias="SONAR_UDP_HOST",
    )
    udp_port: int = Field(
        default=5005,
        alias="SONAR_UDP_PORT",
    )
    udp_hz: float = Field(
        default=30.0,
        alias="SONAR_UDP_HZ",
        description="Target UDP send rate to Blender in Hz.",
    )

    # WebSocket telemetry
    ws_hz: float = Field(
        default=15.0,
        alias="SONAR_WS_HZ",
        description="Target WebSocket broadcast rate to Dashboard in Hz.",
    )

    # Vision
    camera_index: int = Field(
        default=0,
        alias="SONAR_CAMERA_INDEX",
        description="OpenCV camera device index.",
    )

    model_config = {"populate_by_name": True, "env_file": ".env", "extra": "ignore"}


# ---------------------------------------------------------------------------
# Mutable calibration state (can be updated at runtime via WS / REST)
# ---------------------------------------------------------------------------

class CalibrationState(BaseModel):
    """
    Runtime-adjustable calibration parameters.

    These are serialised to/from JSON and sent bidirectionally between the
    Dashboard and the Backend. They MUST match the CalibrationState interface
    in dashboard/src/types/sonar.ts.
    """

    # Chirp synthesis
    chirp_f_start: int = Field(
        default=16000,
        ge=8000,
        le=22000,
        description="LFM chirp start frequency in Hz.",
    )
    chirp_f_end: int = Field(
        default=20000,
        ge=8000,
        le=22050,
        description="LFM chirp end frequency in Hz.",
    )
    chirp_duration_ms: int = Field(
        default=20,
        ge=5,
        le=100,
        description="Chirp duration in milliseconds.",
    )

    # Blanking / gating
    blanking_distance_cm: float = Field(
        default=35.0,
        ge=0.0,
        le=200.0,
        description=(
            "Zone A: direct acoustic bleed distance. Correlation samples inside "
            "this range are forced to 0.0 to suppress crosstalk."
        ),
    )

    # Detection
    detection_threshold: float = Field(
        default=0.12,
        ge=0.0,
        le=1.0,
        description=(
            "Normalised amplitude threshold [0–1]. Peaks below this are "
            "considered noise and do not update the depth estimate."
        ),
    )

    # Depth post-processing
    depth_scale_multiplier: float = Field(
        default=1.0,
        ge=0.1,
        le=5.0,
        description="Linear scale applied to the raw ToF distance before output.",
    )
    depth_zero_offset_cm: float = Field(
        default=0.0,
        ge=-200.0,
        le=200.0,
        description="Additive offset in cm applied after scaling (for ground-truth alignment).",
    )

    # Smoothing
    smoothing_alpha: float = Field(
        default=0.25,
        ge=0.01,
        le=1.0,
        description=(
            "EMA smoothing factor α. Higher = faster response, lower = smoother. "
            "Applied as: depth = α * raw + (1 - α) * prev_depth"
        ),
    )

    # Autopilot / idle
    autopilot_timeout_sec: int = Field(
        default=10,
        ge=1,
        le=300,
        description=(
            "Seconds without a valid echo peak before the system enters autopilot "
            "idle mode and broadcasts a sweeping demo depth."
        ),
    )


# ---------------------------------------------------------------------------
# Module-level singletons (imported by other modules)
# ---------------------------------------------------------------------------

settings = SonarSettings()

# Shared mutable calibration state — updated by WebSocket / REST handlers
calibration = CalibrationState()


def apply_calibration_update(data: dict) -> CalibrationState:
    """
    Merge a partial dict of calibration fields into the shared calibration
    state. Unknown or invalid fields are silently ignored via model_validate.

    Returns the updated CalibrationState.
    """
    global calibration
    merged = {**calibration.model_dump(), **data}
    calibration = CalibrationState.model_validate(merged)
    return calibration
