# AGENTS.md — Sonar Imaging Exhibition System

## Project Overview
**Sonar Imaging** is a real-time interactive art and engineering installation. It fuses 2D camera silhouettes/depth with true acoustic Time-of-Flight (ToF) ranging using a studio microphone and loudspeaker (16–20 kHz Linear Frequency Modulated chirps) to drive a live 3D point cloud inside Blender at 60 FPS.

---

## System Architecture & Port Map

| Tier | Directory | Tech Stack | Port / Protocol | Responsibility |
| :--- | :--- | :--- | :--- | :--- |
| **1. Core Engine** | `/backend` | Python 3.11+, FastAPI, `sounddevice`, `scipy`, `opencv` | `8000` (HTTP/WS) | Runs full-duplex audio chirps, matched filtering, camera masking, and sensor fusion. |
| **2. Calibration UI** | `/dashboard` | Next.js / Vite, TypeScript, Tailwind, `shadcn/ui` | `3000` (HTTP) | Operator control panel for crosstalk blanking, noise thresholding, and ground-truth calibration. |
| **3. 3D Renderer** | `/blender` | Blender 4.x Python (`bpy`), Geometry Nodes | `5005` (Local UDP) | Receives fused 3D coordinates and acoustic wave triggers; renders point cloud for exhibition projector. |

---

## Strict Rules for All AI Agents

### Rule 1: Never Block the Audio or Blender Threads
- **Backend Agents:** `sounddevice.playrec()` and OpenCV capture MUST run in dedicated background worker threads or `asyncio` executors. Never run heavy FFT or cross-correlation inside the main FastAPI event loop.
- **Blender Agents:** Never use `while True:` loops or blocking sockets inside `/blender/live_receiver.py`. Always use `sock.setblocking(False)` inside a `bpy.app.timers` callback returning `0.016` (60 FPS), draining the UDP buffer to the latest packet each frame.

### Rule 2: High-Frequency UI Rendering in `shadcn/ui`
- **Frontend Agents:** The WebSocket streams telemetry at 15–30 Hz. Do NOT store high-frequency arrays (`correlation_curve`) in standard React `useState` tied to DOM elements or SVG charts (like Recharts)—it will freeze the browser.
- Store incoming high-frequency telemetry in a `useRef` and draw the cross-correlation curve and threshold lines inside a `requestAnimationFrame` loop on an HTML5 `<canvas>`.
- Use `shadcn/ui` components (`<Slider>`, `<Switch>`, `<Card>`, `<Badge>`) strictly for layout, state controls, and debounced calibration inputs.

### Rule 3: Always Maintain Hardware Mock Mode
- Agents frequently test code in environments without a physical studio mic, audio interface, or webcam.
- Any modification to `sonar_engine.py` or `vision_engine.py` MUST preserve `MOCK_MODE`. When `SONAR_MOCK_MODE=true`, synthesize a realistic correlation curve with a simulated crosstalk spike at index 0–20, Gaussian noise floor, and a moving target echo peak between `0.5m` and `2.5m`.

---

## Data Contracts (Single Source of Truth)

### 1. WebSocket Telemetry (`Server -> Dashboard` @ 15 Hz)
Endpoint: `ws://localhost:8000/ws/telemetry`
```json
{
  "timestamp": 1729700000.12,
  "status": "tracking",
  "mock_mode": false,
  "sonar_depth_m": 1.45,
  "peak_amplitude": 0.34,
  "fps_vision": 30.0,
  "ping_rate_hz": 10.0,
  "correlation_curve": [0.0, 0.0, 0.12, 0.85, 0.04]
}
```
*(Note: `correlation_curve` is downsampled to exactly 256 normalized floats [0.0–1.0] representing 0.0 m to 4.0 m range)*

### 2. Calibration State (`Dashboard -> Server` via WS or `POST /api/calibration`)
```json
{
  "chirp_f_start": 16000,
  "chirp_f_end": 20000,
  "chirp_duration_ms": 20,
  "blanking_distance_cm": 35.0,
  "detection_threshold": 0.12,
  "depth_scale_multiplier": 1.0,
  "depth_zero_offset_cm": 0.0,
  "smoothing_alpha": 0.25,
  "autopilot_timeout_sec": 10
}
```

