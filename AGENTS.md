# AGENTS.md — Autonomous Engineering Specification for "Sonar Imaging"

> **NOTICE FOR AI AGENTS:** This document is the **single source of truth**. Follow all schemas, mathematical formulas, port mappings, and threading rules literally. Never prompt the human operator for architectural clarification if the answer is defined here.

---

## 1. Repository Layout & Service Boundaries

```text
depthinfo/
├── AGENTS.md                     # Technical spec (this file)
├── README.md                     # Overview and quick start
├── GETTING_STARTED.md            # Setup, hardware mode, troubleshooting, test log
├── ROADMAP.md                    # Status, next steps, phase plan
├── run.py / run.sh               # Start backend + dashboard; Ctrl+C stops both
├── launch.sh                     # run.sh --real-camera
├── backend/                      # Python 3.11/3.12 FastAPI engine (port 8000)
│   ├── main.py                   # ASGI app: REST, WebSocket telemetry, MJPEG, export
│   ├── config.py                 # Pydantic V2 settings + SystemConfig, persisted to config_state.json
│   ├── devices.py                # PortAudio / OpenCV enumeration, stable audio-device names
│   ├── sonar_dsp.py              # Chirp synthesis, matched filter, bi-static depth, rounds 1-2
│   ├── depth_ai.py               # Camera grabber, depth modes, hybrid depth, fog map, Blender frame
│   ├── stitcher.py               # Unprojection, temporal fusion, meshing, colour routing
│   ├── blender_udp.py            # Depth-frame streamer to the Blender extension (UDP 5005)
│   ├── blender_exporter.py       # Headless Blender runner for .blend / .glb / .ply export
│   ├── models/                   # depth_anything_v2_vits.onnx (downloaded, git-ignored)
│   ├── exports/                  # Export output (git-ignored)
│   └── tests/                    # pytest suite (mock mode)
├── dashboard/                    # Next.js 14 + TypeScript + Tailwind + shadcn/ui (port 3000)
│   └── src/
│       ├── app/page.tsx          # Dashboard layout
│       ├── components/sonar/     # Panels: rig, synth, vision, echo canvas, export, calibration wizard
│       ├── components/ui/        # shadcn/ui primitives
│       ├── hooks/useSonarSocket.ts  # Reconnecting WebSocket telemetry + config sync
│       ├── lib/api.ts            # Backend URLs (NEXT_PUBLIC_API_URL)
│       └── types/sonar.ts        # TypeScript mirror of SystemConfig and telemetry, adapters
└── blender/
    ├── sonar_imaging/            # Blender 4.2+/5 extension: displaced-mesh live receiver
    │   ├── protocol.py           #   wire format + chunk reassembler (no bpy)
    │   ├── nodes.py              #   "Sonar_Displace" Geometry Nodes tree + material
    │   ├── receiver.py           #   UDP timer loop; writes the depth / colour textures
    │   ├── operators.py, ui.py, properties.py, __init__.py
    │   └── blender_manifest.toml
    ├── export_static_scene.py    # Headless static scene builder (run by blender_exporter.py)
    ├── build_template.py         # Regenerates template_exhibition.blend
    ├── _sonar_common.py          # Helpers shared by the two scripts above
    └── template_exhibition.blend # Base scene for exports
```

### Port Map

| Service | Port | Protocol | Description |
|---|---|---|---|
| FastAPI Backend | `8000` | HTTP + WS | REST config, MJPEG streams, WebSocket telemetry |
| Next.js Dashboard | `3000` | HTTP | Operator control panel |
| Blender UDP Bridge | `5005` | UDP (local) | Live depth frames → Blender displaced mesh |

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

### 3.2 Relative Depth, Hybrid Metric Depth and the Fog Map (`depth_ai.py`)

**Relative depth** $D_{\text{rel}}(u,v) \in [0,1]$, **0 = nearest**, chosen by `vision_and_fog.depth_inference_mode`:

