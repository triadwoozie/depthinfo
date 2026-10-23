/**
 * sonar.ts — Shared TypeScript interfaces for Sonar Imaging.
 *
 * SINGLE SOURCE OF TRUTH for data contracts between:
 *   - backend/config.py  (CalibrationState, SonarTelemetry)
 *   - Dashboard components
 *
 * Keep this file in sync with backend/config.py when adding fields.
 */

// ---------------------------------------------------------------------------
// Server → Dashboard  (WebSocket @ settings.ws_hz)
// ---------------------------------------------------------------------------

/** Telemetry frame broadcast from the FastAPI backend over WebSocket. */
export interface SonarTelemetry {
  /** Unix timestamp (seconds) when the reading was produced. */
  timestamp: number

  /**
   * Engine state:
   * - "tracking"   — valid echo peak detected above threshold
   * - "idle"       — no valid peak; holding last EMA value
   * - "autopilot"  — no valid peak for > autopilot_timeout_sec; sweeping demo
   * - "mock"       — synthetic data (SONAR_MOCK_MODE=true)
   * - "mock-idle"  — synthetic data with no simulated peak above threshold
   * - "initialising" — engine not yet ready
   */
  status: "tracking" | "idle" | "autopilot" | "mock" | "mock-idle" | "initialising"

  /** Whether the backend is running in mock mode (no hardware). */
  mock_mode: boolean

  /**
   * Measured depth in metres from the acoustic ToF calculation.
   * Computed as: distance_m = (peak_sample_index / sample_rate) * 343.0 / 2.0
   * Then post-processed with depth_scale_multiplier and depth_zero_offset_cm.
   */
  sonar_depth_m: number

  /** Normalised peak amplitude [0.0–1.0] of the best echo peak. */
  peak_amplitude: number

  /** Vision engine frame rate (FPS). */
  fps_vision: number

  /** Actual WebSocket broadcast rate (Hz). */
  ping_rate_hz: number

  /** Configured sonar ping rate (Hz). */
  fps_sonar_hz?: number

  /** Vertices in the current stitched point cloud / mesh. */
  active_vertex_count?: number

  /** Direction of arrival (multi-mic experiment); null when disabled. */
  doa_azimuth_deg?: number | null

  /** Measured telemetry broadcast rate (Hz). */
  system_fps?: number

  /**
   * Cross-correlation curve — exactly 256 normalised floats [0.0–1.0].
   * Index 0 corresponds to 0.0 m; index 255 corresponds to 4.0 m.
   * Rendered on the CorrelationCanvas via requestAnimationFrame.
   */
  correlation_curve: number[]
}

// ---------------------------------------------------------------------------
// Dashboard → Server  (WebSocket or POST /api/calibration)
// ---------------------------------------------------------------------------

/** Runtime-adjustable calibration parameters. */
export interface CalibrationState {
  // --- Chirp synthesis ---

  /** LFM chirp start frequency (Hz). Range: 8000–22000. */
  chirp_f_start: number

  /** LFM chirp end frequency (Hz). Range: 8000–22050. */
  chirp_f_end: number

  /** Chirp pulse duration (ms). Range: 5–100. */
  chirp_duration_ms: number

  // --- Blanking / gating ---

  /**
   * Zone A blanking distance (cm). All correlation samples within this
   * round-trip range are zeroed to suppress direct acoustic crosstalk.
   * Range: 0–200 cm.
   */
  blanking_distance_cm: number

  // --- Detection ---

  /**
   * Normalised amplitude threshold [0.0–1.0].
   * Peaks below this are treated as noise; depth estimate is held.
   */
  detection_threshold: number

  // --- Depth post-processing ---

  /**
   * Linear scale applied to the raw ToF distance.
   * Use to compensate for speed-of-sound temperature drift.
   */
  depth_scale_multiplier: number

  /** Additive offset (cm) for ground-truth distance alignment. */
  depth_zero_offset_cm: number

  // --- Smoothing ---

  /**
   * Exponential Moving Average factor α [0.01–1.0].
   * Applied as: depth = α × raw + (1 − α) × prev_depth
   * High = fast response; low = heavy smoothing.
   */
  smoothing_alpha: number

  // --- Autopilot / idle ---

  /**
   * Seconds without a valid echo peak before entering autopilot demo mode.
   * Range: 1–300 s.
   */
  autopilot_timeout_sec: number
}

