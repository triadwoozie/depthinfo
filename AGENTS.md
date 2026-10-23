# AGENTS.md — Autonomous Engineering Specification for "Sonar Imaging"

> **NOTICE FOR AI AGENTS:** This document is the **single source of truth**. Follow all schemas, mathematical formulas, port mappings, and threading rules literally. Never prompt the human operator for architectural clarification if the answer is defined here.

---

## 1. Repository Layout & Service Boundaries

```text
sonar-imaging/
├── AGENTS.md                           # Master technical spec (this file)
├── README.md                           # Operator manual & hardware setup guide
├── ROADMAP.md                          # Phased implementation checklist
├── backend/                            # Python 3.11+ FastAPI Core Engine (Port 8000)
│   ├── main.py                         # ASGI server, REST endpoints, WS broadcaster
│   ├── config.py                       # Pydantic V2 state schemas & JSON persistence
│   ├── devices.py                      # PortAudio & OpenCV hardware enumeration
│   ├── sonar_dsp.py                    # Chirp synthesis, matched filter, GCC-PHAT (multi-mic)
│   ├── depth_ai.py                     # Depth-Anything-V2-Small ONNX inference & Fog Map generator
│   ├── stitcher.py                     # Multi-frame point cloud fusion & surface reconstruction
│   ├── blender_udp.py                  # 60Hz UDP streamer to live Blender viewport (Port 5005)
│   ├── blender_exporter.py             # Headless Blender subprocess runner for static .blend files
│   └── exports/                        # Generated .blend, .glb, and .ply static assets
├── dashboard/                          # Next.js + TypeScript + Tailwind + shadcn/ui (Port 3000)
│   └── src/
│       ├── components/
│       │   ├── hardware/               # Device selectors & 3D/2D Rig Placement Canvas
│       │   ├── acoustics/              # Frequency, waveform, volume & oscilloscope controls
│       │   ├── vision/                 # Live Fog Map viewer, RGB feed, & scan distance slicers
│       │   ├── calibration/            # 4-Round Interactive Calibration Wizard
│       │   ├── blender/                # Color mode router, live preview, & static .blend exporter
│       │   └── experimental/           # Multi-Mic TDOA polar radar & beamforming controls
│       ├── hooks/
│       │   └── useSonarEngine.ts       # WebSocket state sync & command dispatcher
│       └── types/
│           └── schema.ts               # Strict TypeScript mirror of backend/config.py
└── blender/                            # Blender 4.x Python Scripts & Template Assets
    ├── template_exhibition.blend       # Base scene with Geometry Nodes & exhibition lighting
    ├── sonar_imaging/                  # Blender 4.2+/5 extension: UDP receiver + N-panel (blender_manifest.toml)
    └── export_static_scene.py          # Headless script that builds & saves static .blend models
```

### Port Map

| Service | Port | Protocol | Description |
|---|---|---|---|
| FastAPI Backend | `8000` | HTTP + WS | REST config, MJPEG streams, WebSocket telemetry |
| Next.js Dashboard | `3000` | HTTP | Operator control panel |
| Blender UDP Bridge | `5005` | UDP (local) | Live point-cloud → Blender viewport |

---

## 2. Strict Threading & Non-Blocking Rules for All Agents

### Rule 1 — Never Block the FastAPI Event Loop
- All `sounddevice.playrec()`, `cv2.VideoCapture.read()`, ONNX inference, and Open3D operations **MUST** run in dedicated `threading.Thread` workers or `asyncio.run_in_executor` calls.
- The main `asyncio` loop handles only: WebSocket fan-out, REST routing, and MJPEG frame serving.

### Rule 2 — Never Block the Blender UI Thread
- `blender/sonar_imaging/receiver.py` must **only** use `sock.setblocking(False)` with `bpy.app.timers` at `0.016 s` intervals.
- Never use `while True:` loops, `sock.recv()` blocking calls, or Python `time.sleep()` inside any `bpy.app.timers` callback.

### Rule 3 — Always Maintain `SONAR_MOCK_MODE=true` Compatibility
- Every new module must synthesize deterministic fake data when `settings.mock_mode is True`.
- Mock data must be physically plausible: depth oscillating 0.5 m – 2.5 m, synthetic correlation curve with blanking spike at index 0–20, procedural 640×480 frames with a moving ellipse silhouette.
- CI/CD pipelines run exclusively in mock mode — hardware absence must never cause an import error or crash.

