"""
config.py — Pydantic V2 settings & persisted SystemConfig for Sonar Imaging.

Two layers:

* ``SonarSettings``  — immutable startup settings from the environment / .env.
* ``SystemConfig``   — the master runtime schema from AGENTS.md § 4.1, persisted to
                       ``backend/config_state.json`` on every update.

The dashboard's original flat ``CalibrationState`` (dashboard/src/types/sonar.ts)
is kept as a thin compatibility view over ``SystemConfig`` so existing WebSocket
clients keep working. New code should use ``config_store`` / ``SystemConfig``.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings

BACKEND_DIR = Path(__file__).resolve().parent
REPO_DIR = BACKEND_DIR.parent


# ---------------------------------------------------------------------------
# Immutable startup settings (read from environment / .env)
# ---------------------------------------------------------------------------

class SonarSettings(BaseSettings):
    """Startup configuration loaded from environment variables."""

    mock_mode: bool = Field(default=False, alias="SONAR_MOCK_MODE")
    sample_rate: int = Field(default=48000, alias="SONAR_SAMPLE_RATE")
    audio_device_index: int | None = Field(default=None, alias="SONAR_AUDIO_DEVICE")
    udp_host: str = Field(default="127.0.0.1", alias="SONAR_UDP_HOST")
    udp_port: int = Field(default=5005, alias="SONAR_UDP_PORT")
    udp_hz: float = Field(default=60.0, alias="SONAR_UDP_HZ")
    ws_hz: float = Field(default=20.0, alias="SONAR_WS_HZ")
    camera_index: int = Field(default=0, alias="SONAR_CAMERA_INDEX")
    depth_model_path: str = Field(
        default="models/depth_anything_v2_vits.onnx", alias="DEPTH_MODEL_PATH"
    )
    depth_auto_download: bool = Field(default=False, alias="DEPTH_AUTO_DOWNLOAD")
    real_camera: bool = Field(
        default=False,
        alias="SONAR_REAL_CAMERA",
        description="Use the physical camera even when SONAR_MOCK_MODE=true (mock sonar, real video).",
    )
    exports_dir: str = Field(default="exports", alias="EXPORTS_DIR")
    blender_bin: str | None = Field(default=None, alias="BLENDER_BIN")

    model_config = {"populate_by_name": True, "env_file": ".env", "extra": "ignore"}

    @property
    def depth_model_file(self) -> Path:
        p = Path(self.depth_model_path)
        return p if p.is_absolute() else BACKEND_DIR / p

    @property
    def exports_path(self) -> Path:
        p = Path(self.exports_dir)
        if p.is_absolute():
            return p
        # "backend/exports" (AGENTS.md default) is repo-relative, "exports" backend-relative.
        return REPO_DIR / p if p.parts and p.parts[0] == "backend" else BACKEND_DIR / p


settings = SonarSettings()


# ---------------------------------------------------------------------------
# SystemConfig (AGENTS.md § 4.1)
# ---------------------------------------------------------------------------

class _Model(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra="ignore")


class Pose(_Model):
    x_cm: float = 0.0
    y_cm: float = 0.0
    z_cm: float = 0.0
    pitch_deg: float = 0.0
    yaw_deg: float = 0.0
    roll_deg: float = 0.0


class Microphone(_Model):
    channel: int = 0
    label: str = "Main Studio Condenser"
    x_cm: float = 15.0
    y_cm: float = -10.0
    z_cm: float = 0.0
    polar_pattern: str = "cardioid"


class HardwareConfig(_Model):
    # Device names are stable hints; PortAudio integer IDs can change between boots.
    input_audio_device_id: int | None = None
    output_audio_device_id: int | None = None
    input_audio_device_name: str | None = None
    output_audio_device_name: str | None = None
    camera_device_index: int = 0
    sample_rate_hz: int = Field(default=48000, ge=8000, le=192000)
    camera_fov_deg: float = Field(default=65.0, gt=10.0, lt=170.0)
    camera_pose: Pose = Field(default_factory=Pose)
    speaker_pose: Pose = Field(default_factory=lambda: Pose(x_cm=-15.0, y_cm=-10.0))
    microphones: list[Microphone] = Field(default_factory=lambda: [Microphone()])


Waveform = Literal["linear_fm_chirp", "log_chirp", "audible_ping", "ultrasonic_sweep"]


class AcousticsConfig(_Model):
    waveform: Waveform = "linear_fm_chirp"
    freq_start_hz: float = Field(default=16000.0, ge=100.0, le=96000.0)
    freq_end_hz: float = Field(default=20000.0, ge=100.0, le=96000.0)
    chirp_duration_ms: float = Field(default=20.0, ge=1.0, le=200.0)
    ping_rate_hz: float = Field(default=10.0, ge=0.5, le=30.0)
    speaker_volume: float = Field(default=0.65, ge=0.0, le=1.0)
    mic_gain_db: float = Field(default=6.0, ge=-24.0, le=60.0)
    highpass_cutoff_hz: float = Field(default=1000.0, ge=0.0, le=20000.0)


class CalibrationConfig(_Model):
    blanking_distance_cm: float = Field(default=38.0, ge=0.0, le=200.0)
    detection_threshold: float = Field(default=0.14, ge=0.0, le=1.0)
    metric_scale_factor: float = Field(default=1.0, ge=0.1, le=5.0)
    metric_offset_m: float = Field(default=0.0, ge=-2.0, le=2.0)
    smoothing_alpha: float = Field(default=0.25, ge=0.01, le=1.0)
    background_fog_captured: bool = False
    ambient_noise_floor_rms: float = Field(default=0.012, ge=0.0)
    calibration_round_completed: int = Field(default=0, ge=0, le=4)
    autopilot_timeout_sec: int = Field(default=10, ge=1, le=300)


FalloffCurve = Literal["linear", "exponential", "squared"]
FogColormap = Literal["mist_grayscale", "thermal", "viridis", "custom_hex"]
DepthMode = Literal["disabled", "lightweight_fast", "neural_onnx"]


class VisionAndFogConfig(_Model):
    min_scan_distance_m: float = Field(default=0.35, ge=0.0, le=10.0)
    max_scan_distance_m: float = Field(default=2.50, ge=0.1, le=10.0)
    # linear keeps uniform contrast across the foreground (exponential flattens the near field)
    fog_falloff_curve: FalloffCurve = "linear"
    fog_density: float = Field(default=1.2, ge=0.0, le=10.0)
    fog_colormap: FogColormap = "mist_grayscale"
    # Front-to-back depth of the subject (nose tip to back of head): 0.35 to 0.50 m for a person.
    relief_depth_span_m: float = Field(default=0.45, ge=0.0, le=5.0)
    # Subject isolation (stitcher): drop points whose fog intensity (0..1) is at or below this ...
    fog_discard_threshold: float = Field(default=0.12, ge=0.0, le=1.0)
    # ... and points more than this far behind the sonar anchor. The hybrid model only places
    # background about one relief span behind the subject, so a pure max-distance gate cannot
    # remove a wall that is nearer than max_scan_distance_m.
    subject_thickness_m: float = Field(default=0.30, ge=0.05, le=2.0)
    # CLAHE on the normalised neural disparity to bring out facial topology.
    depth_contrast_enhance: bool = True
    # disabled: flat plane at Z_sonar | lightweight_fast: CPU heuristic | neural_onnx: Depth-Anything-V2
    depth_inference_mode: DepthMode = "lightweight_fast"
    # Half-angle of the acoustic beam cone whose median relative depth anchors the relief to Z_sonar.
    acoustic_beam_deg: float = Field(default=20.0, ge=2.0, le=60.0)
    # Which percentile of D_rel inside the beam cone is treated as "the subject" and pinned to
    # Z_sonar. The sonar returns the NEAREST reflector, so this must lean to the near side: the
    # median (50) lands on the background whenever the subject fills less than half the cone, and
    # even 25 does if it fills less than a quarter (a head is a few percent of a 20 degree cone).
    anchor_percentile: float = Field(default=5.0, ge=0.5, le=50.0)
    temporal_stitch_frames: int = Field(default=5, ge=1, le=60)
    voxel_downsample_m: float = Field(default=0.01, ge=0.001, le=0.5)

    @model_validator(mode="after")
    def _range_ordered(self) -> "VisionAndFogConfig":
        if self.min_scan_distance_m >= self.max_scan_distance_m:
            raise ValueError("min_scan_distance_m must be below max_scan_distance_m")
        return self


ColorMode = Literal["optical_rgb", "fog_gradient", "acoustic_heatmap", "custom_palette"]
ReconMode = Literal["point_cloud", "poisson", "bpa"]


class BlenderStyleConfig(_Model):
    color_mode: ColorMode = "optical_rgb"
    custom_near_hex: str = Field(default="#00F5D4", pattern=r"^#[0-9a-fA-F]{6}$")
    custom_far_hex: str = Field(default="#3A0CA3", pattern=r"^#[0-9a-fA-F]{6}$")
    point_radius_mm: float = Field(default=4.0, gt=0.0, le=100.0)
    surface_reconstruction_mode: ReconMode = "point_cloud"
    acoustic_wave_distortion: float = Field(default=0.35, ge=0.0, le=1.0)


class ExperimentalMultiMicConfig(_Model):
    enabled: bool = False
    algorithm: Literal["gcc_phat"] = "gcc_phat"
    speed_of_sound_mps: float = Field(default=343.2, gt=300.0, lt=400.0)
    overlay_acoustic_lobes: bool = True


class SystemConfig(_Model):
    hardware: HardwareConfig = Field(default_factory=HardwareConfig)
    acoustics: AcousticsConfig = Field(default_factory=AcousticsConfig)
    calibration: CalibrationConfig = Field(default_factory=CalibrationConfig)
    vision_and_fog: VisionAndFogConfig = Field(default_factory=VisionAndFogConfig)
    blender_style: BlenderStyleConfig = Field(default_factory=BlenderStyleConfig)
    experimental_multi_mic: ExperimentalMultiMicConfig = Field(
        default_factory=ExperimentalMultiMicConfig
    )


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, val in patch.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


class ConfigStore:
    """Thread-safe holder for the live ``SystemConfig`` with JSON persistence."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._config = self._load()

    def _load(self) -> SystemConfig:
        try:
            return SystemConfig.model_validate(json.loads(self.path.read_text()))
        except FileNotFoundError:
            return SystemConfig()
        except Exception as exc:  # corrupt / outdated file must never block startup
            print(f"[config] Ignoring unreadable {self.path.name}: {exc}")
            return SystemConfig()

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(self._config.model_dump_json(indent=2))
        tmp.replace(self.path)

    @property
    def config(self) -> SystemConfig:
        with self._lock:
            return self._config

    def patch(self, partial: dict[str, Any]) -> SystemConfig:
        """Deep-merge ``partial`` into the config, validate, persist, return it.

        Raises ``pydantic.ValidationError`` if the merged result is invalid; the
        stored config is left untouched in that case.
        """
        with self._lock:
            merged = _deep_merge(self._config.model_dump(), partial)
            self._config = SystemConfig.model_validate(merged)
            self._save()
            return self._config


