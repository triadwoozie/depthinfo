"""3D Viewport > N-panel > "Sonar Imaging"."""

import bpy
from bpy.types import Panel

from . import receiver
from .operators import SONAR_OT_create_displaced_surface, SONAR_OT_toggle_receiver
from .properties import OBJECT_NAME

_ICONS = None


def _icon(*candidates):
    """First icon name that exists in this Blender (status-dot icons were renamed in 4.4/5.0)."""
    global _ICONS
    if _ICONS is None:
        _ICONS = set(bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items.keys())
    return next((name for name in candidates if name in _ICONS), "DOT")


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
            icon, text = _icon("STRIP_COLOR_04", "SEQUENCE_COLOR_04"), "Receiving frames"  # green dot
        elif rx.running:
            icon, text = _icon("STRIP_COLOR_03", "SEQUENCE_COLOR_03"), "Listening: no frames"  # yellow dot
        else:
            icon, text = "RADIOBUT_OFF", "Receiver stopped"
        layout.label(text=text, icon=icon)

        info = rx.info
        col = layout.column(align=True)
        col.label(text=f"Frame rate: {rx.fps:.0f} /s")
        if info:
            col.label(text=f"Frame {info['width']} x {info['height']}, subject {rx.subject_px} px")
            col.label(text=f"Sonar {info['z_sonar']:.2f} m, backend relief {info['relief']:.2f} m")
            col.label(text=f"Update {rx.last_apply_ms:.1f} ms")


class SONAR_PT_mesh(_SonarPanel, Panel):
    bl_idname = "SONAR_PT_mesh"
    bl_parent_id = "SONAR_PT_main"
    bl_label = "Displaced Mesh"

    def draw(self, context):
        layout = self.layout
        s = context.scene.sonar_imaging
        exists = bpy.data.objects.get(OBJECT_NAME) is not None
        layout.operator(SONAR_OT_create_displaced_surface.bl_idname,
                        text="Reset Displaced Mesh" if exists else "Create Displaced Mesh", icon="MESH_GRID")
        layout.prop(s, "resolution", text="Resolution")

        col = layout.column(align=True)
        col.prop(s, "auto_relief")
        sub = col.column(align=True)
        sub.enabled = not s.auto_relief
        sub.prop(s, "relief_scale", slider=True)
        layout.prop(s, "cutoff", slider=True)
        layout.prop(s, "show_wireframe", toggle=True, icon="MOD_WIREFRAME")
