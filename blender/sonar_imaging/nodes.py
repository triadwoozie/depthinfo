"""Geometry Nodes and material for the displaced sonar surface (Blender 4.2+ / 5.x).

The "Sonar_Displace" node tree turns a flat grid into the subject's 3D shape:

    grid vertex (x, y) in [-1, 1]            UV = (x, y) * 0.5 + 0.5
        near   = Sonar_Depth_Texture(UV).R   0 = background, 1 = nearest subject surface
        disp   = near * Relief               metres toward the camera
        scale  = (PlaneDistance - disp) / PlaneDistance
        P'     = ( x * HalfWidth * scale,  y * HalfWidth * Aspect * scale,  disp )

    The scale term is the pinhole frustum warp: a point that is nearer the camera sits at a smaller
    lateral extent, so the surface is a true perspective back-projection and not a stretched relief.
    Vertices with near < Cutoff are deleted, which removes the background entirely and leaves only
    the subject's silhouette. The optical colours are sampled the same way into the point attribute
    "Optical_Color", which the material displays.

Local space: +Z points at the camera, which sits at (0, 0, PlaneDistance). The object is rotated so
that this reads as "toward the viewer" in the viewport (see operators.py).
"""

import bpy

GROUP_NAME = "Sonar_Displace"
MATERIAL_NAME = "Sonar_Surface_Material"
COLOR_ATTRIBUTE = "Optical_Color"
NEAR_ATTRIBUTE = "Sonar_Near"

# Modifier inputs, in interface order
INPUTS = (
    ("Relief", 0.75),           # m of displacement at depth 1.0
    ("Cutoff", 0.05),           # nearness below this is deleted
    ("Plane Distance", 1.7),    # m from the camera to the depth-0 plane
    ("Half Width", 1.0),        # m, half the image width at the plane
    ("Aspect", 0.75),           # height / width
)


def _new(nodes, idname, x, y, **props):
    node = nodes.new(idname)
    node.location = (x, y)
    for key, value in props.items():
        setattr(node, key, value)
    return node


