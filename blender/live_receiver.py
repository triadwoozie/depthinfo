"""
live_receiver.py — Blender 4.x UDP Listener for Sonar Imaging.

Installation
------------
1. Open Blender 4.x
2. Go to the **Scripting** workspace.
3. Open this file (or paste it into a new text block).
4. Click **Run Script**.

The listener registers a bpy.app.timers callback that fires at 60 FPS,
draining the UDP socket buffer each frame. Blender's viewport is updated
in-place using the received point cloud and sonar_z value.

Design Rules (from AGENTS.md Rule 1 — Blender)
-----------------------------------------------
- NEVER use `while True:` or blocking socket calls inside a timer callback.
- ALWAYS call `sock.setblocking(False)` before entering the timer loop.
- Drain all pending UDP packets each frame; keep only the latest.
- The callback MUST return 1/60 (≈ 0.016) to re-schedule itself at 60 FPS.

Data Contract (from AGENTS.md)
-------------------------------
Incoming JSON packet (from backend/blender_bridge.py):
{
    "state":       "tracking" | "idle" | "autopilot" | "mock",
    "sonar_z":     1.45,          # float, metres
    "ping_pulse":  0 | 1,         # alternating, used for visual ping ring
    "audio_energy": 0.42,         # float [0..1], used for shader intensity
    "points":      [[x, y, z], ...]  # 3D point cloud from vision+sonar fusion
}
"""

import json
import socket
import time

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
UDP_HOST = "127.0.0.1"
UDP_PORT = 5005
TIMER_INTERVAL = 1 / 60  # seconds — 60 FPS callback rate

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_sock: socket.socket | None = None
_last_packet: dict | None = None
_last_received: float = 0.0
_frame_count: int = 0
_registered: bool = False


# ---------------------------------------------------------------------------
# Helper: update Blender scene from received packet
# ---------------------------------------------------------------------------

def _apply_packet(packet: dict) -> None:
    """
    Apply the latest fused sonar+vision data to the Blender scene.

    Extend this function to drive Geometry Nodes, materials, or custom objects.
    All bpy calls MUST happen here (inside the timer callback), never in a
    separate thread.
    """
    global _frame_count
    _frame_count += 1

    try:
        import bpy  # type: ignore
    except ImportError:
        return  # Running outside Blender for testing

    sonar_z = float(packet.get("sonar_z", 0.0))
    state = packet.get("state", "unknown")
    audio_energy = float(packet.get("audio_energy", 0.0))
    ping_pulse = int(packet.get("ping_pulse", 0))
    points = packet.get("points", [])

    # ---- Example: move a "SonarTarget" empty to the tracked depth ----
    target_obj = bpy.data.objects.get("SonarTarget")
    if target_obj:
        target_obj.location.z = sonar_z
        # Subtle scale pulse driven by audio energy
        scale = 1.0 + audio_energy * 0.3
        target_obj.scale = (scale, scale, scale)

    # ---- Example: drive a "SonarPing" mesh scale for the ping ring ----
    ping_obj = bpy.data.objects.get("SonarPing")
    if ping_obj:
        if ping_pulse == 1:
            ping_obj.scale = (sonar_z, sonar_z, sonar_z)

    # ---- Example: update a "PointCloud" mesh with received points ----
    if points:
        pc_mesh = bpy.data.meshes.get("SonarPointCloud")
        pc_obj = bpy.data.objects.get("SonarPointCloud")
        if pc_mesh and pc_obj:
            # Rebuild mesh vertices in-place
            pc_mesh.clear_geometry()
            vertices = [(p[0], p[1], p[2]) for p in points if len(p) >= 3]
            pc_mesh.from_pydata(vertices, [], [])
            pc_mesh.update()

    # ---- Status readout in Blender console ----
    if _frame_count % 60 == 0:
        print(
            f"[SonarImaging] state={state!r}  z={sonar_z:.3f}m  "
            f"pts={len(points)}  energy={audio_energy:.2f}  "
            f"frame={_frame_count}"
        )


# ---------------------------------------------------------------------------
# bpy.app.timers callback
# ---------------------------------------------------------------------------

def _sonar_timer_callback() -> float:
    """
    Non-blocking UDP drain callback registered with bpy.app.timers.

    Reads all pending packets from the socket buffer, keeps only the
    most recent one, then applies it to the Blender scene.

    Returns TIMER_INTERVAL to re-schedule at 60 FPS.
    """
    global _sock, _last_packet, _last_received

    if _sock is None:
        # Socket was closed externally — unregister by returning None
        print("[SonarImaging] Socket closed; stopping timer.")
        return None  # type: ignore[return-value]

    latest_data: bytes | None = None

    # Drain the entire socket buffer — keep only the last packet
    while True:
        try:
            data, _ = _sock.recvfrom(8192)
            latest_data = data
        except BlockingIOError:
            break  # Buffer empty; all packets consumed
        except OSError:
            break

    if latest_data is not None:
        try:
            _last_packet = json.loads(latest_data.decode("utf-8"))
            _last_received = time.monotonic()
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass  # Drop malformed packets silently

    # Apply the most recent valid packet (even if no new data arrived this frame)
    if _last_packet is not None:
        _apply_packet(_last_packet)

    return TIMER_INTERVAL


# ---------------------------------------------------------------------------
# Registration helpers (called from Blender scripting panel)
# ---------------------------------------------------------------------------

def start_receiver() -> None:
    """Open the UDP socket and register the bpy.app.timers callback."""
    global _sock, _registered

    try:
        import bpy  # type: ignore
    except ImportError:
        print("[SonarImaging] Not running inside Blender — start_receiver() is a no-op.")
        return

    if _registered:
        print("[SonarImaging] Receiver already running.")
        return

    _sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    _sock.setblocking(False)
    _sock.bind((UDP_HOST, UDP_PORT))
    print(f"[SonarImaging] Listening on udp://{UDP_HOST}:{UDP_PORT} @ 60 FPS")

    bpy.app.timers.register(_sonar_timer_callback, first_interval=TIMER_INTERVAL)
    _registered = True


def stop_receiver() -> None:
    """Close the UDP socket; the timer will self-unregister on next tick."""
    global _sock, _registered
    if _sock:
        _sock.close()
        _sock = None
    _registered = False
    print("[SonarImaging] Receiver stopped.")


# ---------------------------------------------------------------------------
# Auto-start when script is executed via Blender's "Run Script" button
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    start_receiver()
