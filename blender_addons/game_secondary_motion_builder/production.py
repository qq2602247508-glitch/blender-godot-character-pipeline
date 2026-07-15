"""Production authoring tools for modular Godot secondary-motion equipment.

This module deliberately stays separate from the historical cutter/optimizer MVP.
It creates an equipment-local copy of a humanoid armature, appends smooth hair or
skirt chains, bone-parents collider authoring proxies, and exports a small JSON
profile consumed by the Godot side of the pipeline.
"""

import json
import heapq
import math
import os
import re

import bpy
import bmesh
from mathutils import Matrix, Vector


PRODUCTION_COLLECTION = "GSMB_PRODUCTION"
HAIR_GUIDE_COLLECTION = "GSMB_HAIR_GUIDES"


BONE_ALIASES = {
    "head": ("head",),
    "chest": ("chest", "upperchest", "spine2", "spine_02", "spine02"),
    "hips": ("hips", "pelvis", "hip"),
    "thigh_l": ("leftupperleg", "leftupleg", "thigh_l", "upperleg_l", "upleg_l"),
    "thigh_r": ("rightupperleg", "rightupleg", "thigh_r", "upperleg_r", "upleg_r"),
}

GODOT_PROFILE_BONES = {
    "head": "Head",
    "chest": "Chest",
    "hips": "Hips",
    "thigh_l": "LeftUpperLeg",
    "thigh_r": "RightUpperLeg",
}


def _normalized_bone_name(name):
    base = name.rsplit(":", 1)[-1]
    return re.sub(r"[^a-z0-9]", "", base.lower())


def _safe_id(name):
    value = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")
    return value or "Equipment"


def _production_collection(scene):
    collection = bpy.data.collections.get(PRODUCTION_COLLECTION)
    if collection is None:
        collection = bpy.data.collections.new(PRODUCTION_COLLECTION)
    if collection.name not in {child.name for child in scene.collection.children}:
        scene.collection.children.link(collection)
    return collection


def _source_armature(context):
    configured = context.scene.gsmb_prod_armature
    if configured and configured.type == "ARMATURE":
        return configured
    active = context.active_object
    if active and active.type == "ARMATURE":
        return active
    return next((obj for obj in context.selected_objects if obj.type == "ARMATURE"), None)


def _selected_meshes(context):
    return [obj for obj in context.selected_objects if obj.type == "MESH"]


def _resolve_bone(armature, role, explicit=""):
    if explicit and explicit in armature.data.bones:
        return explicit
    normalized = {_normalized_bone_name(bone.name): bone.name for bone in armature.data.bones}
    for alias in BONE_ALIASES[role]:
        match = normalized.get(_normalized_bone_name(alias))
        if match:
            return match
    return ""


def _clone_armature(source, name, collection):
    data = source.data.copy()
    data.name = f"{name}_Data"
    armature = bpy.data.objects.new(name, data)
    collection.objects.link(armature)
    armature.matrix_world = source.matrix_world.copy()
    armature.show_in_front = True
    armature["gsmb_production_equipment"] = True
    armature["gsmb_source_armature"] = source.name
    return armature


def _mesh_points_in_armature(mesh, armature):
    to_armature = armature.matrix_world.inverted() @ mesh.matrix_world
    return [to_armature @ vertex.co for vertex in mesh.data.vertices]


def _bounds(points):
    minimum = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    maximum = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return minimum, maximum


def _add_chain(armature, prefix, points, parent_name, settings):
    created = []
    parent = armature.data.edit_bones.get(parent_name)
    if parent is None:
        raise ValueError(f"Parent bone not found: {parent_name}")
    for index in range(len(points) - 1):
        bone = armature.data.edit_bones.new(f"{prefix}_{index + 1:02d}")
        bone.head = points[index]
        bone.tail = points[index + 1]
        bone.parent = created[-1] if created else parent
        bone.use_connect = bool(created)
        bone["gsmb_secondary"] = True
        bone["gsmb_stiffness"] = settings["stiffness"]
        bone["gsmb_drag"] = settings["drag"]
        bone["gsmb_gravity"] = settings["gravity"]
        bone["gsmb_radius"] = settings["radius"]
        created.append(bone)
    return [bone.name for bone in created]


def _ensure_modifier(mesh, armature):
    modifiers = [modifier for modifier in mesh.modifiers if modifier.type == "ARMATURE"]
    modifier = modifiers[0] if modifiers else mesh.modifiers.new("GSMB Equipment Armature", "ARMATURE")
    modifier.name = "GSMB Equipment Armature"
    modifier.object = armature
    for extra in modifiers[1:]:
        mesh.modifiers.remove(extra)


def _parent_mesh_to_armature(mesh, armature):
    """Keep the authored world transform while producing a glTF skin hierarchy."""
    world_matrix = mesh.matrix_world.copy()
    mesh.parent = armature
    mesh.parent_type = "OBJECT"
    mesh.matrix_parent_inverse = armature.matrix_world.inverted_safe()
    mesh.matrix_world = world_matrix


def _clear_deform_groups(mesh, armature):
    deform_names = {bone.name for bone in armature.data.bones if bone.use_deform}
    for group in list(mesh.vertex_groups):
        if group.name in deform_names or group.name.startswith("GSMB_"):
            mesh.vertex_groups.remove(group)


def _group(mesh, name):
    return mesh.vertex_groups.get(name) or mesh.vertex_groups.new(name=name)


def _add_normalized_weights(mesh, vertex_index, weights):
    filtered = [(name, max(0.0, weight)) for name, weight in weights if weight > 1e-6]
    total = sum(weight for _, weight in filtered)
    if total <= 1e-8:
        return
    for name, weight in filtered:
        _group(mesh, name).add([vertex_index], weight / total, "REPLACE")


