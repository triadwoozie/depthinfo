"""
sonar_engine.py — LFM Chirp Generation & Matched-Filter Cross-Correlation DSP.

Runs in a dedicated background thread to avoid blocking the FastAPI event loop.

MOCK MODE
---------
When settings.mock_mode is True (or env SONAR_MOCK_MODE=true), no real audio
hardware is used. Instead, the engine synthesizes:
  - A crosstalk spike at indices 0–20 (direct bleed)
  - Gaussian white noise floor at σ ≈ 0.03
  - A moving echo peak that oscillates between 0.5 m and 2.5 m at 0.15 Hz

The 256-float output array is normalised to [0.0, 1.0] in both real and mock
modes, so downstream consumers (WebSocket, Blender bridge) are identical.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from config import CalibrationState, settings

# Lazily imported to avoid ImportError in mock mode when sounddevice not installed
try:
    import sounddevice as sd  # type: ignore
    _SD_AVAILABLE = True
except ImportError:
    _SD_AVAILABLE = False

try:
    from scipy.signal import correlate, resample  # type: ignore
    _SCIPY_AVAILABLE = True
except ImportError:
    _SCIPY_AVAILABLE = False

# Number of floats in the downsampled correlation curve sent to clients
CURVE_BINS = 256
# Speed of sound in m/s at ~20 °C
SPEED_OF_SOUND = 343.0


@dataclass
class SonarReading:
    """Result produced by one chirp cycle."""

    timestamp: float
    sonar_depth_m: float
    peak_amplitude: float
    correlation_curve: list[float]  # exactly CURVE_BINS floats, normalised [0.0, 1.0]
    status: str  # "tracking" | "idle" | "mock"


class SonarEngine:
    """
    Full-duplex acoustic ToF ranging engine.

    Usage::

        engine = SonarEngine()
        engine.start()
        reading = engine.latest_reading   # thread-safe property

    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._latest: SonarReading = self._zero_reading()
        self._last_valid_depth: float = 1.0
        self._last_track_time: float = time.monotonic()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background chirp loop."""
        if self._running:
            return
        self._running = True
        target = self._mock_loop if settings.mock_mode else self._hardware_loop
        self._thread = threading.Thread(target=target, daemon=True, name="sonar-engine")
        self._thread.start()

    def stop(self) -> None:
        """Signal the background loop to stop and wait for it."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    @property
    def latest_reading(self) -> SonarReading:
        with self._lock:
            return self._latest

    # ------------------------------------------------------------------
    # Chirp synthesis helpers
    # ------------------------------------------------------------------

    def _make_chirp(self, cal: CalibrationState) -> np.ndarray:
        """
        Generate a normalised LFM (linear frequency-modulated) chirp.

        Returns a float32 array of length = sample_rate * chirp_duration_ms / 1000.
        """
        sr = settings.sample_rate
        duration = cal.chirp_duration_ms / 1000.0
        t = np.linspace(0, duration, int(sr * duration), endpoint=False, dtype=np.float32)
        # Instantaneous phase: φ(t) = 2π [ f0*t + (f1-f0)/(2*T) * t² ]
        phase = 2 * np.pi * (
            cal.chirp_f_start * t
            + (cal.chirp_f_end - cal.chirp_f_start) / (2 * duration) * t ** 2
        )
        chirp = np.sin(phase)
        # Hann window to suppress sidelobes
        chirp *= np.hanning(len(t)).astype(np.float32)
        return chirp

    # ------------------------------------------------------------------
    # DSP helpers
    # ------------------------------------------------------------------

    def _apply_blanking(self, corr: np.ndarray, cal: CalibrationState) -> np.ndarray:
        """
        Zero-out Zone A (direct acoustic bleed / crosstalk) in the raw
        cross-correlation array.
        """
        sr = settings.sample_rate
        # Convert blanking_distance_cm to a round-trip sample count
        blank_samples = int((cal.blanking_distance_cm / 100.0) / SPEED_OF_SOUND * 2 * sr)
        corr[:blank_samples] = 0.0
        return corr

    def _find_peak(
        self,
        corr: np.ndarray,
        cal: CalibrationState,
    ) -> tuple[float, float]:
        """
        Locate the highest peak beyond the blanking zone.

        Returns (depth_m, normalised_amplitude).
        """
        if len(corr) == 0 or corr.max() == 0:
            return 0.0, 0.0

        peak_idx = int(np.argmax(corr))
        peak_amp = float(corr[peak_idx])

        if peak_amp < cal.detection_threshold:
            return 0.0, peak_amp  # below noise floor

        # Round-trip time → one-way distance
        depth_raw = (peak_idx / settings.sample_rate) * SPEED_OF_SOUND / 2.0
        # Apply post-processing
        depth_m = (depth_raw + cal.depth_zero_offset_cm / 100.0) * cal.depth_scale_multiplier
        return max(0.0, depth_m), peak_amp

    def _ema(self, new_val: float, cal: CalibrationState) -> float:
        α = cal.smoothing_alpha
        smoothed = α * new_val + (1 - α) * self._last_valid_depth
        self._last_valid_depth = smoothed
        return smoothed

    def _downsample_curve(self, corr: np.ndarray) -> list[float]:
        """
        Downsample and normalise the raw correlation array to exactly CURVE_BINS
        floats in [0.0, 1.0].
        """
        if len(corr) == 0:
            return [0.0] * CURVE_BINS

        if _SCIPY_AVAILABLE:
            from scipy.signal import resample as sp_resample
            ds = sp_resample(corr, CURVE_BINS).astype(np.float32)
        else:
            # Fallback: linear interpolation
            indices = np.linspace(0, len(corr) - 1, CURVE_BINS)
            ds = np.interp(indices, np.arange(len(corr)), corr).astype(np.float32)

        ds = np.clip(ds, 0.0, None)
        max_val = ds.max()
        if max_val > 0:
            ds /= max_val
        return ds.tolist()

    # ------------------------------------------------------------------
    # Hardware loop
    # ------------------------------------------------------------------

    def _hardware_loop(self) -> None:
        """
        Real audio loop using sounddevice.playrec().

        Continuously plays the chirp and records the microphone simultaneously,
        then computes matched-filter cross-correlation on the result.
        """
        if not _SD_AVAILABLE:
            print("[SonarEngine] sounddevice not available — falling back to mock mode.")
            self._mock_loop()
            return
        if not _SCIPY_AVAILABLE:
            print("[SonarEngine] scipy not available — falling back to mock mode.")
            self._mock_loop()
            return

        sr = settings.sample_rate

        while self._running:
            from config import calibration as cal

            chirp = self._make_chirp(cal)
            n_frames = len(chirp)

            try:
                # Full-duplex: play chirp, simultaneously record
                recording = sd.playrec(
                    chirp.reshape(-1, 1),
                    samplerate=sr,
                    channels=1,
                    device=settings.audio_device_index,
                    dtype="float32",
                )
                sd.wait()
                recorded = recording[:, 0].astype(np.float64)
            except Exception as exc:
                print(f"[SonarEngine] Audio I/O error: {exc}. Retrying in 1 s.")
                time.sleep(1.0)
                continue

            # Matched filter: cross-correlate recorded signal with chirp template
            corr = correlate(recorded, chirp.astype(np.float64), mode="full")
            # Take only the causal (positive-lag) half
            corr = np.abs(corr[len(chirp) - 1:])

            corr = self._apply_blanking(corr, cal)
            depth_raw, peak_amp = self._find_peak(corr, cal)

            now = time.monotonic()
            if peak_amp >= cal.detection_threshold:
                depth_m = self._ema(depth_raw, cal)
                self._last_track_time = now
                status = "tracking"
            else:
                idle_sec = now - self._last_track_time
                if idle_sec > cal.autopilot_timeout_sec:
                    # Gentle autopilot sweep
                    depth_m = 1.0 + 0.8 * math.sin(now * 0.3)
                    status = "autopilot"
                else:
                    depth_m = self._last_valid_depth
                    status = "idle"

            curve = self._downsample_curve(corr / (corr.max() + 1e-9))

            reading = SonarReading(
                timestamp=time.time(),
                sonar_depth_m=round(depth_m, 4),
                peak_amplitude=round(peak_amp, 4),
                correlation_curve=curve,
                status=status,
            )
            with self._lock:
                self._latest = reading

    # ------------------------------------------------------------------
    # Mock loop (no hardware required)
    # ------------------------------------------------------------------

    def _mock_loop(self) -> None:
        """
        Synthetic chirp loop for development / CI without physical hardware.

        Produces a realistic-looking correlation curve:
          - Zone A spike (crosstalk) at indices 0–20
          - Gaussian noise floor (σ = 0.03)
          - A moving echo peak oscillating between 0.5 m and 2.5 m at 0.15 Hz
        """
        rng = np.random.default_rng(42)
        interval = 1.0 / max(settings.ws_hz, 15.0)

        while self._running:
            from config import calibration as cal

            t = time.monotonic()

            # Synthesize correlation array at full sample resolution (shortened for mock)
            n = 4096  # representative length
            corr = rng.normal(0, 0.03, n).clip(0, None).astype(np.float32)

            # Zone A: crosstalk spike at sample 0–20
            blank_samples = min(20, n // 10)
            corr[:blank_samples] += np.linspace(0.9, 0.1, blank_samples)

            # Zone B: moving echo peak
            target_dist_m = 1.5 + 1.0 * math.sin(2 * math.pi * 0.15 * t)
            target_idx = int((target_dist_m * 2.0 / SPEED_OF_SOUND) * settings.sample_rate)
            target_idx = min(target_idx, n - 30)
            # Gaussian echo pulse
            width = 8
            for i in range(max(0, target_idx - width), min(n, target_idx + width)):
                corr[i] += 0.85 * math.exp(-((i - target_idx) ** 2) / (2 * (width / 3) ** 2))

            # Apply blanking (force Zone A to 0 in output)
            corr = self._apply_blanking(corr, cal)

            depth_raw, peak_amp = self._find_peak(corr / (corr.max() + 1e-9), cal)
            if peak_amp >= cal.detection_threshold:
                depth_m = self._ema(depth_raw, cal)
                status = "mock"
            else:
                depth_m = self._last_valid_depth
                status = "mock-idle"

            curve = self._downsample_curve(corr / (corr.max() + 1e-9))

            reading = SonarReading(
                timestamp=time.time(),
                sonar_depth_m=round(depth_m, 4),
                peak_amplitude=round(float(corr.max()), 4),
                correlation_curve=curve,
                status=status,
            )
            with self._lock:
                self._latest = reading

            time.sleep(interval)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _zero_reading() -> SonarReading:
        return SonarReading(
            timestamp=time.time(),
            sonar_depth_m=0.0,
            peak_amplitude=0.0,
            correlation_curve=[0.0] * CURVE_BINS,
            status="initialising",
        )
