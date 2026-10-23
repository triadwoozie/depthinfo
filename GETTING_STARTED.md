# Getting Started

How to run Sonar Imaging from a fresh clone, and a record of how these instructions were
tested on a real machine (including what went wrong). For architecture and formulas see
[`AGENTS.md`](AGENTS.md); for the build plan see [`ROADMAP.md`](ROADMAP.md).

> **Fastest path:** run the backend and dashboard in **mock mode**. It needs no microphone,
> speaker or camera and exercises the whole pipeline with synthetic data.

---

## 1. Requirements

| Tool | Version | Notes |
|---|---|---|
| **Python** | **3.11 or 3.12** | **3.13+ does not work** (see Troubleshooting). `uv` makes this painless. |
| Node.js | 18+ (22 tested) | Dashboard |
| Blender | 4.2+ (5.3 beta tested) | Optional: static export and the live viewport |
| PortAudio | system library | Only for real audio (`libportaudio2` on Debian/Ubuntu, `portaudio` on Fedora) |
| `uv` | any recent | Optional but recommended: `pip install --user uv` |

Real-hardware mode additionally needs an audio interface able to play and record 16 to
21 kHz, and a camera. See [Real hardware](#5-real-hardware-mode).

---

## Quick start: one command

```bash
./run.sh --setup --mock     # first run: creates the venv, installs npm packages, builds, starts
./run.sh --mock             # afterwards
```

`./run.py` is the Python equivalent with identical flags. Both start the backend and the
dashboard, wait until each answers, print the URLs, and **stop everything cleanly on Ctrl+C**
(open video streams included). Logs are written to `logs/`.

| Flag | Effect |
|---|---|
| `--mock` | Synthetic audio and video (no hardware) |
| `--real-camera` | Use the real camera even with `--mock` (synthetic sonar, live video) |
| `--model` | Download the neural depth model (99 MB) if missing |
| `--dev` | Dashboard dev server with hot reload |
| `--rebuild` | Force a production dashboard rebuild (it also rebuilds automatically when sources are newer) |
| `--backend-only` | Do not start the dashboard |
| `--open` | Open the dashboard in a browser |
| `--setup` | Create `backend/.venv` and run `npm ci` if they are missing |

The manual steps below do the same thing one piece at a time.

---

## 2. Start the backend (port 8000)

```bash
git clone https://github.com/triadwoozie/depthinfo.git
cd depthinfo/backend

# Python 3.12 virtualenv (uv downloads the interpreter if you do not have one)
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt

cp .env.example .env          # then set SONAR_MOCK_MODE=true for no hardware
.venv/bin/python main.py --mock
```

Without `uv`: `python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt`.

The `--mock` flag and `SONAR_MOCK_MODE=true` are equivalent. Check it is alive:

```bash
curl -fsS http://localhost:8000/healthz
curl -fsS http://localhost:8000/api/devices
```

`/healthz` reports `mock`, `sonar_status`, `sonar_error`, `vision_fps`, `depth_backend`,
`vertex_count` and the detected Blender path. API docs: <http://localhost:8000/docs>.

## 3. Start the dashboard (port 3000)

In a second terminal:

```bash
cd depthinfo/dashboard
npm ci
npm run build && npm run start      # production build, recommended
# or: npm run dev                    # hot reload
```

Open **<http://localhost:3000>**. The header badges should show Mic *Active*, Vision FPS
and (in mock mode) *Mock mode*. The dashboard talks to `http://localhost:8000`; set
`NEXT_PUBLIC_API_URL` to point it elsewhere (the backend's CORS only allows
`localhost:3000` by default).

## 4. What to try first

1. **Fog map / Optical / 3D cloud** tabs in *Spatial monitor*: all three should stream.
2. Drag the **scan range** slider: the fog map clips at the new distances.
3. **Echo analyzer**: drag the red blanking zone; the value is stored in the backend.
4. **Calibrate** (header button): chirp, gating and smoothing sliders.
5. **Export static .blend scene** (left column): the dialog lists `.blend`, `.glb`, `.ply`.
   Files land in `backend/exports/` and are also served at `http://localhost:8000/exports/`.

From the command line:

```bash
curl -X PATCH localhost:8000/api/config -H 'Content-Type: application/json' \
     -d '{"vision_and_fog": {"max_scan_distance_m": 1.5}}'
curl -X POST localhost:8000/api/export/blend
```

### Depth modes and the point cloud

*Spatial monitor ▸ Depth inference mode* (or `vision_and_fog.depth_inference_mode`):

| Mode | What runs | Cost |
|---|---|---|
| **Disabled / Direct sonar** | Flat plane at the sonar distance | none |
| **Fast heuristic** (default) | Edge, brightness and background-difference cues, bilateral-smoothed | about 1 ms |
| **Neural ONNX** | Depth-Anything-V2-Small in a background thread | about 1.5 FPS of depth updates on CPU |

All modes feed the same hybrid equation, with the sonar as the metric anchor:

```
Z(u,v) = Z_sonar + (D_rel(u,v) - D_anchor) * relief_depth_span_m
```

`D_rel` is 0 for near and 1 for far; `D_anchor` is the depth of the *subject* inside the acoustic
beam cone (`acoustic_beam_deg`, default 20°). It is taken as a low percentile
(`anchor_percentile`, default 5) rather than the median, because the sonar reports the nearest
reflector: a median over the cone lands on the background whenever the subject covers less than half
of it, which pushes the subject in front of the sonar distance and the wall onto it. Each pixel is then unprojected through the pinhole model and
the camera pose, so the cloud has true front-to-back relief. If a mode cannot run (for example the
model file is missing) the dashboard shows why and the **Running:** badge shows the mode that is
really active. The 3D tab and the Blender stream show the newest single frame; the exported scene
uses the fused (stitched) cloud. The camera is read in its own thread; if it cannot be opened or
disconnects, the app logs a warning, shows *Camera unavailable: synthetic video* and keeps
retrying every 5 s.

### Subject isolation (background culling)

The 3D cloud keeps only points that are inside the scan range, brighter than the fog cutoff
(`fog_discard_threshold`, default 0.12) **and** no more than `subject_thickness_m` (default 0.30 m)
behind the sonar anchor; everything else (walls, floating room dust) is dropped. Both are sliders in
*Spatial monitor*. The relief span is `relief_depth_span_m` (default 0.45 m, the nose-to-back-of-head
depth of a person) and the fog falloff now defaults to **linear** (existing `config_state.json`
files keep their saved value).

**Calibrate the empty background first (wizard Round 3).** On a synthetic scene with known ground
truth, isolation with a calibrated background kept 100% of the subject with under 2% background
leakage; the cue heuristic alone cannot tell a wall from a person, and a gate alone cannot separate
them if the anchor sits on the wall. The neural model's isolation has not been measured on a real
person.

For the neural mode, depth is normalised between its 2nd and 98th percentiles (not min-max, which one
outlier pixel can ruin) and then passed through a subtle CLAHE pass (`depth_contrast_enhance`, a switch
in the panel) to bring out nose, eyes and lips. CLAHE trades global relief (about 25% lower overall depth
spread in a live test) for local detail, so compare with the switch on and off.

### Calibration wizard

**Calibrate** opens a guided wizard: a live microphone level meter, then Rounds 1 to 4
(blanking, noise floor, empty background, metric anchor) with progress, pass/check/fail verdicts
and a diagnostics log. Every step calls the real backend endpoints. The *Manual* tab keeps the
sliders.

### Blender live viewport (optional)

The receiver is a Blender extension in `blender/sonar_imaging/`.

```bash
blender --command extension build --source-dir blender/sonar_imaging --output-dir .
```

In Blender: **Edit ▸ Preferences ▸ Get Extensions ▸ (menu) ▸ Install from Disk…**, pick
`sonar_imaging_receiver-1.0.0.zip`. Then in the 3D viewport press **N**, open the
**Sonar Imaging** tab, click **Create Sonar Cloud Mesh**, then **Start Receiver**
(UDP `127.0.0.1:5005`). The backend must be running.

---

## 5. Real hardware mode

Set `SONAR_MOCK_MODE=false` (the default) and start `python main.py`.

**Before you play sound:** the sonar emits 16 to 20 kHz chirps ten times a second. Start with
a low volume (`{"acoustics": {"speaker_volume": 0.2}}`), keep it away from ears and pets, and
do not use headphones.

1. **Pick devices.** `GET /api/devices` lists them. Choose input and output in the dashboard
   (*Hardware and rig*). The backend saves their names and re-resolves current PortAudio IDs at
   runtime, since those numeric IDs can change after reboot. Existing configs without saved names
   use the current system-default devices. For command-line configuration, set
   `hardware.input_audio_device_name` and `hardware.output_audio_device_name` to stable name
   substrings (for example, `"USB Audio"`); both devices must be available through a compatible
   PortAudio host API.
2. **Route audio to real speakers.** The default output must be able to reproduce 16 to 20 kHz.
   A Bluetooth headset very likely cannot (see test log). Use built-in speakers or an audio interface.
3. **Calibrate in an empty room, in order** (also available as `POST` endpoints):

   | Round | Command | Needs |
   |---|---|---|
   | 1 Blanking | `curl -X POST localhost:8000/api/calibration/round/1` | 10 chirps, no subject |
   | 2 Noise floor | `curl -X POST localhost:8000/api/calibration/round/2` | 5 s of silence |
   | 3 Background | `curl -X POST localhost:8000/api/calibration/round/3` | Empty scene in front of the camera |
   | 4 Metric anchor | `curl -X POST localhost:8000/api/calibration/round/4 -H 'Content-Type: application/json' -d '{"distance_m": 0.5}'` | Flat target at 0.5, then 1.5, then 2.5 m |

   Round 1 returns `"quality": "noisy"` and Round 2 returns `"quality": "input_too_loud"`
   when the audio path is not usable. Treat those as "fix the setup", not "carry on".
4. **Depth model (optional).** Without it a procedural depth map is used. For the real
   Depth-Anything-V2-Small (99 MB): start with `DEPTH_AUTO_DOWNLOAD=true`, or place
   `depth_anything_v2_vits.onnx` in `backend/models/`. Expect about 1.5 FPS on CPU.
5. **Camera.** `hardware.camera_device_index` (default 0). The camera is held open by the
   backend, so other apps cannot use it while it runs.

### Configuration

`backend/.env` (see `.env.example`): `SONAR_MOCK_MODE`, `SONAR_SAMPLE_RATE`, `SONAR_UDP_HOST`,
`SONAR_UDP_PORT`, `SONAR_UDP_HZ`, `SONAR_WS_HZ`, `DEPTH_MODEL_PATH`, `DEPTH_AUTO_DOWNLOAD`,
`EXPORTS_DIR`, `BLENDER_BIN`. Runtime settings live in `backend/config_state.json` (created on
the first change, git-ignored); delete it to reset to defaults.

| Service | Port |
|---|---|
| Backend (HTTP, WebSocket, MJPEG) | 8000 |
| Dashboard | 3000 |
| Blender UDP stream | 5005 (localhost only) |

### Stopping

`Ctrl+C` in each terminal. If a browser still has a stream open, the backend can take a
while to exit; `kill -9 <pid>` is safe (find it with `ss -ltnp | grep :8000`).

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `pip install` fails with *Failed to build 'scipy'* | Python 3.13/3.14: no wheels for the pinned numpy/scipy/open3d | Use Python 3.11 or 3.12 (`uv venv --python 3.12`) |
| `/healthz` shows `"sonar_status": "error"` and a `sonar_error` | Audio device cannot be opened | Read `sonar_error`. *Invalid number of channels*: wrong device id. *Device unavailable*: another app (PipeWire/JACK) owns it. Use `null` for the system default. |
| Sonar says `tracking` but depth wanders, curve is flat | No real echo: speaker cannot emit 16 to 20 kHz, or mic does not hear it | Fix routing/hardware; run Rounds 1 and 2 and read `quality` |
| `[Errno 98] address already in use` on port 8000 | A backend is already running | Stop it first (see Stopping) |
| Dashboard loads but is empty / *Waiting for backend* | Backend not running or CORS | Start the backend; use `localhost:3000`, not a LAN IP |
| Blender not found on export | Not on `PATH` | Set `BLENDER_BIN=/path/to/blender` in `backend/.env` |
| `Cannot listen on UDP 5005` in Blender | Another receiver (or a second Blender) is bound | Close it, or change `SONAR_UDP_PORT` and the extension's port field |
| OpenCV/ALSA lines such as `Camera index out of range`, `pa_linux_alsa.c` in the log | Harmless device probing noise | Ignore |

---

## 7. Real-world test log

A fresh `git clone` of `main` was installed and run following only the steps above, then
exercised on a real machine. Date: 2026-10-23.

**Machine:** Fedora Linux, 12 cores, 14 GB RAM, system Python 3.14, Node 22.23, Blender 5.3
beta, built-in webcam (640×480) and laptop audio codec (ALC256), default output a Bluetooth
headset, PipeWire audio.

### Install and start (fresh clone)

| Step | Result |
|---|---|
| `git clone` | 3 s |
| `pip install` on system Python 3.14 | **Failed** after 22 s (scipy build): documented under Troubleshooting |
| `uv venv --python 3.12` + `uv pip install -r requirements.txt` | 1.7 s with a warm uv cache (venv is 1.5 GB; a cold install downloads it all) |
| Backend start to `/healthz` OK | about 8 s |
| `npm ci` | 8 s |
| `npm run build` | 20 s |
| `npm run dev` | ready in about 1.2 s |
| `blender --command extension build` from the repo root | OK, 9 KB zip; installs and enables via the extension system |
| Second backend on port 8000 | Clear `address already in use` error |

### Mock mode

| Check | Result |
|---|---|
| `/healthz`, `/api/devices`, `/api/config` PATCH (valid and invalid), calibration rounds 1 to 4 | Pass |
| Fog and RGB MJPEG streams | Pass, 640×480 |
| `POST /api/export/blend` | Pass: `.blend`, `.glb` (15 MB), `.ply`; renders correctly in Cycles |
| WebSocket telemetry | 20 Hz |
| Dashboard at 1600×900 and 1280×720 | Fits the viewport exactly, no console errors |
| Dashboard at 390×844 | Scrolls vertically, no horizontal overflow |
| Blender extension against the live backend | Receives packets, builds the cloud, colour modes, ripple, flash and clean stop all work |

### Real hardware (webcam, laptop audio)

| Check | Result |
|---|---|
| Real camera capture | Works: `camera_mock: false`, 7.5 FPS with the procedural depth map |
| Real ONNX model on the real camera | 1.5 FPS, about 570% CPU, 720 MB RSS |
| Model auto-download from a clean clone | Pass (99 MB) |
| Telemetry rate in real mode | 20 Hz |
| **Sonar echo detection** | **Not achieved on this machine.** The system default output is a Bluetooth headset, which most likely cannot play 16 to 20 kHz (not confirmed), so no chirp appears to have reached the microphone. Round 2 measured an implausible noise floor (0.2 to 0.46 RMS) and Round 1 scattered between 6 and 47 cm (`noisy`). Selecting the built-in codec directly failed with *Device unavailable* because PipeWire holds it exclusively. Changing system audio routing was out of scope, so a physical echo test is still **outstanding**. |

### Bugs found by this testing, and fixed

1. **Sonar could never start on real hardware.** The default audio device ids were hard-coded
   to `1` and `2`. On this machine id 1 is an HDMI output with no inputs, so every ping failed
   with *Invalid number of channels*. Defaults are now `null` (system default).
2. **Silence was reported as a target.** Detection compared the biggest peak with a threshold
   relative to itself, so noise always passed (`tracking` on 100% of frames, depth wandering
   1.4 to 3.3 m). A peak must now also stand at least 6× above the median correlation. False
   tracking dropped to about 9% of frames; the status reads `idle` instead.
3. **A failing audio device froze telemetry** on the last reading with no hint. Telemetry now
   reports `status: "error"` and `/healthz` includes `sonar_error`; it recovers by itself when
   the device is fixed.
4. **Calibration endpoints returned a bare HTTP 500** on audio failures. They now return 503
   with a JSON `detail`. Round 2 also returns `quality` (`ok`, `input_too_loud`, `input_silent`).
5. **`/api/devices` reported a working camera as mock**, because the camera was already held
   open and could not be re-probed. The in-use camera is now listed (`Camera 0 (in use)`).
6. **`hw:` ALSA devices rejected the stream** because they only open at their native channel
   count. The channel count is now matched to the device (virtual devices are unaffected).

### Known limitations

- A real echo has **not** been demonstrated end to end; do the hardware steps in section 5 on
  a machine with a proper audio path.
- Peak-to-median gating is a heuristic: about 9% of noise-only frames still read `tracking`
  at first, before the idle timeout takes over.
- *Echo strength* on the dashboard is normalised to the strongest return, so it can read 100%
  on noise; trust the status badge and the depth stability instead.
- The mock backend sends 30 to 50 UDP packets per second rather than 60 on a loaded machine.
- There is no CI yet. The backend has unit tests (`cd backend && .venv/bin/python -m pytest tests`,
  needs `requirements-dev.txt`) covering unprojection, camera pose, the hybrid equation, depth
  modes and camera fallback; the rest was checked by hand.
- The draggable rig editor, multi-mic (GCC-PHAT) and autopilot UI from `ROADMAP.md` phases 4
  and 6 are not built yet. See "What's next" in `ROADMAP.md`.
- The fast heuristic is a cue-based approximation, not true depth; use the neural mode (or a
  calibrated empty background) when relief accuracy matters.