export type WaveformType =
  | "linear_fm_chirp"
  | "log_chirp"
  | "audible_ping"
  | "ultrasonic_sweep"

export type ColorMode =
  | "optical_rgb"
  | "fog_gradient"
  | "acoustic_heatmap"
  | "custom_palette"

export type FogFalloff = "linear" | "exponential" | "squared"

export interface RigPose {
  x_cm: number
  y_cm: number
  z_cm: number
}

export interface OperatorConfig {
  waveform: WaveformType
  speaker_volume: number
  silent_mode: boolean
  ping_rate_hz: number
  scan_min_m: number
  scan_max_m: number
  fog_falloff_curve: FogFalloff
  color_mode: ColorMode
  custom_near_hex: string
  custom_far_hex: string
  point_radius_mm: number
  audio_input_device_id: number | null
  audio_output_device_id: number | null
  camera_device_index: number | null
  rig: {
    camera: RigPose
    speaker: RigPose
    mic: RigPose
  }
}

export interface DeviceInfo {
  id: number
  name: string
  channels?: number
}

/** Raw `GET /api/devices` payload (backend/devices.py). */
export interface RawDevice {
  id?: number
  index?: number
  name: string
  max_input_channels?: number
  max_output_channels?: number
}

export interface RawDeviceList {
  mock?: boolean
  audio_inputs: RawDevice[]
  audio_outputs: RawDevice[]
  cameras: RawDevice[]
}

export interface DeviceList {
  audio_inputs: DeviceInfo[]
  audio_outputs: DeviceInfo[]
  cameras: DeviceInfo[]
}

// ---------------------------------------------------------------------------
// WebSocket message union (for type-narrowing in useSonarSocket)
// ---------------------------------------------------------------------------

/** Raw text payload received from the backend WebSocket. */
export type WsIncomingMessage =
  | SonarTelemetry
  | { type: "calibration_ack"; calibration: CalibrationState }
  | { type: "config_update"; config: SystemConfig }
  | { type: "error"; detail: unknown }

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------

/** Colour tokens used across sonar dashboard components. */
export const SONAR_COLORS = {
  background: "#09090b",      // zinc-950
  surface: "#18181b",         // zinc-900
  border: "#27272a",          // zinc-800
  accent: "#10b981",          // emerald-500
  accentGlow: "#34d399",      // emerald-400
  cyan: "#06b6d4",            // cyan-500
  warning: "#f59e0b",         // amber-500
  danger: "#ef4444",          // red-500
  muted: "#71717a",           // zinc-500
  blankingZone: "rgba(239,68,68,0.15)",   // Zone A overlay
  threshold: "rgba(245,158,11,0.8)",      // threshold line
  curveLine: "#10b981",                   // correlation curve stroke
  peakMarker: "#34d399",                  // echo peak indicator
} as const

/** Map a status string to a badge colour class. */
export function statusToVariant(
  status: SonarTelemetry["status"]
): "default" | "secondary" | "destructive" | "outline" {
  switch (status) {
    case "tracking":
      return "default"
    case "mock":
      return "secondary"
    case "idle":
    case "mock-idle":
      return "outline"
    case "autopilot":
    case "initialising":
      return "destructive"
    default:
      return "outline"
  }
}

/** Default calibration values (matches backend CalibrationState defaults). */
export const DEFAULT_CALIBRATION: CalibrationState = {
  chirp_f_start: 16000,
  chirp_f_end: 20000,
  chirp_duration_ms: 20,
  blanking_distance_cm: 35,
  detection_threshold: 0.12,
  depth_scale_multiplier: 1.0,
  depth_zero_offset_cm: 0.0,
  smoothing_alpha: 0.25,
  autopilot_timeout_sec: 10,
}

export const DEFAULT_OPERATOR_CONFIG: OperatorConfig = {
  waveform: "linear_fm_chirp",
  speaker_volume: 0.65,
  silent_mode: false,
  ping_rate_hz: 10,
  scan_min_m: 0.35,
  scan_max_m: 2.5,
  fog_falloff_curve: "exponential",
  color_mode: "optical_rgb",
  custom_near_hex: "#00F5D4",
  custom_far_hex: "#3A0CA3",
  point_radius_mm: 4,
  audio_input_device_id: null,
  audio_output_device_id: null,
  camera_device_index: null,
  rig: {
    camera: { x_cm: 0, y_cm: 0, z_cm: 0 },
    speaker: { x_cm: -15, y_cm: -10, z_cm: 0 },
    mic: { x_cm: 15, y_cm: -10, z_cm: 0 },
  },
}