### 3. Blender UDP Packet (`Server -> Blender` @ 30–60 Hz)
Target: `127.0.0.1:5005` (Max payload size: < 8192 bytes to prevent UDP fragmentation)
```json
{
  "state": "tracking",
  "sonar_z": 1.45,
  "ping_pulse": 1,
  "audio_energy": 0.42,
  "points": [[0.1, 0.2, 1.45], [0.3, -0.1, 1.52]]
}
```

---

## Calibration Guide & Acoustic Physics Reference

When working on the DSP or Calibration UI, understand the three physical zones of the cross-correlation graph:

### Zone A — Direct Acoustic Bleed (0 cm to `blanking_distance_cm`)
The sound traveling directly from the speaker cone into the studio mic capsule. The backend sets all correlation samples in this index range to 0.0. On the dashboard `<CorrelationCanvas />`, render this zone with a semi-transparent red overlay as the **"Blanking Zone"** controlled by the Blanking Slider.

### Zone B — Target Echo Peak (`blanking_distance_cm` to 400 cm)
The highest peak in this region that exceeds `detection_threshold` is selected as the true echo. Distance is calculated as:
```
distance_m = ((peak_sample_index / sample_rate) * 343.0) / 2.0
```

### Zone C — Room Reverb & Noise Floor
Any peak below `detection_threshold` is ignored, causing the system to hold its last valid Exponential Moving Average (EMA) depth or enter idle autopilot mode after `autopilot_timeout_sec`.

---

## File Map

```
sonar-imaging/
├── AGENTS.md                       ← You are here
├── backend/
│   ├── main.py                     ← FastAPI app, WebSocket manager, lifecycle
│   ├── config.py                   ← Pydantic settings & calibration state
│   ├── sonar_engine.py             ← LFM chirp generation & cross-correlation DSP
│   ├── vision_engine.py            ← OpenCV camera capture & contour extraction
│   ├── blender_bridge.py           ← Non-blocking UDP sender to Blender (port 5005)
│   └── requirements.txt
├── dashboard/                      ← Next.js 14 + shadcn/ui calibration panel
│   ├── package.json
│   ├── next.config.mjs
│   └── src/
│       ├── app/
│       │   ├── layout.tsx
│       │   └── page.tsx            ← Main dashboard page
│       ├── components/
│       │   └── sonar/
│       │       ├── CorrelationCanvas.tsx   ← 60fps canvas renderer
│       │       ├── VisionPreview.tsx       ← MJPEG viewer
│       │       ├── CalibrationPanel.tsx    ← shadcn Slider controls
│       │       └── TelemetryHeader.tsx     ← Status badges
│       ├── hooks/
│       │   └── useSonarSocket.ts           ← Reconnecting WebSocket hook
│       └── types/
│           └── sonar.ts                    ← Shared TypeScript interfaces
└── blender/
    └── live_receiver.py            ← bpy.app.timers UDP listener (port 5005)
```

## Development Commands

### Backend
```bash
cd backend
pip install -r requirements.txt

# Run with real hardware
python main.py

# Run in mock mode (no mic/camera needed)
SONAR_MOCK_MODE=true python main.py
# or:
python main.py --mock
```

### Dashboard
```bash
cd dashboard
npm install
npm run dev          # http://localhost:3000
```

### Blender
1. Open Blender 4.x
2. Go to **Scripting** workspace
3. Open `blender/live_receiver.py`
4. Click **Run Script** — the UDP listener starts immediately at 60 FPS

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SONAR_MOCK_MODE` | `false` | Enable synthetic data (no hardware required) |
| `SONAR_SAMPLE_RATE` | `44100` | Audio sample rate in Hz |
| `SONAR_UDP_HOST` | `127.0.0.1` | Blender UDP target host |
| `SONAR_UDP_PORT` | `5005` | Blender UDP target port |
| `SONAR_WS_HZ` | `15` | WebSocket telemetry broadcast rate |
| `SONAR_UDP_HZ` | `30` | Blender UDP send rate |