### Rule 4 — High-Frequency UI Rendering
- The WebSocket streams telemetry at **20 Hz**. `correlation_curve` (256 floats) must **never** be stored in React `useState`.
- Store it in `useRef` and render via `requestAnimationFrame` on a raw HTML5 `<canvas>`.
- All other scalar telemetry (depth, FPS, status) may use `useState` at 20 Hz.

---

## 3. Core Mathematical & DSP Specifications

### 3.1 Active Acoustic Sonar — Bi-Static Rig Compensation

**Emitted Signal:** Hanning-windowed chirp swept from $f_0$ to $f_1$ over duration $T_c$ at sample rate $F_s = 48000\ \text{Hz}$, scaled by `speaker_volume` ∈ [0.0, 1.0] with soft-clipping via `np.tanh`.

$$x[n] = \text{tanh}\left( V \cdot w[n] \cdot \sin\!\left(2\pi \left[ f_0 \frac{n}{F_s} + \frac{f_1 - f_0}{2 T_c} \left(\frac{n}{F_s}\right)^2 \right]\right)\right)$$

where $w[n]$ is a Hanning window of length $N_c = \lfloor T_c \cdot F_s \rfloor$.

**Matched Filtering (FFT Cross-Correlation):**

$$R_{yx}[k] = \left| \mathcal{F}^{-1}\!\left\{ \mathcal{F}\{y\} \cdot \mathcal{F}^*\!\{x\} \right\} \right|$$

**Direct Bleed Blanking:** Baseline speaker-to-mic distance:
$d_{\text{base}} = \|\mathbf{P}_{\text{spk}} - \mathbf{P}_{\text{mic}}\|$

Zero all indices $k \le k_{\text{blank}}$:

$$k_{\text{blank}} = \left\lceil \frac{d_{\text{base}} + d_{\text{margin}}}{v_{\text{sound}}} \cdot F_s \right\rceil$$

**Bi-Static Metric Depth ($Z_{\text{sonar}}$):** For peak index $k_{\text{peak}}$ exceeding `detection_threshold`:

$$Z_{\text{sonar}} = \frac{1}{2}\sqrt{\max\!\left(0,\; L^2 - d_{\text{base}}^2\right)} \cdot \alpha_{\text{scale}} + \beta_{\text{offset}}$$

where $L = \frac{k_{\text{peak}}}{F_s} \cdot v_{\text{sound}}$ (total acoustic path length).

**EMA Smoothing:** $\hat{Z}[t] = \alpha \cdot Z_{\text{sonar}} + (1-\alpha) \cdot \hat{Z}[t-1]$, $\alpha =$ `smoothing_alpha`.

---

### 3.2 Real-Time Fog Map (Atmospheric Z-Depth Mist Pass)

**Inference:** Depth-Anything-V2-Small (ViT-S ONNX checkpoint, input 518×518) → raw relative inverse depth $D_{\text{raw}}(u,v) \in [0,1]$.

**Metric Anchoring:**
$$Z_{\text{metric}}(u,v) = Z_{\text{sonar}} + \left(\bar{D}_{\text{fg}} - D_{\text{raw}}(u,v)\right) \cdot S_{\text{relief}}$$

where $\bar{D}_{\text{fg}}$ = median relative depth of the foreground ROI and $S_{\text{relief}}$ = `vision_and_fog.relief_depth_span_m`.

**Scan Distance Clipping:** Mask all pixels where $Z_{\text{metric}}(u,v) \notin [Z_{\min},\ Z_{\max}]$.

**Fog Falloff Equations** (output $I_{\text{fog}} \in [0, 255]$, near = bright):

| Mode | Formula |
|---|---|
| `linear` | $f = \text{clamp}\!\left(\dfrac{Z_{\max} - Z}{Z_{\max} - Z_{\min}},\ 0,\ 1\right)$ |
| `exponential` | $f = \exp\!\left(-\rho \cdot \dfrac{Z - Z_{\min}}{Z_{\max} - Z_{\min}}\right)$ |
| `squared` | $f = \exp\!\left(-\left(\rho \cdot \dfrac{Z - Z_{\min}}{Z_{\max} - Z_{\min}}\right)^2\right)$ |

$I_{\text{fog}} = \lfloor f \cdot 255 \rfloor$, $\rho =$ `vision_and_fog.fog_density`.

