"""Scene settings for the Sonar Imaging receiver."""

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty
from bpy.types import PropertyGroup

OBJECT_NAME = "Sonar_Displacement_Mesh"
DEPTH_IMAGE = "Sonar_Depth_Texture"
COLOR_IMAGE = "Sonar_Color_Texture"
RESOLUTIONS = [("128", "128 x 128", "16,641 vertices"),
               ("256", "256 x 256", "66,049 vertices"),
               ("512", "512 x 512", "263,169 vertices")]


def _apply(self, context):
    from . import receiver
    receiver.RECEIVER.apply_params(self)


def _wireframe(self, context):
    obj = bpy.data.objects.get(OBJECT_NAME)
    if obj is not None:
        obj.show_wire = self.show_wireframe
        obj.show_all_edges = self.show_wireframe


class SonarSettings(PropertyGroup):
    port: IntProperty(name="UDP Port", description="Port the backend streams to (SONAR_UDP_PORT)",
                      default=5005, min=1024, max=65535)
    resolution: EnumProperty(name="Resolution", items=RESOLUTIONS, default="256",
                             description="Grid density used by Create / Reset")
    auto_relief: BoolProperty(name="Use backend relief", default=True, update=_apply,
                              description="Take the displacement depth from the stream "
                                          "(true metric depth); turn off to use the slider")
    relief_scale: FloatProperty(name="Relief Depth Scale", default=0.75, min=0.1, max=1.0,
                                precision=2, unit="LENGTH", update=_apply,
                                description="Metres of displacement at full nearness")
    cutoff: FloatProperty(name="Background Cutoff", default=0.05, min=0.0, max=0.3, precision=3,
                          update=_apply,
                          description="Vertices with nearness below this are deleted (the background)")
    show_wireframe: BoolProperty(name="Wireframe Overlay", default=False, update=_wireframe,
                                 description="Show the quad grid to visualise mesh density")
