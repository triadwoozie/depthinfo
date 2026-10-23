"""Helpers shared by build_template.py and export_static_scene.py (Blender 4.2+/5.x API)."""

import bpy


def ensure_col_attribute(mesh, name="Col"):
    """Return a FLOAT_COLOR POINT attribute called ``name`` on ``mesh`` (created if needed)."""
    attr = mesh.color_attributes.get(name)
    if attr is None or attr.data_type != "FLOAT_COLOR" or attr.domain != "POINT":
        if attr is not None:
            mesh.color_attributes.remove(attr)
        attr = mesh.color_attributes.new(name=name, type="FLOAT_COLOR", domain="POINT")
    return attr


def make_vertex_color_material(name="SonarScanMaterial", attr_name="Col"):
    """Principled BSDF whose Base Color reads the ``Col`` colour attribute."""
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    col = nt.nodes.new("ShaderNodeVertexColor")
    col.layer_name = attr_name
    col.location = (-400, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (-100, 0)
    bsdf.inputs["Roughness"].default_value = 0.55
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (200, 0)
    nt.links.new(col.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    mat.use_backface_culling = False
    return mat


def make_point_instancer(radius_m, material, name="SonarPointInstancer"):
    """Geometry-nodes group: Mesh to Points (radius) → Set Material. Keeps point attributes."""
    ng = bpy.data.node_groups.get(name)
    if ng is not None:
        bpy.data.node_groups.remove(ng)
    ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n_in = ng.nodes.new("NodeGroupInput")
    n_in.location = (-400, 0)
    to_points = ng.nodes.new("GeometryNodeMeshToPoints")
    to_points.mode = "VERTICES"
    to_points.inputs["Radius"].default_value = radius_m
    to_points.location = (-150, 0)
    set_mat = ng.nodes.new("GeometryNodeSetMaterial")
    set_mat.inputs["Material"].default_value = material
    set_mat.location = (100, 0)
    n_out = ng.nodes.new("NodeGroupOutput")
    n_out.location = (350, 0)
    ng.links.new(n_in.outputs[0], to_points.inputs["Mesh"])
    ng.links.new(to_points.outputs["Points"], set_mat.inputs["Geometry"])
    ng.links.new(set_mat.outputs["Geometry"], n_out.inputs[0])
    return ng


def add_realized_sphere_modifier(obj, radius_m, material, name="SonarGlbBake"):
    """Geometry nodes: instance a small cube on every vertex and realize it.

    glTF has no usable representation for loose mesh vertices, so for .glb export each
    point becomes a tiny real mesh (cube = fewest vertices). Point attributes (``Col``)
    propagate to the geometry.
    """
    ng = bpy.data.node_groups.get(name)
    if ng is not None:
        bpy.data.node_groups.remove(ng)
    ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n_in = ng.nodes.new("NodeGroupInput")
    ico = ng.nodes.new("GeometryNodeMeshCube")
    ico.inputs["Size"].default_value = (radius_m * 1.6,) * 3
    inst = ng.nodes.new("GeometryNodeInstanceOnPoints")
    realize = ng.nodes.new("GeometryNodeRealizeInstances")
    set_mat = ng.nodes.new("GeometryNodeSetMaterial")
    set_mat.inputs["Material"].default_value = material
    n_out = ng.nodes.new("NodeGroupOutput")
    ng.links.new(n_in.outputs[0], inst.inputs["Points"])
    ng.links.new(ico.outputs["Mesh"], inst.inputs["Instance"])
    ng.links.new(inst.outputs["Instances"], realize.inputs["Geometry"])
    ng.links.new(realize.outputs["Geometry"], set_mat.inputs["Geometry"])
    ng.links.new(set_mat.outputs["Geometry"], n_out.inputs[0])
    mod = obj.modifiers.new(name, "NODES")
    mod.node_group = ng
    return mod


def add_point_instancer_modifier(obj, radius_m, material):
    ng = make_point_instancer(radius_m, material)
    mod = obj.modifiers.new("PointInstancer", "NODES")
    mod.node_group = ng
    return mod


def get_or_create_light(name, light_type, location, energy, color=(1.0, 1.0, 1.0), size=2.0):
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "LIGHT":
        data = bpy.data.lights.new(name, light_type)
        obj = bpy.data.objects.new(name, data)
        bpy.context.scene.collection.objects.link(obj)
    obj.data.type = light_type
    obj.data.energy = energy
    obj.data.color = color
    if light_type == "AREA":
        obj.data.size = size
    obj.location = location
    return obj