def build_group(depth_image, color_image):
    """(Re)create the "Sonar_Displace" node group bound to the two image datablocks."""
    old = bpy.data.node_groups.get(GROUP_NAME)
    if old is not None:
        bpy.data.node_groups.remove(old)
    ng = bpy.data.node_groups.new(GROUP_NAME, "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    for name, default in INPUTS:
        sock = ng.interface.new_socket(name, in_out="INPUT", socket_type="NodeSocketFloat")
        sock.default_value = default
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    n, link = ng.nodes, ng.links.new
    grp_in = _new(n, "NodeGroupInput", -1400, 0)
    grp_out = _new(n, "NodeGroupOutput", 1500, 0)

    # UV from position: (x, y) * 0.5 + 0.5
    pos = _new(n, "GeometryNodeInputPosition", -1300, -300)
    uv = _new(n, "ShaderNodeVectorMath", -1100, -300, operation="MULTIPLY_ADD")
    uv.inputs[1].default_value = (0.5, 0.5, 0.0)
    uv.inputs[2].default_value = (0.5, 0.5, 0.0)
    link(pos.outputs["Position"], uv.inputs[0])

    # nearness from the depth image (red channel)
    depth_tex = _new(n, "GeometryNodeImageTexture", -900, 100, interpolation="Linear", extension="EXTEND")
    depth_tex.inputs["Image"].default_value = depth_image
    link(uv.outputs["Vector"], depth_tex.inputs["Vector"])
    near = _new(n, "FunctionNodeSeparateColor", -700, 100)
    link(depth_tex.outputs["Color"], near.inputs["Color"])

    # displacement toward the camera and the frustum scale
    disp = _new(n, "ShaderNodeMath", -500, 100, operation="MULTIPLY")
    link(near.outputs["Red"], disp.inputs[0])
    link(grp_in.outputs["Relief"], disp.inputs[1])
    dist_minus = _new(n, "ShaderNodeMath", -500, -100, operation="SUBTRACT")
    link(grp_in.outputs["Plane Distance"], dist_minus.inputs[0])
    link(disp.outputs["Value"], dist_minus.inputs[1])
    scale = _new(n, "ShaderNodeMath", -300, -100, operation="DIVIDE")
    link(dist_minus.outputs["Value"], scale.inputs[0])
    link(grp_in.outputs["Plane Distance"], scale.inputs[1])

    # lateral position: x,y * HalfWidth * scale (y additionally * Aspect)
    xyz = _new(n, "ShaderNodeSeparateXYZ", -1100, -500)
    link(pos.outputs["Position"], xyz.inputs["Vector"])
    kx = _new(n, "ShaderNodeMath", -300, -300, operation="MULTIPLY")
    link(grp_in.outputs["Half Width"], kx.inputs[0])
    link(scale.outputs["Value"], kx.inputs[1])
    x_out = _new(n, "ShaderNodeMath", -100, -300, operation="MULTIPLY")
    link(xyz.outputs["X"], x_out.inputs[0])
    link(kx.outputs["Value"], x_out.inputs[1])
    ky = _new(n, "ShaderNodeMath", -100, -500, operation="MULTIPLY")
    link(kx.outputs["Value"], ky.inputs[0])
    link(grp_in.outputs["Aspect"], ky.inputs[1])
    y_out = _new(n, "ShaderNodeMath", 100, -500, operation="MULTIPLY")
    link(xyz.outputs["Y"], y_out.inputs[0])
    link(ky.outputs["Value"], y_out.inputs[1])
    new_pos = _new(n, "ShaderNodeCombineXYZ", 300, -300)
    link(x_out.outputs["Value"], new_pos.inputs["X"])
    link(y_out.outputs["Value"], new_pos.inputs["Y"])
    link(disp.outputs["Value"], new_pos.inputs["Z"])

    # Fields are evaluated where they are consumed, and Set Position moves the vertices. So the
    # colour and the nearness (both sampled from the ORIGINAL grid position) are stamped onto the
    # vertices first, and read back by name after the warp.
    color_tex = _new(n, "GeometryNodeImageTexture", -900, -700, interpolation="Linear", extension="EXTEND")
    color_tex.inputs["Image"].default_value = color_image
    link(uv.outputs["Vector"], color_tex.inputs["Vector"])
    store_color = _new(n, "GeometryNodeStoreNamedAttribute", 100, 200, data_type="FLOAT_COLOR", domain="POINT")
    store_color.inputs["Name"].default_value = COLOR_ATTRIBUTE
    link(grp_in.outputs["Geometry"], store_color.inputs["Geometry"])
    link(color_tex.outputs["Color"], store_color.inputs["Value"])

    store_near = _new(n, "GeometryNodeStoreNamedAttribute", 350, 200, data_type="FLOAT", domain="POINT")
    store_near.inputs["Name"].default_value = NEAR_ATTRIBUTE
    link(store_color.outputs["Geometry"], store_near.inputs["Geometry"])
    link(near.outputs["Red"], store_near.inputs["Value"])

    set_pos = _new(n, "GeometryNodeSetPosition", 600, 200)
    link(store_near.outputs["Geometry"], set_pos.inputs["Geometry"])
    link(new_pos.outputs["Vector"], set_pos.inputs["Position"])

    # delete what is not the subject: nearness < Cutoff
    stored = _new(n, "GeometryNodeInputNamedAttribute", 600, -150, data_type="FLOAT")
    stored.inputs["Name"].default_value = NEAR_ATTRIBUTE
    below = _new(n, "FunctionNodeCompare", 850, -150, data_type="FLOAT", operation="LESS_THAN")
    link(stored.outputs["Attribute"], below.inputs[0])
    link(grp_in.outputs["Cutoff"], below.inputs[1])
    delete = _new(n, "GeometryNodeDeleteGeometry", 1100, 200, domain="POINT")
    link(set_pos.outputs["Geometry"], delete.inputs["Geometry"])
    link(below.outputs["Result"], delete.inputs["Selection"])
    link(delete.outputs["Geometry"], grp_out.inputs["Geometry"])
    return ng


def input_identifier(ng, name):
    for item in ng.interface.items_tree:
        if getattr(item, "in_out", None) == "INPUT" and item.name == name:
            return item.identifier
    return None


def set_modifier_input(mod, name, value):
    """Set a Geometry Nodes modifier input by its display name.

    Blender 5.3 moved the inputs from ID properties (``mod[id]``) to
    ``mod.properties.inputs[id]["value"]``; 4.2 to 5.2 still use ``mod[id]``.
    """
    ident = input_identifier(mod.node_group, name)
    if ident is None:
        return False
    props = getattr(mod, "properties", None)
    if props is not None:
        try:
            props.inputs[ident]["value"] = float(value)
            return True
        except (KeyError, TypeError, AttributeError):
            pass
    mod[ident] = float(value)
    return True


def ensure_material():
    """Principled BSDF coloured by the "Optical_Color" point attribute."""
    mat = bpy.data.materials.get(MATERIAL_NAME) or bpy.data.materials.new(MATERIAL_NAME)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    attr = nt.nodes.new("ShaderNodeAttribute")
    attr.attribute_type = "GEOMETRY"
    attr.attribute_name = COLOR_ATTRIBUTE
    attr.location = (-400, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = 0.65
    bsdf.location = (-100, 0)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (200, 0)
    nt.links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    mat.use_backface_culling = False
    return mat