---

### 3.3 Point Cloud Stitching & Surface Meshing (`stitcher.py`)

**Pinhole Unprojection** (using `camera_pose` + `camera_fov_deg`):
$$\mathbf{P}_{3D}(u,v) = \mathbf{T}_{\text{cam}} \cdot Z_{\text{metric}}(u,v) \cdot \begin{pmatrix}(u - c_x)/f_x \\ (v - c_y)/f_y \\ 1\end{pmatrix}$$

**Open3D Pipeline:**
1. Voxel grid downsample: `voxel_size = vision_and_fog.voxel_downsample_m`
2. Statistical outlier removal: `nb_neighbors=20`, `std_ratio=2.0`
3. Normal estimation oriented toward camera origin
4. Surface reconstruction: Screened Poisson (depth=7) or Ball-Pivoting (BPA)

**Temporal Accumulation:** Rolling ring buffer of `temporal_stitch_frames` frames; new frames registered against the previous frame via ICP (point-to-plane) when `acoustic_wave_distortion` > 0 (subject is moving).

---

### 3.4 Static `.blend` Exporter (`export_static_scene.py`)

Triggered by `POST /api/export/blend`:

1. `stitcher.py` serialises `(V, F, C)` arrays → `temp_<timestamp>.ply`
2. Backend spawns non-blocking subprocess:
   ```bash
   blender --background blender/template_exhibition.blend \
           --python blender/export_static_scene.py -- \
           --input  backend/exports/temp_<ts>.ply \
           --output backend/exports/scan_<ts>.blend \
           --color_mode <optical_rgb|fog_gradient|acoustic_heatmap|custom_palette>
   ```
3. `export_static_scene.py`:
   - Imports PLY → `bpy.ops.import_mesh.ply()`
   - Creates `bpy.types.FloatColorAttribute` named `"Col"`
   - Builds ShaderNodeTree: `ShaderNodeVertexColor("Col")` → `ShaderNodeBsdfPrincipled` → Material Output
   - Optionally adds `ShaderNodeVolumeScatter` world volume to simulate Fog Map atmosphere
   - Positions isometric studio camera + 3-point HDRI lighting rig
   - `bpy.ops.export_scene.gltf(filepath=..., export_format='GLB')`
   - `bpy.ops.wm.save_as_mainfile(filepath=..., compress=True)`

---

### 3.5 Experimental: Multi-Microphone GCC-PHAT Array

**Enable condition:** `experimental_multi_mic.enabled == True` AND audio device has ≥ 2 input channels.

**GCC-PHAT Cross-Correlation** (per mic pair $i, j$):

$$R_{ij}^{\text{PHAT}}(\tau) = \int_{-\infty}^{\infty} \frac{Y_i(\omega)\, Y_j^*(\omega)}{\left|Y_i(\omega)\, Y_j^*(\omega)\right| + \varepsilon}\, e^{j\omega\tau}\, d\omega$$

**TDOA extraction:** $\Delta\tau_{ij} = \arg\max_\tau R_{ij}^{\text{PHAT}}(\tau)$, bounded by $\tau_{\max} = \|\mathbf{P}_i - \mathbf{P}_j\| / v_{\text{sound}}$.

**Direction of Arrival:**

$$\theta_{ij} = \arcsin\!\left(\frac{\Delta\tau_{ij} \cdot v_{\text{sound}}}{\|\mathbf{P}_i - \mathbf{P}_j\|}\right)$$

Intersect DoA cones with sonar range sphere $Z_{\text{sonar}}$ to obtain directional acoustic hotspot coordinates $(x, y, z)$ overlaid on the Acoustic Polar Radar widget.

---

## 4. Unified Data Contracts

### 4.1 Master `SystemConfig` Schema (backend/config.py ↔ dashboard/src/types/schema.ts)