def _linear_pair(value, count):
    if count <= 1:
        return ((0, 1.0),)
    position = max(0.0, min(0.999999, value)) * (count - 1)
    first = int(math.floor(position))
    second = min(count - 1, first + 1)
    blend = position - first
    if first == second:
        return ((first, 1.0),)
    return ((first, 1.0 - blend), (second, blend))


def _weight_hair(mesh, armature, chains, parent_bone):
    points = _mesh_points_in_armature(mesh, armature)
    minimum, maximum = _bounds(points)
    height = max(1e-6, maximum.z - minimum.z)
    width = max(1e-6, maximum.x - minimum.x)
    for vertex, point in zip(mesh.data.vertices, points):
        down = max(0.0, min(1.0, (maximum.z - point.z) / height))
        across = max(0.0, min(0.999999, (point.x - minimum.x) / width))
        chain_pairs = _linear_pair(across, len(chains))
        root_weight = max(0.0, 1.0 - down / 0.18)
        secondary_scale = 1.0 - root_weight
        weights = [(parent_bone, root_weight)]
        for chain_index, chain_weight in chain_pairs:
            if root_weight > 0.0:
                weights.append((chains[chain_index][0], secondary_scale * chain_weight))
            else:
                for bone_index, bone_weight in _linear_pair(down, len(chains[chain_index])):
                    weights.append((chains[chain_index][bone_index], secondary_scale * chain_weight * bone_weight))
        _add_normalized_weights(mesh, vertex.index, weights)


def _weight_hair_from_guides(mesh, armature, chains, parent_bone):
    chain_points = []
    for chain in chains:
        bones = [armature.data.bones[name] for name in chain]
        chain_points.append([bone.head_local.copy() for bone in bones] + [bones[-1].tail_local.copy()])
    for vertex, point in zip(mesh.data.vertices, _mesh_points_in_armature(mesh, armature)):
        best = None
        for chain_index, points in enumerate(chain_points):
            segment_count = len(points) - 1
            for segment_index in range(segment_count):
                first, second = points[segment_index], points[segment_index + 1]
                direction = second - first
                length_squared = max(1e-12, direction.length_squared)
                factor = max(0.0, min(1.0, (point - first).dot(direction) / length_squared))
                projected = first + direction * factor
                candidate = ((point - projected).length_squared, chain_index, (segment_index + factor) / segment_count)
                if best is None or candidate[0] < best[0]:
                    best = candidate
        _, chain_index, progress = best
        root_weight = max(0.0, 1.0 - progress / 0.18)
        weights = [(parent_bone, root_weight)]
        for bone_index, bone_weight in _linear_pair(progress, len(chains[chain_index])):
            weights.append((chains[chain_index][bone_index], (1.0 - root_weight) * bone_weight))
        _add_normalized_weights(mesh, vertex.index, weights)


def _weight_skirt(mesh, armature, chains, parent_bone, center):
    points = _mesh_points_in_armature(mesh, armature)
    minimum, maximum = _bounds(points)
    height = max(1e-6, maximum.z - minimum.z)
    chain_count = len(chains)
    for vertex, point in zip(mesh.data.vertices, points):
        down = max(0.0, min(1.0, (maximum.z - point.z) / height))
        angle = (math.atan2(point.y - center.y, point.x - center.x) + 2.0 * math.pi) % (2.0 * math.pi)
        chain_position = angle / (2.0 * math.pi) * chain_count
        first_chain = int(math.floor(chain_position)) % chain_count
        second_chain = (first_chain + 1) % chain_count
        chain_blend = chain_position - math.floor(chain_position)
        root_weight = max(0.0, 1.0 - down / 0.16)
        secondary_scale = 1.0 - root_weight
        weights = [(parent_bone, root_weight)]
        for chain_index, chain_weight in ((first_chain, 1.0 - chain_blend), (second_chain, chain_blend)):
            if root_weight > 0.0:
                weights.append((chains[chain_index][0], secondary_scale * chain_weight))
            else:
                for bone_index, bone_weight in _linear_pair(down, len(chains[chain_index])):
                    weights.append((chains[chain_index][bone_index], secondary_scale * chain_weight * bone_weight))
        _add_normalized_weights(mesh, vertex.index, weights)


def _make_hair_chains(armature, meshes, asset_id, parent_bone, chain_count, bones_per_chain, settings):
    all_points = []
    for mesh in meshes:
        all_points.extend(_mesh_points_in_armature(mesh, armature))
    minimum, maximum = _bounds(all_points)
    chains = []
    for chain_index in range(chain_count):
        factor = 0.5 if chain_count == 1 else chain_index / (chain_count - 1)
        x = minimum.x + (maximum.x - minimum.x) * factor
        y = (minimum.y + maximum.y) * 0.5
        points = [
            Vector((x, y, maximum.z + (minimum.z - maximum.z) * level / bones_per_chain))
            for level in range(bones_per_chain + 1)
        ]
        prefix = f"GSMB_{asset_id}_Hair_{chain_index + 1:02d}"
        chains.append(_add_chain(armature, prefix, points, parent_bone, settings))
    return chains


