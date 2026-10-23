# Sonar Imaging

**An interactive real-time exhibition installation** that fuses active acoustic Time-of-Flight (ToF) sonar — using studio microphones and loudspeakers — with lightweight monocular AI depth maps to construct, colorize, and export 3D point clouds and static Blender (`.blend`) scenes.

---

## System Overview

```
┌──────────────────────────────────────────────────────────────┐
│  Studio Speaker  →  LFM Chirp (16–21 kHz)  →  Exhibition    │
│  Studio Mic      ←  Echo Return             ←  Subject/Wall  │
│         ↓                                                     │
│  FastAPI Backend (Port 8000)                                  │
│    • Acoustic ToF DSP      • Fog Map Generator                │
│    • AI Depth (ONNX)       • Open3D Stitcher                  │
│         ↓                          ↓                          │
│  Dashboard (Port 3000)     Blender UDP (Port 5005)            │
│  shadcn/ui Operator Panel  Live 3D Viewport + .blend Export   │
└──────────────────────────────────────────────────────────────┘
```

## Hardware Requirements

| Component | Specification |
|---|---|
| Studio Microphone | Cardioid condenser, ideally flat response ≥ 16 kHz |
| Loudspeaker | Full-range capable of 16–21 kHz output |
| Audio Interface | ≥ 2 in / 2 out, 48 kHz, ≥ 24-bit (e.g. Focusrite Scarlett, MOTU) |
| Camera | Any USB/CSI webcam; wide-angle (60–80° FOV) preferred |
| Computer | Python 3.11 or 3.12 (3.13+ lacks wheels for the pinned packages), Node.js 18+, Blender 4.2+ (optional, for the live view and exports) |

## Quick Start

> Step-by-step setup, real-hardware notes, troubleshooting and a real-world test log: **[`GETTING_STARTED.md`](GETTING_STARTED.md)**.

One command starts both services and stops both on Ctrl+C:

```bash
./run.sh --setup --mock     # first run (creates the venv, installs packages); then ./run.sh --mock
```

The manual steps:

### 1. Backend

```bash
cd backend
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # set SONAR_MOCK_MODE=true for no-hardware testing
.venv/bin/python main.py     # or: .venv/bin/python main.py --mock
```

Backend health check: `curl http://localhost:8000/healthz`

### 2. Dashboard

```bash
cd dashboard
npm ci
npm run dev                  # → http://localhost:3000
```

### 3. Blender Live Viewport (optional)

The receiver is a Blender 4.2+ / 5 **extension** in `blender/sonar_imaging/`.

```bash
blender --command extension build --source-dir blender/sonar_imaging --output-dir .
# → sonar_imaging_receiver-2.0.1.zip
```

1. In Blender: **Edit ▸ Preferences ▸ Get Extensions ▸ ⌄ ▸ Install from Disk…** and pick the zip
2. In the 3D Viewport press **N** → **Sonar Imaging** tab
3. **Create Displaced Mesh**, then **Start Receiver** (UDP `127.0.0.1:5005`, matches the backend)

The backend streams a subject-isolated depth map and the camera colours; a Geometry Nodes modifier
displaces a dense grid with them, so the subject appears as a connected 3D surface with the background
removed. The panel sets the grid resolution, relief depth, background cutoff and a wireframe overlay.

---

## Mock Mode (No Hardware Required)

Set `SONAR_MOCK_MODE=true` (or pass `--mock` flag) to run the full pipeline with:
- Synthetic LFM chirp correlation curves (crosstalk spike + moving echo peak 0.5–2.5 m)
- Procedural 640×480 frames with animated silhouette ellipse
- Fog Map generated from synthetic depth field

