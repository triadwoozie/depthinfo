"""UDP receive engine: non-blocking socket polled by a bpy.app.timers callback (AGENTS.md Rule 2).

The engine never blocks Blender's UI thread: the socket is non-blocking, the timer drains
everything pending each tick (keeping only the newest packet) and returns 0.016 s.
"""

import json
import math
import socket
import time

import bpy
import numpy as np

from . import geometry

TIMER_INTERVAL = 0.016
HOST = "127.0.0.1"  # the backend streams to localhost only
RECV_BYTES = 65535
RECEIVING_WINDOW_S = 1.0
FLASH_DECAY = 0.72  # per 16 ms tick: ~60 ms time constant, so 10 Hz pings read as discrete flashes
RIPPLE_WAVELENGTH_M = 0.33


def srgb_to_linear(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)


def jet(v):
    """Blue → cyan → green → yellow → red, v in [0,1] → (N,3) linear-ish RGB."""
    v = np.clip(v, 0.0, 1.0)
    r = np.clip(1.5 - np.abs(4.0 * v - 3.0), 0.0, 1.0)
    g = np.clip(1.5 - np.abs(4.0 * v - 2.0), 0.0, 1.0)
    b = np.clip(1.5 - np.abs(4.0 * v - 1.0), 0.0, 1.0)
    return srgb_to_linear(np.stack([r, g, b], axis=1))


def colorize(mode, rgb_in, depth, energy, near_rgb, far_rgb, d_near, d_far):
    """Per-point linear RGB for the Blender-side colour override."""
    n = len(depth)
    if mode == "OPTICAL_RGB":  # camera colours exactly as the backend sent them
        if rgb_in is not None and len(rgb_in) == n:
            return srgb_to_linear(rgb_in)
        return np.full((n, 3), 0.8, np.float32)
    t = np.clip((depth - d_near) / max(d_far - d_near, 1e-6), 0.0, 1.0)[:, None]
    if mode == "FOG_MIST":  # bright near, fading into a cool dark mist
        near_c, far_c = np.array([0.80, 0.92, 1.00]), np.array([0.01, 0.02, 0.04])
        return (far_c + (near_c - far_c) * (1.0 - t) ** 1.5).astype(np.float32)
    if mode == "ACOUSTIC_HEATMAP":  # echo strength, modulated by proximity for relief
        val = max(0.0, min(1.0, energy)) * (0.4 + 0.6 * (1.0 - t[:, 0]))
        return jet(val)
    near_c, far_c = np.array(near_rgb), np.array(far_rgb)  # CUSTOM_PALETTE
    return (near_c + (far_c - near_c) * t).astype(np.float32)