def _make_hair_chains_from_guides(scene, armature, asset_id, parent_bone, bones_per_chain, settings):
    collection = bpy.data.collections.get(HAIR_GUIDE_COLLECTION)
    guides = [] if collection is None else sorted(
        (obj for obj in collection.objects if obj.type == "CURVE" and obj.get("gsmb_hair_guide")),
        key=lambda obj: obj.name,
    )
    if not guides:
        raise ValueError("No semi-auto hair guides found; capture a root and tip first")
    to_armature = armature.matrix_world.inverted()
    chains = []
    for guide_index, guide in enumerate(guides, 1):
        if not guide.data.splines or not guide.data.splines[0].points:
            continue
        world_points = [guide.matrix_world @ Vector(point.co[:3]) for point in guide.data.splines[0].points]
        local_points = [to_armature @ point for point in _resample_polyline(world_points, bones_per_chain + 1)]
        prefix = f"GSMB_{asset_id}_Hair_{guide_index:02d}"
        chains.append(_add_chain(armature, prefix, local_points, parent_bone, settings))
    if not chains:
        raise ValueError("Hair guide collection contains no usable poly guides")
    return chains


def _make_skirt_chains(armature, meshes, asset_id, parent_bone, chain_count, bones_per_chain, settings):
    all_points = []
    for mesh in meshes:
        all_points.extend(_mesh_points_in_armature(mesh, armature))
    minimum, maximum = _bounds(all_points)
    center = Vector(((minimum.x + maximum.x) * 0.5, (minimum.y + maximum.y) * 0.5, 0.0))
    radius = max(max(abs(p.x - center.x), abs(p.y - center.y)) for p in all_points)
    chains = []
    for chain_index in range(chain_count):
        angle = 2.0 * math.pi * chain_index / chain_count
        points = []
        for level in range(bones_per_chain + 1):
            down = level / bones_per_chain
            local_radius = radius * (0.72 + 0.28 * down)
            points.append(Vector((
                center.x + math.cos(angle) * local_radius,
                center.y + math.sin(angle) * local_radius,
                maximum.z + (minimum.z - maximum.z) * down,
            )))
        prefix = f"GSMB_{asset_id}_Skirt_{chain_index + 1:02d}"
        chains.append(_add_chain(armature, prefix, points, parent_bone, settings))
    return chains, center


def _bone_world_matrix(armature, bone_name):
    return armature.matrix_world @ armature.data.bones[bone_name].matrix_local


def _create_collider(collection, armature, bone_name, role, shape, radius, height):
    collider = bpy.data.objects.new(f"GSMB_COL_{_safe_id(armature.name)}_{role}", None)
    collection.objects.link(collider)
    collider.empty_display_type = "SPHERE" if shape == "sphere" else "CUBE"
    collider.empty_display_size = radius
    collider.show_in_front = True
    world = _bone_world_matrix(armature, bone_name) @ Matrix.Translation((0.0, max(height * 0.5, radius), 0.0))
    collider.parent = armature
    collider.parent_type = "BONE"
    collider.parent_bone = bone_name
    collider.matrix_world = world
    collider["gsmb_collider"] = True
    collider["gsmb_collider_role"] = role
    collider["gsmb_bone"] = bone_name
    collider["gsmb_shape"] = shape
    collider["gsmb_radius"] = radius
    collider["gsmb_height"] = height
    collider["gsmb_position_offset"] = [0.0, 0.0, 0.0]
    return collider


def _create_standard_colliders(collection, armature, mapping, scale):
    specs = (
        ("head", "sphere", scale * 0.105, 0.0),
        ("chest", "capsule", scale * 0.115, scale * 0.28),
        ("hips", "capsule", scale * 0.13, scale * 0.18),
        ("thigh_l", "capsule", scale * 0.065, scale * 0.27),
        ("thigh_r", "capsule", scale * 0.065, scale * 0.27),
    )
    colliders = []
    for role, shape, radius, height in specs:
        bone_name = mapping.get(role, "")
        if bone_name:
            colliders.append(_create_collider(collection, armature, bone_name, role, shape, radius, height))
    return colliders


def _canonicalize_anchor_bones(armature, meshes, colliders, source_mapping):
    """Rename required equipment anchors without touching the source rig."""
    renamed = {}
    for role, source_name in source_mapping.items():
        target_name = GODOT_PROFILE_BONES[role]
        if not source_name:
            continue
        bone = armature.data.bones.get(source_name)
        if bone is None:
            continue
        existing = armature.data.bones.get(target_name)
        if existing is not None and existing != bone:
            raise ValueError(
                f"Cannot canonicalize {source_name} to {target_name}: target already exists"
            )
        if source_name != target_name:
            bone.name = target_name
            for mesh in meshes:
                group = mesh.vertex_groups.get(source_name)
                if group is not None:
                    group.name = target_name
            renamed[source_name] = target_name

    for collider in colliders:
        source_name = collider.get("gsmb_bone", collider.parent_bone)
        collider["gsmb_source_bone"] = source_name
        canonical_name = renamed.get(source_name, source_name)
        collider.parent_bone = canonical_name
        collider["gsmb_bone"] = canonical_name
    return {
        role: GODOT_PROFILE_BONES[role] if source_mapping.get(role) else ""
        for role in source_mapping
    }


def _armature_height(armature):
    points = []
    for bone in armature.data.bones:
        points.extend((bone.head_local, bone.tail_local))
    if not points:
        return 1.8
    return max(0.01, max(point.z for point in points) - min(point.z for point in points))


def _settings(scene):
    return {
        "stiffness": scene.gsmb_prod_stiffness,
        "drag": scene.gsmb_prod_drag,
        "gravity": scene.gsmb_prod_gravity,
        "radius": scene.gsmb_prod_radius,
    }