Mock mode does **not** open the microphone or speaker and produces no sound. In real-hardware
mode the sonar emits 16–20 kHz chirps; these are ultrasonic and may not be audible on ordinary
speakers. Do not test them through headphones. See [Real hardware mode](GETTING_STARTED.md#5-real-hardware-mode)
before enabling audio.

## GPU Execution Providers

Neural ONNX inference negotiates available providers in this order: CUDA, Vulkan, ROCm, DirectML,
then CPU. The active provider and measured neural inference FPS are reported by `/healthz` and
WebSocket telemetry; the dashboard shows the provider when neural inference is active.

`backend/requirements.txt` intentionally uses the standard `onnxruntime` PyPI wheel, which
provides the portable CPU fallback but does not include `VulkanExecutionProvider`. A Vulkan loader
and GPU driver alone are not sufficient to enable ONNX Vulkan inference. On this AMD/Vulkan test
machine, `vulkaninfo` detects the Radeon 680M, but `onnxruntime.get_available_providers()` reports
only Azure and CPU. PyPI has no `onnxruntime-vulkan` distribution; Vulkan requires an ONNX Runtime
build compiled with the Vulkan execution provider. For NVIDIA, use a CUDA-enabled ONNX Runtime
build compatible with the installed CUDA/cuDNN libraries instead of the base package. Without a
system CUDA toolkit, the pip wheels can supply the libraries; this was verified on an RTX 3050 Laptop
GPU (driver CUDA 13.4), where neural depth ran at about 19 FPS against 1.5 FPS on CPU:

```bash
backend/.venv/bin/pip uninstall -y onnxruntime
backend/.venv/bin/pip install "onnxruntime-gpu[cuda,cudnn]"
```

The backend calls `onnxruntime.preload_dlls()` before creating the session, which is what makes those
pip-installed libraries loadable. Always verify with:

```bash
backend/.venv/bin/python -c "import onnxruntime as ort; print(ort.get_available_providers())"
```

If an accelerator provider is unavailable or fails to initialize, inference falls back to CPU.

---

## Calibration (4-Round Wizard)

Launch the **Calibration Wizard** from the dashboard. Each round takes under 60 seconds:

| Round | Action | Result |
|---|---|---|
| **1** | Empty room chirp sweep | Sets acoustic blanking distance |
| **2** | Passive room listening | Sets noise floor & detection threshold |
| **3** | Empty room camera capture | Builds background fog subtraction mask |
| **4** | Target at 0.5 m / 1.5 m / 2.5 m | Fits metric anchor regression |

---

## Fog Map — Z-Depth Mist Pass

The **Fog Map** is a normalized atmospheric depth visualization where:
- **Bright (white/cyan)** = subject close to `min_scan_distance_m`
- **Dark (black fog)** = subject approaching `max_scan_distance_m` or beyond

Controlled by dashboard sliders for `min_scan_distance_m`, `max_scan_distance_m`, and falloff curve (`linear` / `exponential` / `squared`).

---

## Mesh Colour Modes

These apply to the dashboard's 3D preview and to exported scenes; the live Blender receiver always
uses the camera colours.

| Mode | Description |
|---|---|
| `optical_rgb` | Raw camera texture projected onto the mesh |
| `fog_gradient` | Near→far colour gradient (customizable hex palette) |
| `acoustic_heatmap` | Echo intensity mapped blue→red |
| `custom_palette` | Operator-defined near/far hex colours |

---

## Exporting a Static `.blend` Scene

Click **"Export Static .blend Scene"** in the dashboard:
1. Backend freezes the current stitched mesh
2. Headless Blender renders vertex colours, Principled BSDF shader, fog environment, and studio camera
3. Downloads available: **`.blend`** (full scene), **`.glb`** (WebXR/AR), **`.ply`** (raw geometry)

---

## Architecture Documentation

- **[`AGENTS.md`](AGENTS.md)** — Complete technical spec: formulas, schemas, threading rules, API surface. Read this before writing any code.
- **[`ROADMAP.md`](ROADMAP.md)** — Phased implementation plan with per-file assignments and acceptance criteria.

---

## Port Map

| Service | Port | Protocol |
|---|---|---|
| FastAPI Backend | `8000` | HTTP + WebSocket |
| Next.js Dashboard | `3000` | HTTP |
| Blender UDP Bridge | `5005` | UDP (localhost only) |

---

## Technology Stack

**Backend:** Python 3.11, FastAPI, `sounddevice`, `scipy`, `numpy`, `opencv-python`, `onnxruntime`, `open3d`, `pydantic-settings`

**Dashboard:** Next.js 14, TypeScript, Tailwind CSS, `shadcn/ui` (new-york style, zinc dark theme)

**3D / Blender:** Blender 4.2+ / 5.x Python API (`bpy`), Geometry Nodes, Cycles / EEVEE

**AI Depth Model:** Depth-Anything-V2-Small (ViT-S, ONNX, about 99 MB fp32; downloaded on demand)

---

## Planned: Multi-Mic TDOA Array

**Not built yet.** The plan is GCC-PHAT triangulation across 2–4 microphones with a live acoustic polar
radar in the dashboard (ROADMAP Phase 6). The specification is in [`AGENTS.md § 3.5`](AGENTS.md).
