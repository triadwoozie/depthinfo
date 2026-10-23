# ROADMAP.md — Autonomous Agent Execution Plan

Each phase is self-contained and includes strict acceptance criteria. Agents must maintain `SONAR_MOCK_MODE=true` compatibility across **all** phases so every feature runs deterministically on machines without studio audio interfaces or cameras.

> **Assignment protocol:** An agent assigned "Execute Phase N" must produce every file listed under that phase, pass all self-verification criteria, and leave the codebase in a state where the next phase can begin without human intervention.

---

## Phase 1: Schemas, Hardware Device Enumerator & Mock Telemetry Spine

**Assigned To:** Backend / Full-Stack Agent
**Files to Build:**
- `backend/config.py`
- `backend/devices.py`
- `backend/main.py` *(replace stub)*
- `dashboard/src/types/schema.ts` *(replace stub)*
- `dashboard/src/hooks/useSonarEngine.ts`

### Tasks

1. **`backend/config.py`** — Implement the full `SystemConfig` Pydantic V2 model (all sections: `hardware`, `acoustics`, `calibration`, `vision_and_fog`, `blender_style`, `experimental_multi_mic`). Auto-load from `backend/config_state.json` on startup; auto-save on every `PATCH /api/config` call.

2. **`backend/devices.py`** — Use `sounddevice.query_devices()` to enumerate all PortAudio input/output devices with their channel counts and default sample rates. Use `cv2` index probing (0–9) to enumerate available cameras. When `SONAR_MOCK_MODE=true`, return a hardcoded list of virtual devices.

3. **`backend/main.py`** — Implement:
   - `GET /api/devices` — real or mock device list
   - `GET /api/config` / `PATCH /api/config` — read/partial-merge `SystemConfig`
   - `WS /ws/telemetry` — broadcast `SonarTelemetryFrame` at 20 Hz; accept partial `SystemConfig` patches from clients
   - `GET /healthz`

4. **`dashboard/src/types/schema.ts`** — TypeScript mirror of `SystemConfig` + `SonarTelemetryFrame` exactly matching `AGENTS.md § 4.1–4.2`. Export `DEFAULT_CONFIG`.

5. **`dashboard/src/hooks/useSonarEngine.ts`** — Auto-reconnecting WebSocket hook. `correlation_curve` in `useRef` (never `useState`). Expose `config`, `telemetry`, `curveRef`, `patchConfig(partial)`, `pingMs`.

### Self-Verification

```bash
# Backend running with --mock:
curl http://localhost:8000/api/devices
# → JSON with audio + camera device arrays

curl -X PATCH http://localhost:8000/api/config \
  -H 'Content-Type: application/json' \
  -d '{"vision_and_fog": {"min_scan_distance_m": 0.5}}'
# → Updated SystemConfig; WS /ws/telemetry reflects change within 50 ms
```

---

## Phase 2: Acoustic Synthesizer, Bi-Static Sonar DSP & Audio Calibration Rounds 1 & 2

**Assigned To:** Audio DSP Agent
**Files to Build:**
- `backend/sonar_dsp.py`
- Extend `backend/main.py` with `POST /api/calibration/round/1` and `/round/2`

### Tasks

1. **Waveform Generator** — Support `linear_fm_chirp`, `log_chirp`, `audible_ping`, `ultrasonic_sweep`. All controlled by `acoustics.*` fields. Soft-clipping via `np.tanh(V · x)`.

2. **Background Audio Worker** — `sounddevice.playrec()` at `sample_rate_hz = 48000` in a daemon thread. Never block the FastAPI event loop.

3. **Matched Filter Pipeline** — FFT cross-correlation per `AGENTS.md § 3.1`. Bi-static rig blanking using Euclidean distance between `speaker_pose` and `microphones[0]`. EMA depth smoothing (α = `calibration.smoothing_alpha`).

4. **Calibration Round 1** — 10-chirp empty-room sweep → sets `calibration.blanking_distance_cm`.