def _hair_guide_collection(scene):
    collection = bpy.data.collections.get(HAIR_GUIDE_COLLECTION)
    if collection is None:
        collection = bpy.data.collections.new(HAIR_GUIDE_COLLECTION)
    if collection.name not in {child.name for child in scene.collection.children}:
        scene.collection.children.link(collection)
    return collection


def _selected_edit_vertex_index(obj):
    if obj is None or obj.type != "MESH" or obj.mode != "EDIT":
        return -1
    mesh = bmesh.from_edit_mesh(obj.data)
    mesh.verts.ensure_lookup_table()
    selected = [vertex for vertex in mesh.verts if vertex.select]
    if not selected:
        return -1
    center = sum((vertex.co for vertex in selected), Vector()) / len(selected)
    return min(selected, key=lambda vertex: (vertex.co - center).length_squared).index


def _surface_path(obj, start_index, end_index):
    obj.update_from_editmode()
    vertices = obj.data.vertices
    adjacency = [[] for _ in vertices]
    world_points = [obj.matrix_world @ vertex.co for vertex in vertices]
    for edge in obj.data.edges:
        first, second = edge.vertices
        distance = (world_points[first] - world_points[second]).length
        adjacency[first].append((second, distance))
        adjacency[second].append((first, distance))
    queue = [(0.0, start_index)]
    costs = {start_index: 0.0}
    previous = {}
    target = world_points[end_index]
    while queue:
        _, current = heapq.heappop(queue)
        if current == end_index:
            break
        current_cost = costs[current]
        for neighbor, distance in adjacency[current]:
            new_cost = current_cost + distance
            if new_cost >= costs.get(neighbor, float("inf")):
                continue
            costs[neighbor] = new_cost
            previous[neighbor] = current
            heuristic = (world_points[neighbor] - target).length
            heapq.heappush(queue, (new_cost + heuristic, neighbor))
    if end_index not in costs:
        return []
    indices = [end_index]
    while indices[-1] != start_index:
        indices.append(previous[indices[-1]])
    indices.reverse()
    return [world_points[index] for index in indices]


def _resample_polyline(points, count):
    if len(points) < 2:
        return points
    cumulative = [0.0]
    for first, second in zip(points, points[1:]):
        cumulative.append(cumulative[-1] + (second - first).length)
    total = cumulative[-1]
    if total <= 1e-8:
        return [points[0].copy() for _ in range(count)]
    result = []
    segment = 0
    for index in range(count):
        target = total * index / (count - 1)
        while segment < len(cumulative) - 2 and cumulative[segment + 1] < target:
            segment += 1
        span = max(1e-8, cumulative[segment + 1] - cumulative[segment])
        factor = (target - cumulative[segment]) / span
        result.append(points[segment].lerp(points[segment + 1], factor))
    for _ in range(2):
        result = [result[0]] + [
            (result[index - 1] + result[index] * 2.0 + result[index + 1]) / 4.0
            for index in range(1, len(result) - 1)
        ] + [result[-1]]
    return result


def _create_hair_guide(scene, source, root_index, tip_index, points):
    collection = _hair_guide_collection(scene)
    guide_number = 1 + sum(1 for obj in collection.objects if obj.get("gsmb_hair_guide"))
    name = f"GSMB_GUIDE_{_safe_id(source.name)}_{guide_number:02d}"
    data = bpy.data.curves.new(name, "CURVE")
    data.dimensions = "3D"
    data.bevel_depth = max(0.0005, scene.gsmb_hair_guide_display_size)
    data.bevel_resolution = 2
    spline = data.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for point, coordinate in zip(spline.points, points):
        point.co = (*coordinate, 1.0)
    guide = bpy.data.objects.new(name, data)
    collection.objects.link(guide)
    material = bpy.data.materials.get("GSMB_HairGuide") or bpy.data.materials.new("GSMB_HairGuide")
    material.diffuse_color = (0.05, 1.0, 0.18, 1.0)
    data.materials.append(material)
    guide.show_in_front = True
    guide["gsmb_hair_guide"] = True
    guide["gsmb_source_mesh"] = source.name
    guide["gsmb_root_vertex"] = root_index
    guide["gsmb_tip_vertex"] = tip_index
    return guide


class GSMB_OT_capture_hair_root(bpy.types.Operator):
    bl_idname = "gsmb.capture_hair_root"
    bl_label = "Capture Selected Root"
    bl_description = "In Edit Mode, store the center vertex of the selected hair-root region"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.active_object
        index = _selected_edit_vertex_index(obj)
        if index < 0:
            self.report({"ERROR"}, "Edit a hair mesh and select one or more root vertices")
            return {"CANCELLED"}
        context.scene.gsmb_hair_root_object = obj
        context.scene.gsmb_hair_root_vertex = index
        context.scene["gsmb_prod_status"] = f"Hair root captured: {obj.name} vertex {index}"
        self.report({"INFO"}, context.scene["gsmb_prod_status"])
        return {"FINISHED"}


class GSMB_OT_create_hair_guide(bpy.types.Operator):
    bl_idname = "gsmb.create_hair_guide"
    bl_label = "Create Guide to Selected Tip"
    bl_description = "Trace a smooth editable guide from the stored root to the selected hair tip"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        obj = context.active_object
        if obj is None or obj != scene.gsmb_hair_root_object or obj.mode != "EDIT":
            self.report({"ERROR"}, "Keep editing the same mesh used to capture the root")
            return {"CANCELLED"}
        tip_index = _selected_edit_vertex_index(obj)
        root_index = scene.gsmb_hair_root_vertex
        if tip_index < 0 or root_index < 0 or tip_index == root_index:
            self.report({"ERROR"}, "Select a different vertex or face at the hair tip")
            return {"CANCELLED"}
        path = _surface_path(obj, root_index, tip_index)
        if not path:
            self.report({"ERROR"}, "Root and tip are on disconnected mesh islands")
            return {"CANCELLED"}
        points = _resample_polyline(path, scene.gsmb_hair_guide_points)
        guide = _create_hair_guide(scene, obj, root_index, tip_index, points)
        scene["gsmb_prod_status"] = f"Created editable guide {guide.name} with {len(points)} points"
        self.report({"INFO"}, scene["gsmb_prod_status"])
        return {"FINISHED"}