```json
{
  "hardware": {
    "input_audio_device_id": 1,
    "output_audio_device_id": 2,
    "camera_device_index": 0,
    "sample_rate_hz": 48000,
    "camera_fov_deg": 65.0,
    "camera_pose":  { "x_cm": 0.0,   "y_cm": 0.0,   "z_cm": 0.0, "pitch_deg": 0.0, "yaw_deg": 0.0, "roll_deg": 0.0 },
    "speaker_pose": { "x_cm": -15.0, "y_cm": -10.0, "z_cm": 0.0, "pitch_deg": 0.0, "yaw_deg": 0.0 },
    "microphones": [
      { "channel": 0, "label": "Main Studio Condenser", "x_cm": 15.0, "y_cm": -10.0, "z_cm": 0.0, "polar_pattern": "cardioid" }
    ]
  },
  "acoustics": {
    "waveform": "linear_fm_chirp",
    "freq_start_hz": 16000.0,
    "freq_end_hz": 20000.0,
    "chirp_duration_ms": 20.0,
    "ping_rate_hz": 10.0,
    "speaker_volume": 0.65,
    "mic_gain_db": 6.0,
    "highpass_cutoff_hz": 1000.0
  },
  "calibration": {
    "blanking_distance_cm": 38.0,
    "detection_threshold": 0.14,
    "metric_scale_factor": 1.0,
    "metric_offset_m": 0.0,
    "smoothing_alpha": 0.25,
    "background_fog_captured": false,
    "ambient_noise_floor_rms": 0.012,
    "calibration_round_completed": 0
  },
  "vision_and_fog": {
    "min_scan_distance_m": 0.35,
    "max_scan_distance_m": 2.50,
    "fog_falloff_curve": "exponential",
    "fog_density": 1.2,
    "fog_colormap": "mist_grayscale",
    "relief_depth_span_m": 0.45,
    "depth_inference_mode": "lightweight_fast",
    "acoustic_beam_deg": 20.0,
    "temporal_stitch_frames": 5,
    "voxel_downsample_m": 0.01
  },
  "blender_style": {
    "color_mode": "optical_rgb",
    "custom_near_hex": "#00F5D4",
    "custom_far_hex": "#3A0CA3",
    "point_radius_mm": 4.0,
    "surface_reconstruction_mode": "point_cloud",
    "acoustic_wave_distortion": 0.35
  },
  "experimental_multi_mic": {
    "enabled": false,
    "algorithm": "gcc_phat",
    "speed_of_sound_mps": 343.2,
    "overlay_acoustic_lobes": true
  }
}
```

### 4.2 WebSocket Telemetry Frame (`/ws/telemetry` → Dashboard @ 20 Hz)

```json
{
  "timestamp": 1729700000.12,
  "status": "tracking",
  "mock_mode": false,
  "sonar_depth_m": 1.45,
  "peak_amplitude": 0.34,
  "fps_vision": 30.0,
  "fps_sonar_hz": 10.0,
  "active_vertex_count": 4821,
  "doa_azimuth_deg": null,
  "system_fps": 20.0,
  "correlation_curve": [0.0, 0.0, 0.12, 0.85, 0.04]
}
```

> `correlation_curve` is **exactly 256 normalised floats [0.0–1.0]** representing 0.0 m → 4.0 m. Stored in a `useRef` on the client; never in `useState`.