| Mode | Source |
|---|---|
| `disabled` | Constant map, so $Z = Z_{\text{sonar}}$ everywhere |
| `lightweight_fast` | CPU cues at 160×120: difference from the calibrated empty background when captured, otherwise edge energy, brightness and a ground-plane prior; bilateral-smoothed. About 1 ms. A heuristic, not true depth |
| `neural_onnx` | Depth-Anything-V2-Small (518×518) in a worker thread. Its inverse-depth output is normalised between the 2nd and 98th percentiles, optionally CLAHE-enhanced (`depth_contrast_enhance`), then $D_{\text{rel}} = 1 - \text{disparity}$ |

A mode that cannot run reports `depth_error` and runs `lightweight_fast`; `depth_mode_active` is what really runs. The neural ONNX session is freed 30 s after neural mode was last selected.

**Hybrid metric depth** (the sonar anchors, vision supplies the relief):
$$Z(u,v) = Z_{\text{sonar}} + \left(D_{\text{rel}}(u,v) - D_{\text{anchor}}\right) \cdot S_{\text{relief}}$$

$D_{\text{anchor}}$ is the `anchor_percentile` (default 5th) percentile of $D_{\text{rel}}$ inside the acoustic beam cone of half-angle `acoustic_beam_deg`: the near side, because the sonar reports the nearest reflector, and a median would land on the background whenever the subject fills less than half the cone. $S_{\text{relief}}$ = `relief_depth_span_m`. When the empty background has been captured (Round 3), pixels that still match it are excluded.

**Scan Distance Clipping:** Mask all pixels where $Z(u,v) \notin [Z_{\min},\ Z_{\max}]$.

**Fog Falloff Equations** (output $I_{\text{fog}} \in [0, 255]$, near = bright; default `linear`):

| Mode | Formula |
|---|---|
| `linear` | $f = \text{clamp}\!\left(\dfrac{Z_{\max} - Z}{Z_{\max} - Z_{\min}},\ 0,\ 1\right)$ |
| `exponential` | $f = \exp\!\left(-\rho \cdot \dfrac{Z - Z_{\min}}{Z_{\max} - Z_{\min}}\right)$ |
| `squared` | $f = \exp\!\left(-\left(\rho \cdot \dfrac{Z - Z_{\min}}{Z_{\max} - Z_{\min}}\right)^2\right)$ |

$I_{\text{fog}} = \lfloor f \cdot 255 \rfloor$, $\rho =$ `vision_and_fog.fog_density`.

**Subject isolation** (3D cloud and Blender stream): a pixel is kept only if $Z$ is finite, $I_{\text{fog}}/255 >$ `fog_discard_threshold`, $Z \le Z_{\max}$ and $Z - Z_{\text{sonar}} \le$ `subject_thickness_m`.

---

### 3.3 Point Cloud Stitching & Surface Meshing (`stitcher.py`)

**Pinhole Unprojection** (using `camera_pose` + `camera_fov_deg`):
$$\mathbf{P}_{3D}(u,v) = \mathbf{T}_{\text{cam}} \cdot Z_{\text{metric}}(u,v) \cdot \begin{pmatrix}(u - c_x)/f_x \\ (v - c_y)/f_y \\ 1\end{pmatrix}$$

**Open3D Pipeline:**
1. Voxel grid downsample: `voxel_size = vision_and_fog.voxel_downsample_m`
2. Statistical outlier removal: `nb_neighbors=20`, `std_ratio=2.0`
3. Normal estimation oriented toward camera origin
4. Surface reconstruction: Screened Poisson (depth=7) or Ball-Pivoting (BPA)

