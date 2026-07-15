"""Landmark-driven humanoid rig and modular part authoring for game characters."""

import json
from mathutils import Vector

import bpy
from bpy_extras import view3d_utils


LANDMARKS = (
    ("pelvis", "Pelvis / hips center"),
    ("chest", "Chest center"),
    ("neck", "Neck center"),
    ("head", "Top of head"),
    ("shoulder.L", "Left shoulder"),
    ("elbow.L", "Left elbow"),
    ("wrist.L", "Left wrist"),
    ("hand.L", "Left middle knuckle"),
    ("hip.L", "Left hip joint"),
    ("knee.L", "Left knee"),
    ("ankle.L", "Left ankle"),
    ("toe.L", "Left toe"),
)

PART_ROLES = (
    ("BODY_SKINNED", "Body / skinned", "Deforms with the complete hero skeleton"),
    ("FIXED_SKINNED", "Fixed replacement", "Clothes or shoes that use ordinary skeleton weights"),
    ("RIGID", "Rigid / floating", "Rigid accessory attached to one bone"),
    ("HAIR", "Hair dynamic", "Hair with a fixed root and dynamic length"),
    ("SKIRT", "Skirt / cape dynamic", "Cloth-like surface with a fixed waist or shoulder band"),
    ("IGNORE", "Ignore", "Not part of the runtime character"),
)

CORE_REQUIRED = {item[0] for item in LANDMARKS}
MARKER_COLLECTION = "GSMB_HERO_LANDMARKS"


def _points(scene):
    try:
        raw = json.loads(scene.get("gsmb_hero_landmarks", "{}"))
        return {key: Vector(value) for key, value in raw.items()}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _save_points(scene, points):
    scene["gsmb_hero_landmarks"] = json.dumps(
        {key: list(value) for key, value in points.items()}, separators=(",", ":")
    )


def _target(scene):
    obj = scene.gsmb_hero_target
    return obj if obj and obj.type == "MESH" else None


def _marker_collection(scene):
    collection = bpy.data.collections.get(MARKER_COLLECTION)
    if collection is None:
        collection = bpy.data.collections.new(MARKER_COLLECTION)
        scene.collection.children.link(collection)
    return collection


