"""Sonar Imaging Live Receiver — Blender 4.2+ / 5.x extension.

Receives the backend's UDP point-cloud stream (default 127.0.0.1:5005) and drives a
``SonarCloud`` mesh in the viewport. UI: 3D Viewport › N-panel › "Sonar Imaging".
"""

import bpy
from bpy.app.handlers import persistent
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
)
from bpy.types import Operator, Panel, PropertyGroup

from . import geometry, receiver

COLOR_MODES = [
    ("OPTICAL_RGB", "Optical RGB", "Camera colours exactly as the backend sends them"),
    ("FOG_MIST", "Fog Mist", "Depth mist: bright near, fading into dark fog"),
    ("ACOUSTIC_HEATMAP", "Acoustic Heatmap", "Echo strength, blue to red"),
    ("CUSTOM_PALETTE", "Custom Palette", "Near/far gradient from the two colour swatches"),
]


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

def _poll_mesh(self, obj):
    return obj.type == "MESH"


def _update_target(self, context):
    if self.target is not None:
        geometry.ensure_instancer(self.target, self.point_size_mm / 1000.0)


def _update_point_size(self, context):
    obj = geometry.find_cloud(self)
    if obj is not None:
        geometry.set_point_radius(obj, self.point_size_mm / 1000.0)


def _force_refresh(self, context):
    receiver.RECEIVER._dirty = True  # re-colour / re-write on the next tick


class SonarSettings(PropertyGroup):
    port: IntProperty(name="UDP Port", description="Port the backend streams to (SONAR_UDP_PORT)",
                      default=5005, min=1024, max=65535)
    target: PointerProperty(name="Target Object", type=bpy.types.Object, poll=_poll_mesh,
                            update=_update_target,
                            description="Mesh driven by the stream (defaults to 'SonarCloud')")
    point_size_mm: FloatProperty(name="Point Size", description="Point radius", default=4.0,
                                 min=0.2, max=60.0, precision=1, subtype="NONE", unit="NONE",
                                 update=_update_point_size)
    wave_speed: FloatProperty(name="Wave Ripple Speed", description="Ripple travel speed (cycles/s)",
                              default=0.6, min=0.0, max=5.0)
    wave_amplitude: FloatProperty(name="Wave Distortion", description="Ripple height in metres (0 = off)",
                                  default=0.0, min=0.0, max=0.25, precision=3, unit="LENGTH")
    flash_on_ping: BoolProperty(name="Flash on Ping Pulse", default=True,
                                description="Flash and swell the points whenever a fresh chirp echo arrives")
    color_mode: EnumProperty(name="Color Mode", items=COLOR_MODES, default="OPTICAL_RGB",
                             update=_force_refresh)
    near_color: FloatVectorProperty(name="Near", subtype="COLOR", size=4, min=0.0, max=1.0,
                                    default=(0.0, 0.91, 0.66, 1.0), update=_force_refresh)
    far_color: FloatVectorProperty(name="Far", subtype="COLOR", size=4, min=0.0, max=1.0,
                                   default=(0.04, 0.0, 0.37, 1.0), update=_force_refresh)
    depth_near: FloatProperty(name="Near Depth", default=0.35, min=0.0, max=10.0, unit="LENGTH",
                              update=_force_refresh, description="Depth mapped to the near colour")
    depth_far: FloatProperty(name="Far Depth", default=2.5, min=0.1, max=10.0, unit="LENGTH",
                             update=_force_refresh, description="Depth mapped to the far colour")


_ICONS = None


def _icon(*candidates):
    """First icon name that exists in this Blender (the status-dot icons were renamed in 4.4/5.0)."""
    global _ICONS
    if _ICONS is None:
        _ICONS = set(bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items.keys())
    return next((name for name in candidates if name in _ICONS), "DOT")


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class SONAR_OT_toggle_receiver(Operator):
    bl_idname = "sonar.toggle_receiver"
    bl_label = "Start / Stop Receiver"
    bl_description = "Start or stop listening for the sonar UDP stream"

    def execute(self, context):
        rx = receiver.RECEIVER
        if rx.running:
            rx.stop()
            self.report({"INFO"}, "Sonar receiver stopped")
            return {"FINISHED"}
        s = context.scene.sonar_imaging
        try:
            rx.start(s.port)
        except OSError as exc:
            self.report({"ERROR"}, f"Cannot listen on UDP {s.port}: {exc}")
            return {"CANCELLED"}
        if geometry.find_cloud(s) is None:
            self.report({"WARNING"}, "Listening, but there is no target mesh — create a Sonar Cloud")
        else:
            self.report({"INFO"}, f"Listening on udp://{receiver.HOST}:{s.port}")
        return {"FINISHED"}


