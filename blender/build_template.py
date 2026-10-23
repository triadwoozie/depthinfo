"""
build_template.py — generates blender/template_exhibition.blend (Blender 4.2+ / 5.x).

    blender --background --factory-startup --python blender/build_template.py

Contents: empty ``SonarPointCloud`` mesh (with a ``Col`` attribute and a point-instancing
Geometry Nodes modifier, driven live by the sonar_imaging extension), ``SonarTarget`` empty,
``SonarPing`` ring, ``ExhibitionFloor``, a 3-point light rig and ``StudioCamera``.
export_static_scene.py reuses these by name.
"""

import math
import os
import sys

import bpy
import bmesh
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _sonar_common as common  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "template_exhibition.blend")


def link(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj


bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
for engine in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
    try:
        scene.render.engine = engine
        break
    except TypeError:
        continue

# Live point cloud placeholder + point instancer.
mat = common.make_vertex_color_material()
pc_mesh = bpy.data.meshes.new("SonarPointCloud")
pc_mesh.materials.append(mat)
common.ensure_col_attribute(pc_mesh, "Col")
pc = link(bpy.data.objects.new("SonarPointCloud", pc_mesh))
common.add_point_instancer_modifier(pc, 0.004, mat)

# Tracked-target empty.
target = bpy.data.objects.new("SonarTarget", None)
target.empty_display_type = "SPHERE"
target.empty_display_size = 0.1
link(target)

# Ping ring (circle outline, scaled by the sonar_imaging extension on each ping).
ring_mesh = bpy.data.meshes.new("SonarPing")
bm = bmesh.new()
bmesh.ops.create_circle(bm, cap_ends=False, radius=1.0, segments=96)
bm.to_mesh(ring_mesh)
bm.free()
ring_mesh.update()
link(bpy.data.objects.new("SonarPing", ring_mesh))

# Exhibition floor.
floor_mesh = bpy.data.meshes.new("ExhibitionFloor")
bm = bmesh.new()
bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=1.0)
bm.to_mesh(floor_mesh)
bm.free()
floor = link(bpy.data.objects.new("ExhibitionFloor", floor_mesh))
floor.location = (0, 0, -1.0)
floor.scale = (4, 4, 1)
floor_mat = bpy.data.materials.new("ExhibitionFloor")
floor_mat.use_nodes = True
floor_mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.03, 0.03, 0.035, 1.0)
floor_mat.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.35
floor_mesh.materials.append(floor_mat)

# 3-point rig + isometric studio camera.
common.get_or_create_light("KeyLight", "AREA", (2.0, -3.0, 2.5), 600, (1.0, 0.96, 0.9), size=2.0)
common.get_or_create_light("FillLight", "AREA", (-3.0, -2.0, 1.2), 220, (0.8, 0.88, 1.0), size=3.0)
common.get_or_create_light("RimLight", "AREA", (0.5, 3.5, 2.5), 500, (0.7, 0.9, 1.0), size=2.0)
for name in ("KeyLight", "FillLight", "RimLight"):
    o = bpy.data.objects[name]
    o.rotation_euler = (Vector((0, 0, 0)) - o.location).to_track_quat("-Z", "Y").to_euler()

cam_data = bpy.data.cameras.new("StudioCamera")
cam_data.type = "ORTHO"
cam_data.ortho_scale = 4.0
cam = link(bpy.data.objects.new("StudioCamera", cam_data))
d = Vector((1, -1, 1)).normalized() * 8.0
cam.location = d
cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
scene.camera = cam

bpy.ops.wm.save_as_mainfile(filepath=OUT, compress=True)
print("[template] saved", OUT)