5. **Calibration Round 2** — 5 s passive listening → sets `calibration.ambient_noise_floor_rms` and `calibration.detection_threshold` (3× RMS, clamped).

6. **Mock Mode** — Synthesize a realistic 256-float correlation curve with crosstalk spike at indices 0–20, Gaussian noise floor (σ = 0.03), and a moving echo peak oscillating 0.5–2.5 m at 0.15 Hz.

### Self-Verification

```bash
# Verify chirp generation adapts to config change:
curl -X PATCH http://localhost:8000/api/config \
  -d '{"acoustics": {"freq_start_hz": 14000, "speaker_volume": 0.8}}'
# → Subsequent audio buffer uses new frequency sweep

# Calibration rounds:
curl -X POST http://localhost:8000/api/calibration/round/1
# → {"status": "ok", "blanking_distance_cm": 42.3}

curl -X POST http://localhost:8000/api/calibration/round/2
# → {"status": "ok", "detection_threshold": 0.11, "ambient_noise_floor_rms": 0.037}
```

---

## Phase 3: Real-Time Fog Map Engine, AI Depth Inference & 3D Stitcher

**Assigned To:** Computer Vision & 3D Geometry Agent
**Files to Build:**
- `backend/depth_ai.py`
- `backend/stitcher.py`
- Extend `backend/main.py` with `GET /api/stream/fog`, `GET /api/stream/rgb`, `POST /api/calibration/round/3`, `POST /api/calibration/round/4`

### Tasks

1. **`backend/depth_ai.py`** — Load Depth-Anything-V2-Small via `onnxruntime`. Include an automatic ONNX weight downloader (from HuggingFace Hub). Provide a deterministic procedural fallback depth map for offline / CI environments (concentric distance rings centred on frame midpoint).

2. **Real-Time Fog Map Generator** — Per `AGENTS.md § 3.2`:
   - Anchor relative depth to `Z_sonar` via metric formula
   - Apply hard frustum clipping at `[min_scan_distance_m, max_scan_distance_m]`
   - Apply selected falloff curve (`linear` / `exponential` / `squared`)
   - Apply selectable colourmap (`mist_grayscale`, `thermal`, `viridis`, `custom_hex`)
   - MJPEG-stream result at 30 FPS via `GET /api/stream/fog`

3. **`GET /api/stream/rgb`** — MJPEG stream of raw camera frame with contour and sonar depth overlay at 30 FPS.

4. **Calibration Round 3** — Capture 30 empty-room frames; compute median background depth mask; store as `calibration_bg_mask.npy`. Sets `calibration.background_fog_captured = true`.

5. **Calibration Round 4** — Three-point metric anchor at 0.5 m, 1.5 m, 2.5 m. `numpy.polyfit(degree=1)` regression. Sets `calibration.metric_scale_factor` and `calibration.metric_offset_m`.

6. **`backend/stitcher.py`** — Per `AGENTS.md § 3.3`:
   - Pinhole unprojection with `camera_pose` extrinsics
   - Rolling ring buffer of `temporal_stitch_frames` depth + RGB frames
   - Voxel downsampling, outlier removal, normal estimation (Open3D)
   - Colour routing per `blender_style.color_mode`
   - Surface reconstruction toggle: point cloud vs. Screened Poisson / BPA mesh

### Self-Verification

```bash
# Fog Map stream:
# Open http://localhost:8000/api/stream/fog in a browser → smooth 30 FPS mist

# Adjust scan range and see immediate Fog Map change:
curl -X PATCH http://localhost:8000/api/config \
  -d '{"vision_and_fog": {"max_scan_distance_m": 1.5}}'
# → Fog Map clips sharply at 1.5 m depth

# Stitcher performance:
# stitcher.output_frame() returns (N,3) vertices + (M,3) faces in < 25 ms
```

---

## Phase 4: Polished shadcn/ui Operator Dashboard & Calibration Wizard

