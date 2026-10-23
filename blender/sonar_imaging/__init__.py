"""Sonar Imaging Live Receiver: Blender 4.2+ / 5.x extension.

Receives the backend's depth-frame stream (UDP 127.0.0.1:5005) and displaces a dense grid with it,
which gives a connected 3D surface of the subject. UI: 3D Viewport > N-panel > "Sonar Imaging".
"""

import bpy
from bpy.app.handlers import persistent
from bpy.props import PointerProperty

from . import operators, receiver, ui
from .properties import SonarSettings


@persistent
def _on_load_pre(*_args):
    receiver.RECEIVER.stop()  # scene data is about to be replaced


CLASSES = (
    SonarSettings,
    operators.SONAR_OT_create_displaced_surface,
    operators.SONAR_OT_toggle_receiver,
    ui.SONAR_PT_main,
    ui.SONAR_PT_connection,
    ui.SONAR_PT_mesh,
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