**Temporal Accumulation:** Rolling ring buffer of `temporal_stitch_frames` frames; new frames registered against the previous frame via ICP (point-to-plane) when `acoustic_wave_distortion` > 0 (subject is moving). Live views (`/api/pointcloud`, the dashboard 3D tab) use the newest single frame, which keeps true relief; the fused cloud (`?stitched=true`) is used for export. Surface meshing runs at most every 2 s live and is always rebuilt fresh for an export.

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
   - Imports PLY → `bpy.ops.wm.ply_import()` and remaps the backend frame (x right, y up, z forward) to Blender's Z-up
   - Creates `bpy.types.FloatColorAttribute` named `"Col"`
   - Builds ShaderNodeTree: `ShaderNodeVertexColor("Col")` → `ShaderNodeBsdfPrincipled` → Material Output
   - Optionally adds `ShaderNodeVolumeScatter` world volume to simulate Fog Map atmosphere
   - Positions an isometric orthographic studio camera and a 3-point area-light rig
   - `bpy.ops.export_scene.gltf(filepath=..., export_format='GLB')` (a point cloud is baked to small cubes first, because glTF drops loose vertices)
   - `bpy.ops.wm.save_as_mainfile(filepath=..., compress=True)`

---

### 3.5 Experimental: Multi-Microphone GCC-PHAT Array

> **Not implemented yet** (ROADMAP Phase 6). The config section `experimental_multi_mic` exists, but no GCC-PHAT code runs and `doa_azimuth_deg` is always `null`.

**Enable condition:** `experimental_multi_mic.enabled == True` AND audio device has ≥ 2 input channels.

**GCC-PHAT Cross-Correlation** (per mic pair $i, j$):

$$R_{ij}^{\text{PHAT}}(\tau) = \int_{-\infty}^{\infty} \frac{Y_i(\omega)\, Y_j^*(\omega)}{\left|Y_i(\omega)\, Y_j^*(\omega)\right| + \varepsilon}\, e^{j\omega\tau}\, d\omega$$

**TDOA extraction:** $\Delta\tau_{ij} = \arg\max_\tau R_{ij}^{\text{PHAT}}(\tau)$, bounded by $\tau_{\max} = \|\mathbf{P}_i - \mathbf{P}_j\| / v_{\text{sound}}$.

**Direction of Arrival:**

$$\theta_{ij} = \arcsin\!\left(\frac{\Delta\tau_{ij} \cdot v_{\text{sound}}}{\|\mathbf{P}_i - \mathbf{P}_j\|}\right)$$

Intersect DoA cones with sonar range sphere $Z_{\text{sonar}}$ to obtain directional acoustic hotspot coordinates $(x, y, z)$ overlaid on the Acoustic Polar Radar widget.

---

## 4. Unified Data Contracts

### 4.1 Master `SystemConfig` Schema (`backend/config.py` ↔ `dashboard/src/types/sonar.ts`)

Defaults, exactly as `SystemConfig().model_dump()` produces them. Audio devices default to `null` (system default); a device picked in the dashboard is saved by name and re-resolved at runtime, because PortAudio ids change between boots.

```json
{
  "hardware": {
    "input_audio_device_id": null,
    "output_audio_device_id": null,
    "input_audio_device_name": null,
    "output_audio_device_name": null,
    "camera_device_index": 0,
    "sample_rate_hz": 48000,
    "camera_fov_deg": 65.0,
    "camera_pose": {
      "x_cm": 0.0,
      "y_cm": 0.0,
      "z_cm": 0.0,
      "pitch_deg": 0.0,
      "yaw_deg": 0.0,
      "roll_deg": 0.0
    },
    "speaker_pose": {
      "x_cm": -15.0,
      "y_cm": -10.0,
      "z_cm": 0.0,
      "pitch_deg": 0.0,
      "yaw_deg": 0.0,
      "roll_deg": 0.0
    },
    "microphones": [
      {
        "channel": 0,
        "label": "Main Studio Condenser",
        "x_cm": 15.0,
        "y_cm": -10.0,
        "z_cm": 0.0,
        "polar_pattern": "cardioid"
      }
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
    "calibration_round_completed": 0,
    "autopilot_timeout_sec": 10
  },
  "vision_and_fog": {
    "min_scan_distance_m": 0.35,
    "max_scan_distance_m": 2.5,
    "fog_falloff_curve": "linear",
    "fog_density": 1.2,
    "fog_colormap": "mist_grayscale",
    "relief_depth_span_m": 0.45,
    "fog_discard_threshold": 0.12,
    "subject_thickness_m": 0.3,
    "depth_contrast_enhance": true,
    "depth_inference_mode": "lightweight_fast",
    "acoustic_beam_deg": 20.0,
    "anchor_percentile": 5.0,
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
  "fps_vision": 15.0,
  "fps_sonar_hz": 10.0,
  "active_vertex_count": 4821,
  "doa_azimuth_deg": null,
  "depth_mode": "neural_onnx",
  "depth_mode_active": "neural_onnx",
  "depth_active_provider": "CUDAExecutionProvider",
  "depth_model_fps": 15.0,
  "depth_error": null,
  "camera_source": "camera",
  "system_fps": 20.0,
  "ping_rate_hz": 20.0,
  "correlation_curve": [0.0, 0.0, 0.12, 0.85, 0.04]
}
```

