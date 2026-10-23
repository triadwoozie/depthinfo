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

// ---------------------------------------------------------------------------
// WebSocket message union (for type-narrowing in useSonarSocket)
// ---------------------------------------------------------------------------

/** Raw text payload received from the backend WebSocket. */
export type WsIncomingMessage =
  | SonarTelemetry
  | { type: "calibration_ack"; calibration: CalibrationState }

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