class Receiver:
    def __init__(self):
        self.sock = None
        self.port = 0
        self._reset()

    def _reset(self):
        self.base = np.zeros((0, 3), np.float32)       # Blender-frame positions
        self.rgb = np.zeros((0, 3), np.float32)        # colours after override (linear)
        self.state = "-"
        self.sonar_z = 0.0
        self.energy = 0.0
        self.flash = 0.0
        self.last_packet = 0.0
        self.packets_total = 0
        self.pps = 0.0
        self._win_start = time.monotonic()
        self._win_count = 0
        self._last_redraw = 0.0
        self._dirty = False
        self._last_scale = 1.0
        self._error_shown = False

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self):
        return self.sock is not None

    @property
    def receiving(self):
        return self.running and (time.monotonic() - self.last_packet) < RECEIVING_WINDOW_S

    def start(self, port):
        """Bind a non-blocking UDP socket and register the timer. Raises OSError on bind failure."""
        if self.running:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((HOST, port))
            sock.setblocking(False)
        except OSError:
            sock.close()
            raise
        self._reset()
        self.sock, self.port = sock, port
        if not bpy.app.timers.is_registered(_timer):
            bpy.app.timers.register(_timer, first_interval=0.0)

    def stop(self):
        """Unregister the timer, close the socket and free all buffers."""
        if bpy.app.timers.is_registered(_timer):
            bpy.app.timers.unregister(_timer)
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        self._reset()
        self._tag_redraw()

    # -- timer tick --------------------------------------------------------

    def tick(self):
        """One timer step. Returns the next interval, or None to unregister."""
        if self.sock is None:
            return None
        try:
            self._step()
        except Exception as exc:  # a timer must never raise: Blender would drop it silently
            if not self._error_shown:
                print(f"[SonarImaging] receiver error: {exc!r}")
                self._error_shown = True
        return TIMER_INTERVAL

    def _step(self):
        settings = getattr(bpy.context.scene, "sonar_imaging", None)
        if settings is None:
            return
        latest, ping, count = self._drain()
        now = time.monotonic()
        if count:
            self.last_packet = now
            self._win_count += count
        if now - self._win_start >= 1.0:
            self.pps = self._win_count / (now - self._win_start)
            self._win_start, self._win_count = now, 0
        if latest is not None:
            self._ingest(latest, settings)
        if ping and settings.flash_on_ping:
            self.flash = 1.0
        self._apply(settings, now, ping)
        if now - self._last_redraw > 0.25:
            self._tag_redraw()
            self._last_redraw = now

    def _drain(self):
        """Read every pending datagram; keep the newest valid packet and remember any ping."""
        latest, ping, count = None, False, 0
        while True:
            try:
                data, _ = self.sock.recvfrom(RECV_BYTES)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                break
            count += 1
            try:
                pkt = json.loads(data)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(pkt, dict):
                ping = ping or bool(pkt.get("ping_pulse"))
                latest = pkt
        return latest, ping, count

    def _ingest(self, pkt, s):
        self.state = str(pkt.get("state", "-"))
        self.sonar_z = float(pkt.get("sonar_z", 0.0))
        self.energy = float(pkt.get("audio_energy", 0.0))
        pts = np.asarray(pkt.get("points") or [], dtype=np.float32).reshape(-1, 3)
        # Backend frame (x right, y up, z forward) → Blender (x right, y forward, z up).
        self.base = np.ascontiguousarray(pts[:, [0, 2, 1]])
        cols = pkt.get("colors")
        rgb_in = np.asarray(cols, dtype=np.float32).reshape(-1, 3) if cols else None
        self.rgb = colorize(
            s.color_mode, rgb_in, self.base[:, 1], self.energy,
            tuple(s.near_color[:3]), tuple(s.far_color[:3]), s.depth_near, s.depth_far,
        ) if len(self.base) else np.zeros((0, 3), np.float32)
        self._dirty = True

    # -- scene updates -----------------------------------------------------

    def _apply(self, s, now, ping):
        self._drive_helpers(ping)
        obj = geometry.find_cloud(s)
        if obj is None or obj.type != "MESH" or obj.mode == "EDIT" or len(self.base) == 0:
            self.flash *= FLASH_DECAY
            return
        rippling = s.wave_amplitude > 0.0
        if not (self._dirty or rippling or self.flash > 0.01):
            return
        co = self.base.copy()
        if rippling:
            centre = self.base.mean(axis=0)
            r = np.hypot(co[:, 0] - centre[0], co[:, 1] - centre[1])
            phase = 2.0 * math.pi * (r / RIPPLE_WAVELENGTH_M - s.wave_speed * now)
            co[:, 2] += s.wave_amplitude * np.sin(phase)
        rgb = self.rgb
        if self.flash > 0.01:
            rgb = rgb + (1.0 - rgb) * (0.8 * self.flash)
        rgba = np.ones((len(co), 4), np.float32)
        rgba[:, :3] = rgb
        self._write_mesh(obj.data, co, rgba)

        scale = 1.0 + 0.6 * self.flash
        if self._dirty or abs(scale - self._last_scale) > 1e-3:
            geometry.set_point_radius(obj, s.point_size_mm / 1000.0 * scale)
            self._last_scale = scale
        self.flash = self.flash * FLASH_DECAY if self.flash > 0.01 else 0.0
        self._dirty = False

    @staticmethod
    def _write_mesh(mesh, co, rgba):
        n = len(co)
        if len(mesh.vertices) != n:
            mesh.clear_geometry()
            mesh.vertices.add(n)
        col = geometry.ensure_col_attribute(mesh)
        mesh.vertices.foreach_set("co", co.ravel())
        col.data.foreach_set("color", rgba.ravel())
        mesh.update()

    def _drive_helpers(self, ping):
        """Optional template objects: SonarTarget follows the depth, SonarPing pulses."""
        target = bpy.data.objects.get("SonarTarget")
        if target is not None:
            target.location.y = self.sonar_z  # Blender forward axis
        ring = bpy.data.objects.get("SonarPing")
        if ring is not None and ping:
            ring.scale = (max(self.sonar_z, 0.05),) * 3

    @staticmethod
    def _tag_redraw():
        wm = bpy.context.window_manager
        for window in getattr(wm, "windows", ()):
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()


RECEIVER = Receiver()


def _timer():
    return RECEIVER.tick()
