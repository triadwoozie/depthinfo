"""Scene helpers: the SonarCloud mesh, its vertex-colour material and point-radius modifier."""

import bpy

CLOUD_NAME = "SonarCloud"
LEGACY_CLOUD_NAME = "SonarPointCloud"  # name used by template_exhibition.blend
GROUP_NAME = "SonarLivePoints"          # our group: has an exposed Radius input
TEMPLATE_GROUP = "SonarPointInstancer"  # template_exhibition.blend group (no Radius input)
RADIUS_SOCKET = "Radius"
COL = "Col"


def ensure_col_attribute(mesh):
    """Return the FLOAT_COLOR / POINT attribute ``Col`` (recreated if it has another type)."""
    attr = mesh.color_attributes.get(COL)
    if attr is None or attr.data_type != "FLOAT_COLOR" or attr.domain != "POINT":
        if attr is not None:
            mesh.color_attributes.remove(attr)
        attr = mesh.color_attributes.new(name=COL, type="FLOAT_COLOR", domain="POINT")
    return attr


def vertex_color_material():
    mat = bpy.data.materials.get("SonarCloudMaterial") or bpy.data.materials.new("SonarCloudMaterial")
    if not mat.node_tree or "SonarVertexColor" not in mat.node_tree.nodes:
        mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        col = nt.nodes.new("ShaderNodeVertexColor")
        col.name = "SonarVertexColor"
        col.layer_name = COL
        col.location = (-400, 0)
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (-100, 0)
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        out.location = (200, 0)
        nt.links.new(col.outputs["Color"], bsdf.inputs["Base Color"])
        nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def _instancer_group(material):
    """Geometry Nodes: Mesh to Points with an exposed ``Radius`` input, then Set Material."""
    ng = bpy.data.node_groups.get(GROUP_NAME)
    if ng is not None:
        return ng
    ng = bpy.data.node_groups.new(GROUP_NAME, "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    radius = ng.interface.new_socket(RADIUS_SOCKET, in_out="INPUT", socket_type="NodeSocketFloat")
    radius.default_value = 0.004
    radius.min_value = 0.0001
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n_in = ng.nodes.new("NodeGroupInput")
    n_in.location = (-400, 0)
    to_points = ng.nodes.new("GeometryNodeMeshToPoints")
    to_points.mode = "VERTICES"
    to_points.location = (-150, 0)
    set_mat = ng.nodes.new("GeometryNodeSetMaterial")
    set_mat.inputs["Material"].default_value = material
    set_mat.location = (100, 0)
    n_out = ng.nodes.new("NodeGroupOutput")
    n_out.location = (350, 0)
    ng.links.new(n_in.outputs["Geometry"], to_points.inputs["Mesh"])
    ng.links.new(n_in.outputs[RADIUS_SOCKET], to_points.inputs["Radius"])
    ng.links.new(to_points.outputs["Points"], set_mat.inputs["Geometry"])
    ng.links.new(set_mat.outputs["Geometry"], n_out.inputs["Geometry"])
    return ng


def _radius_identifier(ng):
    for item in ng.interface.items_tree:
        if getattr(item, "in_out", None) == "INPUT" and item.name == RADIUS_SOCKET:
            return item.identifier
    return None


def ensure_instancer(obj, radius_m):
    """Make sure ``obj`` renders its vertices as points of ``radius_m`` (a modifier input)."""
    for m in list(obj.modifiers):  # the template's own instancer would double the points
        if m.type == "NODES" and m.node_group and m.node_group.name.startswith(TEMPLATE_GROUP):
            obj.modifiers.remove(m)
    mod = next((m for m in obj.modifiers if m.type == "NODES" and m.node_group
                and m.node_group.name.startswith(GROUP_NAME)), None)
    if mod is None:
        mod = obj.modifiers.new("SonarLiveInstancer", "NODES")
        mod.node_group = _instancer_group(vertex_color_material())
    set_point_radius(obj, radius_m)
    return mod


def _set_modifier_input(mod, identifier, value):
    """Set a Geometry Nodes modifier input. Blender 5.3 moved these off the ID properties
    (``mod[id]``) onto ``mod.properties.inputs[id]["value"]``; 4.2–5.2 still use ``mod[id]``."""
    props = getattr(mod, "properties", None)
    if props is not None:
        try:
            props.inputs[identifier]["value"] = value
            return
        except (KeyError, TypeError, AttributeError):
            pass
    mod[identifier] = value


def set_point_radius(obj, radius_m):
    """Drive the point radius (metres) through the modifier's ``Radius`` input."""
    for mod in obj.modifiers:
        if mod.type == "NODES" and mod.node_group and mod.node_group.name.startswith(GROUP_NAME):
            ident = _radius_identifier(mod.node_group)
            if ident is not None:
                _set_modifier_input(mod, ident, float(radius_m))
                obj.update_tag()
            return True
    return False


def create_cloud(context, radius_m):
    """Create the ``SonarCloud`` mesh object (empty, vertex-coloured, point-rendered)."""
    mesh = bpy.data.meshes.new(CLOUD_NAME)
    ensure_col_attribute(mesh)
    mesh.materials.append(vertex_color_material())
    obj = bpy.data.objects.new(CLOUD_NAME, mesh)
    (context.collection or context.scene.collection).objects.link(obj)
    ensure_instancer(obj, radius_m)
    return obj


def find_cloud(scene_settings=None):
    """The user-chosen target, else ``SonarCloud``, else the template's ``SonarPointCloud``."""
    if scene_settings is not None and scene_settings.target is not None:
        return scene_settings.target
    for name in (CLOUD_NAME, LEGACY_CLOUD_NAME):
        obj = bpy.data.objects.get(name)
        if obj is not None and obj.type == "MESH":
            return obj
    return None