class GSMB_OT_generate_production_equipment(bpy.types.Operator):
    bl_idname = "gsmb.generate_production_equipment"
    bl_label = "Build Dynamic Equipment Rig"
    bl_description = "Clone the selected humanoid rig and create smooth equipment-local secondary chains"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        source = _source_armature(context)
        meshes = _selected_meshes(context)
        if source is None:
            self.report({"ERROR"}, "Choose or select a source humanoid armature")
            return {"CANCELLED"}
        if not meshes:
            self.report({"ERROR"}, "Select at least one hair/skirt mesh together with the source armature")
            return {"CANCELLED"}
        if any(len(mesh.data.vertices) == 0 for mesh in meshes):
            self.report({"ERROR"}, "Selected equipment contains an empty mesh")
            return {"CANCELLED"}

        scene = context.scene
        equipment_type = scene.gsmb_prod_equipment_type
        asset_id = _safe_id(scene.gsmb_prod_asset_id or meshes[0].name)
        collection = _production_collection(scene)
        armature = _clone_armature(source, f"GSMB_EQ_{asset_id}_Rig", collection)
        armature["gsmb_asset_id"] = asset_id
        armature["gsmb_equipment_type"] = equipment_type
        armature["gsmb_skeleton_version"] = scene.gsmb_prod_skeleton_version

        explicit = {
            "head": scene.gsmb_prod_head_bone,
            "chest": scene.gsmb_prod_chest_bone,
            "hips": scene.gsmb_prod_hips_bone,
            "thigh_l": scene.gsmb_prod_thigh_l_bone,
            "thigh_r": scene.gsmb_prod_thigh_r_bone,
        }
        mapping = {role: _resolve_bone(armature, role, explicit[role]) for role in BONE_ALIASES}
        parent_role = "head" if equipment_type == "HAIR" else "hips"
        parent_bone = mapping[parent_role]
        if not parent_bone:
            bpy.data.objects.remove(armature, do_unlink=True)
            self.report({"ERROR"}, f"Could not resolve the required {parent_role} bone")
            return {"CANCELLED"}

        for obj in context.selected_objects:
            obj.select_set(False)
        armature.select_set(True)
        context.view_layer.objects.active = armature
        bpy.ops.object.mode_set(mode="EDIT")
        settings = _settings(scene)
        try:
            if equipment_type == "HAIR":
                if scene.gsmb_hair_build_mode == "GUIDES":
                    chains = _make_hair_chains_from_guides(
                        scene, armature, asset_id, parent_bone,
                        scene.gsmb_prod_bones_per_chain, settings,
                    )
                else:
                    chains = _make_hair_chains(
                        armature, meshes, asset_id, parent_bone,
                        scene.gsmb_prod_chain_count, scene.gsmb_prod_bones_per_chain, settings,
                    )
                center = None
            else:
                chains, center = _make_skirt_chains(
                    armature, meshes, asset_id, parent_bone,
                    scene.gsmb_prod_chain_count, scene.gsmb_prod_bones_per_chain, settings,
                )
        except Exception as exc:
            bpy.ops.object.mode_set(mode="OBJECT")
            bpy.data.objects.remove(armature, do_unlink=True)
            self.report({"ERROR"}, f"Secondary chain generation failed: {exc}")
            return {"CANCELLED"}
        bpy.ops.object.mode_set(mode="OBJECT")

        armature["gsmb_bone_mapping"] = json.dumps(mapping, ensure_ascii=False)
        armature["gsmb_chain_manifest"] = json.dumps(chains, ensure_ascii=False)
        for mesh in meshes:
            _clear_deform_groups(mesh, armature)
            _ensure_modifier(mesh, armature)
            _parent_mesh_to_armature(mesh, armature)
            mesh["gsmb_dynamic_equipment"] = True
            mesh["gsmb_equipment_rig"] = armature.name
            if equipment_type == "HAIR":
                if scene.gsmb_hair_build_mode == "GUIDES":
                    _weight_hair_from_guides(mesh, armature, chains, parent_bone)
                else:
                    _weight_hair(mesh, armature, chains, parent_bone)
            else:
                _weight_skirt(mesh, armature, chains, parent_bone, center)

        colliders = _create_standard_colliders(collection, armature, mapping, _armature_height(armature))
        try:
            canonical_mapping = _canonicalize_anchor_bones(
                armature, meshes, colliders, mapping
            )
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        armature["gsmb_canonical_mapping"] = json.dumps(
            canonical_mapping, ensure_ascii=False
        )
        armature["gsmb_collider_names"] = json.dumps([obj.name for obj in colliders], ensure_ascii=False)
        scene.gsmb_prod_equipment_armature = armature
        scene["gsmb_prod_status"] = (
            f"Built {armature.name}: {len(chains)} chains, "
            f"{sum(len(chain) for chain in chains)} bones, {len(colliders)} colliders"
        )
        self.report({"INFO"}, scene["gsmb_prod_status"])
        return {"FINISHED"}