**Assigned To:** Frontend UI Agent
**Files to Build:**
- `dashboard/src/components/hardware/DeviceSelector.tsx`
- `dashboard/src/components/hardware/RigCanvas.tsx`
- `dashboard/src/components/acoustics/AcousticSynthPanel.tsx`
- `dashboard/src/components/acoustics/OscilloscopeCanvas.tsx`
- `dashboard/src/components/vision/FogMapMonitor.tsx`
- `dashboard/src/components/vision/ScanRangeSlider.tsx`
- `dashboard/src/components/calibration/CalibrationWizard.tsx`
- `dashboard/src/components/blender/ColorModeDrawer.tsx`
- `dashboard/src/components/blender/ExportPanel.tsx`
- `dashboard/src/components/experimental/MultiMicLab.tsx`
- `dashboard/src/app/page.tsx`

### Tasks

1. **Top Device & Telemetry Bar** — `<Select>` dropdowns (Speaker, Mic, Camera populated from `GET /api/devices`). Live `<Badge>` readouts: Sonar Depth (m), Vision FPS, WS RTT, Mock Mode indicator.

2. **Center Split Viewport:**
   - **Left:** `FogMapMonitor.tsx` — `<img>` MJPEG from `/api/stream/fog` with dual-thumb `ScanRangeSlider.tsx` overlay (0.2–4.0 m range), `<ToggleGroup>` for fog falloff curve, fog density `<Slider>`.
   - **Right tab 1:** `<img>` MJPEG from `/api/stream/rgb`.
   - **Right tab 2:** React-Three-Fiber interactive 3D preview of live stitched point cloud (via WebSocket vertex data).

3. **`RigCanvas.tsx`** — 2D top-down schematic (SVG or canvas) showing Camera, Speaker, and Mic positions as draggable icons. Clicking opens an `<Input>`-based XYZ/angle editor. FOV and acoustic cones rendered as overlapping arcs.

4. **`AcousticSynthPanel.tsx` + `OscilloscopeCanvas.tsx`:**
   - 60 FPS `<canvas>` drawing `curveRef` (256 floats) with draggable red blanking zone and dashed green threshold line.
   - `<Select>` for waveform type. `<Slider>` for start/end Hz, duration, ping rate, volume. `<Switch>` for Mute / Ultrasonic Silent Mode.

5. **`CalibrationWizard.tsx`** — `<Dialog>` stepper (Rounds 1–4). Animated `<Progress>` bar per round. Live diagnostic text. Pass/fail quality score badge.

6. **`ColorModeDrawer.tsx` + `ExportPanel.tsx`** — `<ToggleGroup>` for colour mode. Mesh reconstruction radio. **"Export Static .blend Scene"** `<Button>` → polls `POST /api/export/blend` → shows download links.

### Design System Rules
- Background: `zinc-950`. Accent: `emerald-500`. Secondary: `cyan-500`. Warning: `amber-500`.
- No Recharts / D3 for high-frequency data. Raw `<canvas>` + `requestAnimationFrame` only.
- All slider changes debounced 300 ms before `PATCH /api/config`.

### Self-Verification
- Open dashboard with mock backend → dual MJPEG feeds load within 2 s.
- Drag blanking zone handle in oscilloscope → backend `blanking_distance_cm` updates; Fog Map clips accordingly.
- Run Calibration Round 1 → progress bar animates; result badge shows new blanking distance.

---

## Phase 5: Live Blender Viewport Bridge & Headless Static `.blend` Exporter

**Assigned To:** Blender Python (`bpy`) Agent
**Files to Build:**
- `backend/blender_udp.py`
- `backend/blender_exporter.py`
- `blender/live_receiver.py` *(replace stub)*
- `blender/export_static_scene.py`
- `blender/template_exhibition.blend`

### Tasks

1. **`backend/blender_udp.py`** — Pack `(points, colors, sonar_z, ping_pulse, audio_energy, color_mode)` into UDP JSON packets (≤ 8192 bytes). Truncate points array if necessary. Non-blocking `socket.setblocking(False)`.

