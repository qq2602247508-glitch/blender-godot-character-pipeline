"""Landmark-driven humanoid rig and modular part authoring for game characters."""

import json
from math import pi
from mathutils import Vector

import bpy
from bpy_extras import view3d_utils


LANDMARKS = (
    ("pelvis", "骨盆中心"),
    ("chest", "胸口中心"),
    ("neck", "脖子中心"),
    ("head", "头顶"),
    ("shoulder.L", "角色左肩（画面右侧）"),
    ("elbow.L", "角色左肘（画面右侧）"),
    ("wrist.L", "角色左手腕（画面右侧）"),
    ("hand.L", "角色左手掌/中指根部（画面右侧）"),
    ("hip.L", "角色左髋关节（画面右侧）"),
    ("knee.L", "角色左膝（画面右侧）"),
    ("ankle.L", "角色左脚踝（画面右侧）"),
    ("toe.L", "角色左脚尖（画面右侧）"),
)

PART_ROLES = (
    ("BODY_SKINNED", "身体/完整蒙皮", "跟随完整英雄骨架变形"),
    ("FIXED_SKINNED", "普通换装", "使用普通骨骼权重的衣服或鞋子"),
    ("RIGID", "刚性/漂浮挂件", "整体挂接到一根骨骼的附件"),
    ("HAIR", "动态头发", "根部固定、下方飘动的头发"),
    ("SKIRT", "动态裙摆/披风", "腰部或肩部固定的布料表面"),
    ("IGNORE", "忽略", "不属于运行时角色"),
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
    size = max(target.dimensions) * 0.020 if target else 0.035
    display_points = _mirror(points)
    for key, point in display_points.items():
        ring = bpy.data.objects.new("LM_" + key + "_橙色圈", None)
        ring.empty_display_type = "CIRCLE"
        ring.empty_display_size = size
        ring.rotation_euler.x = pi / 2.0
        ring.color = (1.0, 0.22, 0.02, 1.0)
        ring.show_in_front = True
        ring.location = point
        ring.show_name = False
        ring["gsmb_landmark_id"] = key
        ring["gsmb_landmark_ring"] = True
        collection.objects.link(ring)
        ring.select_set(True)

        dot = bpy.data.objects.new("LM_" + key + "_中心点", None)
        dot.empty_display_type = "SPHERE"
        dot.empty_display_size = size * 0.20
        dot.color = (1.0, 0.22, 0.02, 1.0)
        dot.show_in_front = True
        dot.location = point
        dot["gsmb_landmark_id"] = key
        dot["gsmb_landmark_dot"] = True
        collection.objects.link(dot)
        dot.select_set(True)


def _update_hover_cursor(scene, point=None):
    collection = _marker_collection(scene)
    for name in ("LM_当前光标_橙色圈", "LM_当前光标_中心点"):
        obj = bpy.data.objects.get(name)
        if obj and point is None:
            bpy.data.objects.remove(obj, do_unlink=True)
    if point is None:
        return
    target = _target(scene)
    size = max(target.dimensions) * 0.024 if target else 0.040
    ring = bpy.data.objects.get("LM_当前光标_橙色圈")
    if ring is None:
        ring = bpy.data.objects.new("LM_当前光标_橙色圈", None)
        ring.empty_display_type = "CIRCLE"
        ring.rotation_euler.x = pi / 2.0
        ring.color = (1.0, 0.45, 0.0, 1.0)
        ring.show_in_front = True
        ring["gsmb_landmark_preview"] = True
        collection.objects.link(ring)
    ring.empty_display_size = size
    ring.location = point
    ring.select_set(True)
    dot = bpy.data.objects.get("LM_当前光标_中心点")
    if dot is None:
        dot = bpy.data.objects.new("LM_当前光标_中心点", None)
        dot.empty_display_type = "SPHERE"
        dot.color = (1.0, 0.45, 0.0, 1.0)
        dot.show_in_front = True
        dot["gsmb_landmark_preview"] = True
        collection.objects.link(dot)
    dot.empty_display_size = size * 0.22
    dot.location = point
    dot.select_set(True)


def _next_landmark(scene):
    points = _points(scene)
    for key, label in LANDMARKS:
        if key not in points:
            return key, label
    return None, "所有标记点已经完成"


def _undo_last_landmark(scene):
    points = _points(scene)
    removed = None
    for key, _label in reversed(LANDMARKS):
        if key in points:
            removed = key
            del points[key]
            break
    _save_points(scene, points)
    _sync_markers(scene, points)
    key, label = _next_landmark(scene)
    scene["gsmb_hero_status"] = "已撤回上一个点；请点击：" + label if key else "所有标记点已经完成"
    return removed


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
        raise ValueError("缺少人体标记点：" + key)
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
        raise ValueError("缺少人体标记点：" + ", ".join(missing))

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
    scene["gsmb_hero_status"] = f"HERO_RIG_V2 已生成：{len(arm_data.bones)} 根骨骼"
    return armature


def _mouse_surface_point(context, event):
    target = _target(context.scene)
    if target is None or context.region is None or context.region_data is None:
        return None
    coord = (event.mouse_region_x, event.mouse_region_y)
    origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, coord)
    direction = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, coord)
    inv = target.matrix_world.inverted()
    hit, location, _normal, _face = target.ray_cast(inv @ origin, inv.to_3x3() @ direction)
    if not hit:
        return None
    return _normalized_surface_point(target, target.matrix_world @ location, "")