config_store = ConfigStore(BACKEND_DIR / "config_state.json")


def get_config() -> SystemConfig:
    return config_store.config


# ---------------------------------------------------------------------------
# Legacy flat CalibrationState (dashboard/src/types/sonar.ts) — compatibility view
# ---------------------------------------------------------------------------

class CalibrationState(BaseModel):
    """Flat calibration payload used by the original dashboard."""

    chirp_f_start: int = Field(default=16000, ge=8000, le=22000)
    chirp_f_end: int = Field(default=20000, ge=8000, le=22050)
    chirp_duration_ms: int = Field(default=20, ge=5, le=100)
    blanking_distance_cm: float = Field(default=35.0, ge=0.0, le=200.0)
    detection_threshold: float = Field(default=0.12, ge=0.0, le=1.0)
    depth_scale_multiplier: float = Field(default=1.0, ge=0.1, le=5.0)
    depth_zero_offset_cm: float = Field(default=0.0, ge=-200.0, le=200.0)
    smoothing_alpha: float = Field(default=0.25, ge=0.01, le=1.0)
    autopilot_timeout_sec: int = Field(default=10, ge=1, le=300)


def legacy_calibration() -> CalibrationState:
    c = get_config()
    return CalibrationState(
        chirp_f_start=int(min(max(c.acoustics.freq_start_hz, 8000), 22000)),
        chirp_f_end=int(min(max(c.acoustics.freq_end_hz, 8000), 22050)),
        chirp_duration_ms=int(min(max(c.acoustics.chirp_duration_ms, 5), 100)),
        blanking_distance_cm=c.calibration.blanking_distance_cm,
        detection_threshold=c.calibration.detection_threshold,
        depth_scale_multiplier=c.calibration.metric_scale_factor,
        depth_zero_offset_cm=c.calibration.metric_offset_m * 100.0,
        smoothing_alpha=c.calibration.smoothing_alpha,
        autopilot_timeout_sec=c.calibration.autopilot_timeout_sec,
    )


def apply_calibration_update(data: dict[str, Any]) -> CalibrationState:
    """Merge a partial *legacy flat* calibration dict into the SystemConfig."""
    merged = {**legacy_calibration().model_dump(), **data}
    flat = CalibrationState.model_validate(merged)
    config_store.patch(
        {
            "acoustics": {
                "freq_start_hz": float(flat.chirp_f_start),
                "freq_end_hz": float(flat.chirp_f_end),
                "chirp_duration_ms": float(flat.chirp_duration_ms),
            },
            "calibration": {
                "blanking_distance_cm": flat.blanking_distance_cm,
                "detection_threshold": flat.detection_threshold,
                "metric_scale_factor": flat.depth_scale_multiplier,
                "metric_offset_m": flat.depth_zero_offset_cm / 100.0,
                "smoothing_alpha": flat.smoothing_alpha,
                "autopilot_timeout_sec": flat.autopilot_timeout_sec,
            },
        }
    )
    return legacy_calibration()


LEGACY_KEYS = frozenset(CalibrationState.model_fields)
SYSTEM_KEYS = frozenset(SystemConfig.model_fields)
