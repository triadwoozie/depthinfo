"""Wire format of the depth-frame stream (backend/blender_udp.py  ->  this extension).

No ``bpy`` here on purpose: the backend's tests import this module to check that the sender and
the receiver agree on the format.

A logical frame is a normalised depth map plus the optical colours, sent as several UDP datagrams
because one frame (320x240 depth + half-resolution RGB = 134 KB) does not fit in a single datagram
(max 65,507 B). Keep a frame small: its datagrams arrive in a burst and must fit the receiving
socket's buffer, otherwise the frame is silently incomplete.

Every datagram starts with this little-endian header (32 bytes):

    magic       4s   b"SDF1"
    frame_id    u32  increases by one per frame; chunks of an older frame are dropped
    chunk       u8   index of this datagram within the frame
    chunk_count u8   datagrams in the frame
    width       u16  depth map width
    height      u16  depth map height
    color_div   u16  0 = depth only; n >= 1 = an RGB plane downscaled by n follows (2 = half resolution)
    z_sonar     f32  physical sonar distance (metres)
    relief      f32  metres of displacement for depth value 255
    fov_deg     f32  horizontal camera field of view
    plane_m     f32  distance from the camera to the plane that depth 0 sits on (metres)

The payload of all chunks, concatenated in order, is ``width*height`` depth bytes (row-major, top
row first, 0 = background / culled, 255 = nearest subject surface), followed, if color_div > 0, by
``(width//color_div)*(height//color_div)*3`` interleaved RGB bytes (top row first).
"""

import struct

import numpy as np

MAGIC = b"SDF1"
HEADER = struct.Struct("<4sIBBHHHffff")
CHUNK_PAYLOAD = 32768  # bytes of image data per datagram
MAX_DATAGRAM = 65507


class Frame:
    __slots__ = ("frame_id", "width", "height", "z_sonar", "relief", "fov_deg", "plane_m", "depth", "rgb")

    def __init__(self, frame_id, width, height, z_sonar, relief, fov_deg, plane_m, depth, rgb):
        self.frame_id, self.width, self.height = frame_id, width, height
        self.z_sonar, self.relief, self.fov_deg, self.plane_m = z_sonar, relief, fov_deg, plane_m
        self.depth, self.rgb = depth, rgb  # (h, w) uint8 and (h, w, 3) uint8 or None


class FrameAssembler:
    """Reassembles chunked frames. Feed datagrams in; get a complete ``Frame`` out or ``None``.

    Only the newest frame is tracked: a datagram from a newer frame discards the half-built older
    one, and stragglers from an older frame are ignored (UDP may reorder and drop).
    """

    def __init__(self):
        self._id = None
        self._parts = {}
        self._head = None
        self._complete = False
        self.dropped_frames = 0

    @staticmethod
    def _newer(a, b):
        return ((a - b) & 0xFFFFFFFF) < 0x80000000 and a != b  # u32 wrap-around comparison

    def feed(self, datagram):
        if len(datagram) < HEADER.size or datagram[:4] != MAGIC:
            return None
        (_, frame_id, chunk, count, w, h, color_div, z, relief, fov, plane) = HEADER.unpack_from(datagram)
        if count == 0 or chunk >= count or w == 0 or h == 0 or color_div > 16:
            return None
        if self._id is None or self._newer(frame_id, self._id):
            if self._parts:
                self.dropped_frames += 1  # moved on before the previous frame completed
            self._id, self._parts, self._complete = frame_id, {}, False
            self._head = (count, w, h, color_div, z, relief, fov, plane)
        elif frame_id != self._id or self._complete:
            return None  # stale frame, or a duplicate chunk of one already delivered
        if self._head[:4] != (count, w, h, color_div):
            return None
        self._parts[chunk] = datagram[HEADER.size:]
        if len(self._parts) < count:
            return None
        payload = b"".join(self._parts[i] for i in range(count))
        count, w, h, color_div, z, relief, fov, plane = self._head
        self._complete = True
        self._parts = {}
        cw, ch = (w // color_div, h // color_div) if color_div else (0, 0)
        if len(payload) != w * h + cw * ch * 3:
            return None
        arr = np.frombuffer(payload, dtype=np.uint8)
        depth = arr[: w * h].reshape(h, w)
        rgb = arr[w * h:].reshape(ch, cw, 3) if color_div else None
        return Frame(frame_id, w, h, z, relief, fov, plane, depth, rgb)