def _sync_markers(scene, points):
    collection = _marker_collection(scene)
    for obj in list(collection.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    target = _target(scene)
    size = max(target.dimensions) * 0.012 if target else 0.025
    for key, point in points.items():
        marker = bpy.data.objects.new("LM_" + key, None)
        marker.empty_display_type = "SPHERE"
        marker.empty_display_size = size
        marker.color = (0.05, 0.8, 1.0, 1.0)
        marker.location = point
        marker["gsmb_landmark_id"] = key
        collection.objects.link(marker)


def _next_landmark(scene):
    points = _points(scene)
    for key, label in LANDMARKS:
        if key not in points:
            return key, label
    return None, "All required landmarks placed"


def _normalized_surface_point(target, world_point, key):
    """Mixamo-like surface markers are collapsed onto a stable body-depth plane."""
    center_y = sum((target.matrix_world @ Vector(corner)).y for corner in target.bound_box) / 8.0
    point = world_point.copy()
    point.y = center_y
    return point


def _mirror(points):
    if "pelvis" not in points:
        return points
    axis = points["pelvis"].x
    result = dict(points)
    for key, point in list(points.items()):
        if key.endswith(".L"):
            mirrored = point.copy()
            mirrored.x = 2.0 * axis - point.x
            result[key[:-2] + ".R"] = mirrored
    return result


def _require(points, key):
    if key not in points:
        raise ValueError("Missing landmark: " + key)
    return points[key]


def _add_bone(edit_bones, name, head, tail, parent=None, connected=False, deform=True):
    bone = edit_bones.new(name)
    bone.head = head
    bone.tail = tail if (tail - head).length > 1e-5 else head + Vector((0, 0, 0.02))
    bone.parent = parent
    bone.use_connect = bool(connected and parent and (bone.head - parent.tail).length < 1e-4)
    bone.use_deform = deform
    return bone


def build_hero_rig(scene):
    points = _mirror(_points(scene))
    missing = sorted(CORE_REQUIRED - set(points))
    if missing:
        raise ValueError("Missing landmarks: " + ", ".join(missing))

    arm_data = bpy.data.armatures.new("HERO_RIG_V2")
    armature = bpy.data.objects.new("HERO_RIG_V2", arm_data)
    scene.collection.objects.link(armature)
    armature.show_in_front = True
    armature["gsmb_hero_rig_version"] = 2
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm_data.edit_bones

    pelvis = _require(points, "pelvis")
    chest = _require(points, "chest")
    neck = _require(points, "neck")
    head = _require(points, "head")
    root = _add_bone(eb, "root", pelvis - Vector((0, 0, 0.08)), pelvis, deform=False)
    hips = _add_bone(eb, "Hips", pelvis, pelvis + (chest - pelvis) * 0.25, root)
    spine = _add_bone(eb, "Spine", hips.tail, pelvis + (chest - pelvis) * 0.58, hips, True)
    spine1 = _add_bone(eb, "Spine1", spine.tail, chest, spine, True)
    spine2 = _add_bone(eb, "Spine2", chest, neck, spine1, True)
    neck_bone = _add_bone(eb, "Neck", neck, neck.lerp(head, 0.34), spine2)
    head_bone = _add_bone(eb, "Head", neck_bone.tail, head, neck_bone, True)
    _add_bone(eb, "Head_end", head, head + (head - neck) * 0.18, head_bone, True, False)

    for side in ("L", "R"):
        shoulder = _require(points, "shoulder." + side)
        elbow = _require(points, "elbow." + side)
        wrist = _require(points, "wrist." + side)
        hand = _require(points, "hand." + side)
        hip = _require(points, "hip." + side)
        knee = _require(points, "knee." + side)
        ankle = _require(points, "ankle." + side)
        toe = _require(points, "toe." + side)

        clavicle = _add_bone(eb, "Shoulder." + side, chest, shoulder, spine2)
        upper_arm = _add_bone(eb, "UpperArm." + side, shoulder, elbow, clavicle, True)
        forearm = _add_bone(eb, "LowerArm." + side, elbow, wrist, upper_arm, True)
        hand_bone = _add_bone(eb, "Hand." + side, wrist, hand, forearm, True)
        thigh = _add_bone(eb, "UpperLeg." + side, hip, knee, hips)
        shin = _add_bone(eb, "LowerLeg." + side, knee, ankle, thigh, True)
        foot = _add_bone(eb, "Foot." + side, ankle, toe, shin, True)
        _add_bone(eb, "ToeBase." + side, toe, toe + (toe - ankle) * 0.18, foot, True)

        palm = hand - wrist
        if palm.length < 1e-4:
            palm = Vector((0.12 if side == "L" else -0.12, 0, 0))
        palm_len = palm.length
        direction = palm.normalized()
        vertical = Vector((0, 0, 1))
        finger_specs = (
            ("Thumb", -0.55, 0.70),
            ("Index", 0.35, 1.00),
            ("Middle", 0.12, 1.08),
            ("Ring", -0.12, 1.00),
            ("Pinky", -0.35, 0.84),
        )
        side_sign = 1.0 if side == "L" else -1.0
        for finger, spread, length_scale in finger_specs:
            base = wrist.lerp(hand, 0.72) + vertical * spread * palm_len * 0.18
            if finger == "Thumb":
                base = wrist.lerp(hand, 0.42) - vertical * palm_len * 0.16
            tip = base + direction * palm_len * 0.78 * length_scale
            if finger == "Thumb":
                tip += Vector((0, -0.18 * palm_len * side_sign, -0.12 * palm_len))
            previous = hand_bone
            start = base
            for segment in range(1, 4):
                end = base.lerp(tip, segment / 3.0)
                previous = _add_bone(
                    eb, f"{finger}{segment}.{side}", start, end, previous, segment > 1
                )
                start = end

    bpy.ops.object.mode_set(mode="OBJECT")
    scene.gsmb_hero_armature = armature
    scene["gsmb_hero_status"] = f"HERO_RIG_V2 built: {len(arm_data.bones)} bones"
    return armature


class GSMB_OT_start_hero_landmarks(bpy.types.Operator):
    bl_idname = "gsmb.start_hero_landmarks"
    bl_label = "Start / Reset Landmark Rigging"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        target = context.active_object if context.active_object and context.active_object.type == "MESH" else _target(context.scene)
        if target is None:
            self.report({"ERROR"}, "Select the complete character mesh first")
            return {"CANCELLED"}
        context.scene.gsmb_hero_target = target
        _save_points(context.scene, {})
        _sync_markers(context.scene, {})
        context.scene["gsmb_hero_status"] = "Ready: click Pelvis / hips center"
        bpy.ops.gsmb.place_hero_landmarks("INVOKE_DEFAULT")
        return {"FINISHED"}


class GSMB_OT_place_hero_landmarks(bpy.types.Operator):
    bl_idname = "gsmb.place_hero_landmarks"
    bl_label = "Continue Placing Landmarks"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        if context.area is None or context.area.type != "VIEW_3D" or _target(context.scene) is None:
            self.report({"ERROR"}, "Run this inside a 3D View with a target mesh")
            return {"CANCELLED"}
        context.window_manager.modal_handler_add(self)
        key, label = _next_landmark(context.scene)
        context.scene["gsmb_hero_status"] = "Click: " + label if key else label
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "ESC":
            return {"CANCELLED"}
        if event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS":
            return {"FINISHED"}
        if event.type == "BACK_SPACE" and event.value == "PRESS":
            points = _points(context.scene)
            for key, _label in reversed(LANDMARKS):
                if key in points:
                    del points[key]
                    break
            _save_points(context.scene, points)
            _sync_markers(context.scene, points)
            return {"RUNNING_MODAL"}
        if event.type != "LEFTMOUSE" or event.value != "PRESS":
            return {"PASS_THROUGH"}

        key, label = _next_landmark(context.scene)
        if key is None:
            return {"FINISHED"}
        target = _target(context.scene)
        region = context.region
        rv3d = context.region_data
        coord = (event.mouse_region_x, event.mouse_region_y)
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        inv = target.matrix_world.inverted()
        hit, location, _normal, _face = target.ray_cast(inv @ origin, inv.to_3x3() @ direction)
        if not hit:
            context.scene["gsmb_hero_status"] = "No mesh hit; click directly on " + label
            return {"RUNNING_MODAL"}
        world = target.matrix_world @ location
        points = _points(context.scene)
        points[key] = _normalized_surface_point(target, world, key)
        _save_points(context.scene, points)
        _sync_markers(context.scene, points)
        next_key, next_label = _next_landmark(context.scene)
        context.scene["gsmb_hero_status"] = "Click: " + next_label if next_key else "Landmarks complete; build HERO_RIG_V2"
        return {"RUNNING_MODAL"} if next_key else {"FINISHED"}


class GSMB_OT_landmark_from_cursor(bpy.types.Operator):
    bl_idname = "gsmb.landmark_from_cursor"
    bl_label = "Set Next from 3D Cursor"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        key, _label = _next_landmark(context.scene)
        if key is None:
            self.report({"INFO"}, "All landmarks are already set")
            return {"FINISHED"}
        points = _points(context.scene)
        points[key] = context.scene.cursor.location.copy()
        _save_points(context.scene, points)
        _sync_markers(context.scene, points)
        return {"FINISHED"}


class GSMB_OT_build_hero_rig(bpy.types.Operator):
    bl_idname = "gsmb.build_hero_rig"
    bl_label = "Build HERO_RIG_V2 + Fingers"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            armature = build_hero_rig(context.scene)
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Created {len(armature.data.bones)}-bone HERO_RIG_V2")
        return {"FINISHED"}


class GSMB_OT_analyze_part_roles(bpy.types.Operator):
    bl_idname = "gsmb.analyze_part_roles"
    bl_label = "Auto Classify Selected Parts"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        meshes = [obj for obj in context.selected_objects if obj.type == "MESH"]
        if not meshes:
            self.report({"ERROR"}, "Select one or more mesh parts")
            return {"CANCELLED"}
        for obj in meshes:
            name = obj.name.lower()
            if any(word in name for word in ("hair", "bang", "ponytail", "braid")):
                obj.gsmb_part_role = "HAIR"
            elif any(word in name for word in ("skirt", "dress", "cape", "coat_tail")):
                obj.gsmb_part_role = "SKIRT"
            elif any(word in name for word in ("weapon", "earring", "glasses", "accessory")):
                obj.gsmb_part_role = "RIGID"
            elif any(word in name for word in ("body", "skin", "base")):
                obj.gsmb_part_role = "BODY_SKINNED"
            else:
                obj.gsmb_part_role = "FIXED_SKINNED"
        self.report({"INFO"}, f"Classified {len(meshes)} parts; review roles manually")
        return {"FINISHED"}


def _write_mask(obj, role):
    fixed = obj.vertex_groups.get("GSMB_FIXED") or obj.vertex_groups.new(name="GSMB_FIXED")
    dynamic = obj.vertex_groups.get("GSMB_DYNAMIC") or obj.vertex_groups.new(name="GSMB_DYNAMIC")
    if not obj.data.vertices:
        return
    zs = [vertex.co.z for vertex in obj.data.vertices]
    low, high = min(zs), max(zs)
    span = max(high - low, 1e-6)
    cutoff = high - span * (0.18 if role == "HAIR" else 0.15)
    for vertex in obj.data.vertices:
        fixed_weight = max(0.0, min(1.0, (vertex.co.z - cutoff) / (span * 0.10) + 0.5))
        fixed.add([vertex.index], fixed_weight, "REPLACE")
        dynamic.add([vertex.index], 1.0 - fixed_weight, "REPLACE")


class GSMB_OT_auto_dynamic_mask(bpy.types.Operator):
    bl_idname = "gsmb.auto_dynamic_mask"
    bl_label = "Auto Fixed / Dynamic Mask"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        meshes = [obj for obj in context.selected_objects if obj.type == "MESH"]
        meshes = [obj for obj in meshes if obj.gsmb_part_role in {"HAIR", "SKIRT"}]
        if not meshes:
            self.report({"ERROR"}, "Select parts marked Hair or Skirt/Cape")
            return {"CANCELLED"}
        for obj in meshes:
            _write_mask(obj, obj.gsmb_part_role)
        self.report({"INFO"}, f"Created editable GSMB_FIXED / GSMB_DYNAMIC masks on {len(meshes)} parts")
        return {"FINISHED"}


def _armature_modifier(obj, armature):
    modifier = next((item for item in obj.modifiers if item.type == "ARMATURE"), None)
    if modifier is None:
        modifier = obj.modifiers.new(name="HERO_RIG_V2", type="ARMATURE")
    modifier.object = armature
    return modifier


class GSMB_OT_bind_selected_parts(bpy.types.Operator):
    bl_idname = "gsmb.bind_selected_parts"
    bl_label = "Bind Selected Parts"
    bl_description = "Automatic weights for skinned parts; nearest-bone parenting for rigid accessories"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        armature = context.scene.gsmb_hero_armature
        if armature is None or armature.type != "ARMATURE":
            self.report({"ERROR"}, "Build or choose HERO_RIG_V2 first")
            return {"CANCELLED"}
        meshes = [obj for obj in context.selected_objects if obj.type == "MESH"]
        if not meshes:
            self.report({"ERROR"}, "Select mesh parts to bind")
            return {"CANCELLED"}
        failures = []
        for obj in meshes:
            role = obj.gsmb_part_role
            if role == "IGNORE":
                continue
            world = obj.matrix_world.copy()
            if role == "RIGID":
                center = obj.matrix_world.translation
                bone = min(
                    (item for item in armature.data.bones if item.use_deform),
                    key=lambda item: (
                        (armature.matrix_world @ ((item.head_local + item.tail_local) * 0.5)) - center
                    ).length,
                )
                obj.parent = armature
                obj.parent_type = "BONE"
                obj.parent_bone = bone.name
                obj.matrix_world = world
                continue
            if role in {"HAIR", "SKIRT"}:
                _armature_modifier(obj, armature)
                continue
            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            armature.select_set(True)
            context.view_layer.objects.active = armature
            try:
                bpy.ops.object.parent_set(type="ARMATURE_AUTO", keep_transform=True)
                # Blender creates the modifier as part of ARMATURE_AUTO.
                _armature_modifier(obj, armature)
            except RuntimeError as error:
                failures.append(f"{obj.name}: {error}")
        if failures:
            self.report({"WARNING"}, "Some auto weights failed; use Transfer Body Weights")
        else:
            self.report({"INFO"}, f"Bound {len(meshes)} modular parts")
        return {"FINISHED"}


class GSMB_OT_transfer_body_weights(bpy.types.Operator):
    bl_idname = "gsmb.transfer_body_weights"
    bl_label = "Transfer Body Weights to Clothes"
    bl_description = "Copy nearby vertex-group weights from the Character mesh to selected clothing"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        source = _target(scene)
        armature = scene.gsmb_hero_armature
        if source is None or armature is None:
            self.report({"ERROR"}, "Choose the weighted base Character and HERO_RIG_V2")
            return {"CANCELLED"}
        targets = [obj for obj in context.selected_objects if obj.type == "MESH" and obj != source]
        if not targets:
            self.report({"ERROR"}, "Select the new clothing parts (not the base body)")
            return {"CANCELLED"}
        failures = []
        for obj in targets:
            # DATA_TRANSFER with NAME matching only writes to existing layers.
            for source_group in source.vertex_groups:
                if obj.vertex_groups.get(source_group.name) is None:
                    obj.vertex_groups.new(name=source_group.name)
            modifier = obj.modifiers.new(name="GSMB_WeightTransfer", type="DATA_TRANSFER")
            modifier.object = source
            modifier.use_vert_data = True
            modifier.data_types_verts = {"VGROUP_WEIGHTS"}
            modifier.vert_mapping = "POLYINTERP_NEAREST"
            modifier.layers_vgroup_select_src = "ALL"
            modifier.layers_vgroup_select_dst = "NAME"
            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            context.view_layer.objects.active = obj
            try:
                bpy.ops.object.modifier_apply(modifier=modifier.name)
                obj.parent = armature
                _armature_modifier(obj, armature)
            except RuntimeError as error:
                failures.append(f"{obj.name}: {error}")
        if failures:
            self.report({"WARNING"}, "Weight transfer needs manual review on some parts")
        else:
            self.report({"INFO"}, f"Transferred body weights to {len(targets)} parts")
        return {"FINISHED"}


class GSMB_OT_assign_mask_selection(bpy.types.Operator):
    bl_idname = "gsmb.assign_mask_selection"
    bl_label = "Assign Selected Vertices"
    bl_options = {"REGISTER", "UNDO"}

    mask: bpy.props.EnumProperty(items=(("FIXED", "Fixed", ""), ("DYNAMIC", "Dynamic", "")))

    def execute(self, context):
        obj = context.edit_object
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "Enter Mesh Edit Mode and select vertices")
            return {"CANCELLED"}
        name = "GSMB_" + self.mask
        opposite = "GSMB_DYNAMIC" if self.mask == "FIXED" else "GSMB_FIXED"
        group = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
        other = obj.vertex_groups.get(opposite) or obj.vertex_groups.new(name=opposite)
        selected = [vertex.index for vertex in obj.data.vertices if vertex.select]
        if not selected:
            self.report({"ERROR"}, "No vertices selected")
            return {"CANCELLED"}
        group.add(selected, 1.0, "REPLACE")
        other.add(selected, 0.0, "REPLACE")
        return {"FINISHED"}