class GSMB_OT_start_hero_landmarks(bpy.types.Operator):
    bl_idname = "gsmb.start_hero_landmarks"
    bl_label = "开始/重新标记人体点"
    bl_description = "从骨盆开始点击；默认左右对称，Ctrl+Z 撤回上一个点"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        target = context.active_object if context.active_object and context.active_object.type == "MESH" else _target(context.scene)
        if target is None:
            self.report({"ERROR"}, "请先选择完整角色模型")
            return {"CANCELLED"}
        context.scene.gsmb_hero_target = target
        _save_points(context.scene, {})
        _sync_markers(context.scene, {})
        context.scene["gsmb_hero_status"] = "准备完成；请点击：骨盆中心"
        bpy.ops.gsmb.place_hero_landmarks("INVOKE_DEFAULT")
        return {"FINISHED"}


class GSMB_OT_place_hero_landmarks(bpy.types.Operator):
    bl_idname = "gsmb.place_hero_landmarks"
    bl_label = "继续放置人体标记点"
    bl_description = "移动橙色圆环确认位置，左键放置；Ctrl+Z 撤回"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        if context.area is None or context.area.type != "VIEW_3D" or _target(context.scene) is None:
            self.report({"ERROR"}, "请在 3D 视图中选择目标角色")
            return {"CANCELLED"}
        context.window_manager.modal_handler_add(self)
        key, label = _next_landmark(context.scene)
        context.scene["gsmb_hero_status"] = "请点击：" + label if key else label
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "ESC":
            _update_hover_cursor(context.scene, None)
            return {"CANCELLED"}
        if event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS":
            _update_hover_cursor(context.scene, None)
            return {"FINISHED"}
        if event.value == "PRESS" and (
            event.type == "BACK_SPACE" or (event.type == "Z" and event.ctrl)
        ):
            _undo_last_landmark(context.scene)
            return {"RUNNING_MODAL"}
        if event.type == "MOUSEMOVE":
            point = _mouse_surface_point(context, event)
            _update_hover_cursor(context.scene, point)
            if context.area:
                context.area.tag_redraw()
            return {"RUNNING_MODAL"}
        if event.type != "LEFTMOUSE" or event.value != "PRESS":
            return {"PASS_THROUGH"}

        key, label = _next_landmark(context.scene)
        if key is None:
            _update_hover_cursor(context.scene, None)
            return {"FINISHED"}
        world = _mouse_surface_point(context, event)
        if world is None:
            context.scene["gsmb_hero_status"] = "没有点到模型；请直接点击：" + label
            return {"RUNNING_MODAL"}
        points = _points(context.scene)
        points[key] = world
        _save_points(context.scene, points)
        _sync_markers(context.scene, points)
        _update_hover_cursor(context.scene, world)
        next_key, next_label = _next_landmark(context.scene)
        context.scene["gsmb_hero_status"] = (
            "请点击：" + next_label
            if next_key else "标记完成；请点击“生成 HERO_RIG_V2＋手指骨”"
        )
        if next_key:
            return {"RUNNING_MODAL"}
        _update_hover_cursor(context.scene, None)
        return {"FINISHED"}