def _equipment_armature(context):
    configured = context.scene.gsmb_prod_equipment_armature
    if configured and configured.type == "ARMATURE" and configured.get("gsmb_production_equipment"):
        return configured
    active = context.active_object
    if active and active.type == "ARMATURE" and active.get("gsmb_production_equipment"):
        return active
    return next(
        (obj for obj in context.selected_objects if obj.type == "ARMATURE" and obj.get("gsmb_production_equipment")),
        None,
    )


def _profile(armature):
    chain_names = json.loads(armature.get("gsmb_chain_manifest", "[]"))
    chains = []
    for names in chain_names:
        if not names:
            continue
        bone = armature.data.bones.get(names[0])
        chains.append({
            "root": names[0],
            "end": names[-1],
            "joints": names,
            "stiffness": float(bone.get("gsmb_stiffness", 0.45)),
            "drag": float(bone.get("gsmb_drag", 0.7)),
            "gravity": float(bone.get("gsmb_gravity", 0.35)),
            "gravity_direction": [0.0, -1.0, 0.0],
            "radius": float(bone.get("gsmb_radius", 0.035)),
            "rotation_axis": "all",
        })
    colliders = []
    for obj in bpy.data.objects:
        if obj.parent == armature and obj.get("gsmb_collider"):
            role = obj.get("gsmb_collider_role", "")
            colliders.append({
                "name": obj.name,
                "bone": GODOT_PROFILE_BONES.get(role, obj.get("gsmb_bone", "")),
                "source_bone": obj.get("gsmb_source_bone", obj.get("gsmb_bone", "")),
                "role": role,
                "shape": obj.get("gsmb_shape", "sphere"),
                "radius": float(obj.get("gsmb_radius", 0.05)),
                "height": float(obj.get("gsmb_height", 0.0)),
                "position_offset": list(obj.get("gsmb_position_offset", [0.0, 0.0, 0.0])),
            })
    return {
        "schema": "gsmb-secondary-motion-v1",
        "asset_id": armature.get("gsmb_asset_id", armature.name),
        "equipment_type": armature.get("gsmb_equipment_type", "UNKNOWN"),
        "skeleton_version": armature.get("gsmb_skeleton_version", "HERO_RIG_V1"),
        "source_armature": armature.get("gsmb_source_armature", ""),
        "equipment_armature": armature.name,
        "bone_mapping": json.loads(armature.get("gsmb_bone_mapping", "{}")),
        "godot_bone_mapping": GODOT_PROFILE_BONES,
        "retarget": {
            "profile": "SkeletonProfileHumanoid",
            "position": True,
            "rotation": True,
            "scale": False,
            "use_global_pose": False,
        },
        "chains": chains,
        "colliders": colliders,
    }