class GSMB_PT_hero_rig(bpy.types.Panel):
    bl_label = "Hero Auto-Rig (MVP)"
    bl_idname = "GSMB_PT_hero_rig"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Secondary Motion"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.prop(scene, "gsmb_hero_target")
        row = layout.row()
        row.scale_y = 1.35
        row.operator("gsmb.start_hero_landmarks", icon="EMPTY_AXIS")
        layout.operator("gsmb.place_hero_landmarks", icon="RESTRICT_SELECT_OFF")
        layout.operator("gsmb.landmark_from_cursor", icon="CURSOR")
        points = _points(scene)
        layout.label(text=f"Landmarks: {len(points)}/{len(LANDMARKS)}")
        layout.label(text=scene.get("gsmb_hero_status", "Select complete character mesh"))
        layout.operator("gsmb.build_hero_rig", icon="ARMATURE_DATA")

        box = layout.box()
        box.label(text="Modular Parts")
        active = context.active_object
        if active and active.type == "MESH":
            box.prop(active, "gsmb_part_role")
        box.operator("gsmb.analyze_part_roles", icon="VIEWZOOM")
        box.operator("gsmb.bind_selected_parts", icon="MOD_ARMATURE")
        box.operator("gsmb.transfer_body_weights", icon="MOD_DATA_TRANSFER")
        box.operator("gsmb.auto_dynamic_mask", icon="MOD_VERTEX_WEIGHT")
        row = box.row(align=True)
        fixed = row.operator("gsmb.assign_mask_selection", text="Selected = Fixed")
        fixed.mask = "FIXED"
        dynamic = row.operator("gsmb.assign_mask_selection", text="Selected = Dynamic")
        dynamic.mask = "DYNAMIC"
        box.label(text="Masks remain editable in Weight Paint")