### 4.3 REST API Surface

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/devices` | PortAudio + OpenCV device enumeration |
| `GET` | `/api/config` | Full `SystemConfig` JSON |
| `PATCH` | `/api/config` | Partial update; broadcasts over WS within 50 ms |
| `POST` | `/api/calibration/round/{1\|2\|3\|4}` | Execute calibration round |
| `GET` | `/api/stream/rgb` | MJPEG — annotated camera feed @ 30 FPS |
| `GET` | `/api/stream/fog` | MJPEG — live Fog Map (Z-depth mist) @ 30 FPS |
| `POST` | `/api/export/blend` | Freeze + export `.blend` / `.glb` / `.ply` |
| `GET` | `/healthz` | `{ ok, mock, ws_clients, sonar_status }` |

### 4.4 Blender UDP Packet (`/blender_udp.py` → Blender @ 60 Hz, max 8192 bytes)

```json
{
  "state": "tracking",
  "sonar_z": 1.45,
  "ping_pulse": 1,
  "audio_energy": 0.42,
  "color_mode": "optical_rgb",
  "points": [[0.1, 0.2, 1.45], [0.3, -0.1, 1.52]],
  "colors": [[0.9, 0.8, 0.7], [0.5, 0.6, 0.8]]
}
```

---

## 5. Calibration Round Specifications

### Round 1 — Direct Acoustic Bleed & Blanking
- Play 10 chirps into an empty room (no subject present).
- Record cross-correlation peak indices across 10 samples.
- `blanking_distance_cm` = mean of peak distances + 15 cm safety margin.
- Sets `calibration.blanking_distance_cm` and saves to `config_state.json`.

### Round 2 — Room Reverb & Spectral Noise Floor
- Listen passively for 5 s with no chirp emission.
- Compute RMS of received mic signal → `ambient_noise_floor_rms`.
- `detection_threshold` = 3× `ambient_noise_floor_rms` (clamped to [0.05, 0.5]).

### Round 3 — Optical Intrinsics, Exposure Lock & Background Fog Subtraction
- Capture 30 depth frames of empty room with no subject present.
- Build a static background depth mask: `bg_depth_map = median(frames)`.
- Subsequent runtime frames subtract `bg_depth_map` before fog computation.
- Sets `calibration.background_fog_captured = true`.

### Round 4 — Multi-Point Metric Anchor (Linear Regression)
- Operator places a flat target at known distances: **0.5 m, 1.5 m, 2.5 m**.
- For each: record `Z_sonar` and median `D_raw` from AI depth model.
- Fit `Z_metric = α · D_raw + β` via `numpy.polyfit(degree=1)`.
- Sets `calibration.metric_scale_factor = α`, `calibration.metric_offset_m = β`.

---

## 6. Colour Mode Routing

Applied in both `stitcher.py` (live) and `export_static_scene.py` (baked):

| `color_mode` | Per-Vertex Colour Source |
|---|---|
| `optical_rgb` | Raw camera RGB at `(u, v)` |
| `fog_gradient` | Fog Map $I_{\text{fog}}(u,v)$ mapped to `custom_near_hex` → `custom_far_hex` |
| `acoustic_heatmap` | Echo amplitude `peak_amplitude` mapped through a heat colourmap (blue→red) |
| `custom_palette` | Linear interpolation of `custom_near_hex` → `custom_far_hex` by metric depth |

---

## 7. shadcn/ui Component Inventory (All Confirmed Present in Registry)

> **Dashboard agents:** import from `@/components/ui/<name>` — all primitives are pre-installed. Do NOT add Recharts, D3, or any SVG charting library for high-frequency data. Use raw `<canvas>` + `requestAnimationFrame` instead.

| Component | Import Path | Used In |
|---|---|---|
| `<Slider>` | `@/components/ui/slider` | Fog range, chirp Hz, scan distance |
| `<Card>` | `@/components/ui/card` | All panel containers |
| `<Badge>` | `@/components/ui/badge` | Status / telemetry readouts |
| `<Tabs>` | `@/components/ui/tabs` | RGB ↔ Fog ↔ 3D viewport switcher |
| `<Switch>` | `@/components/ui/switch` | Mock mode, mute, multi-mic enable |
| `<Button>` | `@/components/ui/button` | Export, calibrate, mute |
| `<Select>` | `@/components/ui/select` | Device pickers, colour mode, waveform |
| `<Dialog>` | `@/components/ui/dialog` | Calibration wizard modal |
| `<Progress>` | `@/components/ui/progress` | Calibration round progress bars |
| `<Separator>` | `@/components/ui/separator` | Panel dividers |
| `<Tooltip>` | `@/components/ui/tooltip` | Rig coordinate labels |
| `<ToggleGroup>` | `@/components/ui/toggle-group` | Fog falloff curve picker |
| `<Input>` | `@/components/ui/input` | Exact XYZ cm coordinate inputs |

---

## 8. Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SONAR_MOCK_MODE` | `false` | Synthetic data — no hardware required |
| `SONAR_SAMPLE_RATE` | `48000` | Audio sample rate (Hz) |
| `SONAR_UDP_HOST` | `127.0.0.1` | Blender UDP target |
| `SONAR_UDP_PORT` | `5005` | Blender UDP port |
| `SONAR_UDP_HZ` | `60` | Blender update rate |
| `SONAR_WS_HZ` | `20` | Dashboard telemetry rate |
| `SONAR_CAMERA_INDEX` | `0` | OpenCV camera index |
| `SONAR_REAL_CAMERA` | `false` | Use the real camera even when `SONAR_MOCK_MODE=true` |
| `DEPTH_AUTO_DOWNLOAD` | `false` | Fetch the ONNX depth model on first use of `neural_onnx` |
| `DEPTH_MODEL_PATH` | `models/depth_anything_v2_vits.onnx` | ONNX model file path |
| `EXPORTS_DIR` | `backend/exports` | Static `.blend` / `.glb` / `.ply` output |
