"""Operators: create / reset the displaced surface, start / stop the receiver."""

import math

import bmesh
import bpy
from bpy.types import Operator

from . import nodes, receiver
from .properties import COLOR_IMAGE, DEPTH_IMAGE, OBJECT_NAME

DEPTH_SIZE = (320, 240)   # matches the backend's depth frame
COLOR_SIZE = (160, 120)   # half resolution colour plane


def _fresh_image(name, size, is_data):
    old = bpy.data.images.get(name)
    if old is not None:
        bpy.data.images.remove(old)
    img = bpy.data.images.new(name, size[0], size[1], alpha=False, float_buffer=True, is_data=is_data)
    img.use_fake_user = True  # survive a save/reload so the node group never loses its inputs
    return img


class SONAR_OT_create_displaced_surface(Operator):
    bl_idname = "sonar.create_displaced_surface"
    bl_label = "Create / Reset Displaced Mesh"
    bl_description = ("Build the 'Sonar_Displacement_Mesh' grid, its float depth and colour textures, the "
                      "'Sonar_Displace' Geometry Nodes modifier and its material")

    def execute(self, context):
        s = context.scene.sonar_imaging
        res = int(s.resolution)

        old = bpy.data.objects.get(OBJECT_NAME)
        if old is not None:
            old_mesh = old.data
            bpy.data.objects.remove(old)
            if old_mesh.users == 0:
                bpy.data.meshes.remove(old_mesh)

        depth = _fresh_image(DEPTH_IMAGE, DEPTH_SIZE, is_data=True)
        color = _fresh_image(COLOR_IMAGE, COLOR_SIZE, is_data=False)
        group = nodes.build_group(depth, color)

        mesh = bpy.data.meshes.new(OBJECT_NAME)
        bm = bmesh.new()
        bmesh.ops.create_grid(bm, x_segments=res, y_segments=res, size=1.0)  # 2 m x 2 m, res x res quads
        bm.to_mesh(mesh)
        bm.free()
        mesh.materials.append(nodes.ensure_material())

        obj = bpy.data.objects.new(OBJECT_NAME, mesh)
        (context.collection or context.scene.collection).objects.link(obj)
        # Local +Z points at the camera; rotate so the image plane is vertical and +Z faces the
        # viewer of the front view (numpad 1): local Y -> world Z (up), local Z -> world -Y.
        obj.rotation_euler = (math.pi / 2.0, 0.0, 0.0)
        mod = obj.modifiers.new("Sonar_Displace", "NODES")
        mod.node_group = group
        obj.show_wire = obj.show_all_edges = s.show_wireframe

        receiver.RECEIVER.apply_params(s, obj)
        context.view_layer.objects.active = obj
        obj.select_set(True)
        self.report({"INFO"}, f"Created '{OBJECT_NAME}' ({res} x {res} quads); start the receiver to fill it")
        return {"FINISHED"}


class SONAR_OT_toggle_receiver(Operator):
    bl_idname = "sonar.toggle_receiver"
    bl_label = "Start / Stop Receiver"
    bl_description = "Start or stop listening for the sonar depth-frame stream"

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
        if bpy.data.objects.get(OBJECT_NAME) is None:
            self.report({"WARNING"}, "Listening, but there is no mesh yet: click Create / Reset Displaced Mesh")
        else:
            self.report({"INFO"}, f"Listening on udp://{receiver.HOST}:{s.port}")
        return {"FINISHED"}