CLASSES = (
    GSMB_OT_start_hero_landmarks,
    GSMB_OT_place_hero_landmarks,
    GSMB_OT_landmark_from_cursor,
    GSMB_OT_build_hero_rig,
    GSMB_OT_analyze_part_roles,
    GSMB_OT_auto_dynamic_mask,
    GSMB_OT_bind_selected_parts,
    GSMB_OT_transfer_body_weights,
    GSMB_OT_assign_mask_selection,
    GSMB_PT_hero_rig,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.gsmb_hero_target = bpy.props.PointerProperty(
        name="Character", type=bpy.types.Object,
        poll=lambda _self, obj: obj.type == "MESH",
    )
    bpy.types.Scene.gsmb_hero_armature = bpy.props.PointerProperty(
        name="Hero Rig", type=bpy.types.Object,
        poll=lambda _self, obj: obj.type == "ARMATURE",
    )
    bpy.types.Object.gsmb_part_role = bpy.props.EnumProperty(
        name="Part Role", items=PART_ROLES, default="FIXED_SKINNED"
    )


def unregister():
    for owner, name in (
        (bpy.types.Scene, "gsmb_hero_target"),
        (bpy.types.Scene, "gsmb_hero_armature"),
        (bpy.types.Object, "gsmb_part_role"),
    ):
        if hasattr(owner, name):
            delattr(owner, name)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