class GSMB_OT_landmark_from_cursor(bpy.types.Operator):
    bl_idname = "gsmb.landmark_from_cursor"
    bl_label = "用 3D 光标设置下一个点"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        key, _label = _next_landmark(context.scene)
        if key is None:
            self.report({"INFO"}, "所有人体点都已经设置")
            return {"FINISHED"}
        points = _points(context.scene)
        points[key] = context.scene.cursor.location.copy()
        _save_points(context.scene, points)
        _sync_markers(context.scene, points)
        return {"FINISHED"}


class GSMB_OT_build_hero_rig(bpy.types.Operator):
    bl_idname = "gsmb.build_hero_rig"
    bl_label = "生成 HERO_RIG_V2＋手指骨"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            armature = build_hero_rig(context.scene)
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"已生成 HERO_RIG_V2：{len(armature.data.bones)} 根骨骼")
        return {"FINISHED"}


class GSMB_OT_analyze_part_roles(bpy.types.Operator):
    bl_idname = "gsmb.analyze_part_roles"
    bl_label = "自动识别所选部件"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        meshes = [obj for obj in context.selected_objects if obj.type == "MESH"]
        if not meshes:
            self.report({"ERROR"}, "请选择一个或多个模型部件")
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
        self.report({"INFO"}, f"已识别 {len(meshes)} 个部件，请手工复核类型")
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
    bl_label = "自动生成固定/飘动遮罩"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        meshes = [obj for obj in context.selected_objects if obj.type == "MESH"]
        meshes = [obj for obj in meshes if obj.gsmb_part_role in {"HAIR", "SKIRT"}]
        if not meshes:
            self.report({"ERROR"}, "请选择已标记为动态头发或裙摆/披风的部件")
            return {"CANCELLED"}
        for obj in meshes:
            _write_mask(obj, obj.gsmb_part_role)
        self.report({"INFO"}, f"已为 {len(meshes)} 个部件生成可编辑的固定/飘动遮罩")
        return {"FINISHED"}


def _armature_modifier(obj, armature):
    modifier = next((item for item in obj.modifiers if item.type == "ARMATURE"), None)
    if modifier is None:
        modifier = obj.modifiers.new(name="HERO_RIG_V2", type="ARMATURE")
    modifier.object = armature
    return modifier


class GSMB_OT_bind_selected_parts(bpy.types.Operator):
    bl_idname = "gsmb.bind_selected_parts"
    bl_label = "绑定所选部件"
    bl_description = "普通部件自动权重；刚性挂件自动挂到最近骨骼"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        armature = context.scene.gsmb_hero_armature
        if armature is None or armature.type != "ARMATURE":
            self.report({"ERROR"}, "请先生成或选择 HERO_RIG_V2")
            return {"CANCELLED"}
        meshes = [obj for obj in context.selected_objects if obj.type == "MESH"]
        if not meshes:
            self.report({"ERROR"}, "请选择要绑定的模型部件")
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
            self.report({"WARNING"}, "部分自动权重失败，请改用“从身体迁移权重到衣服”")
        else:
            self.report({"INFO"}, f"已绑定 {len(meshes)} 个部件")
        return {"FINISHED"}


class GSMB_OT_transfer_body_weights(bpy.types.Operator):
    bl_idname = "gsmb.transfer_body_weights"
    bl_label = "从身体迁移权重到衣服"
    bl_description = "把 Character 基础身体的临近权重复制到所选新衣服"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        source = _target(scene)
        armature = scene.gsmb_hero_armature
        if source is None or armature is None:
            self.report({"ERROR"}, "请选择已有权重的基础身体和 HERO_RIG_V2")
            return {"CANCELLED"}
        targets = [obj for obj in context.selected_objects if obj.type == "MESH" and obj != source]
        if not targets:
            self.report({"ERROR"}, "请选择新衣服部件，不要选择基础身体")
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
            self.report({"WARNING"}, "部分部件的权重迁移需要手工检查")
        else:
            self.report({"INFO"}, f"已把身体权重迁移到 {len(targets)} 个部件")
        return {"FINISHED"}


