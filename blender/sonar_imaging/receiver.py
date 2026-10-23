"""UDP receive engine: reassembles depth frames and writes them straight into image datablocks.

A non-blocking socket is drained by a ``bpy.app.timers`` callback every 0.016 s (AGENTS.md Rule 2).
Only the newest complete frame is used. The frame's pixels go into ``Sonar_Depth_Texture`` and
``Sonar_Color_Texture`` with ``foreach_set`` (no per-vertex Python); the Geometry Nodes modifier
then displaces the grid from those images.
"""

import math
import socket
import time

import bpy
import numpy as np

from . import nodes
from .properties import COLOR_IMAGE, DEPTH_IMAGE, OBJECT_NAME
from .protocol import FrameAssembler

TIMER_INTERVAL = 0.016
HOST = "127.0.0.1"
RECV_BYTES = 65535
RECEIVING_WINDOW_S = 1.0
RCVBUF = 4 * 1024 * 1024  # a frame arrives as a burst of datagrams; the kernel caps this at rmem_max

# sRGB byte -> linear float (the colour image is a float buffer holding linear values)
_c = np.arange(256, dtype=np.float32) / 255.0
SRGB_TO_LINEAR = np.where(_c <= 0.04045, _c / 12.92, ((_c + 0.055) / 1.055) ** 2.4).astype(np.float32)


def _set_image(img, w, h, rgb_hw3):
    """Write an (h, w, 3) float array into ``img`` (alpha 1), resizing the datablock if needed."""
    if tuple(img.size) != (w, h):
        img.scale(w, h)
    rgba = np.ones((h, w, 4), np.float32)
    rgba[..., :3] = rgb_hw3
    img.pixels.foreach_set(np.ascontiguousarray(rgba[::-1]).ravel())  # Blender rows run bottom-up
    img.update()


class Receiver:
    def __init__(self):
        self.sock = None
        self.port = 0
        self.assembler = FrameAssembler()
        self._reset()

    def _reset(self):
        self.assembler = FrameAssembler()
        self.info = None            # last frame: dict of width/height/z_sonar/relief/fov/plane
        self.frames_total = 0
        self.fps = 0.0
        self.last_frame = 0.0
        self.last_apply_ms = 0.0
        self.subject_px = 0
        self._win_start = time.monotonic()
        self._win_count = 0
        self._last_redraw = 0.0
        self._applied = {}          # last values pushed to the modifier...
        self._applied_to = None     # ...and which modifier they were pushed to
        self._error_shown = False

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self):
        return self.sock is not None

    @property
    def receiving(self):
        return self.running and (time.monotonic() - self.last_frame) < RECEIVING_WINDOW_S

    def start(self, port):
        """Bind a non-blocking UDP socket and register the timer. Raises OSError on bind failure."""
        if self.running:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RCVBUF)
            except OSError:
                pass
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
        """Unregister the timer, close the socket and free the frame buffers."""
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

    # -- timer -------------------------------------------------------------

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
        frame = None
        while True:  # drain everything; keep the newest complete frame
            try:
                data, _ = self.sock.recvfrom(RECV_BYTES)
            except (BlockingIOError, InterruptedError, OSError):
                break
            got = self.assembler.feed(data)
            if got is not None:
                frame = got
        now = time.monotonic()
        if frame is not None and settings is not None:
            t0 = time.perf_counter()
            self._apply_frame(frame, settings)
            self.last_apply_ms = (time.perf_counter() - t0) * 1000.0
            self.last_frame = now
            self.frames_total += 1
            self._win_count += 1
        if now - self._win_start >= 1.0:
            self.fps = self._win_count / (now - self._win_start)
            self._win_start, self._win_count = now, 0
        if now - self._last_redraw > 0.25:
            self._tag_redraw()
            self._last_redraw = now

    # -- scene updates -----------------------------------------------------

    def _apply_frame(self, frame, settings):
        self.info = dict(width=frame.width, height=frame.height, z_sonar=frame.z_sonar,
                         relief=frame.relief, fov=frame.fov_deg, plane=frame.plane_m)
        self.subject_px = int(np.count_nonzero(frame.depth))
        depth_img = bpy.data.images.get(DEPTH_IMAGE)
        color_img = bpy.data.images.get(COLOR_IMAGE)
        obj = bpy.data.objects.get(OBJECT_NAME)
        if depth_img is None or color_img is None or obj is None:
            return  # nothing to drive yet (Create / Reset Displaced Mesh not clicked)
        near = frame.depth.astype(np.float32) / 255.0
        _set_image(depth_img, frame.width, frame.height, near[..., None])
        if frame.rgb is not None:
            ch, cw = frame.rgb.shape[:2]
            _set_image(color_img, cw, ch, SRGB_TO_LINEAR[frame.rgb])
        self.apply_params(settings, obj)

    def forget_applied(self):
        """Call when the modifier is rebuilt: its inputs are back at their defaults."""
        self._applied, self._applied_to = {}, None

    def apply_params(self, settings, obj=None):
        """Push relief / cutoff / frustum parameters to the modifier (only values that changed)."""
        obj = obj or bpy.data.objects.get(OBJECT_NAME)
        if obj is None:
            return
        mod = obj.modifiers.get("Sonar_Displace")
        if mod is None or mod.node_group is None:
            return
        info = self.info
        relief = info["relief"] if (info and settings.auto_relief) else settings.relief_scale
        plane = info["plane"] if info else 1.7
        fov = info["fov"] if info else 65.0
        aspect = (info["height"] / info["width"]) if info else 0.75
        values = {
            "Relief": relief,
            "Cutoff": settings.cutoff,
            "Plane Distance": plane,
            "Half Width": plane * math.tan(math.radians(fov) / 2.0),
            "Aspect": aspect,
        }
        if self._applied_to != mod.as_pointer():  # a different modifier: its inputs are at defaults
            self._applied, self._applied_to = {}, mod.as_pointer()
        for name, value in values.items():
            if self._applied.get(name) != value:
                nodes.set_modifier_input(mod, name, value)
                self._applied[name] = value
        obj.update_tag()

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