class SONAR_OT_create_cloud(Operator):
    bl_idname = "sonar.create_cloud"
    bl_label = "Create Sonar Cloud Mesh"
    bl_description = "Add the 'SonarCloud' mesh (vertex colours + point rendering) and use it as the target"

    def execute(self, context):
        s = context.scene.sonar_imaging
        existing = bpy.data.objects.get(geometry.CLOUD_NAME)
        obj = existing if existing is not None and existing.type == "MESH" \
            else geometry.create_cloud(context, s.point_size_mm / 1000.0)
        if obj.name not in context.scene.objects:
            context.collection.objects.link(obj)
        s.target = obj
        self.report({"INFO"}, f"Target set to '{obj.name}'")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Panels (View3D › UI › Sonar Imaging)
# ---------------------------------------------------------------------------

class _SonarPanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Sonar Imaging"


class SONAR_PT_main(_SonarPanel, Panel):
    bl_idname = "SONAR_PT_main"
    bl_label = "Sonar Imaging"

    def draw(self, context):
        rx = receiver.RECEIVER
        state = "Receiving" if rx.receiving else ("Listening" if rx.running else "Stopped")
        self.layout.label(text=state)


class SONAR_PT_connection(_SonarPanel, Panel):
    bl_idname = "SONAR_PT_connection"
    bl_parent_id = "SONAR_PT_main"
    bl_label = "Connection"

    def draw(self, context):
        layout = self.layout
        s = context.scene.sonar_imaging
        rx = receiver.RECEIVER

        row = layout.row()
        row.enabled = not rx.running
        row.prop(s, "port")

        layout.operator(SONAR_OT_toggle_receiver.bl_idname,
                        text="Stop Receiver" if rx.running else "Start Receiver",
                        icon="PAUSE" if rx.running else "PLAY", depress=rx.running)

        if rx.receiving:
            icon, text = _icon("STRIP_COLOR_04", "SEQUENCE_COLOR_04"), "Receiving packets"  # green dot
        elif rx.running:
            icon, text = _icon("STRIP_COLOR_03", "SEQUENCE_COLOR_03"), "Listening — no packets"  # yellow dot
        else:
            icon, text = "RADIOBUT_OFF", "Receiver stopped"
        layout.label(text=text, icon=icon)

        col = layout.column(align=True)
        col.label(text=f"Packet rate: {rx.pps:.0f} /s")
        col.label(text=f"Points: {len(rx.base)}   State: {rx.state}")
        col.label(text=f"Sonar depth: {rx.sonar_z:.2f} m")


class SONAR_PT_geometry(_SonarPanel, Panel):
    bl_idname = "SONAR_PT_geometry"
    bl_parent_id = "SONAR_PT_main"
    bl_label = "Mesh & Geometry Nodes"

    def draw(self, context):
        layout = self.layout
        s = context.scene.sonar_imaging
        layout.prop(s, "target")
        if geometry.find_cloud(s) is None:
            layout.operator(SONAR_OT_create_cloud.bl_idname, icon="MESH_DATA")
        elif s.target is None:
            layout.label(text=f"Using '{geometry.find_cloud(s).name}'", icon="INFO")
        layout.prop(s, "point_size_mm", slider=True, text="Point Size (mm)")


class SONAR_PT_wave(_SonarPanel, Panel):
    bl_idname = "SONAR_PT_wave"
    bl_parent_id = "SONAR_PT_main"
    bl_label = "Acoustic Wave Modulation"

    def draw(self, context):
        s = context.scene.sonar_imaging
        col = self.layout.column()
        col.prop(s, "wave_speed", slider=True)
        col.prop(s, "wave_amplitude", slider=True)
        col.prop(s, "flash_on_ping", toggle=True, icon="LIGHT")


class SONAR_PT_color(_SonarPanel, Panel):
    bl_idname = "SONAR_PT_color"
    bl_parent_id = "SONAR_PT_main"
    bl_label = "Color Override"

    def draw(self, context):
        layout = self.layout
        s = context.scene.sonar_imaging
        layout.prop(s, "color_mode", text="")
        if s.color_mode != "OPTICAL_RGB":
            col = layout.column(align=True)
            col.prop(s, "depth_near")
            col.prop(s, "depth_far")
        if s.color_mode == "CUSTOM_PALETTE":
            row = layout.row(align=True)
            row.prop(s, "near_color", text="")
            row.prop(s, "far_color", text="")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

@persistent
def _on_load_pre(*_args):
    receiver.RECEIVER.stop()  # scene data is about to be replaced


CLASSES = (
    SonarSettings,
    SONAR_OT_toggle_receiver,
    SONAR_OT_create_cloud,
    SONAR_PT_main,
    SONAR_PT_connection,
    SONAR_PT_geometry,
    SONAR_PT_wave,
    SONAR_PT_color,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.sonar_imaging = PointerProperty(type=SonarSettings)
    bpy.app.handlers.load_pre.append(_on_load_pre)


def unregister():
    receiver.RECEIVER.stop()  # timer, socket and buffers
    if _on_load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_on_load_pre)
    del bpy.types.Scene.sonar_imaging
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