`status` is one of `tracking`, `idle`, `autopilot`, `mock`, `mock-idle`, `initialising`, `error`. `camera_source` is `camera`, `mock` or `mock-fallback` (camera failed; synthetic frames while it retries). `doa_azimuth_deg` stays `null` until the multi-mic work (§ 3.5) exists. On connect, and after every config change, the server also sends `{"type": "config_update", "config": SystemConfig}`. Clients send partial `SystemConfig` patches (or the legacy flat calibration keys) over the same socket.

> `correlation_curve` is **exactly 256 normalised floats [0.0–1.0]** representing 0.0 m → 4.0 m. Stored in a `useRef` on the client; never in `useState`.

### 4.3 REST API Surface

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/devices` | PortAudio + OpenCV device enumeration (`mock` when synthetic) |
| `GET` | `/api/config` | Full `SystemConfig` JSON |
| `PATCH` | `/api/config` | Deep-merge a partial update (422 if invalid); broadcast over WS |
| `GET` / `POST` | `/api/calibration` | Legacy flat calibration view used by older clients |
| `GET` | `/api/calibration/input-level` | 0.3 s microphone level: `rms`, `peak`, `dbfs`, `quality` |
| `POST` | `/api/calibration/round/{1\|2\|3\|4}` | Run a calibration round (503 + `detail` on device failure) |
| `GET` | `/api/stream/rgb` | MJPEG annotated camera feed (`/video_feed` is an alias) |
| `GET` | `/api/stream/fog` | MJPEG Fog Map |
| `GET` | `/api/pointcloud` | `?max_points=` (default 4000) `&stitched=` (default false): subsampled points + colours |
| `POST` | `/api/export/blend` | Export `.blend` / `.glb` / `.ply`; 409 if one is already running |
| `GET` | `/exports/<file>` | Download export output |
| `GET` | `/healthz` | `ok`, `mock`, `ws_clients`, `sonar_status`, `sonar_error`, `vision_fps`, `camera_mock`, `camera_source`, `camera_error`, `depth_mode`, `depth_mode_active`, `depth_model` (`mode`, `active_provider`, `fps`), `depth_error`, `depth_ms`, `vertex_count`, `blender`, `udp_sent`, `udp_dropped` |

### 4.4 Blender depth-frame stream (`backend/blender_udp.py` → Blender extension, UDP 5005)

Per new vision frame the backend sends a 320x240 `uint8` nearness map (0 = background/culled, 255 =
nearest subject surface, subject-isolation mask already applied) plus a 160x120 RGB plane. The
extension writes both into float image datablocks and a Geometry Nodes modifier displaces a dense
grid with them (no per-vertex Python). One frame (134 KB) exceeds the maximum UDP datagram (65,507 B),
so it is sent as 32 KB chunks behind this 32-byte little-endian header (`<4sIBBHHHffff`):

| Field | Type | Meaning |
|---|---|---|
| magic | 4s | `SDF1` |
| frame_id | u32 | increments per frame; older chunks are dropped |
| chunk / chunk_count | u8 / u8 | position of this datagram in the frame |
| width / height | u16 / u16 | depth map size (320 x 240) |
| color_div | u16 | 0 = no colour; n = RGB plane downscaled by n follows (2) |
| z_sonar | f32 | physical sonar distance (m) |
| relief | f32 | metres of displacement at depth 255 (`subject_thickness_m + relief_depth_span_m`) |
| fov_deg | f32 | horizontal camera FOV |
| plane_m | f32 | camera distance to the depth-0 plane (`z_sonar + subject_thickness_m`) |

The reference parser is `blender/sonar_imaging/protocol.py`; `backend/tests/test_blender_udp.py`
checks that sender and receiver agree. Displacement toward the camera = `depth/255 * relief`, so the
mesh is metric.

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
- Capture 30 frames of the empty scene with no subject present.
- Store the median relative depth (`calibration_bg_mask.npy`) and the median 160×120 grey frame (`calibration_bg_gray.npy`).
- Afterwards, pixels whose grey value still matches the empty scene are excluded from the fog map, the 3D cloud and the Blender stream, and the fast heuristic uses the difference as its nearness cue.
- Sets `calibration.background_fog_captured = true`.

### Round 4 — Multi-Point Metric Anchor (Linear Regression)
- Operator places a flat target at known distances: **0.5 m, 1.5 m, 2.5 m**.
- For each (`POST /api/calibration/round/4` with `{"distance_m": d}`): record the raw sonar depth (current α/β undone) and the anchor relative depth.
- Fit `true_distance = α · Z_sonar_raw + β` via `numpy.polyfit(degree=1)`. This calibrates the sonar's own scale and offset (fitting against `D_raw` would corrupt them, since § 3.1 uses the same α/β).
- Sets `calibration.metric_scale_factor = α`, `calibration.metric_offset_m = β`. In mock mode a clean three-point sweep is synthesised.

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

## 7. shadcn/ui Component Inventory (`dashboard/src/components/ui/`)

> **Dashboard agents:** import from `@/components/ui/<name>` — all primitives are pre-installed. Do NOT add Recharts, D3, or any SVG charting library for high-frequency data. Use raw `<canvas>` + `requestAnimationFrame` instead.

| Component | Import Path | Typical use |
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
| `<Accordion>`, `<Alert>`, `<Label>`, `<Toggle>` | `@/components/ui/<name>` | Collapsible sections, status messages, form labels |

---

## 8. Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SONAR_MOCK_MODE` | `false` | Synthetic data — no hardware required |
| `SONAR_SAMPLE_RATE` | `48000` | Default for `hardware.sample_rate_hz` (a saved config value wins) |
| `SONAR_UDP_HOST` | `127.0.0.1` | Blender UDP target |
| `SONAR_UDP_PORT` | `5005` | Blender UDP port |
| `SONAR_UDP_HZ` | `60` | How often the Blender bridge checks for a new frame (each frame is sent once) |
| `SONAR_WS_HZ` | `20` | Dashboard telemetry rate |
| `SONAR_CAMERA_INDEX` | `0` | Default for `hardware.camera_device_index` (a saved config value wins) |
| `SONAR_REAL_CAMERA` | `false` | Use the real camera even when `SONAR_MOCK_MODE=true` |
| `DEPTH_AUTO_DOWNLOAD` | `false` | Fetch the ONNX depth model on first use of `neural_onnx` |
| `DEPTH_MODEL_PATH` | `models/depth_anything_v2_vits.onnx` | ONNX model file path |
| `EXPORTS_DIR` | `exports` | Export output; relative paths are under `backend/` (a value starting with `backend/` is repo-relative) |
| `BLENDER_BIN` | auto-detected | Blender executable for exports (`PATH`, then `~/blender*/blender`) |
