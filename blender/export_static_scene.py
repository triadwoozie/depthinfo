"""
export_static_scene.py — headless static scene builder (AGENTS.md § 3.4). Blender 4.2+ / 5.x.

    blender --background blender/template_exhibition.blend \
            --python blender/export_static_scene.py -- \
            --input scan.ply --output scan.blend --color_mode optical_rgb

Imports the stitched PLY, bakes a ``Col`` FloatColorAttribute, builds a
Principled BSDF reading it, adds an isometric studio camera + 3-point lights and
an atmospheric volume / mist pass matching the dashboard fog settings, then saves
a compressed .blend and exports a .glb next to it.

The input PLY uses the backend frame (x right, y up, z forward); it is remapped to
Blender's Z-up frame as (x, z, y).
"""

import argparse
import math
import os
import sys
import traceback

import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _sonar_common as common  # noqa: E402

MAX_GLB_POINTS = 15000
FALLOFF_TO_MIST = {"linear": "LINEAR", "exponential": "INVERSE_QUADRATIC", "squared": "QUADRATIC"}


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--color_mode", default="optical_rgb",
                   choices=["optical_rgb", "fog_gradient", "acoustic_heatmap", "custom_palette"])
    p.add_argument("--near_hex", default="#00F5D4")
    p.add_argument("--far_hex", default="#3A0CA3")
    p.add_argument("--point_radius_mm", type=float, default=4.0)
    p.add_argument("--min_scan", type=float, default=0.35)
    p.add_argument("--max_scan", type=float, default=2.5)
    p.add_argument("--fog_density", type=float, default=1.2)
    p.add_argument("--falloff", default="exponential", choices=list(FALLOFF_TO_MIST))
    return p.parse_args(argv)


def import_ply(path):
    before = set(bpy.data.objects)
    bpy.ops.wm.ply_import(filepath=path, forward_axis="Y", up_axis="Z")
    new = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    if not new:
        raise RuntimeError(f"PLY import produced no mesh: {path}")
    return new[0]


def bake_colors(obj, mode):
    """Ensure the imported vertex colours live in a FLOAT_COLOR POINT attribute named ``Col``.

    Colours were already routed per ``color_mode`` by the backend stitcher
    (AGENTS.md § 6); here we only normalise the storage format.
    """
    mesh = obj.data
    src = None
    for attr in mesh.color_attributes:
        src = attr
        if attr.name == "Col":
            break
    n = len(mesh.vertices)
    rgba = np.ones((n, 4), dtype=np.float32)
    if src is not None and src.domain == "POINT":
        src.data.foreach_get("color", rgba.ravel())
    elif src is not None:  # corner-domain colours: take the first corner per vertex
        loops = np.zeros(len(mesh.loops), dtype=np.int32)
        mesh.loops.foreach_get("vertex_index", loops)
        corner = np.ones((len(mesh.loops), 4), dtype=np.float32)
        src.data.foreach_get("color", corner.ravel())
        rgba[loops] = corner
    else:
        print(f"[export] WARNING: PLY had no colours for mode '{mode}'; using white")
    if src is not None:
        mesh.color_attributes.remove(src)
    col = common.ensure_col_attribute(mesh, "Col")
    col.data.foreach_set("color", rgba.ravel())
    mesh.update()


def to_blender_frame(obj):
    """Backend (x right, y up, z fwd) → Blender (x right, y fwd, z up)."""
    mesh = obj.data
    co = np.zeros(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)[:, [0, 2, 1]].copy()
    mesh.vertices.foreach_set("co", co.ravel())
    mesh.update()
    return co


def setup_world(args, cam_dist, extent):
    scene = bpy.context.scene
    world = bpy.data.worlds.get("SonarWorld") or bpy.data.worlds.new("SonarWorld")
    scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value = (0.012, 0.012, 0.016, 1.0)
    bg.inputs["Strength"].default_value = 1.0
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])

    # Atmospheric haze: optical depth across the cloud ~ 0.03 x fog_density. The world volume
    # fills the whole view, so density must scale with the subject, not with the scan range.
    vol = nt.nodes.new("ShaderNodeVolumeScatter")
    vol.inputs["Density"].default_value = 0.03 * args.fog_density / max(extent, 0.1)
    vol.inputs["Anisotropy"].default_value = 0.3
    vol.inputs["Color"].default_value = (0.75, 0.85, 0.9, 1.0)
    nt.links.new(vol.outputs["Volume"], out.inputs["Volume"])

    # Mist (Z-depth) pass over the cloud's depth extent as seen from the studio camera.
    world.mist_settings.start = max(cam_dist - extent / 2.0, 0.0)
    world.mist_settings.depth = max(extent, 0.1)
    world.mist_settings.falloff = FALLOFF_TO_MIST[args.falloff]
    bpy.context.view_layer.use_pass_mist = True