// ---------------------------------------------------------------------------
// Backend SystemConfig (mirror of backend/config.py, AGENTS.md § 4.1)
// ---------------------------------------------------------------------------

export interface Pose6 extends RigPose {
  pitch_deg: number
  yaw_deg: number
  roll_deg: number
}

export interface MicrophoneConfig extends RigPose {
  channel: number
  label: string
  polar_pattern: string
}

export interface SystemConfig {
  hardware: {
    input_audio_device_id: number | null
    output_audio_device_id: number | null
    camera_device_index: number
    sample_rate_hz: number
    camera_fov_deg: number
    camera_pose: Pose6
    speaker_pose: Pose6
    microphones: MicrophoneConfig[]
  }
  acoustics: {
    waveform: WaveformType
    freq_start_hz: number
    freq_end_hz: number
    chirp_duration_ms: number
    ping_rate_hz: number
    speaker_volume: number
    mic_gain_db: number
    highpass_cutoff_hz: number
  }
  calibration: {
    blanking_distance_cm: number
    detection_threshold: number
    metric_scale_factor: number
    metric_offset_m: number
    smoothing_alpha: number
    background_fog_captured: boolean
    ambient_noise_floor_rms: number
    calibration_round_completed: number
    autopilot_timeout_sec: number
  }
  vision_and_fog: {
    min_scan_distance_m: number
    max_scan_distance_m: number
    fog_falloff_curve: FogFalloff
    fog_density: number
    fog_colormap: string
    relief_depth_span_m: number
    temporal_stitch_frames: number
    voxel_downsample_m: number
  }
  blender_style: {
    color_mode: ColorMode
    custom_near_hex: string
    custom_far_hex: string
    point_radius_mm: number
    surface_reconstruction_mode: "point_cloud" | "poisson" | "bpa"
    acoustic_wave_distortion: number
  }
  experimental_multi_mic: {
    enabled: boolean
    algorithm: "gcc_phat"
    speed_of_sound_mps: number
    overlay_acoustic_lobes: boolean
  }
}

/** Alias matching AGENTS.md § 4.2 naming. */
export type TelemetryFrame = SonarTelemetry

type DeepPartial<T> = { [K in keyof T]?: T[K] extends object ? DeepPartial<T[K]> : T[K] }
export type SystemConfigPatch = DeepPartial<SystemConfig>

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))
const pose = (p: RigPose): RigPose => ({ x_cm: p.x_cm, y_cm: p.y_cm, z_cm: p.z_cm })

/**
 * The silent-mode switch has no backend field: it is expressed as speaker_volume = 0.
 * `lastVolume` is the volume to show/restore while muted.
 */
export function systemToOperator(sys: SystemConfig, lastVolume: number): OperatorConfig {
  const silent = sys.acoustics.speaker_volume === 0
  const mic = sys.hardware.microphones[0]
  return {
    waveform: sys.acoustics.waveform,
    speaker_volume: silent ? lastVolume : sys.acoustics.speaker_volume,
    silent_mode: silent,
    ping_rate_hz: sys.acoustics.ping_rate_hz,
    scan_min_m: sys.vision_and_fog.min_scan_distance_m,
    scan_max_m: sys.vision_and_fog.max_scan_distance_m,
    fog_falloff_curve: sys.vision_and_fog.fog_falloff_curve,
    color_mode: sys.blender_style.color_mode,
    custom_near_hex: sys.blender_style.custom_near_hex.toUpperCase(),
    custom_far_hex: sys.blender_style.custom_far_hex.toUpperCase(),
    point_radius_mm: sys.blender_style.point_radius_mm,
    audio_input_device_id: sys.hardware.input_audio_device_id,
    audio_output_device_id: sys.hardware.output_audio_device_id,
    camera_device_index: sys.hardware.camera_device_index,
    rig: {
      camera: pose(sys.hardware.camera_pose),
      speaker: pose(sys.hardware.speaker_pose),
      mic: pose(mic ?? DEFAULT_OPERATOR_CONFIG.rig.mic),
    },
  }
}