2. **`blender/live_receiver.py`** — `bpy.app.timers` at 0.016 s. Drain full socket buffer; keep only latest packet. Update `SonarPointCloud` mesh vertices and `Col` FloatColorAttribute in-place. Drive `SonarTarget` empty Z-location from `sonar_z`.

3. **`blender/export_static_scene.py`** — CLI args: `--input temp.ply --output scan_<ts>.blend --color_mode <mode>`. Import PLY, create `Col` colour attribute, build full ShaderNodeTree, add atmospheric volume world shader matching dashboard fog settings, position isometric studio camera and 3-point HDRI rig. Export `.blend` + `.glb` + `.ply`.

4. **`backend/blender_exporter.py`** — Triggered by `POST /api/export/blend`. Writes temp PLY, spawns non-blocking subprocess, polls for completion, moves outputs to `backend/exports/`, returns download URLs.

5. **`blender/template_exhibition.blend`** — Base Blender scene with: empty `SonarPointCloud` mesh, `SonarTarget` empty, `SonarPing` ring mesh, exhibition floor plane, 3-point studio light rig, Geometry Nodes modifier ready for point-cloud instancing.

### Self-Verification
```bash
curl -X POST http://localhost:8000/api/export/blend
# → {"status": "ok", "blend_url": "/exports/scan_<ts>.blend",
#    "glb_url": "/exports/scan_<ts>.glb",
#    "ply_url": "/exports/scan_<ts>.ply"}
# Open .blend in Blender 4.x → vertex colours visible, shader connected, camera framed
```

---

## Phase 6: Experimental Multi-Mic TDOA Lab & Exhibition Autopilot

**Assigned To:** Experimental DSP & Systems Agent
**Files to Build:**
- GCC-PHAT extensions in `backend/sonar_dsp.py`
- `dashboard/src/components/experimental/MultiMicLab.tsx`
- Autopilot logic in `backend/main.py`

### Tasks

1. **Multi-Mic GCC-PHAT** — Per `AGENTS.md § 3.5`:
   - Support 2–4 mics from `hardware.microphones[]`, each mapped to a hardware input channel.
   - Compute GCC-PHAT cross-correlation across all mic pairs.
   - Extract TDOA → DoA angle → acoustic hotspot 3D coordinates.
   - Broadcast `doa_azimuth_deg` (and `doa_elevation_deg`) in WS telemetry frame.

2. **`MultiMicLab.tsx`** — Polar SVG radar widget showing live directional acoustic lobes. Mic position editor. TDOA timeline canvas.

3. **Exhibition Autopilot / Watchdog:**
   - When no subject detected inside `[min_scan_distance_m, max_scan_distance_m]` for `N` seconds (`calibration.autopilot_timeout_sec`), emit a gentle ambient breathing depth sweep.
   - Optionally replay the most recent exported `.blend` snapshot as a point cloud animation.
   - Dashboard badge shows `AUTOPILOT` state; exits immediately when subject re-detected.

### Self-Verification
```bash
# Enable multi-mic:
curl -X PATCH http://localhost:8000/api/config \
  -d '{"experimental_multi_mic": {"enabled": true}}'

# WS telemetry now includes:
# "doa_azimuth_deg": 34.2, "doa_elevation_deg": -8.1

# Dashboard Experimental tab shows animated polar radar
```

---

## Post-Exhibition Roadmap (V2.0+)

| Extension | Description |
|---|---|
| **A — Gaussian Splatting** | Replace Poisson meshing with lightweight 3DGS initialized from sonar-anchored Fog Map points |
| **B — Ultrasonic Phased Array** | Drive 4+ tweeters with phase-shifted chirps for beamsteering without moving parts |
| **C — Visitor QR Takeaway** | Render turntable preview and host `.glb` on local network portal; visitors scan QR for mobile AR |
| **D — Neural Denoising** | Train a lightweight noise-conditioned denoising U-Net on exhibition-collected depth pairs |