def setup_camera_and_lights(center, extent):
    scene = bpy.context.scene
    iso_dir = Vector((1.0, -1.0, 1.0)).normalized()  # true isometric: 35.26° elevation, 45° azimuth
    dist = max(extent, 0.5) * 3.0
    cam_obj = bpy.data.objects.get("StudioCamera")
    if cam_obj is None or cam_obj.type != "CAMERA":
        cam_obj = bpy.data.objects.new("StudioCamera", bpy.data.cameras.new("StudioCamera"))
        scene.collection.objects.link(cam_obj)
    cam_obj.data.type = "ORTHO"
    cam_obj.data.ortho_scale = max(extent, 0.5) * 1.35
    cam_obj.data.clip_end = dist * 4.0
    cam_obj.location = center + iso_dir * dist
    cam_obj.rotation_euler = (-iso_dir).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam_obj

    # 3-point rig, scaled to the subject. Area light power grows with distance².
    r = max(extent, 0.5)
    k = (r * 2.5) ** 2 / 6.0
    common.get_or_create_light("KeyLight", "AREA", center + Vector((1.2, -1.6, 1.4)) * r, 120 * k,
                               (1.0, 0.96, 0.9), size=r * 1.2)
    common.get_or_create_light("FillLight", "AREA", center + Vector((-1.6, -1.0, 0.6)) * r, 45 * k,
                               (0.8, 0.88, 1.0), size=r * 1.6)
    common.get_or_create_light("RimLight", "AREA", center + Vector((0.2, 1.8, 1.2)) * r, 100 * k,
                               (0.7, 0.9, 1.0), size=r)
    for name in ("KeyLight", "FillLight", "RimLight"):
        light = bpy.data.objects[name]
        light.rotation_euler = (center - light.location).to_track_quat("-Z", "Y").to_euler()
    return cam_obj


def set_engine():
    scene = bpy.context.scene
    for engine in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):  # id differs across 4.2 / 5.x
        try:
            scene.render.engine = engine
            return
        except TypeError:
            continue


def export_glb(obj, path, point_radius_m):
    """Export ``obj`` as .glb. Loose-vertex clouds are baked to small instanced spheres first."""
    target, tmp = obj, None
    if len(obj.data.polygons) == 0:
        # Uniformly thin the cloud so the baked file stays shareable (cubes still add up).
        n = len(obj.data.vertices)
        co = np.zeros(n * 3, dtype=np.float32)
        obj.data.vertices.foreach_get("co", co)
        rgba = np.ones(n * 4, dtype=np.float32)
        obj.data.color_attributes["Col"].data.foreach_get("color", rgba)
        idx = np.linspace(0, n - 1, min(n, MAX_GLB_POINTS)).astype(np.int64)
        mesh = bpy.data.meshes.new("SonarGlbPoints")
        mesh.from_pydata(co.reshape(-1, 3)[idx].tolist(), [], [])
        col = common.ensure_col_attribute(mesh, "Col")
        col.data.foreach_set("color", rgba.reshape(-1, 4)[idx].ravel())
        mesh.materials.append(obj.data.materials[0])
        tmp = bpy.data.objects.new("SonarGlbPoints", mesh)
        bpy.context.scene.collection.objects.link(tmp)
        common.add_realized_sphere_modifier(tmp, point_radius_m, obj.data.materials[0])
        target = tmp
    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    kwargs = dict(filepath=path, export_format="GLB", use_selection=True, export_apply=tmp is not None)
    ok = False
    for extra in ({"export_vertex_color": "ACTIVE"}, {"export_colors": True}, {}):
        try:
            bpy.ops.export_scene.gltf(**kwargs, **extra)
            ok = True
            break
        except TypeError:
            continue  # option renamed in this Blender version
    if tmp is not None:
        mesh = tmp.data
        bpy.data.objects.remove(tmp)
        bpy.data.meshes.remove(mesh)
    return ok


def main():
    args = parse_args()
    in_path, out_path = os.path.abspath(args.input), os.path.abspath(args.output)
    base, _ = os.path.splitext(out_path)

    obj = import_ply(in_path)
    obj.name = "SonarScan"
    obj.data.name = "SonarScan"
    bake_colors(obj, args.color_mode)
    co = to_blender_frame(obj)

    mat = common.make_vertex_color_material()
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    has_faces = len(obj.data.polygons) > 0

    lo, hi = co.min(axis=0), co.max(axis=0)
    center = Vector(((lo + hi) / 2.0).tolist())
    extent = float(np.linalg.norm(hi - lo))

    # Exhibition floor sits just under the scan; live-only placeholders are hidden.
    floor = bpy.data.objects.get("ExhibitionFloor")
    if floor is not None:
        floor.location = (center.x, center.y, float(lo[2]) - 0.02)
        s = max(extent, 1.0) * 1.2
        floor.scale = (s, s, 1.0)
    for name in ("SonarPointCloud", "SonarPing", "SonarTarget"):
        o = bpy.data.objects.get(name)
        if o is not None:
            o.hide_render = True
            o.hide_viewport = True

    cam = setup_camera_and_lights(center, extent)
    setup_world(args, (cam.location - center).length, extent)
    set_engine()

    # .glb first, from the plain vertex-coloured mesh (modifiers are not applied to it).
    glb_ok = export_glb(obj, base + ".glb", args.point_radius_mm / 1000.0)

    if not has_faces:  # point cloud: render points as spheres of the dashboard's point radius
        common.add_point_instancer_modifier(obj, args.point_radius_mm / 1000.0, mat)

    bpy.ops.wm.save_as_mainfile(filepath=out_path, compress=True)
    print(f"[export] vertices={len(obj.data.vertices)} faces={len(obj.data.polygons)} "
          f"glb={'ok' if glb_ok else 'FAILED'} -> {out_path}")


try:
    main()
except Exception:
    traceback.print_exc()
    sys.exit(1)
