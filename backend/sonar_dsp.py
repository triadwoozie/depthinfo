"""
sonar_dsp.py — Active acoustic sonar: waveform synthesis, matched filtering,
bi-static depth, calibration rounds 1 & 2 (AGENTS.md § 3.1, § 5).

Threading (AGENTS.md Rule 1): all audio I/O and DSP run in a daemon thread
(``SonarDSP``); calibration routines are blocking functions meant to be called
via ``run_in_executor``. They take ``_io_lock`` so they never overlap with the
live ping loop.

Axis convention: the 256-bin ``correlation_curve`` and ``blanking_distance_cm``
share one axis — the one-way acoustic range ``r = L / 2`` (L = total path
length), 0.0 m → 4.0 m. This is what the dashboard oscilloscope draws.
The curve is *not* blanked (so the crosstalk spike stays visible under the red
blanking overlay) and is normalised by the strongest pre-blanking peak, which
is the same scale ``detection_threshold`` is compared on.

MOCK MODE: no audio hardware is touched; a physically plausible correlation
array (crosstalk spike, Gaussian noise σ = 0.03, echo oscillating 0.5–2.5 m at
0.15 Hz) goes through the *same* analysis code as real recordings.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from config import AcousticsConfig, SystemConfig, get_config, settings

try:
    import sounddevice as sd  # type: ignore
except Exception:  # missing module or missing libportaudio
    sd = None

from scipy import signal as sp_signal

CURVE_BINS = 256
CURVE_RANGE_M = 4.0
MAX_RANGE_M = 4.5  # recording window covers echoes out to this one-way range
BLANK_MARGIN_M = 0.15  # Round 1 safety margin


# ---------------------------------------------------------------------------
# Waveform synthesis
# ---------------------------------------------------------------------------

def synthesize_waveform(ac: AcousticsConfig, fs: int) -> np.ndarray:
    """Hanning-windowed, soft-clipped probe signal (float32) per ``ac.waveform``.

    x[n] = tanh(V · w[n] · sin(phase[n]))
    """
    dur = ac.chirp_duration_ms / 1000.0
    n = max(8, int(dur * fs))
    t = np.arange(n) / fs
    nyq_guard = 0.47 * fs
    f0, f1 = ac.freq_start_hz, ac.freq_end_hz

    if ac.waveform == "linear_fm_chirp":
        f0, f1 = min(f0, nyq_guard), min(f1, nyq_guard)
        phase = 2 * np.pi * (f0 * t + (f1 - f0) / (2 * dur) * t**2)
    elif ac.waveform == "log_chirp":
        f0, f1 = max(min(f0, nyq_guard), 20.0), max(min(f1, nyq_guard), 20.0)
        if math.isclose(f0, f1):
            phase = 2 * np.pi * f0 * t
        else:
            k = (f1 / f0) ** (1.0 / dur)
            phase = 2 * np.pi * f0 * (k**t - 1.0) / math.log(k)
    elif ac.waveform == "audible_ping":
        # Short tone burst in the audible band (operator-friendly debugging aid).
        phase = 2 * np.pi * 2500.0 * t
    else:  # ultrasonic_sweep: inaudible 20 kHz → just below Nyquist
        lo, hi = min(20000.0, 0.40 * fs), min(23000.0, nyq_guard)
        phase = 2 * np.pi * (lo * t + (hi - lo) / (2 * dur) * t**2)

    x = ac.speaker_volume * np.hanning(n) * np.sin(phase)
    return np.tanh(x).astype(np.float32)


# ---------------------------------------------------------------------------
# Geometry & depth
# ---------------------------------------------------------------------------

def baseline_distance_m(cfg: SystemConfig) -> float:
    """d_base = ‖P_spk − P_mic‖ in metres (first microphone)."""
    spk, mic = cfg.hardware.speaker_pose, cfg.hardware.microphones[0]
    return math.sqrt(
        (spk.x_cm - mic.x_cm) ** 2 + (spk.y_cm - mic.y_cm) ** 2 + (spk.z_cm - mic.z_cm) ** 2
    ) / 100.0


def bistatic_depth(L: float, d_base: float, scale: float, offset: float) -> float:
    """Z_sonar = ½·√max(0, L² − d_base²) · α + β."""
    return 0.5 * math.sqrt(max(0.0, L * L - d_base * d_base)) * scale + offset


@dataclass
class Analysis:
    z_raw_m: float | None  # None when no peak exceeds the threshold
    amplitude: float
    curve: list[float]
    peak_range_m: float = 0.0


def analyse_correlation(
    corr: np.ndarray,
    cfg: SystemConfig,
    fs: int,
    abs_gate: float = 0.0,
) -> Analysis:
    """Blanking, peak picking, bi-static depth and curve for a lag-domain |R|.

    ``corr[k]`` is the matched-filter magnitude at lag k samples (k ≥ 0).
    ``abs_gate`` is a minimum un-normalised peak height (noise guard).
    """
    v = cfg.experimental_multi_mic.speed_of_sound_mps
    cal = cfg.calibration
    d_base = baseline_distance_m(cfg)

    ref = float(corr.max()) if corr.size else 0.0
    if ref <= 1e-12:
        return Analysis(None, 0.0, [0.0] * CURVE_BINS)
    norm = corr / ref

    # Curve: sample |R| at the lag of each one-way range bin (pre-blanking).
    ranges = np.linspace(0.0, CURVE_RANGE_M, CURVE_BINS)
    lags = 2.0 * ranges / v * fs
    curve = np.clip(np.interp(lags, np.arange(len(norm)), norm), 0.0, 1.0)

    # Direct-bleed blanking on the one-way axis shared with the dashboard.
    r_blank = max(cal.blanking_distance_cm / 100.0, d_base / 2.0)
    k_blank = int(math.ceil(2.0 * r_blank / v * fs))
    k_max = min(len(norm) - 1, int(2.0 * MAX_RANGE_M / v * fs))
    search = norm.copy()
    search[: k_blank + 1] = 0.0
    search[k_max + 1 :] = 0.0

    k_peak = int(np.argmax(search))
    amp = float(search[k_peak])
    if amp < cal.detection_threshold or corr[k_peak] < abs_gate:
        return Analysis(None, amp, curve.tolist())

    L = k_peak / fs * v
    z = bistatic_depth(L, d_base, cal.metric_scale_factor, cal.metric_offset_m)
    return Analysis(max(0.0, z), amp, curve.tolist(), peak_range_m=L / 2.0)


def matched_filter(recorded: np.ndarray, template: np.ndarray) -> np.ndarray:
    """|R_yx[k]| for k ≥ 0 via FFT cross-correlation."""
    full = sp_signal.correlate(recorded, template, mode="full", method="fft")
    return np.abs(full[len(template) - 1 :])


def condition_recording(rec: np.ndarray, cfg: SystemConfig, fs: int) -> np.ndarray:
    """Apply mic gain and high-pass filter."""
    out = rec.astype(np.float64) * 10 ** (cfg.acoustics.mic_gain_db / 20.0)
    hp = cfg.acoustics.highpass_cutoff_hz
    if 0.0 < hp < 0.45 * fs and len(out) > 64:
        sos = sp_signal.butter(4, hp, btype="highpass", fs=fs, output="sos")
        out = sp_signal.sosfiltfilt(sos, out)
    return out


# ---------------------------------------------------------------------------
# Readings
# ---------------------------------------------------------------------------

@dataclass
class SonarReading:
    timestamp: float
    sonar_depth_m: float
    peak_amplitude: float
    correlation_curve: list[float] = field(default_factory=lambda: [0.0] * CURVE_BINS)
    status: str = "initialising"  # tracking | idle | autopilot | mock | mock-idle | initialising
    raw_depth_m: float | None = None


class SonarDSP:
    """Background sonar worker. ``latest_reading`` is thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()  # serialises live loop vs calibration I/O
        self._running = False
        self._thread: threading.Thread | None = None
        self._latest = SonarReading(time.time(), 0.0, 0.0)
        self._ema: float | None = None
        self._last_track = time.monotonic()
        self._rng = np.random.default_rng(42)
        self.last_error: str | None = None

    # -- lifecycle ---------------------------------------------------------

    @property
    def mock(self) -> bool:
        return settings.mock_mode or sd is None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="sonar-dsp")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)

    @property
    def latest_reading(self) -> SonarReading:
        with self._lock:
            return self._latest

    # -- main loop ---------------------------------------------------------

    def _loop(self) -> None:
        while self._running:
            t0 = time.monotonic()
            cfg = get_config()
            fs = cfg.hardware.sample_rate_hz
            try:
                with self._io_lock:
                    corr, abs_gate = (
                        self._mock_correlation(cfg, fs) if self.mock else self._record(cfg, fs)
                    )
                self._publish(analyse_correlation(corr, cfg, fs, abs_gate), cfg)
            except Exception as exc:
                self.last_error = str(exc)
                print(f"[SonarDSP] I/O error: {exc}. Retrying in 1 s.")
                time.sleep(1.0)
                continue
            period = 1.0 / cfg.acoustics.ping_rate_hz
            time.sleep(max(0.0, period - (time.monotonic() - t0)))

    def _publish(self, an: Analysis, cfg: SystemConfig) -> None:
        cal = cfg.calibration
        now = time.monotonic()
        if an.z_raw_m is not None:
            a = cal.smoothing_alpha
            self._ema = an.z_raw_m if self._ema is None else a * an.z_raw_m + (1 - a) * self._ema
            self._last_track = now
            status = "mock" if self.mock else "tracking"
            depth = self._ema
        elif now - self._last_track > cal.autopilot_timeout_sec:
            depth = 1.4 + 0.8 * math.sin(now * 0.3)  # gentle breathing sweep
            status = "autopilot"
        else:
            depth = self._ema if self._ema is not None else 0.0
            status = "mock-idle" if self.mock else "idle"
        reading = SonarReading(
            timestamp=time.time(),
            sonar_depth_m=round(float(depth), 4),
            peak_amplitude=round(an.amplitude, 4),
            correlation_curve=[round(c, 4) for c in an.curve],
            status=status,
            raw_depth_m=an.z_raw_m,
        )
        with self._lock:
            self._latest = reading

    # -- hardware ----------------------------------------------------------

    def _play_record(self, cfg: SystemConfig, fs: int, tx: np.ndarray, n_samples: int) -> np.ndarray:
        """Full-duplex playback of ``tx`` (zero-padded) while recording the mic channel."""
        out = np.zeros((n_samples, 1), dtype=np.float32)
        out[: min(len(tx), n_samples), 0] = tx[:n_samples]
        mic = cfg.hardware.microphones[0]
        n_in = mic.channel + 1
        rec = sd.playrec(
            out,
            samplerate=fs,
            channels=n_in,
            dtype="float32",
            device=(cfg.hardware.input_audio_device_id, cfg.hardware.output_audio_device_id),
        )
        sd.wait()
        return rec[:, mic.channel]

    def _window_samples(self, cfg: SystemConfig, fs: int, tx_len: int) -> int:
        v = cfg.experimental_multi_mic.speed_of_sound_mps
        return tx_len + int(2.0 * MAX_RANGE_M / v * fs) + int(0.02 * fs)

    def _record(self, cfg: SystemConfig, fs: int) -> tuple[np.ndarray, float]:
        tx = synthesize_waveform(cfg.acoustics, fs)
        rec = condition_recording(
            self._play_record(cfg, fs, tx, self._window_samples(cfg, fs, len(tx))), cfg, fs
        )
        corr = matched_filter(rec, tx.astype(np.float64))
        # Noise guard: a peak must stand ≥ 6σ above matched-filtered white noise.
        gate = 6.0 * cfg.calibration.ambient_noise_floor_rms * float(np.linalg.norm(tx))
        return corr, gate

    # -- mock --------------------------------------------------------------

    def _mock_correlation(self, cfg: SystemConfig, fs: int) -> tuple[np.ndarray, float]:
        v = cfg.experimental_multi_mic.speed_of_sound_mps
        d_base = baseline_distance_m(cfg)
        n = int(2.0 * MAX_RANGE_M / v * fs) + 64
        k = np.arange(n)
        corr = np.abs(self._rng.normal(0.0, 0.03, n))

        # Direct crosstalk: spike at the speaker→mic baseline (≈ bins 0–20 of the curve).
        k_bleed = d_base / v * fs
        corr += 1.0 * np.exp(-((k - k_bleed) ** 2) / (2 * 14.0**2))

        # Moving echo: 0.5 – 2.5 m at 0.15 Hz.
        z = 1.5 + 1.0 * math.sin(2 * math.pi * 0.15 * time.monotonic())
        k_echo = math.sqrt((2.0 * z) ** 2 + d_base**2) / v * fs
        corr += 0.7 * np.exp(-((k - k_echo) ** 2) / (2 * 6.0**2))
        return corr, 0.0

    # -- calibration rounds -----------------------------------------------

    def calibrate_round_1(self, n_chirps: int = 10) -> dict:
        """Empty-room sweep → direct-bleed distance + 15 cm margin.

        Returns ``{"blanking_distance_cm", "samples_cm", "spread_cm", "quality"}``.
        The caller persists the result.
        """
        cfg = get_config()
        fs = cfg.hardware.sample_rate_hz
        v = cfg.experimental_multi_mic.speed_of_sound_mps
        d_base = baseline_distance_m(cfg)
        # Search only where bleed can be: out to 1.0 m of acoustic path.
        k_hi = int(1.0 / v * fs)
        peaks_cm: list[float] = []
        with self._io_lock:
            tx = synthesize_waveform(cfg.acoustics, fs)
            for _ in range(n_chirps):
                if self.mock:
                    corr, _ = self._mock_correlation(cfg, fs)
                else:
                    rec = condition_recording(
                        self._play_record(cfg, fs, tx, self._window_samples(cfg, fs, len(tx))),
                        cfg, fs,
                    )
                    corr = matched_filter(rec, tx.astype(np.float64))
                k = int(np.argmax(corr[:k_hi]))
                peaks_cm.append(k / fs * v / 2.0 * 100.0)  # one-way axis, cm
                if self.mock:
                    time.sleep(0.02)
        mean_cm = float(np.mean(peaks_cm))
        spread = float(np.std(peaks_cm))
        blank = float(np.clip(mean_cm + BLANK_MARGIN_M * 100.0, 0.0, 200.0))
        return {
            "blanking_distance_cm": round(blank, 1),
            "samples_cm": [round(p, 2) for p in peaks_cm],
            "spread_cm": round(spread, 2),
            "baseline_cm": round(d_base * 100.0, 1),
            "quality": "good" if spread < 2.0 else "noisy",
        }

    def calibrate_round_2(self, seconds: float = 5.0) -> dict:
        """Passive listening (no emission) → noise floor RMS and 3× threshold."""
        cfg = get_config()
        fs = cfg.hardware.sample_rate_hz
        with self._io_lock:
            if self.mock:
                time.sleep(min(seconds, 1.0))
                rec = self._rng.normal(0.0, 0.025, int(seconds * fs))
            else:
                mic = cfg.hardware.microphones[0]
                rec = sd.rec(
                    int(seconds * fs), samplerate=fs, channels=mic.channel + 1,
                    dtype="float32", device=cfg.hardware.input_audio_device_id,
                )
                sd.wait()
                rec = rec[:, mic.channel].astype(np.float64)
        rec = condition_recording(rec, cfg, fs)
        rms = float(np.sqrt(np.mean(rec**2)))
        # RMS was measured after mic gain, so the stored floor is in post-gain units too.
        threshold = float(np.clip(3.0 * rms, 0.05, 0.5))
        return {
            "ambient_noise_floor_rms": round(rms, 5),
            "detection_threshold": round(threshold, 4),
            "seconds": seconds,
        }