class GSMB_OT_assign_mask_selection(bpy.types.Operator):
    bl_idname = "gsmb.assign_mask_selection"
    bl_label = "设置所选顶点"
    bl_options = {"REGISTER", "UNDO"}

    mask: bpy.props.EnumProperty(items=(("FIXED", "固定", ""), ("DYNAMIC", "飘动", "")))

    def execute(self, context):
        obj = context.edit_object
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "请进入模型编辑模式并选择顶点")
            return {"CANCELLED"}
        name = "GSMB_" + self.mask
        opposite = "GSMB_DYNAMIC" if self.mask == "FIXED" else "GSMB_FIXED"
        group = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
        other = obj.vertex_groups.get(opposite) or obj.vertex_groups.new(name=opposite)
        selected = [vertex.index for vertex in obj.data.vertices if vertex.select]
        if not selected:
            self.report({"ERROR"}, "当前没有选择顶点")
            return {"CANCELLED"}
        group.add(selected, 1.0, "REPLACE")
        other.add(selected, 0.0, "REPLACE")
        return {"FINISHED"}


class GSMB_PT_hero_rig(bpy.types.Panel):
    bl_label = "英雄自动绑骨（测试版）"
    bl_idname = "GSMB_PT_hero_rig"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Secondary Motion"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.prop(scene, "gsmb_hero_target", text="完整角色")
        symmetry = layout.row()
        symmetry.enabled = False
        symmetry.label(text="左右自动对称：已开启", icon="MOD_MIRROR")
        row = layout.row()
        row.scale_y = 1.35
        row.operator("gsmb.start_hero_landmarks", icon="EMPTY_AXIS")
        layout.operator("gsmb.place_hero_landmarks", icon="RESTRICT_SELECT_OFF")
        layout.operator("gsmb.landmark_from_cursor", icon="CURSOR")
        points = _points(scene)
        layout.label(text=f"标记进度：{len(points)}/{len(LANDMARKS)}")
        layout.label(text="左键放置 · Ctrl+Z 撤回 · Esc 退出")
        layout.label(text=scene.get("gsmb_hero_status", "请选择完整角色模型"))
        layout.operator("gsmb.build_hero_rig", icon="ARMATURE_DATA")

        box = layout.box()
        box.label(text="换装与动态部件")
        active = context.active_object
        if active and active.type == "MESH":
            box.prop(active, "gsmb_part_role")
        box.operator("gsmb.analyze_part_roles", icon="VIEWZOOM")
        box.operator("gsmb.bind_selected_parts", icon="MOD_ARMATURE")
        box.operator("gsmb.transfer_body_weights", icon="MOD_DATA_TRANSFER")
        box.operator("gsmb.auto_dynamic_mask", icon="MOD_VERTEX_WEIGHT")
        row = box.row(align=True)
        fixed = row.operator("gsmb.assign_mask_selection", text="所选顶点＝固定")
        fixed.mask = "FIXED"
        dynamic = row.operator("gsmb.assign_mask_selection", text="所选顶点＝飘动")
        dynamic.mask = "DYNAMIC"
        box.label(text="遮罩可继续用权重绘制手工修改")


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
        name="完整角色", type=bpy.types.Object,
        poll=lambda _self, obj: obj.type == "MESH",
    )
    bpy.types.Scene.gsmb_hero_armature = bpy.props.PointerProperty(
        name="英雄骨架", type=bpy.types.Object,
        poll=lambda _self, obj: obj.type == "ARMATURE",
    )
    bpy.types.Object.gsmb_part_role = bpy.props.EnumProperty(
        name="部件类型", items=PART_ROLES, default="FIXED_SKINNED"
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
