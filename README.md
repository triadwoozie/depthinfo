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
| Computer | Python 3.11+, Node.js 18+, Blender 4.x (optional for live view) |

## Quick Start

### 1. Backend

```bash
cd backend
cp .env.example .env        # set SONAR_MOCK_MODE=true for no-hardware testing
pip install -r requirements.txt
python main.py               # or: python main.py --mock
```

Backend health check: `curl http://localhost:8000/healthz`

### 2. Dashboard

```bash
cd dashboard
npm install
npm run dev                  # → http://localhost:3000
```

### 3. Blender Live Viewport (optional)

1. Open **Blender 4.x** → Scripting workspace
2. Open `blender/live_receiver.py` → click **▶ Run Script**
3. UDP listener starts at `127.0.0.1:5005` — the point cloud updates at 60 FPS

---

## Mock Mode (No Hardware Required)

Set `SONAR_MOCK_MODE=true` (or pass `--mock` flag) to run the full pipeline with:
- Synthetic LFM chirp correlation curves (crosstalk spike + moving echo peak 0.5–2.5 m)
- Procedural 640×480 frames with animated silhouette ellipse
- Fog Map generated from synthetic depth field

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

**Dashboard:** Next.js 14, TypeScript, Tailwind CSS, `shadcn/ui` (new-york style, zinc-950 dark theme)

**3D / Blender:** Blender 4.x Python API (`bpy`), Geometry Nodes, Cycles / EEVEE

**AI Depth Model:** Depth-Anything-V2-Small (ViT-S, ONNX, ~24 MB checkpoint)

---

## Experimental: Multi-Mic TDOA Array

Enable in the dashboard's **Experimental** tab to use 2–4 studio microphones for GCC-PHAT acoustic triangulation. Renders a live **Acoustic Polar Radar** showing directional sound lobes and echo hotspots overlaid on the 3D scene.

See [`AGENTS.md § 3.5`](AGENTS.md) for the complete GCC-PHAT mathematical specification.