/** Flat calibration view of a SystemConfig (same mapping as backend `legacy_calibration`). */
export function systemToCalibration(sys: SystemConfig): CalibrationState {
  return {
    chirp_f_start: clamp(Math.round(sys.acoustics.freq_start_hz), 8000, 22000),
    chirp_f_end: clamp(Math.round(sys.acoustics.freq_end_hz), 8000, 22050),
    chirp_duration_ms: clamp(Math.round(sys.acoustics.chirp_duration_ms), 5, 100),
    blanking_distance_cm: sys.calibration.blanking_distance_cm,
    detection_threshold: sys.calibration.detection_threshold,
    depth_scale_multiplier: sys.calibration.metric_scale_factor,
    depth_zero_offset_cm: sys.calibration.metric_offset_m * 100,
    smoothing_alpha: sys.calibration.smoothing_alpha,
    autopilot_timeout_sec: sys.calibration.autopilot_timeout_sec,
  }
}

/**
 * Translate a flat operator patch into a nested SystemConfig patch.
 * `sys` supplies the current microphone list (lists are replaced, not merged, by the backend).
 */
export function operatorPatchToSystem(
  patch: Partial<OperatorConfig>,
  sys: SystemConfig | null,
  lastVolume: number
): SystemConfigPatch {
  const out: SystemConfigPatch = {}
  const set = <S extends keyof SystemConfig>(section: S, values: DeepPartial<SystemConfig[S]>) => {
    out[section] = { ...(out[section] as object), ...values } as SystemConfigPatch[S]
  }

  if (patch.waveform !== undefined) set("acoustics", { waveform: patch.waveform })
  if (patch.ping_rate_hz !== undefined) set("acoustics", { ping_rate_hz: patch.ping_rate_hz })
  if (patch.silent_mode !== undefined) {
    set("acoustics", { speaker_volume: patch.silent_mode ? 0 : (patch.speaker_volume ?? lastVolume) })
  } else if (patch.speaker_volume !== undefined) {
    set("acoustics", { speaker_volume: patch.speaker_volume })
  }

  if (patch.scan_min_m !== undefined) set("vision_and_fog", { min_scan_distance_m: patch.scan_min_m })
  if (patch.scan_max_m !== undefined) set("vision_and_fog", { max_scan_distance_m: patch.scan_max_m })
  if (patch.fog_falloff_curve !== undefined) {
    set("vision_and_fog", { fog_falloff_curve: patch.fog_falloff_curve })
  }

  if (patch.color_mode !== undefined) set("blender_style", { color_mode: patch.color_mode })
  if (patch.custom_near_hex !== undefined) set("blender_style", { custom_near_hex: patch.custom_near_hex })
  if (patch.custom_far_hex !== undefined) set("blender_style", { custom_far_hex: patch.custom_far_hex })
  if (patch.point_radius_mm !== undefined) set("blender_style", { point_radius_mm: patch.point_radius_mm })

  const hardware: DeepPartial<SystemConfig["hardware"]> = {}
  if (patch.audio_input_device_id !== undefined) hardware.input_audio_device_id = patch.audio_input_device_id
  if (patch.audio_output_device_id !== undefined) hardware.output_audio_device_id = patch.audio_output_device_id
  if (patch.camera_device_index !== undefined && patch.camera_device_index !== null) {
    hardware.camera_device_index = patch.camera_device_index
  }
  if (patch.rig?.camera) hardware.camera_pose = pose(patch.rig.camera)
  if (patch.rig?.speaker) hardware.speaker_pose = pose(patch.rig.speaker)
  if (patch.rig?.mic && sys?.hardware.microphones.length) {
    const [first, ...rest] = sys.hardware.microphones
    hardware.microphones = [{ ...first, ...pose(patch.rig.mic) }, ...rest]
  }
  if (Object.keys(hardware).length) set("hardware", hardware)
  return out
}

/** `GET /api/devices` → the id/name lists the selectors use (cameras are keyed by `index`). */
export function normalizeDevices(raw: RawDeviceList): DeviceList {
  const map = (devices: RawDevice[], channelKey?: "max_input_channels" | "max_output_channels"): DeviceInfo[] =>
    devices.map((d) => ({
      id: d.id ?? d.index ?? 0,
      name: d.name,
      channels: channelKey ? d[channelKey] : undefined,
    }))
  return {
    audio_inputs: map(raw.audio_inputs ?? [], "max_input_channels"),
    audio_outputs: map(raw.audio_outputs ?? [], "max_output_channels"),
    cameras: map(raw.cameras ?? []),
  }
}