class GSMB_OT_export_godot_profile(bpy.types.Operator):
    bl_idname = "gsmb.export_godot_profile"
    bl_label = "Export Godot Profile"
    bl_description = "Export retarget, spring-chain and bone-bound collider metadata as JSON"
    bl_options = {"REGISTER"}

    def execute(self, context):
        armature = _equipment_armature(context)
        if armature is None:
            self.report({"ERROR"}, "Choose a generated production equipment armature")
            return {"CANCELLED"}
        path = bpy.path.abspath(context.scene.gsmb_prod_export_path)
        if not path.lower().endswith(".json"):
            path += ".json"
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(_profile(armature), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        context.scene["gsmb_prod_status"] = f"Godot profile exported: {path}"
        self.report({"INFO"}, context.scene["gsmb_prod_status"])
        return {"FINISHED"}


class GSMB_OT_export_dynamic_equipment_package(bpy.types.Operator):
    bl_idname = "gsmb.export_dynamic_equipment_package"
    bl_label = "Export GLB + Godot Profile"
    bl_description = "Export the generated equipment rig/meshes as GLB and its runtime profile as JSON"
    bl_options = {"REGISTER"}

    def execute(self, context):
        armature = _equipment_armature(context)
        if armature is None:
            self.report({"ERROR"}, "Choose a generated production equipment armature")
            return {"CANCELLED"}
        meshes = [
            obj for obj in bpy.data.objects
            if obj.type == "MESH" and obj.get("gsmb_equipment_rig") == armature.name
        ]
        if not meshes:
            self.report({"ERROR"}, "No equipment mesh references the generated rig")
            return {"CANCELLED"}
        directory = bpy.path.abspath(context.scene.gsmb_prod_package_dir)
        os.makedirs(directory, exist_ok=True)
        asset_id = _safe_id(armature.get("gsmb_asset_id", armature.name))
        glb_path = os.path.join(directory, f"{asset_id}.glb")
        profile_path = os.path.join(directory, f"{asset_id}.secondary_motion.json")

        previous_active = context.view_layer.objects.active
        previous_selected = list(context.selected_objects)
        try:
            for obj in context.selected_objects:
                obj.select_set(False)
            armature.select_set(True)
            for mesh in meshes:
                mesh.select_set(True)
            context.view_layer.objects.active = armature
            bpy.ops.export_scene.gltf(
                filepath=glb_path,
                export_format="GLB",
                use_selection=True,
                export_animations=False,
                export_skins=True,
                export_all_influences=False,
            )
            with open(profile_path, "w", encoding="utf-8") as handle:
                json.dump(_profile(armature), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        except Exception as exc:
            self.report({"ERROR"}, f"Dynamic equipment package export failed: {exc}")
            return {"CANCELLED"}
        finally:
            for obj in context.selected_objects:
                obj.select_set(False)
            for obj in previous_selected:
                if obj.name in bpy.data.objects:
                    obj.select_set(True)
            if previous_active and previous_active.name in bpy.data.objects:
                context.view_layer.objects.active = previous_active

        context.scene.gsmb_prod_export_path = profile_path
        context.scene["gsmb_prod_status"] = f"Package exported: {glb_path} + {profile_path}"
        self.report({"INFO"}, context.scene["gsmb_prod_status"])
        return {"FINISHED"}


class GSMB_OT_validate_production_equipment(bpy.types.Operator):
    bl_idname = "gsmb.validate_production_equipment"
    bl_label = "Validate Dynamic Equipment"
    bl_description = "Validate chains, skin modifier, named bones and bone-parented colliders"
    bl_options = {"REGISTER"}

    def execute(self, context):
        armature = _equipment_armature(context)
        if armature is None:
            self.report({"ERROR"}, "Choose a generated production equipment armature")
            return {"CANCELLED"}
        errors = []
        profile = _profile(armature)
        if not profile["chains"]:
            errors.append("no secondary chains")
        for chain in profile["chains"]:
            for name in chain["joints"]:
                if name not in armature.data.bones:
                    errors.append(f"missing bone {name}")
        meshes = [obj for obj in bpy.data.objects if obj.type == "MESH" and obj.get("gsmb_equipment_rig") == armature.name]
        if not meshes:
            errors.append("no equipment mesh references this rig")
        for mesh in meshes:
            if not any(mod.type == "ARMATURE" and mod.object == armature for mod in mesh.modifiers):
                errors.append(f"{mesh.name} has no matching Armature modifier")
            unweighted = sum(1 for vertex in mesh.data.vertices if not vertex.groups)
            if unweighted:
                errors.append(f"{mesh.name} has {unweighted} unweighted vertices")
            over_limit = sum(1 for vertex in mesh.data.vertices if len(vertex.groups) > 4)
            if over_limit:
                errors.append(f"{mesh.name} has {over_limit} vertices with more than 4 bone influences")
        for collider in profile["colliders"]:
            obj = bpy.data.objects.get(collider["name"])
            if obj is None or obj.parent != armature or obj.parent_type != "BONE":
                errors.append(f"collider {collider['name']} is not bone-parented")
        if any(abs(value - 1.0) > 1e-4 for value in armature.scale):
            errors.append("equipment armature scale is not applied")
        if errors:
            context.scene["gsmb_prod_status"] = "Validation failed: " + "; ".join(errors[:6])
            self.report({"ERROR"}, context.scene["gsmb_prod_status"])
            return {"CANCELLED"}
        context.scene["gsmb_prod_status"] = (
            f"Validation passed: {len(meshes)} mesh(es), {len(profile['chains'])} chains, "
            f"{len(profile['colliders'])} colliders"
        )
        self.report({"INFO"}, context.scene["gsmb_prod_status"])
        return {"FINISHED"}


class GSMB_PT_production(bpy.types.Panel):
    bl_label = "Production Dynamic Equipment"
    bl_idname = "GSMB_PT_production"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Secondary Motion"
    bl_order = 0

    def draw(self, context):
        scene = context.scene
        layout = self.layout
        layout.label(text="Godot runtime pipeline", icon="ARMATURE_DATA")
        layout.prop(scene, "gsmb_prod_armature")
        layout.prop(scene, "gsmb_prod_asset_id")
        layout.prop(scene, "gsmb_prod_skeleton_version")
        layout.prop(scene, "gsmb_prod_equipment_type", expand=True)
        if scene.gsmb_prod_equipment_type == "HAIR":
            guides = layout.box()
            guides.label(text="Hair guides (works before rigging)", icon="CURVE_DATA")
            guides.prop(scene, "gsmb_hair_build_mode", expand=True)
            if scene.gsmb_hair_build_mode == "GUIDES":
                guides.label(text="Edit mesh: select root, capture, then select tip")
                guides.prop(scene, "gsmb_hair_guide_points")
                guides.prop(scene, "gsmb_hair_guide_display_size")
                row = guides.row(align=True)
                row.operator("gsmb.capture_hair_root", icon="PINNED")
                row.operator("gsmb.create_hair_guide", icon="CURVE_PATH")
                if scene.gsmb_hair_root_object:
                    guides.label(
                        text=f"Root: {scene.gsmb_hair_root_object.name} / vertex {scene.gsmb_hair_root_vertex}"
                    )
        row = layout.row(align=True)
        row.prop(scene, "gsmb_prod_chain_count")
        row.prop(scene, "gsmb_prod_bones_per_chain")

        physics = layout.box()
        physics.label(text="SpringBone defaults")
        physics.prop(scene, "gsmb_prod_stiffness")
        physics.prop(scene, "gsmb_prod_drag")
        physics.prop(scene, "gsmb_prod_gravity")
        physics.prop(scene, "gsmb_prod_radius")

        mapping = layout.box()
        mapping.label(text="Optional bone-name overrides")
        mapping.prop(scene, "gsmb_prod_head_bone")
        mapping.prop(scene, "gsmb_prod_chest_bone")
        mapping.prop(scene, "gsmb_prod_hips_bone")
        mapping.prop(scene, "gsmb_prod_thigh_l_bone")
        mapping.prop(scene, "gsmb_prod_thigh_r_bone")

        build = layout.row()
        build.scale_y = 1.5
        build.operator("gsmb.generate_production_equipment", icon="BONE_DATA")
        layout.prop(scene, "gsmb_prod_equipment_armature")
        layout.operator("gsmb.validate_production_equipment", icon="CHECKMARK")
        layout.prop(scene, "gsmb_prod_export_path")
        layout.operator("gsmb.export_godot_profile", icon="EXPORT")
        package = layout.box()
        package.label(text="Production package")
        package.prop(scene, "gsmb_prod_package_dir")
        package.operator("gsmb.export_dynamic_equipment_package", icon="PACKAGE")
        layout.label(text=scene.get("gsmb_prod_status", "Select rig + equipment mesh to begin"))


classes = (
    GSMB_OT_capture_hair_root,
    GSMB_OT_create_hair_guide,
    GSMB_OT_generate_production_equipment,
    GSMB_OT_export_godot_profile,
    GSMB_OT_export_dynamic_equipment_package,
    GSMB_OT_validate_production_equipment,
    GSMB_PT_production,
)


def _armature_poll(_self, obj):
    return obj is None or obj.type == "ARMATURE"


def _mesh_poll(_self, obj):
    return obj is None or obj.type == "MESH"


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.gsmb_prod_armature = bpy.props.PointerProperty(
        name="Source Rig", type=bpy.types.Object, poll=_armature_poll,
    )
    bpy.types.Scene.gsmb_prod_equipment_armature = bpy.props.PointerProperty(
        name="Equipment Rig", type=bpy.types.Object, poll=_armature_poll,
    )
    bpy.types.Scene.gsmb_prod_asset_id = bpy.props.StringProperty(name="Asset ID", default="")
    bpy.types.Scene.gsmb_prod_skeleton_version = bpy.props.StringProperty(name="Skeleton Version", default="HERO_RIG_V1")
    bpy.types.Scene.gsmb_prod_equipment_type = bpy.props.EnumProperty(
        name="Type",
        items=(("HAIR", "Hair", "Long hair or braid chains"), ("SKIRT", "Skirt/Cape", "Radial skirt, coat or cape chains")),
        default="SKIRT",
    )
    bpy.types.Scene.gsmb_hair_build_mode = bpy.props.EnumProperty(
        name="Hair Build",
        items=(
            ("AUTO", "Automatic", "Distribute simple vertical chains from the hair bounds"),
            ("GUIDES", "From Guides", "Build and weight chains from manually captured surface guides"),
        ),
        default="AUTO",
    )
    bpy.types.Scene.gsmb_hair_root_object = bpy.props.PointerProperty(
        name="Guide Source", type=bpy.types.Object, poll=_mesh_poll,
    )
    bpy.types.Scene.gsmb_hair_root_vertex = bpy.props.IntProperty(
        name="Root Vertex", default=-1, min=-1,
    )
    bpy.types.Scene.gsmb_hair_guide_points = bpy.props.IntProperty(
        name="Guide Points", default=7, min=3, max=24,
    )
    bpy.types.Scene.gsmb_hair_guide_display_size = bpy.props.FloatProperty(
        name="Guide Thickness", default=0.003, min=0.0001, max=0.05, subtype="DISTANCE",
    )
    bpy.types.Scene.gsmb_prod_chain_count = bpy.props.IntProperty(name="Chains", default=8, min=1, max=32)
    bpy.types.Scene.gsmb_prod_bones_per_chain = bpy.props.IntProperty(name="Bones", default=3, min=1, max=8)
    bpy.types.Scene.gsmb_prod_stiffness = bpy.props.FloatProperty(name="Stiffness", default=0.45, min=0.0, max=4.0)
    bpy.types.Scene.gsmb_prod_drag = bpy.props.FloatProperty(name="Drag", default=0.7, min=0.0, max=1.0)
    bpy.types.Scene.gsmb_prod_gravity = bpy.props.FloatProperty(name="Gravity", default=0.35, min=0.0, max=4.0)
    bpy.types.Scene.gsmb_prod_radius = bpy.props.FloatProperty(name="Joint Radius", default=0.035, min=0.001, max=0.5, subtype="DISTANCE")
    bpy.types.Scene.gsmb_prod_head_bone = bpy.props.StringProperty(name="Head", default="")
    bpy.types.Scene.gsmb_prod_chest_bone = bpy.props.StringProperty(name="Chest", default="")
    bpy.types.Scene.gsmb_prod_hips_bone = bpy.props.StringProperty(name="Hips", default="")
    bpy.types.Scene.gsmb_prod_thigh_l_bone = bpy.props.StringProperty(name="Left Thigh", default="")
    bpy.types.Scene.gsmb_prod_thigh_r_bone = bpy.props.StringProperty(name="Right Thigh", default="")
    bpy.types.Scene.gsmb_prod_export_path = bpy.props.StringProperty(
        name="Godot Profile", default="//secondary_motion.json", subtype="FILE_PATH",
    )
    bpy.types.Scene.gsmb_prod_package_dir = bpy.props.StringProperty(
        name="Package Directory", default="//exports/", subtype="DIR_PATH",
    )


def unregister():
    names = (
        "gsmb_prod_armature", "gsmb_prod_equipment_armature", "gsmb_prod_asset_id",
        "gsmb_prod_skeleton_version", "gsmb_prod_equipment_type", "gsmb_prod_chain_count",
        "gsmb_hair_build_mode", "gsmb_hair_root_object", "gsmb_hair_root_vertex",
        "gsmb_hair_guide_points", "gsmb_hair_guide_display_size",
        "gsmb_prod_bones_per_chain", "gsmb_prod_stiffness", "gsmb_prod_drag",
        "gsmb_prod_gravity", "gsmb_prod_radius", "gsmb_prod_head_bone",
        "gsmb_prod_chest_bone", "gsmb_prod_hips_bone", "gsmb_prod_thigh_l_bone",
        "gsmb_prod_thigh_r_bone", "gsmb_prod_export_path", "gsmb_prod_package_dir",
    )
    for name in names:
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
