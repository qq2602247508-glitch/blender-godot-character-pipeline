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
_KIMODO_SECONDARY_BUSY = False


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


def _json_property(obj, name, fallback):
    try:
        return json.loads(obj.get(name, ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _equipment_for_source(source):
    return [
        obj for obj in bpy.data.objects
        if obj.type == "ARMATURE"
        and obj.get("gsmb_production_equipment")
        and obj.get("gsmb_source_armature") == source.name
        and _json_property(obj, "gsmb_chain_manifest", [])
    ]


def _equipment_bone_map(source, equipment):
    """Map equipment-local humanoid anchors back to the source rig."""
    result = {
        bone.name: bone.name
        for bone in equipment.data.bones
        if bone.name in source.data.bones and not bone.get("gsmb_secondary")
    }
    source_roles = _json_property(equipment, "gsmb_bone_mapping", {})
    target_roles = _json_property(equipment, "gsmb_canonical_mapping", {})
    for role, target_name in target_roles.items():
        source_name = source_roles.get(role)
        if target_name in equipment.data.bones and source_name in source.data.bones:
            result[target_name] = source_name
    return result


def _new_secondary_action(equipment, source_action):
    equipment.animation_data_create()
    old = equipment.animation_data.action
    if old and old.get("gsmb_generated_secondary"):
        bpy.data.actions.remove(old)
    equipment_type = equipment.get("gsmb_equipment_type", "EQUIPMENT").title()
    name = f"{source_action.name}__{equipment_type}_Secondary"
    existing = bpy.data.actions.get(name)
    if existing:
        bpy.data.actions.remove(existing)
    action = bpy.data.actions.new(name)
    action.use_fake_user = True
    action["gsmb_generated_secondary"] = True
    action["gsmb_source_action"] = source_action.name
    equipment.animation_data.action = action
    return action


def bake_secondary_actions(context, source, *, hair_strength=1.0, skirt_strength=1.0):
    """Bake source anchors and a lightweight loop-safe secondary preview.

    Godot still owns runtime SpringBone simulation.  This bake exists so a
    freshly retargeted Kimodo clip cannot leave Blender equipment frozen.
    """
    if source is None or source.type != "ARMATURE":
        raise ValueError("Source must be an armature")
    source_action = source.animation_data.action if source.animation_data else None
    if source_action is None:
        raise ValueError("Source armature has no active Action")
    equipment_items = _equipment_for_source(source)
    if not equipment_items:
        raise ValueError(f"No dynamic equipment references {source.name}")

    scene = context.scene
    start = int(math.floor(source_action.frame_range[0]))
    end = int(math.ceil(source_action.frame_range[1]))
    frames = list(range(start, end + 1))
    if len(frames) < 2:
        raise ValueError("Source Action needs at least two frames")

    source_samples = {}
    for frame in frames:
        scene.frame_set(frame)
        source_samples[frame] = {
            bone.name: bone.matrix.copy() for bone in source.pose.bones
        }

    first = source_samples[frames[0]]
    last = source_samples[frames[-1]]
    looped = True
    for name in first.keys() & last.keys():
        if (first[name].translation - last[name].translation).length > 1e-4:
            looped = False
            break
        if first[name].to_quaternion().rotation_difference(last[name].to_quaternion()).angle > math.radians(0.1):
            looped = False
            break

    results = []
    period = max(1, len(frames) - 1 if looped else len(frames))
    for equipment in equipment_items:
        action = _new_secondary_action(equipment, source_action)
        bone_map = _equipment_bone_map(source, equipment)
        chains = _json_property(equipment, "gsmb_chain_manifest", [])
        source_roles = _json_property(equipment, "gsmb_bone_mapping", {})
        equipment_type = equipment.get("gsmb_equipment_type", "SKIRT")

        def delayed_delta(source_name, index, delay):
            if source_name not in source.data.bones:
                return Vector((0.0, 0.0, 0.0))
            delayed_index = (index - delay) % period if looped else max(0, index - delay)
            current = source_samples[frames[index]][source_name].to_quaternion()
            delayed = source_samples[frames[delayed_index]][source_name].to_quaternion()
            return Vector(current.rotation_difference(delayed).to_euler("XYZ"))

        for index, frame in enumerate(frames):
            scene.frame_set(frame)
            matrices = source_samples[frame]
            # PoseBone.matrix assignment needs a dependency update before its
            # local channels are read. Without it, frame 1 can retain the old
            # static equipment pose even though later frames look correct.
            for bone in equipment.data.bones:
                source_name = bone_map.get(bone.name)
                if not source_name or source_name not in matrices:
                    continue
                pose_bone = equipment.pose.bones[bone.name]
                pose_bone.rotation_mode = "QUATERNION"
                pose_bone.matrix = matrices[source_name]
                context.view_layer.update()
                pose_bone.keyframe_insert("location", frame=frame, group="HUMANOID_ANCHORS")
                pose_bone.keyframe_insert("rotation_quaternion", frame=frame, group="HUMANOID_ANCHORS")
                pose_bone.keyframe_insert("scale", frame=frame, group="HUMANOID_ANCHORS")

            phase = 2.0 * math.pi * index / max(1, len(frames) - 1)
            if equipment_type == "HAIR":
                driver = source_roles.get("head", "Head")
                strength = hair_strength
                max_x, max_z = math.radians(8.0), math.radians(6.0)
            else:
                driver = source_roles.get("hips", "Hips")
                strength = skirt_strength
                max_x, max_z = math.radians(6.0), math.radians(5.0)

            for chain_index, chain in enumerate(chains):
                side = -1.0 if chain_index % 2 else 1.0
                for level, bone_name in enumerate(chain):
                    pose_bone = equipment.pose.bones.get(bone_name)
                    if pose_bone is None:
                        continue
                    delta = delayed_delta(driver, index, 2 + level * (2 if equipment_type == "HAIR" else 1))
                    gain = (0.38 + 0.25 * level) if equipment_type == "HAIR" else (0.30 + 0.24 * level)
                    if equipment_type == "HAIR":
                        idle_x = math.radians(0.35 + 0.22 * level) * math.sin(phase + chain_index * 0.47)
                        idle_z = math.radians(0.25 + 0.18 * level) * math.sin(phase * 2.0 + chain_index * 0.61)
                    else:
                        idle_x = math.radians(0.62 + 0.66 * level) * math.sin(phase + chain_index * 0.72)
                        idle_z = math.radians(0.40 + 0.44 * level) * math.sin(phase * 2.0 + chain_index * 0.53)
                    x = max(-max_x, min(max_x, (-delta.x * gain + idle_x) * strength))
                    z = max(-max_z, min(max_z, (-delta.z * gain + idle_z * side) * strength))
                    pose_bone.rotation_mode = "XYZ"
                    pose_bone.rotation_euler = (x, 0.0, z)
                    pose_bone.keyframe_insert("rotation_euler", frame=frame, group="DYNAMIC_SECONDARY")

        if looped:
            for curve in action.fcurves:
                if not any(modifier.type == "CYCLES" for modifier in curve.modifiers):
                    curve.modifiers.new("CYCLES")

        max_dynamic_angle = 0.0
        max_anchor_error = 0.0
        critical_roles = ("head", "chest", "hips", "thigh_l", "thigh_r")
        target_roles = _json_property(equipment, "gsmb_canonical_mapping", {})
        for frame in frames:
            scene.frame_set(frame)
            for chain in chains:
                for bone_name in chain:
                    pose_bone = equipment.pose.bones.get(bone_name)
                    if pose_bone:
                        max_dynamic_angle = max(
                            max_dynamic_angle,
                            abs(pose_bone.rotation_euler.x),
                            abs(pose_bone.rotation_euler.z),
                        )
            for role in critical_roles:
                target_name = target_roles.get(role)
                source_name = source_roles.get(role)
                if target_name not in equipment.pose.bones or source_name not in source.pose.bones:
                    continue
                target_matrix = equipment.matrix_world @ equipment.pose.bones[target_name].matrix
                source_matrix = source.matrix_world @ source.pose.bones[source_name].matrix
                max_anchor_error = max(
                    max_anchor_error,
                    (target_matrix.translation - source_matrix.translation).length,
                )
        if max_dynamic_angle < math.radians(0.05):
            raise RuntimeError(f"{equipment.name}: generated secondary bones are static")
        if max_anchor_error > 1e-3:
            raise RuntimeError(f"{equipment.name}: anchor drift is {max_anchor_error:.6f} m")
        action["gsmb_anchor_max_error_m"] = max_anchor_error
        action["gsmb_dynamic_max_angle_deg"] = math.degrees(max_dynamic_angle)
        results.append({
            "equipment": equipment.name,
            "action": action.name,
            "dynamic_bones": sum(len(chain) for chain in chains),
            "max_angle_deg": math.degrees(max_dynamic_angle),
            "anchor_error_m": max_anchor_error,
        })

    scene.frame_set(start)
    return results


def kimodo_secondary_timer():
    """Observe Rokoko/Kimodo completion without coupling either addon."""
    global _KIMODO_SECONDARY_BUSY
    if _KIMODO_SECONDARY_BUSY:
        return 1.0
    for scene in bpy.data.scenes:
        settings = getattr(scene, "rro_bridge", None)
        if settings is None or not getattr(scene, "gsmb_kimodo_auto_secondary", True):
            continue
        request_id = settings.last_completed_request_id
        if not request_id or scene.get("gsmb_last_kimodo_secondary_request") == request_id:
            continue
        source = settings.target_object
        if source is None or source.type != "ARMATURE" or not _equipment_for_source(source):
            scene["gsmb_last_kimodo_secondary_request"] = request_id
            continue
        # Mark before work to prevent repeated retries if a malformed asset fails.
        scene["gsmb_last_kimodo_secondary_request"] = request_id
        try:
            _KIMODO_SECONDARY_BUSY = True
            results = bake_secondary_actions(
                bpy.context,
                source,
                hair_strength=scene.gsmb_kimodo_hair_strength,
                skirt_strength=scene.gsmb_kimodo_skirt_strength,
            )
            scene["gsmb_prod_status"] = (
                f"Kimodo secondary baked: {len(results)} equipment rig(s), request {request_id}"
            )
        except Exception as exc:
            scene["gsmb_prod_status"] = f"Kimodo secondary failed: {exc}"
            print("GSMB Kimodo secondary:", exc)
        finally:
            _KIMODO_SECONDARY_BUSY = False
    return 1.0


def _clone_armature(source, name, collection):
    data = source.data.copy()
    data.name = f"{name}_Data"
    armature = bpy.data.objects.new(name, data)
    collection.objects.link(armature)
    armature.matrix_world = source.matrix_world.copy()
    armature.show_in_front = True
    # FBX rigs can carry their authored bind display pose in PoseBone
    # matrix_basis instead of identity.  Copy it to the equipment-local rig;
    # otherwise a Hunyuan/Mixamo source can snap back to its raw rest pose as
    # soon as the dynamic equipment is generated.
    bpy.context.view_layer.update()
    for source_bone in source.pose.bones:
        target_bone = armature.pose.bones.get(source_bone.name)
        if target_bone is not None:
            target_bone.matrix_basis = source_bone.matrix_basis.copy()
    if any(abs(value - 1.0) > 1e-6 for value in armature.scale):
        for selected in list(bpy.context.selected_objects):
            selected.select_set(False)
        armature.select_set(True)
        bpy.context.view_layer.objects.active = armature
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
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
    filtered.sort(key=lambda item: item[1], reverse=True)
    filtered = filtered[:4]
    total = sum(weight for _, weight in filtered)
    if total <= 1e-8:
        return
    for name, weight in filtered:
        _group(mesh, name).add([vertex_index], weight / total, "REPLACE")


def _capture_deform_weights(mesh, armature):
    """Preserve the imported body skin so fixed roots do not snap on FBX rigs."""
    deform_names = {bone.name for bone in armature.data.bones if bone.use_deform}
    group_names = {group.index: group.name for group in mesh.vertex_groups}
    captured = []
    for vertex in mesh.data.vertices:
        weights = [
            (group_names[element.group], element.weight)
            for element in vertex.groups
            if group_names.get(element.group) in deform_names and element.weight > 1e-6
        ]
        captured.append(weights)
    return captured


def _fixed_root_weights(base_weights, vertex_index, fallback_bone, factor):
    if factor <= 1e-6:
        return []
    authored = base_weights[vertex_index] if base_weights else []
    if authored:
        return [(name, weight * factor) for name, weight in authored]
    return [(fallback_bone, factor)]


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


def _weight_hair(mesh, armature, chains, parent_bone, base_weights=None):
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
        weights = _fixed_root_weights(base_weights, vertex.index, parent_bone, root_weight)
        for chain_index, chain_weight in chain_pairs:
            if root_weight > 0.0:
                weights.append((chains[chain_index][0], secondary_scale * chain_weight))
            else:
                for bone_index, bone_weight in _linear_pair(down, len(chains[chain_index])):
                    weights.append((chains[chain_index][bone_index], secondary_scale * chain_weight * bone_weight))
        _add_normalized_weights(mesh, vertex.index, weights)


def _base_object_name(name):
    """Return a stable source name for Blender copies used in preview rigs."""
    value = re.sub(r"\.\d{3}$", "", name or "")
    # Copies can pass through several pipeline stages, for example
    # GAME_RIGGED_part_11.  Strip every recognized wrapper until the stable
    # authored source name remains so guides continue to belong to their mesh.
    prefixes = (
        "GSMB_TEST_", "GSMB_PREVIEW_", "GSMB_COPY_",
        "GAME_", "RIGGED_", "HUNYUAN_OPT_",
    )
    changed = True
    while changed:
        changed = False
        for prefix in prefixes:
            if value.startswith(prefix):
                value = value[len(prefix):]
                changed = True
                break
    return value


def _guide_matches_mesh(mesh, source_name):
    if not source_name:
        return False
    explicit = (
        mesh.get("gsmb_source_mesh")
        or mesh.get("gsmb_original_mesh")
        or mesh.get("gsmb_source_part")
    )
    candidates = {
        _base_object_name(mesh.name),
        _base_object_name(mesh.data.name),
        _base_object_name(explicit),
    }
    return _base_object_name(source_name) in candidates


def _chain_points(armature, chain):
    bones = [armature.data.bones[name] for name in chain]
    return [bone.head_local.copy() for bone in bones] + [bones[-1].tail_local.copy()]


def _blended_deform_matrix(armature, weights):
    valid = [
        (name, weight) for name, weight in weights
        if weight > 1e-6
        and armature.data.bones.get(name) is not None
        and armature.pose.bones.get(name) is not None
    ]
    total = sum(weight for _, weight in valid)
    if total <= 1e-8:
        return None
    result = Matrix(((0.0, 0.0, 0.0, 0.0),) * 4)
    for name, weight in valid:
        bone = armature.data.bones[name]
        deform = armature.pose.bones[name].matrix @ bone.matrix_local.inverted_safe()
        result += deform * (weight / total)
    return result


def _match_secondary_bind_pose(
    armature, chains, meshes=None, base_weights=None, chain_sources=None
):
    """Make new chains neutral under a non-identity imported FBX pose.

    Hunyuan/Mixamo FBX files can store the authored bind display in pose-bone
    bases.  Bones appended later in Edit Mode start with identity pose bases,
    which otherwise moves the equipment before any spring simulation runs.
    """
    mesh_points = {
        mesh.name: _mesh_points_in_armature(mesh, armature)
        for mesh in (meshes or [])
    }
    for chain_index, chain in enumerate(chains):
        source_name = chain_sources[chain_index] if chain_sources else ""
        source_mesh = next(
            (mesh for mesh in (meshes or []) if _guide_matches_mesh(mesh, source_name)),
            None,
        )
        for bone_name in chain:
            bone = armature.data.bones[bone_name]
            parent = bone.parent
            if parent is None:
                continue
            deform = None
            candidates = [source_mesh] if source_mesh is not None else list(meshes or [])
            nearest = None
            for mesh in candidates:
                points = mesh_points[mesh.name]
                vertex_index = min(
                    range(len(points)),
                    key=lambda index: (points[index] - bone.head_local).length_squared,
                )
                distance = (points[vertex_index] - bone.head_local).length_squared
                if nearest is None or distance < nearest[0]:
                    nearest = (distance, mesh, vertex_index)
            if nearest is not None and base_weights:
                _, mesh, vertex_index = nearest
                deform = _blended_deform_matrix(
                    armature, base_weights[mesh.name][vertex_index]
                )
            if deform is None:
                deform = (
                    armature.pose.bones[parent.name].matrix
                    @ parent.matrix_local.inverted_safe()
                )
            armature.pose.bones[bone_name].matrix = deform @ bone.matrix_local
            bpy.context.view_layer.update()


def _point_to_polyline(point, polyline):
    best = None
    segment_count = len(polyline) - 1
    for segment_index in range(segment_count):
        first, second = polyline[segment_index], polyline[segment_index + 1]
        direction = second - first
        length_squared = max(1e-12, direction.length_squared)
        factor = max(0.0, min(1.0, (point - first).dot(direction) / length_squared))
        projected = first + direction * factor
        candidate = ((point - projected).length_squared, (segment_index + factor) / segment_count)
        if best is None or candidate[0] < best[0]:
            best = candidate
    return best


def _surface_chain_weights(mesh, points, chain_points):
    """Assign each connected hair card to one guide without hidden rig seams.

    AI hair meshes often fuse visually separate locks into one topological shell.
    Splitting such a shell between chains creates stretched triangles at the
    invisible boundary. Multiple chains are therefore automatic only across real
    disconnected components; continuous shells require an explicit region mask.
    """
    vertex_count = len(points)
    adjacency = [[] for _ in range(vertex_count)]
    for edge in mesh.data.edges:
        first, second = edge.vertices
        distance = max(1e-8, (points[first] - points[second]).length)
        adjacency[first].append((second, distance))
        adjacency[second].append((first, distance))

    component_ids = [-1] * vertex_count
    components = []
    for start in range(vertex_count):
        if component_ids[start] >= 0:
            continue
        component_index = len(components)
        component = []
        stack = [start]
        component_ids[start] = component_index
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor, _ in adjacency[current]:
                if component_ids[neighbor] < 0:
                    component_ids[neighbor] = component_index
                    stack.append(neighbor)
        components.append(component)

    result = [None] * vertex_count
    progress = [0.0] * vertex_count
    for component in components:
        # A bounded sample is enough to choose the guide while keeping 100k+
        # vertex AI meshes interactive.
        stride = max(1, len(component) // 2048)
        sample = component[::stride]
        scores = []
        for chain_index, polyline in enumerate(chain_points):
            score = sum(_point_to_polyline(points[index], polyline)[0] for index in sample)
            scores.append((score / len(sample), chain_index))
        owner = min(scores)[1]
        root = min(component, key=lambda index: (points[index] - chain_points[owner][0]).length_squared)
        tip = min(component, key=lambda index: (points[index] - chain_points[owner][-1]).length_squared)
        distances = {root: 0.0}
        queue = [(0.0, root)]
        while queue:
            distance, vertex_index = heapq.heappop(queue)
            if distance > distances.get(vertex_index, float("inf")) + 1e-10:
                continue
            for neighbor, edge_length in adjacency[vertex_index]:
                if component_ids[neighbor] != component_ids[vertex_index]:
                    continue
                candidate = distance + edge_length
                if candidate + 1e-10 < distances.get(neighbor, float("inf")):
                    distances[neighbor] = candidate
                    heapq.heappush(queue, (candidate, neighbor))
        span = distances.get(tip, 0.0)
        if span <= 1e-6:
            tip = min(component, key=lambda index: points[index].z)
            span = max(1e-6, distances.get(tip, 0.0))
        for vertex_index in component:
            result[vertex_index] = ((owner, 1.0),)
            progress[vertex_index] = max(0.0, min(1.0, distances[vertex_index] / span))
    return result, progress


def _weight_hair_from_guides(
    mesh, armature, chains, parent_bone, chain_sources=None, base_weights=None
):
    if chain_sources is None:
        eligible = list(range(len(chains)))
    else:
        eligible = [
            index for index, source_name in enumerate(chain_sources)
            if _guide_matches_mesh(mesh, source_name)
        ]
    if not eligible:
        raise ValueError(f"No hair guide belongs to selected mesh {mesh.name}")

    eligible_chains = [chains[index] for index in eligible]
    chain_points = []
    for chain in eligible_chains:
        chain_points.append(_chain_points(armature, chain))
    mesh_points = _mesh_points_in_armature(mesh, armature)
    minimum, maximum = _bounds(mesh_points)
    height = max(1e-6, maximum.z - minimum.z)
    surface_weights, surface_progress = _surface_chain_weights(mesh, mesh_points, chain_points)
    for vertex, point, chain_weights, progress in zip(
        mesh.data.vertices, mesh_points, surface_weights, surface_progress
    ):
        progress_by_chain = [
            (chain_index, chain_weight, progress)
            for chain_index, chain_weight in chain_weights
            if chain_weight > 1e-6
        ]
        vertical_down = max(0.0, min(1.0, (maximum.z - point.z) / height))
        scalp_pin = (
            max(0.0, 1.0 - vertical_down / 0.28)
            if len(eligible_chains) > 1 else 0.0
        )
        guide_root_pin = max(0.0, 1.0 - progress / 0.35)
        root_weight = max(scalp_pin, guide_root_pin)
        weights = _fixed_root_weights(base_weights, vertex.index, parent_bone, root_weight)
        for chain_index, chain_weight, chain_progress in progress_by_chain:
            if root_weight > 0.0:
                weights.append((
                    eligible_chains[chain_index][0],
                    (1.0 - root_weight) * chain_weight,
                ))
            else:
                for bone_index, bone_weight in _linear_pair(
                    chain_progress, len(eligible_chains[chain_index])
                ):
                    weights.append((
                        eligible_chains[chain_index][bone_index],
                        chain_weight * bone_weight,
                    ))
        _add_normalized_weights(mesh, vertex.index, weights)


def _weight_skirt(mesh, armature, chains, parent_bone, center, base_weights=None):
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
        weights = _fixed_root_weights(base_weights, vertex.index, parent_bone, root_weight)
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


def _make_hair_chains_from_guides(
    scene, armature, meshes, asset_id, parent_bone, bones_per_chain, settings
):
    collection = bpy.data.collections.get(HAIR_GUIDE_COLLECTION)
    guides = [] if collection is None else sorted(
        (obj for obj in collection.objects if obj.type == "CURVE" and obj.get("gsmb_hair_guide")),
        key=lambda obj: obj.name,
    )
    if not guides:
        raise ValueError("No semi-auto hair guides found; capture a root and tip first")
    to_armature = armature.matrix_world.inverted()
    chains = []
    chain_sources = []
    for guide_index, guide in enumerate(guides, 1):
        if not guide.data.splines or not guide.data.splines[0].points:
            continue
        world_points = [guide.matrix_world @ Vector(point.co[:3]) for point in guide.data.splines[0].points]
        source_name = guide.get("gsmb_source_mesh", "")
        target_mesh = next(
            (mesh for mesh in meshes if _guide_matches_mesh(mesh, source_name)), None
        )
        source_mesh = bpy.data.objects.get(source_name)
        if target_mesh is not None and source_mesh is not None and target_mesh != source_mesh:
            source_to_target = target_mesh.matrix_world @ source_mesh.matrix_world.inverted_safe()
            world_points = [source_to_target @ point for point in world_points]
        local_points = [to_armature @ point for point in _resample_polyline(world_points, bones_per_chain + 1)]
        prefix = f"GSMB_{asset_id}_Hair_{guide_index:02d}"
        chains.append(_add_chain(armature, prefix, local_points, parent_bone, settings))
        chain_sources.append(source_name)
    if not chains:
        raise ValueError("Hair guide collection contains no usable poly guides")
    return chains, chain_sources


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

        base_weights = {
            mesh.name: _capture_deform_weights(mesh, armature)
            for mesh in meshes
        }

        for obj in context.selected_objects:
            obj.select_set(False)
        armature.select_set(True)
        context.view_layer.objects.active = armature
        bpy.ops.object.mode_set(mode="EDIT")
        settings = _settings(scene)
        try:
            if equipment_type == "HAIR":
                if scene.gsmb_hair_build_mode == "GUIDES":
                    chains, chain_sources = _make_hair_chains_from_guides(
                        scene, armature, meshes, asset_id, parent_bone,
                        scene.gsmb_prod_bones_per_chain, settings,
                    )
                else:
                    chains = _make_hair_chains(
                        armature, meshes, asset_id, parent_bone,
                        scene.gsmb_prod_chain_count, scene.gsmb_prod_bones_per_chain, settings,
                    )
                    chain_sources = None
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
        _match_secondary_bind_pose(
            armature, chains, meshes, base_weights, chain_sources
            if equipment_type == "HAIR" else None,
        )

        if equipment_type == "HAIR" and chain_sources is not None:
            unmatched = [
                mesh.name for mesh in meshes
                if not any(_guide_matches_mesh(mesh, source) for source in chain_sources)
            ]
            if unmatched:
                bpy.data.objects.remove(armature, do_unlink=True)
                self.report({"ERROR"}, "No owned hair guide for: " + ", ".join(unmatched))
                return {"CANCELLED"}

        armature["gsmb_bone_mapping"] = json.dumps(mapping, ensure_ascii=False)
        armature["gsmb_chain_manifest"] = json.dumps(chains, ensure_ascii=False)
        if equipment_type == "HAIR" and chain_sources is not None:
            armature["gsmb_guide_sources"] = json.dumps(chain_sources, ensure_ascii=False)
        for mesh in meshes:
            _clear_deform_groups(mesh, armature)
            _ensure_modifier(mesh, armature)
            _parent_mesh_to_armature(mesh, armature)
            mesh["gsmb_dynamic_equipment"] = True
            mesh["gsmb_equipment_rig"] = armature.name
            if equipment_type == "HAIR":
                if scene.gsmb_hair_build_mode == "GUIDES":
                    _weight_hair_from_guides(
                        mesh, armature, chains, parent_bone, chain_sources,
                        base_weights[mesh.name],
                    )
                    mesh["gsmb_source_mesh"] = next(
                        source_name for source_name in chain_sources
                        if _guide_matches_mesh(mesh, source_name)
                    )
                else:
                    _weight_hair(
                        mesh, armature, chains, parent_bone, base_weights[mesh.name]
                    )
            else:
                _weight_skirt(
                    mesh, armature, chains, parent_bone, center,
                    base_weights[mesh.name],
                )

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
        previous_selected = [
            obj for obj in context.view_layer.objects if obj.select_get()
        ]
        allowed = {armature, *meshes}
        render_states = {obj: obj.hide_render for obj in bpy.data.objects}
        try:
            # Hidden objects can remain selected but are omitted from
            # context.selected_objects.  Deselect the complete view layer so a
            # hidden Hunyuan/reference mesh cannot leak into an equipment GLB.
            for obj in context.view_layer.objects:
                obj.select_set(False)
            for obj in bpy.data.objects:
                obj.hide_render = obj not in allowed
            armature.select_set(True)
            for mesh in meshes:
                mesh.select_set(True)
            context.view_layer.objects.active = armature
            bpy.ops.export_scene.gltf(
                filepath=glb_path,
                export_format="GLB",
                use_selection=True,
                use_active_scene=True,
                use_renderable=True,
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
            for obj, hidden in render_states.items():
                if obj.name in bpy.data.objects:
                    obj.hide_render = hidden
            for obj in context.view_layer.objects:
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


class GSMB_OT_bake_action_secondary(bpy.types.Operator):
    bl_idname = "gsmb.bake_action_secondary"
    bl_label = "为当前动作烘焙头发/裙摆"
    bl_description = "同步主骨架动作到独立装备锚点，并为头发和裙摆生成循环安全的次级预览 Action"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        source = _source_armature(context)
        if source is None:
            self.report({"ERROR"}, "请选择带动画的主角色骨架")
            return {"CANCELLED"}
        try:
            results = bake_secondary_actions(
                context,
                source,
                hair_strength=context.scene.gsmb_kimodo_hair_strength,
                skirt_strength=context.scene.gsmb_kimodo_skirt_strength,
            )
        except Exception as exc:
            context.scene["gsmb_prod_status"] = f"次级动画烘焙失败：{exc}"
            self.report({"ERROR"}, context.scene["gsmb_prod_status"])
            return {"CANCELLED"}
        context.scene["gsmb_prod_status"] = (
            f"已烘焙 {len(results)} 套装备次级动画；"
            + "，".join(f"{item['equipment']} {item['max_angle_deg']:.1f}°" for item in results)
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
        kimodo = layout.box()
        kimodo.label(text="Kimodo / 动作预览", icon="ACTION")
        kimodo.prop(scene, "gsmb_kimodo_auto_secondary")
        row = kimodo.row(align=True)
        row.prop(scene, "gsmb_kimodo_hair_strength")
        row.prop(scene, "gsmb_kimodo_skirt_strength")
        kimodo.operator("gsmb.bake_action_secondary", icon="REC")
        layout.label(text=scene.get("gsmb_prod_status", "Select rig + equipment mesh to begin"))


classes = (
    GSMB_OT_capture_hair_root,
    GSMB_OT_create_hair_guide,
    GSMB_OT_generate_production_equipment,
    GSMB_OT_export_godot_profile,
    GSMB_OT_export_dynamic_equipment_package,
    GSMB_OT_validate_production_equipment,
    GSMB_OT_bake_action_secondary,
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
    bpy.types.Scene.gsmb_kimodo_auto_secondary = bpy.props.BoolProperty(
        name="Kimodo 后自动生成头发/裙摆预览",
        description="Kimodo 完成主骨架绑定后，自动同步装备锚点并烘焙次级 Action",
        default=True,
    )
    bpy.types.Scene.gsmb_kimodo_hair_strength = bpy.props.FloatProperty(
        name="头发", default=1.0, min=0.0, max=3.0,
    )
    bpy.types.Scene.gsmb_kimodo_skirt_strength = bpy.props.FloatProperty(
        name="裙摆", default=1.0, min=0.0, max=3.0,
    )
    if not bpy.app.timers.is_registered(kimodo_secondary_timer):
        bpy.app.timers.register(kimodo_secondary_timer, first_interval=1.0, persistent=True)


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
        "gsmb_kimodo_auto_secondary", "gsmb_kimodo_hair_strength", "gsmb_kimodo_skirt_strength",
    )
    if bpy.app.timers.is_registered(kimodo_secondary_timer):
        bpy.app.timers.unregister(kimodo_secondary_timer)
    for name in names:
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
