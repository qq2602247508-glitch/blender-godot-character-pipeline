bl_info = {
    "name": "Game Secondary Motion Builder",
    "author": "inagi + Codex",
    "version": (1, 7, 1),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Secondary Motion",
    "description": "Build and analyze lightweight hair/skirt secondary-motion test assets",
    "category": "Rigging",
}

import bpy
import bmesh
import json
import time
import gpu
import heapq
import blf
from math import atan2, pi
from mathutils import Vector, geometry
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader

from . import production
from . import hero_rig


GENERATED_COLLECTION = "GSMB_GENERATED"
EXACT_KNIFE_SESSION = {}
EXACT_DRAW_HANDLE = None


def draw_exact_start_overlay():
    session = EXACT_KNIFE_SESSION
    point = session.get("start_world")
    region = session.get("region")
    region_3d = session.get("region_3d")
    if point is None or region is None or region_3d is None:
        return
    screen = view3d_utils.location_3d_to_region_2d(region, region_3d, point)
    if screen is None:
        return
    radius = 18.0
    ring = [
        (screen.x + radius * __import__('math').cos(2 * pi * i / 32),
         screen.y + radius * __import__('math').sin(2 * pi * i / 32))
        for i in range(33)
    ]
    cross = [
        (screen.x - 10, screen.y), (screen.x + 10, screen.y),
        (screen.x, screen.y - 10), (screen.x, screen.y + 10),
    ]
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(4.0)
    shader.bind()
    shader.uniform_float("color", (0.05, 1.0, 0.18, 1.0))
    batch_for_shader(shader, 'LINE_STRIP', {"pos": ring}).draw(shader)
    batch_for_shader(shader, 'LINES', {"pos": cross}).draw(shader)
    gpu.state.line_width_set(1.0)
    blf.position(0, screen.x + 24, screen.y + 12, 0)
    blf.size(0, 15)
    blf.color(0, 0.05, 1.0, 0.18, 1.0)
    blf.draw(0, "START / CLOSE HERE")
    gpu.state.blend_set('NONE')


def remove_exact_marker():
    global EXACT_DRAW_HANDLE
    if EXACT_DRAW_HANDLE is not None:
        bpy.types.SpaceView3D.draw_handler_remove(EXACT_DRAW_HANDLE, 'WINDOW')
        EXACT_DRAW_HANDLE = None
    marker = bpy.data.objects.get("KNIFE START - CLOSE HERE")
    if marker:
        bpy.data.objects.remove(marker, do_unlink=True)


def restore_exact_overlay(session=None):
    session = session or EXACT_KNIFE_SESSION
    area = session.get("area") if session else None
    if not area or area.type != 'VIEW_3D':
        return
    overlay = area.spaces.active.overlay
    if "show_edges" in session and hasattr(overlay, "show_edges"):
        overlay.show_edges = session["show_edges"]
    if "show_wireframes" in session and hasattr(overlay, "show_wireframes"):
        overlay.show_wireframes = session["show_wireframes"]


def launch_exact_knife():
    session = EXACT_KNIFE_SESSION
    if not session or session.get("phase") != "START_PENDING":
        return None
    target = bpy.data.objects.get(session.get("target", ""))
    window, area, region = session.get("window"), session.get("area"), session.get("region")
    if target is None or target.mode != 'EDIT' or not window or not area or not region:
        restore_exact_overlay(session)
        remove_exact_marker()
        session.clear()
        return None
    try:
        with bpy.context.temp_override(window=window, area=area, region=region, active_object=target, object=target):
            result = bpy.ops.mesh.knife_tool(
                'INVOKE_DEFAULT',
                use_occlude_geometry=not bpy.context.scene.gsmb_cut_through,
                only_selected=False,
            )
        if 'RUNNING_MODAL' not in result:
            raise RuntimeError(f"Knife returned {result}")
        session["phase"] = "DRAWING"
        window.scene["gsmb_cut_status"] = (
            "Exact Knife ACTIVE: start/finish at green marker, then press Enter"
        )
        if not bpy.app.timers.is_registered(monitor_exact_knife):
            bpy.app.timers.register(monitor_exact_knife, first_interval=0.15)
    except Exception as exc:
        window.scene["gsmb_cut_status"] = f"Exact Knife failed to start: {exc}"
        restore_exact_overlay(session)
        remove_exact_marker()
        session.clear()
    return None


def monitor_exact_knife():
    session = EXACT_KNIFE_SESSION
    if not session or session.get("phase") != "DRAWING":
        return None
    target = bpy.data.objects.get(session.get("target", ""))
    if target is None:
        restore_exact_overlay(session)
        remove_exact_marker()
        session.clear()
        return None
    if time.monotonic() - session.get("started", 0.0) > 300.0:
        restore_exact_overlay(session)
        remove_exact_marker()
        session.clear()
        return None
    if target.mode != 'EDIT':
        restore_exact_overlay(session)
        remove_exact_marker()
        session.clear()
        return None
    edit_mesh = bmesh.from_edit_mesh(target.data)
    new_edge_count = len(edit_mesh.edges) - session.get("initial_edges", len(edit_mesh.edges))
    selected_edges = sum(1 for edge in edit_mesh.edges if edge.select)
    if new_edge_count > 0 and selected_edges >= 3:
        window = session.get("window")
        area = session.get("area")
        region = session.get("region")
        try:
            with bpy.context.temp_override(window=window, area=area, region=region, active_object=target, object=target):
                result = bpy.ops.gsmb.separate_exact_region()
            if 'FINISHED' in result:
                restore_exact_overlay(session)
                remove_exact_marker()
                session.clear()
                return None
        except Exception as exc:
            print("GSMB exact knife auto-separate:", exc)
    return 0.15


def ensure_collection(name, scene=None):
    scene = scene or bpy.context.scene
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
    if collection.name not in {c.name for c in scene.collection.children}:
        scene.collection.children.link(collection)
    return collection


def move_to_collection(obj, collection):
    for old in list(obj.users_collection):
        old.objects.unlink(obj)
    collection.objects.link(obj)


def material(name, color, metallic=0.0, roughness=0.55):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1.0)
    mat.metallic = metallic
    mat.roughness = roughness
    return mat


def add_uv_sphere(name, location, scale, mat, collection):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(mat)
    move_to_collection(obj, collection)
    return obj


def add_capsule(name, location, radius, depth, mat, collection, rotation=(0, 0, 0)):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=6, location=location, rotation=rotation)
    obj = bpy.context.object
    obj.name = name
    obj.scale = (radius, radius, depth * 0.5 + radius)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(mat)
    obj.display_type = 'WIRE' if name.startswith("GSMB_COL") else 'TEXTURED'
    move_to_collection(obj, collection)
    return obj


def create_armature(collection):
    arm_data = bpy.data.armatures.new("GSMB_TestRig_Data")
    arm = bpy.data.objects.new("GSMB_TestRig", arm_data)
    collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bones = [
        ("root", (0, 0, 0), (0, 0, 0.9), None),
        ("pelvis", (0, 0, 0.9), (0, 0, 1.08), "root"),
        ("spine", (0, 0, 1.08), (0, 0, 1.45), "pelvis"),
        ("neck", (0, 0, 1.45), (0, 0, 1.58), "spine"),
        ("head", (0, 0, 1.58), (0, 0, 1.82), "neck"),
    ]
    for name, head, tail, parent in bones:
        bone = arm_data.edit_bones.new(name)
        bone.head, bone.tail = head, tail
        if parent:
            bone.parent = arm_data.edit_bones[parent]
            bone.use_connect = True
    for side, x in (("L", 0.13), ("R", -0.13)):
        leg = arm_data.edit_bones.new(f"thigh_{side}")
        leg.head, leg.tail = (x, 0, 0.95), (x, 0, 0.48)
        leg.parent = arm_data.edit_bones["pelvis"]
    bpy.ops.object.mode_set(mode='OBJECT')
    return arm


def create_hair_mesh(collection, mat):
    # Five separate tapered solid locks. Each level is a rectangular section,
    # giving the hair real volume instead of a zero-thickness ribbon.
    objects = []
    for index, x in enumerate((-0.22, -0.11, 0.0, 0.11, 0.22), 1):
        verts = []
        faces = []
        levels = 6
        for i in range(levels):
            z = 1.70 - i * 0.13
            y = 0.10 + i * 0.025
            half_x = 0.05 * (1.0 - i / (levels + 1))
            half_y = 0.028 * (1.0 - i / (levels + 1))
            verts.extend([
                (x - half_x, y - half_y, z),
                (x + half_x, y - half_y, z),
                (x + half_x, y + half_y, z),
                (x - half_x, y + half_y, z),
            ])
        for i in range(levels - 1):
            a, b = i * 4, (i + 1) * 4
            faces.extend([
                (a, a + 1, b + 1, b),
                (a + 1, a + 2, b + 2, b + 1),
                (a + 2, a + 3, b + 3, b + 2),
                (a + 3, a, b, b + 3),
            ])
        faces.append((0, 3, 2, 1))
        last = (levels - 1) * 4
        faces.append((last, last + 1, last + 2, last + 3))
        mesh = bpy.data.meshes.new(f"Hair_Strand_{index:02d}_Mesh")
        mesh.from_pydata(verts, [], faces)
        obj = bpy.data.objects.new(f"Hair_Strand_{index:02d}", mesh)
        collection.objects.link(obj)
        obj.data.materials.append(mat)
        obj["gsmb_type"] = "HAIR_STRAND"
        objects.append(obj)
    return objects


def create_skirt_mesh(collection, mat):
    segments, rings = 12, 4
    verts, faces = [], []
    for r in range(rings):
        t = r / (rings - 1)
        radius = 0.30 + t * 0.18
        z = 1.02 - t * 0.58
        for s in range(segments):
            angle = 2.0 * 3.141592653589793 * s / segments
            verts.append((radius * __import__('math').cos(angle), radius * __import__('math').sin(angle), z))
    for r in range(rings - 1):
        for s in range(segments):
            a = r * segments + s
            b = r * segments + (s + 1) % segments
            c = (r + 1) * segments + (s + 1) % segments
            d = (r + 1) * segments + s
            faces.append((a, b, c, d))
    mesh = bpy.data.meshes.new("Skirt_Main_Mesh")
    mesh.from_pydata(verts, [], faces)
    obj = bpy.data.objects.new("Skirt_Main", mesh)
    collection.objects.link(obj)
    obj.data.materials.append(mat)
    obj["gsmb_type"] = "SKIRT_RING"
    return obj


class GSMB_OT_build_test_asset(bpy.types.Operator):
    bl_idname = "gsmb.build_test_asset"
    bl_label = "Build Test Character"
    bl_description = "Create a low-poly humanoid, hair ribbons, skirt and collider proxies"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        collection = ensure_collection(GENERATED_COLLECTION)
        for obj in list(collection.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        skin = material("GSMB_Skin", (0.72, 0.48, 0.34))
        cloth = material("GSMB_Cloth", (0.12, 0.22, 0.55))
        hair = material("GSMB_Hair", (0.055, 0.025, 0.02), roughness=0.35)
        collider = material("GSMB_Collider", (0.05, 0.8, 0.45), metallic=0.1)
        arm = create_armature(collection)
        add_capsule("Body_Torso", (0, 0, 1.25), 0.23, 0.45, cloth, collection)
        add_uv_sphere("Body_Head", (0, 0, 1.68), (0.22, 0.19, 0.25), skin, collection)
        add_capsule("Body_Leg_L", (0.13, 0, 0.55), 0.10, 0.65, skin, collection)
        add_capsule("Body_Leg_R", (-0.13, 0, 0.55), 0.10, 0.65, skin, collection)
        create_hair_mesh(collection, hair)
        create_skirt_mesh(collection, cloth)
        collider_specs = [
            ("GSMB_COL_Head", (0, 0, 1.68), 0.23, 0.18, "head"),
            ("GSMB_COL_Chest", (0, 0, 1.28), 0.25, 0.38, "spine"),
            ("GSMB_COL_Pelvis", (0, 0, 0.96), 0.27, 0.18, "pelvis"),
            ("GSMB_COL_Thigh_L", (0.13, 0, 0.62), 0.11, 0.48, "thigh_L"),
            ("GSMB_COL_Thigh_R", (-0.13, 0, 0.62), 0.11, 0.48, "thigh_R"),
        ]
        for name, loc, radius, depth, bone in collider_specs:
            obj = add_capsule(name, loc, radius, depth, collider, collection)
            obj["gsmb_collider"] = True
            obj["gsmb_bone"] = bone
            obj.hide_render = True
        # Keep the complete fixture moving as one character when the armature
        # object is translated by the motion-test operator.
        for obj in collection.objects:
            if obj != arm:
                obj.parent = arm
                obj.matrix_parent_inverse = arm.matrix_world.inverted()
        arm.show_in_front = True
        context.scene["gsmb_last_analysis"] = "Test asset created: 5 hair strands, 1 skirt, 5 colliders"
        self.report({'INFO'}, "GSMB test character created")
        return {'FINISHED'}


class GSMB_OT_analyze_scene(bpy.types.Operator):
    bl_idname = "gsmb.analyze_scene"
    bl_label = "Analyze Secondary Meshes"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        hair, skirts, unknown = [], [], []
        armatures = [o for o in context.scene.objects if o.type == 'ARMATURE']
        reference_z = max((o.dimensions.z for o in armatures), default=1.8)
        for obj in context.scene.objects:
            if obj.type != 'MESH' or obj.get("gsmb_collider"):
                continue
            name = obj.name.lower()
            explicit = obj.get("gsmb_type", "")
            if explicit == "HAIR_STRAND" or any(k in name for k in ("hair", "bang", "braid")):
                hair.append(obj.name)
                obj["gsmb_detected_type"] = "HAIR_STRAND"
                obj["gsmb_confidence"] = 0.95 if explicit else 0.78
            elif explicit == "SKIRT_RING" or any(k in name for k in ("skirt", "dress", "cape")):
                skirts.append(obj.name)
                obj["gsmb_detected_type"] = "SKIRT_RING"
                obj["gsmb_confidence"] = 0.95 if explicit else 0.78
            elif obj.dimensions.z > reference_z * 0.15:
                unknown.append(obj.name)
        summary = f"Hair: {len(hair)} | Skirts: {len(skirts)} | Review: {len(unknown)}"
        context.scene["gsmb_last_analysis"] = summary
        self.report({'INFO'}, summary)
        return {'FINISHED'}


def remove_secondary_bones(arm):
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for bone in list(arm.data.edit_bones):
        if bone.name.startswith(("GSMB_Hair_", "GSMB_Skirt_")):
            arm.data.edit_bones.remove(bone)
    bpy.ops.object.mode_set(mode='OBJECT')


def add_chain(arm, prefix, points, parent_name):
    created = []
    parent = arm.data.edit_bones.get(parent_name)
    for index in range(len(points) - 1):
        bone = arm.data.edit_bones.new(f"{prefix}_{index + 1:02d}")
        bone.head = points[index]
        bone.tail = points[index + 1]
        bone.parent = created[-1] if created else parent
        bone.use_connect = bool(created)
        bone["gsmb_secondary"] = True
        created.append(bone)
    return created


def add_armature_modifier(obj, arm):
    modifier = next((m for m in obj.modifiers if m.type == 'ARMATURE'), None)
    if modifier is None:
        modifier = obj.modifiers.new("GSMB Armature", 'ARMATURE')
    modifier.object = arm


class GSMB_OT_generate_secondary_rig(bpy.types.Operator):
    bl_idname = "gsmb.generate_secondary_rig"
    bl_label = "Generate Secondary Rig"
    bl_description = "Generate lightweight hair and skirt bone chains with starter weights"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        arm = context.scene.objects.get("GSMB_TestRig")
        if arm is None:
            self.report({'ERROR'}, "Build the test character first")
            return {'CANCELLED'}
        remove_secondary_bones(arm)
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode='EDIT')
        hair_objects = sorted(
            (o for o in context.scene.objects if o.get("gsmb_detected_type") == "HAIR_STRAND"),
            key=lambda obj: obj.name,
        )
        for strand_index, obj in enumerate(hair_objects, 1):
            world_verts = [obj.matrix_world @ v.co for v in obj.data.vertices]
            top = max(v.z for v in world_verts)
            bottom = min(v.z for v in world_verts)
            x = sum(v.x for v in world_verts) / len(world_verts)
            y_top = sum(v.y for v in world_verts if v.z > top - 0.05) / max(1, sum(1 for v in world_verts if v.z > top - 0.05))
            points = [Vector((x, y_top + i * 0.025, top + (bottom - top) * i / 3)) for i in range(4)]
            bones = add_chain(arm, f"GSMB_Hair_{strand_index:02d}", points, "head")
            bone_names = [b.name for b in bones]
            bpy.ops.object.mode_set(mode='OBJECT')
            for group_name in bone_names:
                old = obj.vertex_groups.get(group_name)
                if old:
                    obj.vertex_groups.remove(old)
                obj.vertex_groups.new(name=group_name)
            height = max(top - bottom, 0.001)
            for vertex in obj.data.vertices:
                world_z = (obj.matrix_world @ vertex.co).z
                t = min(0.999, max(0.0, (top - world_z) / height))
                index = min(len(bone_names) - 1, int(t * len(bone_names)))
                obj.vertex_groups[bone_names[index]].add([vertex.index], 1.0, 'REPLACE')
            add_armature_modifier(obj, arm)
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode='EDIT')
        skirt = next((o for o in context.scene.objects if o.get("gsmb_detected_type") == "SKIRT_RING"), None)
        skirt_bone_names = []
        if skirt:
            world_verts = [skirt.matrix_world @ v.co for v in skirt.data.vertices]
            top, bottom = max(v.z for v in world_verts), min(v.z for v in world_verts)
            for chain_index in range(8):
                angle = 2 * pi * chain_index / 8
                points = []
                for level in range(4):
                    t = level / 3
                    radius = 0.30 + 0.18 * t
                    points.append(Vector((radius * __import__('math').cos(angle), radius * __import__('math').sin(angle), top + (bottom - top) * t)))
                bones = add_chain(arm, f"GSMB_Skirt_{chain_index + 1:02d}", points, "pelvis")
                skirt_bone_names.append([b.name for b in bones])
            bpy.ops.object.mode_set(mode='OBJECT')
            for names in skirt_bone_names:
                for group_name in names:
                    old = skirt.vertex_groups.get(group_name)
                    if old:
                        skirt.vertex_groups.remove(old)
                    skirt.vertex_groups.new(name=group_name)
            height = max(top - bottom, 0.001)
            for vertex in skirt.data.vertices:
                world = skirt.matrix_world @ vertex.co
                angle = (atan2(world.y, world.x) + 2 * pi) % (2 * pi)
                chain_index = int((angle / (2 * pi) * 8) + 0.5) % 8
                t = min(0.999, max(0.0, (top - world.z) / height))
                segment = min(2, int(t * 3))
                skirt.vertex_groups[skirt_bone_names[chain_index][segment]].add([vertex.index], 1.0, 'REPLACE')
            add_armature_modifier(skirt, arm)
        context.scene["gsmb_last_analysis"] = f"Rigged: {len(hair_objects)} hair chains + {len(skirt_bone_names)} skirt chains"
        self.report({'INFO'}, context.scene["gsmb_last_analysis"])
        return {'FINISHED'}


class GSMB_OT_create_motion_test(bpy.types.Operator):
    bl_idname = "gsmb.create_motion_test"
    bl_label = "Run Forward / Back Test"
    bl_description = "Create a looping forward/back movement and play it immediately"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        arm = context.scene.objects.get("GSMB_TestRig")
        if arm is None:
            self.report({'ERROR'}, "Build the test character first")
            return {'CANCELLED'}
        scene = context.scene
        scene.frame_start = 1
        scene.frame_end = 80
        arm.animation_data_clear()
        arm.location = (0.0, 0.0, 0.0)
        motion = ((1, 0.0), (20, 1.2), (40, 0.0), (60, -1.2), (80, 0.0))
        for frame, y in motion:
            arm.location.y = y
            arm.keyframe_insert(data_path="location", frame=frame, index=1)
        if arm.animation_data and arm.animation_data.action:
            for fcurve in arm.animation_data.action.fcurves:
                for point in fcurve.keyframe_points:
                    point.interpolation = 'SINE'
        # Bake a clearly visible, depth-delayed secondary response. This is a
        # deterministic preview curve; the runtime spring solver replaces it later.
        bpy.context.view_layer.objects.active = arm
        arm.select_set(True)
        bpy.ops.object.mode_set(mode='POSE')
        secondary = [p for p in arm.pose.bones if p.bone.get("gsmb_secondary")]
        for pose_bone in secondary:
            pose_bone.rotation_mode = 'XYZ'
            pose_bone.rotation_euler = (0.0, 0.0, 0.0)
            depth = int(pose_bone.name.rsplit("_", 1)[-1])
            strength = 0.09 + depth * 0.035
            for frame, sign in ((1, 0), (14, -1), (26, 1), (34, 0), (46, 1), (66, -1), (80, 0)):
                pose_bone.rotation_euler.x = sign * strength
                pose_bone.keyframe_insert(data_path="rotation_euler", frame=frame, index=0)
        bpy.ops.object.mode_set(mode='OBJECT')
        scene.frame_set(1)
        scene["gsmb_last_analysis"] = "Playing forward/back test (frames 1-80)"
        # Start playback when a 3D View context is available.
        try:
            bpy.ops.screen.animation_play()
        except RuntimeError:
            pass
        self.report({'INFO'}, "Forward/back test created and playback started")
        return {'FINISHED'}


class GSMB_OT_mouse_follow_test(bpy.types.Operator):
    bl_idname = "gsmb.mouse_follow_test"
    bl_label = "Mouse Follow Test (Esc to Exit)"
    bl_description = "Move the whole character with the mouse and preview velocity-based secondary sway"
    bl_options = {'REGISTER', 'UNDO'}

    sensitivity: bpy.props.FloatProperty(default=0.004, min=0.0005, max=0.02)

    def invoke(self, context, event):
        if context.area.type != 'VIEW_3D':
            self.report({'ERROR'}, "Run this test from a 3D View")
            return {'CANCELLED'}
        arm = context.scene.objects.get("GSMB_TestRig")
        if arm is None:
            self.report({'ERROR'}, "Build the test character first")
            return {'CANCELLED'}
        if context.screen.is_animation_playing:
            bpy.ops.screen.animation_cancel(restore_frame=False)
        # The deterministic forward/back test writes keyframes to this same
        # armature. Remove that action so it cannot overwrite realtime poses.
        arm.animation_data_clear()
        for pose_bone in arm.pose.bones:
            if pose_bone.bone.get("gsmb_secondary"):
                pose_bone.rotation_mode = 'XYZ'
                pose_bone.rotation_euler = (0.0, 0.0, 0.0)
        self.arm = arm
        self.start_location = arm.location.copy()
        self.start_mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        self.view_depth_world = arm.matrix_world.translation.copy()
        mouse_world = view3d_utils.region_2d_to_location_3d(
            context.region,
            context.region_data,
            self.start_mouse,
            self.view_depth_world,
        )
        self.mouse_world_offset = arm.matrix_world.translation - mouse_world
        self.previous_target = arm.location.copy()
        self.current_target = arm.location.copy()
        self.previous_arm_location = arm.location.copy()
        self.previous_velocity = Vector((0.0, 0.0, 0.0))
        self.filtered_velocity = Vector((0.0, 0.0, 0.0))
        self.last_time = time.perf_counter()
        self.spring_states = {}
        self.original_rotations = {
            bone.name: bone.rotation_euler.copy()
            for bone in arm.pose.bones
            if bone.bone.get("gsmb_secondary")
        }
        for bone in arm.pose.bones:
            if bone.bone.get("gsmb_secondary"):
                self.spring_states[bone.name] = {
                    "angle": Vector((bone.rotation_euler.x, bone.rotation_euler.y)),
                    "velocity": Vector((0.0, 0.0)),
                }
        self.colliders = [obj for obj in context.scene.objects if obj.get("gsmb_collider")]
        for collider in self.colliders:
            collider.hide_viewport = False
            collider.show_in_front = True
        context.view_layer.update()
        self.collision_pairs = {}
        for bone in arm.pose.bones:
            if not bone.bone.get("gsmb_secondary"):
                continue
            tip_world = arm.matrix_world @ bone.tail
            allowed = []
            for collider in self.colliders:
                center = collider.matrix_world.translation
                proxy_radius = max(collider.dimensions.x, collider.dimensions.y) * 0.5
                # Ignore collider/bone pairs already overlapping in the authored
                # rest pose (for example hair roots embedded in the scalp).
                if (tip_world - center).length >= proxy_radius + context.scene.gsmb_collision_radius:
                    allowed.append(collider.name)
            self.collision_pairs[bone.name] = set(allowed)
        self.timer = context.window_manager.event_timer_add(1.0 / 60.0, window=context.window)
        context.scene["gsmb_last_analysis"] = "Mouse follow active — move mouse, Esc to exit"
        context.window.cursor_set('SCROLL_XY')
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set("GSMB Mouse Test: move mouse to drive character • Esc/right-click to exit")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            self._finish(context, restore=True)
            return {'CANCELLED'}
        if event.type == 'MOUSEMOVE':
            mouse = Vector((event.mouse_region_x, event.mouse_region_y))
            mouse_world = view3d_utils.region_2d_to_location_3d(
                context.region,
                context.region_data,
                mouse,
                self.view_depth_world,
            )
            target_world = mouse_world + self.mouse_world_offset
            if self.arm.parent:
                self.current_target = self.arm.parent.matrix_world.inverted() @ target_world
            else:
                self.current_target = target_world
            return {'RUNNING_MODAL'}
        if event.type == 'TIMER':
            now = time.perf_counter()
            dt = max(1.0 / 240.0, min(1.0 / 20.0, now - self.last_time))
            self.last_time = now
            self._step_physics(context, dt)
        return {'RUNNING_MODAL'}

    def _step_physics(self, context, dt):
        scene = context.scene
        self.arm.location = self.current_target
        current = self.arm.location.copy()
        velocity = (current - self.previous_arm_location) / dt
        velocity = self.previous_velocity.lerp(velocity, 0.28)
        acceleration = (velocity - self.previous_velocity) / dt
        self.previous_arm_location = current
        self.previous_velocity = velocity
        stiffness = scene.gsmb_stiffness
        damping = scene.gsmb_damping
        inertia = scene.gsmb_inertia
        gravity = scene.gsmb_gravity
        limit = scene.gsmb_angle_limit
        collision_radius = scene.gsmb_collision_radius
        collisions = 0
        for bone in self.arm.pose.bones:
            state = self.spring_states.get(bone.name)
            if state is None:
                continue
            depth = int(bone.name.rsplit("_", 1)[-1])
            depth_scale = 0.65 + depth * 0.22
            if bone.name.startswith("GSMB_Skirt_"):
                depth_scale *= 1.45
            angle = state["angle"]
            angular_velocity = state["velocity"]
            # Continuous velocity lag keeps mouse-driven motion readable;
            # acceleration adds the sharper kick on starts, stops and reversals.
            external = Vector((
                (-velocity.z * 3.2 - acceleration.z * 0.004) * inertia,
                (velocity.x * 3.2 + acceleration.x * 0.004) * inertia,
            )) * depth_scale
            if bone.name.startswith("GSMB_Hair_"):
                external.x += gravity * 0.025 * depth_scale
            angular_acceleration = (-stiffness * angle) - (damping * angular_velocity) + external
            angular_velocity += angular_acceleration * dt
            angle += angular_velocity * dt
            angle.x = max(-limit, min(limit, angle.x))
            angle.y = max(-limit, min(limit, angle.y))
            bone.rotation_mode = 'XYZ'
            bone.rotation_euler.x = angle.x
            bone.rotation_euler.y = angle.y
            state["angle"] = angle
            state["velocity"] = angular_velocity
        context.view_layer.update()
        # Lightweight game-style collision: treat proxy bounds as spheres and
        # push angular velocity away when a simulated bone tip penetrates.
        for bone in self.arm.pose.bones:
            state = self.spring_states.get(bone.name)
            if state is None:
                continue
            tip_world = self.arm.matrix_world @ bone.tail
            for collider in self.colliders:
                if collider.name not in self.collision_pairs.get(bone.name, set()):
                    continue
                center = collider.matrix_world.translation
                proxy_radius = max(collider.dimensions.x, collider.dimensions.y) * 0.5
                delta = tip_world - center
                distance = delta.length
                minimum = proxy_radius + collision_radius
                if 1e-5 < distance < minimum:
                    penetration = (minimum - distance) / minimum
                    away = delta.normalized()
                    impulse = penetration * 14.0
                    state["velocity"].x += away.z * impulse
                    state["velocity"].y -= away.x * impulse
                    state["angle"].x = max(-limit, min(limit, state["angle"].x + away.z * penetration * 0.12))
                    state["angle"].y = max(-limit, min(limit, state["angle"].y - away.x * penetration * 0.12))
                    collisions += 1
        hair_angle = max(
            (state["angle"].length for name, state in self.spring_states.items() if name.startswith("GSMB_Hair_")),
            default=0.0,
        )
        skirt_angle = max(
            (state["angle"].length for name, state in self.spring_states.items() if name.startswith("GSMB_Skirt_")),
            default=0.0,
        )
        scene["gsmb_runtime_stats"] = (
            f"Speed {velocity.length:.2f} | Hair {hair_angle:.2f} | Skirt {skirt_angle:.2f} | Hits {collisions}"
        )
        if context.area:
            context.area.tag_redraw()


def chaikin_closed(points, iterations=2):
    result = [Vector(p) for p in points]
    for _ in range(iterations):
        refined = []
        for index, point in enumerate(result):
            next_point = result[(index + 1) % len(result)]
            refined.extend((point.lerp(next_point, 0.25), point.lerp(next_point, 0.75)))
        result = refined
    return result


class GSMB_OT_surface_loop_cut(bpy.types.Operator):
    bl_idname = "gsmb.surface_loop_cut"
    bl_label = "Draw Surface Cut Loop"
    bl_description = "Click points on a mesh surface; click the first point to close, cut and separate"
    bl_options = {'REGISTER', 'UNDO'}

    snap_distance: bpy.props.FloatProperty(default=18.0, min=6.0, max=40.0)

    def invoke(self, context, event):
        if context.area.type != 'VIEW_3D':
            self.report({'ERROR'}, "Run the cutter from a 3D View")
            return {'CANCELLED'}
        target = context.active_object
        if target is None or target.type != 'MESH':
            self.report({'ERROR'}, "Select one mesh object to cut")
            return {'CANCELLED'}
        if context.scene.gsmb_cut_method == 'EXACT':
            only_selected = False
            if target.mode == 'EDIT':
                current_mesh = bmesh.from_edit_mesh(target.data)
                only_selected = any(face.select for face in current_mesh.faces)
            else:
                if target.mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_all(action='DESELECT')
            edit_mesh = bmesh.from_edit_mesh(target.data)
            preknife_layer = edit_mesh.edges.layers.int.get("gsmb_preknife")
            if preknife_layer is None:
                preknife_layer = edit_mesh.edges.layers.int.new("gsmb_preknife")
            for edge in edit_mesh.edges:
                edge[preknife_layer] = 1
            bmesh.update_edit_mesh(target.data)
            scope = "selected garment faces only" if only_selected else "whole mesh object"
            context.scene["gsmb_cut_status"] = f"Native Knife active on {scope}: draw, then Enter"
            return bpy.ops.mesh.knife_tool(
                'INVOKE_DEFAULT',
                use_occlude_geometry=not context.scene.gsmb_cut_through,
                only_selected=only_selected,
            )
        if target.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        self.target = target
        self.screen_points = []
        self.world_points = []
        self.draw_region = context.region
        self.draw_region_3d = context.region_data
        self.hover = Vector((event.mouse_region_x, event.mouse_region_y))
        self.draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_overlay, (), 'WINDOW', 'POST_PIXEL'
        )
        context.window_manager.modal_handler_add(self)
        context.window.cursor_set('KNIFE')
        context.workspace.status_text_set(
            "Surface Cutter: LMB add points • click first point to close/cut • Backspace undo • Esc cancel"
        )
        context.scene["gsmb_cut_status"] = "Drawing: click at least 3 surface points"
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if getattr(self, "exact_prepare", False):
            if event.type in {'ESC', 'RIGHTMOUSE'}:
                context.window.cursor_set('DEFAULT')
                context.workspace.status_text_set(None)
                context.scene["gsmb_cut_status"] = "Exact Knife cancelled"
                return {'CANCELLED'}
            if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
                return {'PASS_THROUGH'}
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                mouse = Vector((event.mouse_region_x, event.mouse_region_y))
                hit = self._raycast_target(context, mouse)
                if hit is None:
                    self.report({'WARNING'}, "Start point must be on the selected mesh")
                    return {'RUNNING_MODAL'}
                marker = bpy.data.objects.new("KNIFE START - CLOSE HERE", None)
                marker.empty_display_type = 'SPHERE'
                marker.empty_display_size = max(self.target.dimensions) * 0.08
                marker.location = hit
                marker.color = (0.05, 1.0, 0.12, 1.0)
                marker.show_in_front = True
                marker.show_name = True
                context.scene.collection.objects.link(marker)
                context.window.cursor_set('DEFAULT')
                context.workspace.status_text_set(None)
                for obj in context.selected_objects:
                    obj.select_set(False)
                self.target.select_set(True)
                context.view_layer.objects.active = self.target
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_all(action='DESELECT')
                edit_mesh = bmesh.from_edit_mesh(self.target.data)
                EXACT_KNIFE_SESSION.clear()
                EXACT_KNIFE_SESSION.update({
                    "target": self.target.name,
                    "initial_edges": len(edit_mesh.edges),
                    "started": time.monotonic(),
                    "window": context.window,
                    "area": context.area,
                    "region": context.region,
                    "region_3d": context.region_data,
                    "start_world": hit.copy(),
                    "phase": "START_PENDING",
                })
                # Do not register a POST_PIXEL handler here. Blender 4.5 can
                # invalidate modal draw callbacks during addon reload/crash
                # recovery. The large always-in-front Empty is the stable marker.
                overlay = context.space_data.overlay
                if hasattr(overlay, "show_edges"):
                    EXACT_KNIFE_SESSION["show_edges"] = overlay.show_edges
                    overlay.show_edges = False
                if hasattr(overlay, "show_wireframes"):
                    EXACT_KNIFE_SESSION["show_wireframes"] = overlay.show_wireframes
                    overlay.show_wireframes = False
                context.scene["gsmb_cut_status"] = "Starting Exact Knife…"
                if not bpy.app.timers.is_registered(launch_exact_knife):
                    bpy.app.timers.register(launch_exact_knife, first_interval=0.05)
                # End this preparatory modal completely. The timer launches the
                # native Knife on the next event-loop turn, avoiding nested modal conflict.
                return {'FINISHED'}
            return {'RUNNING_MODAL'}
        # Preserve Blender's native viewport navigation while the cutter is
        # active: MMB rotate, Shift+MMB pan, Ctrl+MMB zoom, wheel and NDOF.
        if event.type in {
            'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE',
            'NDOF_MOTION', 'NDOF_BUTTON_FIT',
            'NUMPAD_1', 'NUMPAD_2', 'NUMPAD_3', 'NUMPAD_4', 'NUMPAD_5',
            'NUMPAD_6', 'NUMPAD_7', 'NUMPAD_8', 'NUMPAD_9',
        }:
            if context.area:
                context.area.tag_redraw()
            return {'PASS_THROUGH'}
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            self._cleanup(context)
            context.scene["gsmb_cut_status"] = "Cut cancelled"
            return {'CANCELLED'}
        if event.type == 'BACK_SPACE' and event.value == 'PRESS':
            if self.screen_points:
                self.screen_points.pop()
                self.world_points.pop()
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            if len(self.world_points) < 3:
                self.report({'WARNING'}, "Place at least three surface points before Enter")
                return {'RUNNING_MODAL'}
            return self._finish_drawn_cut(context)
        if event.type == 'MOUSEMOVE':
            self.hover = Vector((event.mouse_region_x, event.mouse_region_y))
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            mouse = Vector((event.mouse_region_x, event.mouse_region_y))
            projected = self._projected_points()
            if len(projected) >= 3 and projected[0] is not None and (mouse - projected[0]).length <= self.snap_distance:
                return self._finish_drawn_cut(context)
            hit = self._raycast_target(context, mouse)
            if hit is None:
                self.report({'WARNING'}, "Point must be on the selected mesh")
                return {'RUNNING_MODAL'}
            self.screen_points.append(mouse)
            self.world_points.append(hit)
            context.scene["gsmb_cut_status"] = f"Drawing: {len(self.screen_points)} points"
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        return {'RUNNING_MODAL'}

    def _finish_drawn_cut(self, context):
        try:
            self._apply_cut(context)
        except Exception as exc:
            self.report({'ERROR'}, f"Surface cut failed: {exc}")
            self._cleanup(context)
            context.scene["gsmb_cut_status"] = f"Failed: {exc}"
            return {'CANCELLED'}
        self._cleanup(context)
        if not context.scene.get("gsmb_cut_status", "").startswith("Surface cut"):
            context.scene["gsmb_cut_status"] = "Cut complete: created a separate object"
        self.report({'INFO'}, "Surface loop closed from last point to first and separated")
        return {'FINISHED'}

    def _raycast_target(self, context, point):
        origin_world = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, point)
        direction_world = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, point)
        inverse = self.target.matrix_world.inverted()
        origin_local = inverse @ origin_world
        direction_local = (inverse.to_3x3() @ direction_world).normalized()
        hit, location_local, _normal, _face = self.target.ray_cast(origin_local, direction_local)
        return self.target.matrix_world @ location_local if hit else None

    def _path_points(self, context):
        if context.scene.gsmb_cut_mode == 'LINE':
            return list(self.world_points)
        projected = [point for point in self._projected_points() if point is not None]
        smooth_screen = chaikin_closed(projected, iterations=2)
        smooth_world = []
        for point in smooth_screen:
            hit = self._raycast_target(context, point)
            if hit is not None:
                smooth_world.append(hit)
        return smooth_world if len(smooth_world) >= 6 else list(self.world_points)

    def _projected_points(self):
        return [
            view3d_utils.location_3d_to_region_2d(self.draw_region, self.draw_region_3d, point)
            for point in self.world_points
        ]

    def _apply_cut(self, context):
        if context.scene.gsmb_cut_method == 'SURFACE':
            self._apply_surface_topology_cut(context)
            return
        world_points = self._path_points(context)
        if len(world_points) < 3:
            raise RuntimeError("not enough valid surface points")
        curve_data = bpy.data.curves.new("GSMB_SurfaceCut_Path", 'CURVE')
        curve_data.dimensions = '3D'
        curve_data.resolution_u = 1
        spline = curve_data.splines.new('POLY')
        spline.points.add(len(world_points) - 1)
        for point, world in zip(spline.points, world_points):
            point.co = (*world, 1.0)
        spline.use_cyclic_u = True
        cutter = bpy.data.objects.new("GSMB_SurfaceCut_Path", curve_data)
        context.scene.collection.objects.link(cutter)
        for obj in context.selected_objects:
            obj.select_set(False)
        self.target.select_set(True)
        cutter.select_set(True)
        context.view_layer.objects.active = self.target
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='DESELECT')
        result = bpy.ops.mesh.knife_project(cut_through=False)
        if 'FINISHED' not in result:
            bpy.ops.object.mode_set(mode='OBJECT')
            raise RuntimeError("Knife Project did not finish; align the view to the cut area")
        # Blender 4.5 leaves the interior faces selected, but the current mesh
        # selection mode may still be vertex/edge. Flush that face selection so
        # Separate receives a valid region rather than cancelling.
        context.tool_settings.mesh_select_mode = (False, False, True)
        edit_mesh = bmesh.from_edit_mesh(self.target.data)
        edit_mesh.select_flush(True)
        bmesh.update_edit_mesh(self.target.data)
        selected_faces = sum(1 for face in edit_mesh.faces if face.select)
        if selected_faces == 0 or selected_faces == len(edit_mesh.faces):
            bpy.ops.object.mode_set(mode='OBJECT')
            raise RuntimeError("cut did not produce a separable interior region")
        bpy.ops.mesh.separate(type='SELECTED')
        bpy.ops.object.mode_set(mode='OBJECT')
        pieces = [obj for obj in context.selected_objects if obj.type == 'MESH']
        for index, piece in enumerate(pieces, 1):
            if piece != self.target:
                piece.name = f"{self.target.name}_Cut_{index:02d}"
        bpy.data.objects.remove(cutter, do_unlink=True)

    def _apply_surface_topology_cut(self, context):
        mesh = self.target.data
        if len(self.world_points) < 3:
            raise RuntimeError("not enough surface anchors")
        inverse = self.target.matrix_world.inverted()
        local_points = [inverse @ point for point in self.world_points]
        # Surface anchors snap to the nearest base-mesh vertices. This keeps the
        # resulting boundary valid and avoids view-projection self intersections.
        anchors = []
        for point in local_points:
            nearest = min(mesh.vertices, key=lambda vertex: (vertex.co - point).length_squared).index
            if not anchors or anchors[-1] != nearest:
                anchors.append(nearest)
        if len(anchors) < 3:
            raise RuntimeError("anchors collapsed to fewer than three mesh vertices")
        adjacency = [[] for _ in mesh.vertices]
        edge_lookup = {}
        for edge in mesh.edges:
            a, b = edge.vertices
            weight = (mesh.vertices[a].co - mesh.vertices[b].co).length
            adjacency[a].append((b, weight))
            adjacency[b].append((a, weight))
            edge_lookup[frozenset((a, b))] = edge.index

        def shortest_path(start, goal):
            segment_start = mesh.vertices[start].co
            segment_end = mesh.vertices[goal].co
            segment = segment_end - segment_start
            segment_length_squared = max(segment.length_squared, 1e-10)
            segment_scale = max(segment.length, 1e-5)

            def deviation_penalty(a, b):
                midpoint = (mesh.vertices[a].co + mesh.vertices[b].co) * 0.5
                t = max(0.0, min(1.0, (midpoint - segment_start).dot(segment) / segment_length_squared))
                nearest = segment_start + segment * t
                normalized = (midpoint - nearest).length / segment_scale
                return 1.0 + 8.0 * normalized * normalized

            distances = {start: 0.0}
            previous = {}
            queue = [(0.0, start)]
            while queue:
                distance, vertex = heapq.heappop(queue)
                if distance != distances.get(vertex):
                    continue
                if vertex == goal:
                    path = [goal]
                    while path[-1] != start:
                        path.append(previous[path[-1]])
                    path.reverse()
                    return path
                for neighbor, weight in adjacency[vertex]:
                    candidate = distance + weight * deviation_penalty(vertex, neighbor)
                    if candidate < distances.get(neighbor, float('inf')):
                        distances[neighbor] = candidate
                        previous[neighbor] = vertex
                        heapq.heappush(queue, (candidate, neighbor))
            raise RuntimeError("surface anchors are on disconnected mesh islands")

        boundary_edges = set()
        full_path = []
        for index, start in enumerate(anchors):
            goal = anchors[(index + 1) % len(anchors)]
            path = shortest_path(start, goal)
            full_path.extend(path[:-1])
            for a, b in zip(path, path[1:]):
                boundary_edges.add(edge_lookup[frozenset((a, b))])
        edge_faces = {edge.index: [] for edge in mesh.edges}
        for polygon in mesh.polygons:
            for edge_index in polygon.edge_keys:
                mesh_edge = edge_lookup.get(frozenset(edge_index))
                if mesh_edge is not None:
                    edge_faces[mesh_edge].append(polygon.index)
        face_graph = [set() for _ in mesh.polygons]
        touched_faces = set()
        for edge_index, faces in edge_faces.items():
            if edge_index in boundary_edges:
                touched_faces.update(faces)
                continue
            if len(faces) == 2:
                a, b = faces
                face_graph[a].add(b)
                face_graph[b].add(a)
        components = []
        unvisited = set(range(len(mesh.polygons)))
        while unvisited:
            seed = next(iter(unvisited))
            stack = [seed]
            component = set()
            while stack:
                face = stack.pop()
                if face not in unvisited:
                    continue
                unvisited.remove(face)
                component.add(face)
                stack.extend(face_graph[face] & unvisited)
            components.append(component)
        candidates = [component for component in components if component & touched_faces]
        if len(boundary_edges) < 3 or len(candidates) < 2:
            raise RuntimeError(
                "approximate topology path did not form a valid loop; use Exact Draw for garment seams"
            )
        selected = min(
            candidates,
            key=lambda component: sum(mesh.polygons[index].area for index in component),
        )
        for obj in context.selected_objects:
            obj.select_set(False)
        self.target.select_set(True)
        context.view_layer.objects.active = self.target
        bpy.ops.object.mode_set(mode='EDIT')
        edit_mesh = bmesh.from_edit_mesh(mesh)
        edit_mesh.faces.ensure_lookup_table()
        for face in edit_mesh.faces:
            face.select = face.index in selected
        edit_mesh.select_flush(True)
        bmesh.update_edit_mesh(mesh)
        result = bpy.ops.mesh.separate(type='SELECTED')
        bpy.ops.object.mode_set(mode='OBJECT')
        if 'FINISHED' not in result:
            raise RuntimeError("surface region separation failed")
        pieces = [obj for obj in context.selected_objects if obj.type == 'MESH']
        for index, piece in enumerate(pieces, 1):
            if piece != self.target:
                piece.name = f"{self.target.name}_SurfaceCut_{index:02d}"
        context.scene["gsmb_cut_status"] = (
            f"Surface cut: approximate topology loop, {len(anchors)} anchors"
        )

    def _planar_face_selection(self, mesh, points):
        # Newell's method fits a stable normal to an ordered, approximately
        # planar loop. This is ideal for waist/skirt/sleeve rings when coarse
        # topology makes shortest edge paths branch or double back.
        center = sum(points, Vector((0.0, 0.0, 0.0))) / len(points)
        normal = Vector((0.0, 0.0, 0.0))
        for index, point in enumerate(points):
            next_point = points[(index + 1) % len(points)]
            normal.x += (point.y - next_point.y) * (point.z + next_point.z)
            normal.y += (point.z - next_point.z) * (point.x + next_point.x)
            normal.z += (point.x - next_point.x) * (point.y + next_point.y)
        if normal.length < 1e-6:
            for index in range(1, len(points) - 1):
                candidate = (points[index] - points[0]).cross(points[index + 1] - points[0])
                if candidate.length >= 1e-6:
                    normal = candidate
                    break
        if normal.length < 1e-6:
            raise RuntimeError("surface loop is too flat or self-crossing to fit a cut plane")
        normal.normalize()
        positive = {
            polygon.index
            for polygon in mesh.polygons
            if (polygon.center - center).dot(normal) >= 0.0
        }
        negative = set(range(len(mesh.polygons))) - positive
        if not positive or not negative:
            raise RuntimeError("fitted cut plane does not pass through the mesh")
        return min(
            (positive, negative),
            key=lambda faces: sum(mesh.polygons[index].area for index in faces),
        )

    def _draw_overlay(self):
        if not self.world_points:
            return
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        points = [point for point in self._projected_points() if point is not None]
        if not points:
            return
        preview = points + [self.hover]
        batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": preview})
        gpu.state.blend_set('ALPHA')
        gpu.state.line_width_set(2.5)
        shader.bind()
        shader.uniform_float("color", (0.15, 0.9, 1.0, 0.95))
        batch.draw(shader)
        point_batch = batch_for_shader(shader, 'POINTS', {"pos": points})
        gpu.state.point_size_set(8.0)
        shader.uniform_float("color", (1.0, 0.55, 0.1, 1.0))
        point_batch.draw(shader)
        first_batch = batch_for_shader(shader, 'POINTS', {"pos": [points[0]]})
        gpu.state.point_size_set(14.0)
        shader.uniform_float("color", (0.2, 1.0, 0.25, 1.0))
        first_batch.draw(shader)
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set('NONE')

    def _cleanup(self, context):
        if getattr(self, "draw_handle", None):
            bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle, 'WINDOW')
            self.draw_handle = None
        context.window.cursor_set('DEFAULT')
        context.workspace.status_text_set(None)
        if context.area:
            context.area.tag_redraw()


def _nearest_vertex_index(mesh, point):
    return min(mesh.vertices, key=lambda vertex: (vertex.co - point).length_squared).index


def _build_vertex_adjacency(mesh):
    adjacency = [[] for _ in mesh.vertices]
    for edge in mesh.edges:
        a, b = edge.vertices
        weight = (mesh.vertices[a].co - mesh.vertices[b].co).length
        adjacency[a].append((b, weight))
        adjacency[b].append((a, weight))
    return adjacency


def _build_edge_face_map(mesh):
    edge_faces = {}
    for polygon in mesh.polygons:
        for key in polygon.edge_keys:
            edge_faces.setdefault(frozenset(key), []).append(polygon.index)
    return edge_faces


def _corridor_path(mesh, adjacency, start, goal):
    # Corridor-weighted A* between two mesh vertices. The penalty keeps the
    # path hugging the straight anchor segment; the admissible euclidean
    # heuristic keeps runtime interactive on ~100k-edge decimated meshes.
    if start == goal:
        return [start]
    segment_start = mesh.vertices[start].co
    segment_end = mesh.vertices[goal].co
    segment = segment_end - segment_start
    segment_length_squared = max(segment.length_squared, 1e-10)
    segment_scale = max(segment.length, 1e-5)
    distances = {start: 0.0}
    previous = {}
    queue = [(segment_scale, 0.0, start)]
    while queue:
        _priority, distance, vertex = heapq.heappop(queue)
        if distance != distances.get(vertex):
            continue
        if vertex == goal:
            path = [goal]
            while path[-1] != start:
                path.append(previous[path[-1]])
            path.reverse()
            return path
        for neighbor, weight in adjacency[vertex]:
            midpoint = (mesh.vertices[vertex].co + mesh.vertices[neighbor].co) * 0.5
            t = max(0.0, min(1.0, (midpoint - segment_start).dot(segment) / segment_length_squared))
            nearest = segment_start + segment * t
            deviation = (midpoint - nearest).length / segment_scale
            candidate = distance + weight * (1.0 + 12.0 * deviation * deviation)
            if candidate < distances.get(neighbor, float('inf')):
                distances[neighbor] = candidate
                previous[neighbor] = vertex
                heuristic = (mesh.vertices[neighbor].co - segment_end).length
                heapq.heappush(queue, (candidate + heuristic, candidate, neighbor))
    raise RuntimeError("Two contour anchors lie on disconnected surface regions of the target mesh")


def _stroke_boundary(target, adjacency, world_points, closed):
    # Convert clicked world anchors into a chain of REAL mesh edges on the
    # target. The result is a barrier edge set plus a world-space polyline
    # for overlay display. `closed=True` connects the last anchor back to
    # the first automatically.
    mesh = target.data
    inverse = target.matrix_world.inverted()
    anchors = []
    for world in world_points:
        index = _nearest_vertex_index(mesh, inverse @ Vector(world))
        if not anchors or anchors[-1] != index:
            anchors.append(index)
    if closed and len(anchors) >= 2 and anchors[0] == anchors[-1]:
        anchors.pop()
    if len(anchors) < (3 if closed else 2):
        raise RuntimeError("Anchors collapsed onto too few distinct mesh vertices; place points farther apart")
    boundary_keys = set()
    vertex_path = []
    pair_count = len(anchors) if closed else len(anchors) - 1
    for index in range(pair_count):
        start = anchors[index]
        goal = anchors[(index + 1) % len(anchors)]
        segment_path = _corridor_path(mesh, adjacency, start, goal)
        for a, b in zip(segment_path, segment_path[1:]):
            boundary_keys.add(frozenset((a, b)))
        vertex_path.extend(segment_path if not vertex_path else segment_path[1:])
    if closed and vertex_path:
        vertex_path.append(vertex_path[0])
    if not boundary_keys:
        raise RuntimeError("The stroke produced no surface edges; place points farther apart")
    matrix = target.matrix_world
    polyline = [matrix @ mesh.vertices[index].co for index in vertex_path]
    return boundary_keys, polyline


def _flood_region(mesh, edge_faces, seed_face, boundary_keys):
    # Face flood fill that treats the barrier edge set as uncrossable.
    # Never modifies topology, so it is safe to re-run after every stroke.
    region = {seed_face}
    stack = [seed_face]
    polygons = mesh.polygons
    while stack:
        face = stack.pop()
        for key in polygons[face].edge_keys:
            edge_key = frozenset(key)
            if edge_key in boundary_keys:
                continue
            for neighbor in edge_faces.get(edge_key, ()):
                if neighbor not in region:
                    region.add(neighbor)
                    stack.append(neighbor)
    return region


def _region_outside_faces(edge_faces, boundary_keys, region):
    # Faces on the far side of the barrier. Empty means the flood leaked all
    # the way around the boundary through fused topology: the recorded
    # failure mode of this mesh family. Never guess in that case.
    outside = set()
    for key in boundary_keys:
        for face in edge_faces.get(key, ()):
            if face not in region:
                outside.add(face)
    return outside


def _extract_region_copy(context, target, region_faces):
    # Split the chosen face region off a DUPLICATE. The draw target itself is
    # never edited, so vertex/face indices cached by the modal stay valid and
    # the original mesh is preserved. No boolean solver is involved.
    result = _make_optimized_copy(context, target, "CUT")
    result_name = result.name
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='DESELECT')
    context.tool_settings.mesh_select_mode = (False, False, True)
    edit_mesh = bmesh.from_edit_mesh(result.data)
    edit_mesh.faces.ensure_lookup_table()
    for face in edit_mesh.faces:
        face.select = face.index in region_faces
    edit_mesh.select_flush(True)
    bmesh.update_edit_mesh(result.data)
    separate_result = bpy.ops.mesh.separate(type='SELECTED')
    bpy.ops.object.mode_set(mode='OBJECT')
    if 'FINISHED' not in separate_result:
        bpy.data.objects.remove(result, do_unlink=True)
        raise RuntimeError("Region separation failed on the duplicate")
    pieces = [
        obj for obj in context.selected_objects
        if obj.type == 'MESH' and obj.name.startswith(result_name)
    ]
    if len(pieces) < 2:
        for piece in pieces:
            bpy.data.objects.remove(piece, do_unlink=True)
        raise RuntimeError("Separation produced fewer than two pieces")
    target.hide_set(True)
    pieces.sort(key=lambda obj: len(obj.data.polygons), reverse=True)
    for index, piece in enumerate(pieces, 1):
        piece.name = f"{target.name}_CutPiece_{index:02d}"
    return pieces


class GSMB_OT_one_click_contour_cut(bpy.types.Operator):
    bl_idname = "gsmb.one_click_contour_cut"
    bl_label = "Draw Loop & Extract"
    bl_description = (
        "Draw surface points (Enter closes the loop), then click the piece "
        "you want to extract; leaks ask for one extra stroke instead of guessing"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        if context.area.type != 'VIEW_3D':
            self.report({'ERROR'}, "Run the cutter from a 3D View")
            return {'CANCELLED'}
        target = context.active_object
        if target is None or target.type != 'MESH':
            self.report({'ERROR'}, "Select the mesh you want to cut, then run the tool")
            return {'CANCELLED'}
        if target.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        # The active object IS the cut target for the whole session — no
        # silent retargeting. Old cut results are hidden only to keep the
        # viewport readable; they never receive raycasts.
        for obj in context.scene.objects:
            if obj.type == 'MESH' and obj != target and (
                "CutPiece" in obj.name or "RibbonPiece" in obj.name
            ):
                obj.hide_set(True)
        for obj in context.selected_objects:
            obj.select_set(False)
        target.hide_set(False)
        target.select_set(True)
        context.view_layer.objects.active = target
        self.target = target
        self.phase = 'DRAW'
        self.world_points = []
        self.anchor_points = []
        self.extra_strokes = []
        self.committed_polylines = []
        self.boundary_keys = set()
        self.seed_face = None
        self.seed_world = None
        self.adjacency = None
        self.edge_faces = None
        self.draw_region = context.region
        self.draw_region_3d = context.region_data
        self.hover = Vector((event.mouse_region_x, event.mouse_region_y))
        self.draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_overlay, (), 'WINDOW', 'POST_PIXEL'
        )
        context.window_manager.modal_handler_add(self)
        context.window.cursor_set('KNIFE')
        context.workspace.status_text_set(
            f"Cutting [{target.name}]: LMB add points • MMB navigate • Enter close loop • Backspace undo • Esc cancel"
        )
        context.scene["gsmb_cut_status"] = (
            f"Drawing on {target.name}: place at least 3 points, then press Enter"
        )
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {
            'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE',
            'NDOF_MOTION', 'NDOF_BUTTON_FIT',
            'NUMPAD_1', 'NUMPAD_2', 'NUMPAD_3', 'NUMPAD_4', 'NUMPAD_5',
            'NUMPAD_6', 'NUMPAD_7', 'NUMPAD_8', 'NUMPAD_9',
        }:
            if context.area:
                context.area.tag_redraw()
            return {'PASS_THROUGH'}
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            self._cleanup(context)
            context.scene["gsmb_cut_status"] = "Cut cancelled; nothing was modified"
            return {'CANCELLED'}
        if event.type == 'BACK_SPACE' and event.value == 'PRESS':
            if self.phase in {'DRAW', 'EXTRA'} and self.world_points:
                self.world_points.pop()
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE':
            self.hover = Vector((event.mouse_region_x, event.mouse_region_y))
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            if self.phase == 'DRAW':
                if len(self.world_points) < 3:
                    self.report({'WARNING'}, "Place at least three surface points before Enter")
                    return {'RUNNING_MODAL'}
                return self._run_safely(context, self._close_loop)
            if self.phase == 'EXTRA':
                if len(self.world_points) < 2:
                    self.report({'WARNING'}, "An extra stroke needs at least two points")
                    return {'RUNNING_MODAL'}
                return self._run_safely(context, self._commit_extra_stroke)
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            mouse = Vector((event.mouse_region_x, event.mouse_region_y))
            hit = self._raycast_target(context, mouse)
            if hit is None:
                self.report(
                    {'WARNING'},
                    f"Click missed '{self.target.name}' — points must land on that mesh",
                )
                return {'RUNNING_MODAL'}
            location, face_index = hit
            if self.phase == 'SEED':
                self.seed_face = face_index
                self.seed_world = location.copy()
                return self._run_safely(context, self._attempt_extract)
            self.world_points.append(location)
            context.scene["gsmb_cut_status"] = (
                f"Drawing on {self.target.name}: {len(self.world_points)} points"
            )
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        return {'RUNNING_MODAL'}

    def _run_safely(self, context, step):
        try:
            finished = step(context)
        except Exception as exc:
            self._cleanup(context)
            context.scene["gsmb_cut_status"] = f"Cut failed safely: {exc}"
            self.report({'ERROR'}, context.scene["gsmb_cut_status"])
            return {'CANCELLED'}
        if finished:
            self._cleanup(context)
            self.report({'INFO'}, context.scene.get("gsmb_cut_status", "Cut complete"))
            return {'FINISHED'}
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def _close_loop(self, context):
        mesh = self.target.data
        if self.adjacency is None:
            self.adjacency = _build_vertex_adjacency(mesh)
            self.edge_faces = _build_edge_face_map(mesh)
        keys, polyline = _stroke_boundary(self.target, self.adjacency, self.world_points, closed=True)
        self.boundary_keys = set(keys)
        self.committed_polylines = [polyline]
        self.anchor_points = [tuple(point) for point in self.world_points]
        context.scene["gsmb_last_contour_world"] = self.anchor_points
        self.world_points = []
        self.phase = 'SEED'
        self._save_recipe(context)
        context.workspace.status_text_set(
            f"Loop locked on [{self.target.name}] — click the piece you want to extract • Esc cancel"
        )
        context.scene["gsmb_cut_status"] = (
            f"Loop locked ({len(self.boundary_keys)} surface edges). Click the piece to extract."
        )
        return False

    def _commit_extra_stroke(self, context):
        keys, polyline = _stroke_boundary(self.target, self.adjacency, self.world_points, closed=False)
        self.boundary_keys |= keys
        self.committed_polylines.append(polyline)
        self.extra_strokes.append([tuple(point) for point in self.world_points])
        self.world_points = []
        self._save_recipe(context)
        # Re-test automatically from the remembered seed — the user never has
        # to re-click or redraw what already worked.
        return self._attempt_extract(context)

    def _attempt_extract(self, context):
        mesh = self.target.data
        region = _flood_region(mesh, self.edge_faces, self.seed_face, self.boundary_keys)
        outside = _region_outside_faces(self.edge_faces, self.boundary_keys, region)
        total = len(mesh.polygons)
        if not outside or len(region) >= total:
            self.phase = 'EXTRA'
            self.world_points = []
            context.workspace.status_text_set(
                f"Leak on [{self.target.name}]: draw ONE extra stroke across the fused bridge, then Enter"
            )
            context.scene["gsmb_cut_status"] = (
                f"Boundary is closed but the region leaked around it "
                f"({len(region):,}/{total:,} faces). Draw an extra stroke across the "
                "leak path, then press Enter — no plane fallback will be used."
            )
            self.report({'WARNING'}, context.scene["gsmb_cut_status"])
            return False
        self._save_recipe(context)
        pieces = _extract_region_copy(context, self.target, region)
        sizes = " / ".join(f"{len(piece.data.polygons):,}" for piece in pieces)
        context.scene["gsmb_cut_status"] = (
            f"Cut complete: {sizes} faces; {self.target.name} hidden and preserved"
        )
        return True

    def _save_recipe(self, context):
        context.scene["gsmb_last_cut_recipe"] = json.dumps({
            "target": self.target.name,
            "anchors": self.anchor_points,
            "seed": tuple(self.seed_world) if self.seed_world is not None else None,
            "strokes": self.extra_strokes,
        })

    def _raycast_target(self, context, point):
        origin_world = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, point)
        direction_world = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, point)
        inverse = self.target.matrix_world.inverted()
        origin_local = inverse @ origin_world
        direction_local = (inverse.to_3x3() @ direction_world).normalized()
        hit, location_local, _normal, face_index = self.target.ray_cast(origin_local, direction_local)
        if not hit:
            return None
        return self.target.matrix_world @ location_local, face_index

    def _projected(self, world_points):
        return [
            view3d_utils.location_3d_to_region_2d(self.draw_region, self.draw_region_3d, point)
            for point in world_points
        ]

    def _draw_overlay(self):
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        for polyline in self.committed_polylines:
            points = [point for point in self._projected(polyline) if point is not None]
            if len(points) >= 2:
                gpu.state.line_width_set(3.0)
                shader.bind()
                shader.uniform_float("color", (0.15, 1.0, 0.3, 0.9))
                batch_for_shader(shader, 'LINE_STRIP', {"pos": points}).draw(shader)
        points = [point for point in self._projected(self.world_points) if point is not None]
        if points:
            preview = points + [self.hover]
            gpu.state.line_width_set(2.5)
            shader.bind()
            shader.uniform_float("color", (0.15, 0.9, 1.0, 0.95))
            batch_for_shader(shader, 'LINE_STRIP', {"pos": preview}).draw(shader)
            gpu.state.point_size_set(8.0)
            shader.uniform_float("color", (1.0, 0.55, 0.1, 1.0))
            batch_for_shader(shader, 'POINTS', {"pos": points}).draw(shader)
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set('NONE')

    def _cleanup(self, context):
        if getattr(self, "draw_handle", None):
            bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle, 'WINDOW')
            self.draw_handle = None
        context.window.cursor_set('DEFAULT')
        context.workspace.status_text_set(None)
        if context.area:
            context.area.tag_redraw()


class GSMB_OT_replay_saved_cut(bpy.types.Operator):
    bl_idname = "gsmb.replay_saved_cut"
    bl_label = "Replay Last Cut (Regression)"
    bl_description = "Re-run the last saved loop, seed and extra strokes without any hand drawing"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        raw = context.scene.get("gsmb_last_cut_recipe", "")
        if not raw:
            self.report({'ERROR'}, "No saved cut recipe; run Draw Loop & Extract once first")
            return {'CANCELLED'}
        try:
            recipe = json.loads(raw)
        except ValueError:
            self.report({'ERROR'}, "Saved cut recipe is corrupted; redraw once to refresh it")
            return {'CANCELLED'}
        target = bpy.data.objects.get(recipe.get("target") or "")
        if target is None or target.type != 'MESH':
            self.report({'ERROR'}, f"Recipe target '{recipe.get('target')}' is missing from this scene")
            return {'CANCELLED'}
        if recipe.get("seed") is None:
            self.report({'ERROR'}, "Recipe has no seed click yet; finish one interactive cut first")
            return {'CANCELLED'}
        target.hide_set(False)
        if target.mode != 'OBJECT':
            context.view_layer.objects.active = target
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in context.selected_objects:
            obj.select_set(False)
        target.select_set(True)
        context.view_layer.objects.active = target
        mesh = target.data
        adjacency = _build_vertex_adjacency(mesh)
        edge_faces = _build_edge_face_map(mesh)
        try:
            boundary_keys, _polyline = _stroke_boundary(
                target, adjacency, [Vector(point) for point in recipe["anchors"]], closed=True
            )
            for stroke in recipe.get("strokes", []):
                keys, _extra = _stroke_boundary(
                    target, adjacency, [Vector(point) for point in stroke], closed=False
                )
                boundary_keys |= keys
        except RuntimeError as exc:
            context.scene["gsmb_cut_status"] = f"Replay failed safely: {exc}"
            self.report({'ERROR'}, context.scene["gsmb_cut_status"])
            return {'CANCELLED'}
        seed_local = target.matrix_world.inverted() @ Vector(recipe["seed"])
        found, _location, _normal, seed_face = target.closest_point_on_mesh(seed_local)
        if not found:
            self.report({'ERROR'}, "Saved seed point could not be projected onto the target")
            return {'CANCELLED'}
        region = _flood_region(mesh, edge_faces, seed_face, boundary_keys)
        outside = _region_outside_faces(edge_faces, boundary_keys, region)
        total = len(mesh.polygons)
        if not outside or len(region) >= total:
            context.scene["gsmb_cut_status"] = (
                f"Replay: region leaked ({len(region):,}/{total:,} faces) — "
                "recipe reproduces the failure; boundary needs another stroke"
            )
            self.report({'ERROR'}, context.scene["gsmb_cut_status"])
            return {'CANCELLED'}
        try:
            pieces = _extract_region_copy(context, target, region)
        except RuntimeError as exc:
            context.scene["gsmb_cut_status"] = f"Replay failed safely: {exc}"
            self.report({'ERROR'}, context.scene["gsmb_cut_status"])
            return {'CANCELLED'}
        sizes = " / ".join(f"{len(piece.data.polygons):,}" for piece in pieces)
        context.scene["gsmb_cut_status"] = f"Replay cut complete: {sizes} faces"
        self.report({'INFO'}, context.scene["gsmb_cut_status"])
        return {'FINISHED'}


class GSMB_OT_separate_exact_region(bpy.types.Operator):
    bl_idname = "gsmb.separate_exact_region"
    bl_label = "Split Selected Knife Seam"
    bl_description = "Split the edges left selected by native Knife, then separate loose mesh parts"
    bl_options = {'REGISTER', 'UNDO'}

    repair_only: bpy.props.BoolProperty(default=False, options={'HIDDEN'})

    def execute(self, context):
        target = context.active_object
        if target is None or target.type != 'MESH':
            context.scene["gsmb_cut_status"] = "Split failed: select the mesh containing the repaired Knife seam"
            self.report({'ERROR'}, context.scene["gsmb_cut_status"])
            return {'CANCELLED'}
        if target.mode != 'EDIT':
            try:
                context.view_layer.objects.active = target
                target.select_set(True)
                bpy.ops.object.mode_set(mode='EDIT')
            except RuntimeError:
                context.scene["gsmb_cut_status"] = "Split failed: could not enter Mesh Edit Mode"
                self.report({'ERROR'}, context.scene["gsmb_cut_status"])
                return {'CANCELLED'}
        edit_mesh = bmesh.from_edit_mesh(target.data)
        selected_edges = [edge for edge in edit_mesh.edges if edge.select]
        preknife_layer = edit_mesh.edges.layers.int.get("gsmb_preknife")
        ignored_layer = edit_mesh.edges.layers.int.get("gsmb_knife_ignored")
        if ignored_layer is None:
            ignored_layer = edit_mesh.edges.layers.int.new("gsmb_knife_ignored")
            # Creating a BMesh custom-data layer can reallocate element storage.
            # Collect all BMEdge references only after the layer exists.
            selected_edges = [edge for edge in edit_mesh.edges if edge.select]
        if preknife_layer is not None:
            tagged_new_edges = [
                edge for edge in edit_mesh.edges
                if edge[preknife_layer] == 0 and not (ignored_layer and edge[ignored_layer] == 1)
            ]
            if len(tagged_new_edges) >= 3:
                # Tagged edges are the authoritative Knife result.  Do not mix
                # arbitrary pre-existing selected edges into the seam: a single
                # stray selected edge otherwise appears as an impossible extra
                # open chain beside an already valid closed loop.
                selected_edges = tagged_new_edges
        repaired_layer = edit_mesh.edges.layers.int.get("gsmb_repaired_seam")
        if repaired_layer is not None:
            selected_edges = list(set(selected_edges) | {
                edge for edge in edit_mesh.edges if edge[repaired_layer] == 1
            })
        if len(selected_edges) < 3:
            self.report({'ERROR'}, "No new Knife edges found; redraw after starting Exact Draw")
            return {'CANCELLED'}
        selected_set = set(selected_edges)
        edit_mesh.verts.index_update()
        full_adjacency = {vertex: [] for vertex in edit_mesh.verts}
        for edge in edit_mesh.edges:
            a, b = edge.verts
            weight = (a.co - b.co).length
            full_adjacency[a].append((b, edge, weight))
            full_adjacency[b].append((a, edge, weight))

        def seam_components():
            vertex_edges = {}
            for edge in selected_set:
                for vertex in edge.verts:
                    vertex_edges.setdefault(vertex, set()).add(edge)
            unseen = set(selected_set)
            components = []
            while unseen:
                seed = unseen.pop()
                component = {seed}
                stack = [seed]
                while stack:
                    edge = stack.pop()
                    for vertex in edge.verts:
                        for neighbor_edge in vertex_edges.get(vertex, ()):
                            if neighbor_edge in unseen:
                                unseen.remove(neighbor_edge)
                                component.add(neighbor_edge)
                                stack.append(neighbor_edge)
                degree = {}
                for edge in component:
                    for vertex in edge.verts:
                        degree[vertex] = degree.get(vertex, 0) + 1
                endpoints = [vertex for vertex, value in degree.items() if value == 1]
                components.append((component, endpoints))
            return components

        # Native Knife can leave a one-edge Y-shaped spur where a stroke lands
        # exactly on dense topology.  Remove only an unambiguous short arm;
        # never guess when the three arms have comparable lengths.
        pruned_spurs = []
        for _pass in range(8):
            vertex_edges = {}
            for edge in selected_set:
                for vertex in edge.verts:
                    vertex_edges.setdefault(vertex, set()).add(edge)
            changed = False
            for branch, incident in list(vertex_edges.items()):
                if len(incident) != 3:
                    continue
                arms = []
                for first_edge in incident:
                    path = [first_edge]
                    length = (first_edge.verts[0].co - first_edge.verts[1].co).length
                    current = first_edge.other_vert(branch)
                    previous_edge = first_edge
                    while len(vertex_edges.get(current, ())) == 2:
                        next_edge = next(edge for edge in vertex_edges[current] if edge is not previous_edge)
                        path.append(next_edge)
                        length += (next_edge.verts[0].co - next_edge.verts[1].co).length
                        current = next_edge.other_vert(current)
                        previous_edge = next_edge
                    arms.append((length, path))
                arms.sort(key=lambda item: item[0])
                short_length, short_path = arms[0]
                if len(short_path) <= 3 and short_length <= arms[1][0] * 0.4:
                    for edge in short_path:
                        selected_set.discard(edge)
                        edge[ignored_layer] = 1
                        pruned_spurs.append(edge)
                    changed = True
                    break
            if not changed:
                break

        seam_lengths = sorted(
            (edge.verts[0].co - edge.verts[1].co).length
            for edge in selected_set
            if (edge.verts[0].co - edge.verts[1].co).length > 1e-8
        )
        typical_edge = seam_lengths[len(seam_lengths) // 2] if seam_lengths else 0.001

        def connect_surface(start, goal, *, local_gap=False):
            direct = (start.co - goal.co).length
            if direct < 1e-8:
                return []
            # Broken Knife strokes normally leave gaps only a few mesh edges wide.
            # Refuse large automatic bridges: on folded garments a spatially close
            # but unrelated surface can otherwise produce a long vertical detour.
            if local_gap and direct > typical_edge * 24.0:
                return None
            segment = goal.co - start.co
            segment_length_squared = segment.length_squared
            distances = {start: 0.0}
            previous = {}
            queue = [((start.co - goal.co).length, 0.0, start.index, start)]
            while queue:
                _priority, distance, _index, vertex = heapq.heappop(queue)
                if distance != distances.get(vertex):
                    continue
                if vertex == goal:
                    break
                for neighbor, edge, weight in full_adjacency[vertex]:
                    midpoint = (vertex.co + neighbor.co) * 0.5
                    t = max(0.0, min(1.0, (midpoint - start.co).dot(segment) / segment_length_squared))
                    nearest = start.co + segment * t
                    deviation = (midpoint - nearest).length / direct
                    candidate = distance + weight * (1.0 + 40.0 * deviation * deviation)
                    if candidate < distances.get(neighbor, float('inf')):
                        distances[neighbor] = candidate
                        previous[neighbor] = (vertex, edge)
                        heuristic = (neighbor.co - goal.co).length
                        heapq.heappush(queue, (candidate + heuristic, candidate, neighbor.index, neighbor))
            if goal not in previous:
                return None
            path = []
            geometric_length = 0.0
            cursor = goal
            while cursor != start:
                cursor, edge = previous[cursor]
                path.append(edge)
                geometric_length += (edge.verts[0].co - edge.verts[1].co).length
            allowed_length = max(direct * 8.0, typical_edge * 12.0)
            if local_gap:
                allowed_length = min(allowed_length, typical_edge * 48.0)
            if geometric_length > allowed_length:
                return None
            return path

        added_edges = []
        for _iteration in range(64):
            components = seam_components()
            # Cut-through Knife can correctly create separate closed loops on
            # the outside and inside of a solid garment.  They must all remain
            # selected; joining them would create an invalid bridge through the
            # garment wall.
            if components and all(len(endpoints) == 0 for _edges, endpoints in components):
                break
            if len(components) == 1:
                endpoints = components[0][1]
                if len(endpoints) == 0:
                    break
                if len(endpoints) != 2:
                    self.report({'ERROR'}, f"Merged Knife seam still has {len(endpoints)} endpoints")
                    return {'CANCELLED'}
                path = connect_surface(endpoints[0], endpoints[1])
                if path is None:
                    self.report({'ERROR'}, "Final Knife endpoints could not be connected")
                    return {'CANCELLED'}
                selected_set.update(path)
                added_edges.extend(path)
                continue
            # Join the geometrically nearest broken ends first.  Direction-based
            # scoring used in 0.6.11 could prefer a distant continuation and was
            # the cause of the long repair lines seen on dense AI garments.
            pairs = []
            for left_index, (left_edges, left_endpoints) in enumerate(components):
                for right_index in range(left_index + 1, len(components)):
                    right_edges, right_endpoints = components[right_index]
                    for left in left_endpoints:
                        for right in right_endpoints:
                            pairs.append(((left.co - right.co).length_squared, left, right))
            pairs.sort(key=lambda item: item[0])
            connected = False
            for _distance, left, right in pairs:
                path = connect_surface(left, right, local_gap=True)
                if path:
                    selected_set.update(path)
                    added_edges.extend(path)
                    connected = True
                    break
            if not connected:
                self.report({'ERROR'}, "Remaining Knife gaps are too far apart for safe automatic repair")
                return {'CANCELLED'}
        else:
            self.report({'ERROR'}, "Too many Knife chain segments to merge safely")
            return {'CANCELLED'}
        selected_edges = list(selected_set)
        # Cache every value derived from BMesh elements before update_edit_mesh.
        # QuadriFlow meshes can cause Blender to rebuild the edit BMesh during
        # this update, invalidating all BMEdge/BMVert Python references.
        closed_loops = len(seam_components())
        # Preflight the actual face regions without changing topology.  A graph-
        # closed seam can still be non-separating, or can enclose only a tiny
        # accidental fragment.  Treat seam edges as barriers and inspect first.
        unvisited_faces = set(edit_mesh.faces)
        barrier_components = []
        while unvisited_faces:
            seed = unvisited_faces.pop()
            component = {seed}
            stack = [seed]
            while stack:
                face = stack.pop()
                for edge in face.edges:
                    if edge in selected_set:
                        continue
                    for neighbor in edge.link_faces:
                        if neighbor in unvisited_faces:
                            unvisited_faces.remove(neighbor)
                            component.add(neighbor)
                            stack.append(neighbor)
            barrier_components.append(component)
        # Ignore pre-existing loose islands (QuadriFlow can leave tiny shells).
        # Only regions containing faces directly adjacent to the Knife seam are
        # candidates for the two sides of this cut.
        seam_adjacent_faces = {
            face for edge in selected_set for face in edge.link_faces
        }
        barrier_components = [
            component for component in barrier_components
            if component & seam_adjacent_faces
        ]
        barrier_components.sort(key=len)
        region_sizes = [len(component) for component in barrier_components]
        edit_mesh.faces.index_update()
        piece_face_indices = {
            face.index for face in barrier_components[0]
        } if len(barrier_components) >= 2 else set()
        context.scene["gsmb_cut_status"] = (
            f"Merged Knife chains; pruned {len(pruned_spurs)} spur edge(s); added {len(added_edges)} closure edges"
        )
        for edge in edit_mesh.edges:
            edge.select = edge in selected_set
        repaired_layer = edit_mesh.edges.layers.int.get("gsmb_repaired_seam")
        if repaired_layer is None:
            repaired_layer = edit_mesh.edges.layers.int.new("gsmb_repaired_seam")
        for edge in edit_mesh.edges:
            edge[repaired_layer] = 1 if edge.select else 0
        bmesh.update_edit_mesh(target.data)
        if self.repair_only:
            region_text = (
                " / ".join(f"{size:,}" for size in region_sizes[:4])
                if len(region_sizes) >= 2 else "not separable"
            )
            context.scene["gsmb_cut_status"] = (
                f"Preview: {closed_loops} loop(s), {len(selected_set)} edges | regions {region_text} faces"
            )
            self.report({'INFO'}, context.scene["gsmb_cut_status"])
            return {'FINISHED'}
        if len(barrier_components) < 2:
            context.scene["gsmb_cut_status"] = "Split blocked safely: closed seam does not divide the surface"
            self.report({'ERROR'}, context.scene["gsmb_cut_status"])
            return {'CANCELLED'}
        minimum_reasonable_faces = max(20, len(edit_mesh.faces) // 2000)
        if region_sizes[0] < minimum_reasonable_faces:
            context.scene["gsmb_cut_status"] = (
                f"Split blocked safely: seam encloses only {region_sizes[0]} faces; redraw the loop"
            )
            self.report({'ERROR'}, context.scene["gsmb_cut_status"])
            return {'CANCELLED'}
        context.tool_settings.mesh_select_mode = (False, True, False)
        split_result = bpy.ops.mesh.edge_split(type='EDGE')
        if 'FINISHED' not in split_result:
            self.report({'ERROR'}, "Selected knife edges could not be split")
            return {'CANCELLED'}
        edit_mesh = bmesh.from_edit_mesh(target.data)
        edit_mesh.faces.ensure_lookup_table()
        edit_mesh.faces.index_update()
        for face in edit_mesh.faces:
            face.select = face.index in piece_face_indices
        bmesh.update_edit_mesh(target.data)
        separate_result = bpy.ops.mesh.separate(type='SELECTED')
        bpy.ops.object.mode_set(mode='OBJECT')
        if 'FINISHED' not in separate_result:
            self.report({'ERROR'}, "Knife seam still did not form separate loose parts; undo and inspect closure")
            return {'CANCELLED'}
        pieces = [obj for obj in context.selected_objects if obj.type == 'MESH']
        for index, piece in enumerate(pieces, 1):
            if piece != target:
                piece.name = f"{target.name}_ExactCut_{index:02d}"
        context.scene["gsmb_cut_status"] = f"Knife seam split: {len(selected_edges)} selected edges"
        self.report({'INFO'}, "Selected knife seam split and loose parts separated")
        return {'FINISHED'}


class GSMB_OT_clear_knife_lines(bpy.types.Operator):
    bl_idname = "gsmb.clear_knife_lines"
    bl_label = "Clear Previous Knife Lines"
    bl_description = "Dissolve edges tagged as Knife-created, clear selections and reset Knife tracking"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self,
            event,
            title="Delete Current Knife Lines?",
            message="This removes the current Knife seam. Use only when you want to redraw it.",
            confirm_text="Delete Knife Lines",
            icon='WARNING',
        )

    def execute(self, context):
        target = context.active_object
        if target is None or target.type != 'MESH':
            self.report({'ERROR'}, "Select the mesh containing previous Knife lines")
            return {'CANCELLED'}
        if target.mode != 'EDIT':
            if target.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            bpy.ops.object.mode_set(mode='EDIT')
        edit_mesh = bmesh.from_edit_mesh(target.data)
        layer = edit_mesh.edges.layers.int.get("gsmb_preknife")
        removed = 0
        if layer is not None:
            tagged = [edge for edge in edit_mesh.edges if edge.is_valid and edge[layer] == 0]
            removed = len(tagged)
            if tagged:
                bmesh.ops.dissolve_edges(
                    edit_mesh,
                    edges=tagged,
                    use_verts=True,
                    use_face_split=False,
                )
            edit_mesh.edges.layers.int.remove(layer)
        repaired_layer = edit_mesh.edges.layers.int.get("gsmb_repaired_seam")
        if repaired_layer is not None:
            edit_mesh.edges.layers.int.remove(repaired_layer)
        ignored_layer = edit_mesh.edges.layers.int.get("gsmb_knife_ignored")
        if ignored_layer is not None:
            edit_mesh.edges.layers.int.remove(ignored_layer)
        for vertex in edit_mesh.verts:
            vertex.select = False
        for edge in edit_mesh.edges:
            edge.select = False
        for face in edit_mesh.faces:
            face.select = False
        bmesh.update_edit_mesh(target.data, loop_triangles=True, destructive=True)
        context.scene["gsmb_cut_status"] = f"Cleared {removed} tagged Knife edges; tracking reset"
        self.report({'INFO'}, context.scene["gsmb_cut_status"])
        return {'FINISHED'}


def _make_optimized_copy(context, source, suffix):
    if source.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    result = source.copy()
    result.data = source.data.copy()
    result.animation_data_clear()
    result.name = f"{source.name}_{suffix}"
    collection_name = "GSMB_OPTIMIZED"
    collection = bpy.data.collections.get(collection_name)
    if collection is None:
        collection = bpy.data.collections.new(collection_name)
        context.scene.collection.children.link(collection)
    collection.objects.link(result)
    for obj in context.selected_objects:
        obj.select_set(False)
    result.select_set(True)
    context.view_layer.objects.active = result
    return result


class GSMB_OT_analyze_mesh_quality(bpy.types.Operator):
    bl_idname = "gsmb.analyze_mesh_quality"
    bl_label = "Analyze Selected Mesh"
    bl_description = "Report polygon count, topology risks and attribute preservation requirements"
    bl_options = {'REGISTER'}

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, "Select one mesh object")
            return {'CANCELLED'}
        if obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        mesh = obj.data
        mesh.calc_loop_triangles()
        bm = bmesh.new()
        bm.from_mesh(mesh)
        non_manifold = sum(1 for edge in bm.edges if not edge.is_manifold)
        loose = sum(1 for vertex in bm.verts if not vertex.link_edges)
        bm.free()
        tris = len(mesh.loop_triangles)
        context.scene["gsmb_optimize_status"] = (
            f"{obj.name}: {len(mesh.vertices):,} verts | {tris:,} tris | "
            f"{non_manifold:,} non-manifold edges | {len(mesh.uv_layers)} UV | "
            f"{len(obj.vertex_groups)} groups | {loose:,} loose verts"
        )
        self.report({'INFO'}, context.scene["gsmb_optimize_status"])
        return {'FINISHED'}


class GSMB_OT_create_game_optimized_copy(bpy.types.Operator):
    bl_idname = "gsmb.create_game_optimized_copy"
    bl_label = "Create Safe Reduced Copy"
    bl_description = "Duplicate the mesh and reduce triangles while preserving the original, materials and UV layers"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        source = context.active_object
        if source is None or source.type != 'MESH':
            self.report({'ERROR'}, "Select one mesh object")
            return {'CANCELLED'}
        source.data.calc_loop_triangles()
        before = len(source.data.loop_triangles)
        target = min(context.scene.gsmb_optimize_target_tris, before)
        if before <= target:
            self.report({'WARNING'}, f"Mesh already has {before:,} triangles")
            return {'CANCELLED'}
        result = _make_optimized_copy(context, source, f"GAME_{target // 1000}K")
        modifier = result.modifiers.new("GSMB Safe Triangle Reduction", 'DECIMATE')
        modifier.decimate_type = 'COLLAPSE'
        modifier.ratio = max(0.01, target / before)
        modifier.use_collapse_triangulate = True
        try:
            bpy.ops.object.modifier_apply(modifier=modifier.name)
        except RuntimeError as exc:
            bpy.data.objects.remove(result, do_unlink=True)
            self.report({'ERROR'}, f"Decimate failed: {exc}")
            return {'CANCELLED'}
        result.data.calc_loop_triangles()
        after = len(result.data.loop_triangles)
        if context.scene.gsmb_optimize_hide_source:
            source.hide_set(True)
        context.scene["gsmb_optimize_status"] = (
            f"Safe copy ready: {before:,} → {after:,} tris; original kept as {source.name}"
        )
        self.report({'INFO'}, context.scene["gsmb_optimize_status"])
        return {'FINISHED'}


class GSMB_OT_create_quad_retopo_copy(bpy.types.Operator):
    bl_idname = "gsmb.create_quad_retopo_copy"
    bl_label = "Create Clean Quad Copy"
    bl_description = "Duplicate, pre-reduce dense input, then run Blender QuadriFlow; inspect UV and deformation before production use"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        source = context.active_object
        if source is None or source.type != 'MESH':
            self.report({'ERROR'}, "Select one mesh object")
            return {'CANCELLED'}
        if source.data.shape_keys or len(source.vertex_groups):
            self.report({'ERROR'}, "Quad retopo changes vertex identity; use it before rigging/shape keys")
            return {'CANCELLED'}
        source.data.calc_loop_triangles()
        before = len(source.data.loop_triangles)
        target_faces = context.scene.gsmb_retopo_target_faces
        result = _make_optimized_copy(context, source, f"QUAD_{target_faces // 1000}K")
        # QuadriFlow is substantially more stable below ~100k input triangles.
        if before > 100000:
            modifier = result.modifiers.new("GSMB Retopo Pre-Reduce", 'DECIMATE')
            modifier.decimate_type = 'COLLAPSE'
            modifier.ratio = 100000 / before
            modifier.use_collapse_triangulate = True
            bpy.ops.object.modifier_apply(modifier=modifier.name)
        try:
            bpy.ops.object.quadriflow_remesh(
                mode='FACES',
                target_faces=target_faces,
                use_mesh_symmetry=False,
                use_preserve_sharp=True,
                use_preserve_boundary=True,
                preserve_attributes=True,
                smooth_normals=True,
                seed=0,
            )
        except RuntimeError as exc:
            bpy.data.objects.remove(result, do_unlink=True)
            self.report({'ERROR'}, f"QuadriFlow failed: {exc}")
            return {'CANCELLED'}
        if context.scene.gsmb_optimize_hide_source:
            source.hide_set(True)
        result.data.calc_loop_triangles()
        context.scene["gsmb_optimize_status"] = (
            f"Quad copy ready: {before:,} tris → {len(result.data.polygons):,} faces; inspect UV/materials"
        )
        self.report({'INFO'}, context.scene["gsmb_optimize_status"])
        return {'FINISHED'}


class GSMB_OT_local_ribbon_split(bpy.types.Operator):
    bl_idname = "gsmb.local_ribbon_split"
    bl_label = "Contour Membrane Split (Solid)"
    bl_description = "Fill the repaired contour with a thin membrane and split a duplicate solid mesh"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        guide = context.active_object
        if guide is None or guide.type != 'MESH':
            self.report({'ERROR'}, "Select the mesh containing the repaired Knife loop")
            return {'CANCELLED'}
        if guide.mode != 'EDIT':
            bpy.ops.object.mode_set(mode='EDIT')
        bm = bmesh.from_edit_mesh(guide.data)
        repaired = bm.edges.layers.int.get("gsmb_repaired_seam")
        if repaired is None:
            self.report({'ERROR'}, "Run Repair / Preview first to store one closed Knife loop")
            return {'CANCELLED'}
        seam_edges = [edge for edge in bm.edges if edge[repaired] == 1]
        vertex_edges = {}
        for edge in seam_edges:
            for vertex in edge.verts:
                vertex_edges.setdefault(vertex, []).append(edge)
        if len(seam_edges) < 3 or any(len(edges) != 2 for edges in vertex_edges.values()):
            self.report({'ERROR'}, "Local Ribbon requires one unbranched closed Knife loop")
            return {'CANCELLED'}
        # Order the simple closed loop without relying on mesh indices after mode changes.
        start = next(iter(vertex_edges))
        ordered = [start]
        previous = None
        current = start
        for _ in range(len(vertex_edges) + 1):
            candidates = [edge.other_vert(current) for edge in vertex_edges[current]]
            next_vertex = candidates[0] if candidates[0] is not previous else candidates[1]
            if next_vertex is start:
                break
            if next_vertex in ordered:
                self.report({'ERROR'}, "Knife loop self-intersects; redraw before Local Ribbon Split")
                return {'CANCELLED'}
            ordered.append(next_vertex)
            previous, current = current, next_vertex
        if len(ordered) != len(vertex_edges):
            self.report({'ERROR'}, "Knife loop ordering failed; redraw one closed loop")
            return {'CANCELLED'}
        points = [vertex.co.copy() for vertex in ordered]
        bpy.ops.object.mode_set(mode='OBJECT')

        # QuadriFlow is excellent as a clean drawing guide but can fuse nearby
        # clothing/body surfaces.  When a topology-safe pre-decimated sibling
        # exists, transfer the guide contour to that mesh's object space and cut
        # the safe sibling instead.
        source = guide
        if "_QUAD_" in guide.name:
            safe_name = guide.name.split("_QUAD_", 1)[0]
            safe_source = bpy.data.objects.get(safe_name)
            if safe_source is not None and safe_source.type == 'MESH':
                guide_to_world = guide.matrix_world
                world_to_safe = safe_source.matrix_world.inverted()
                points = [world_to_safe @ (guide_to_world @ point) for point in points]
                source = safe_source

        width = context.scene.gsmb_ribbon_width
        membrane_normal = geometry.normal(points)
        if membrane_normal.length < 1e-8:
            self.report({'ERROR'}, "Contour is degenerate and cannot form a cutting membrane")
            return {'CANCELLED'}
        membrane_normal.normalize()
        cutter_vertices = []
        for point in points:
            cutter_vertices.extend((
                point + membrane_normal * width,
                point - membrane_normal * width,
            ))
        cutter_faces = []
        count = len(points)
        for index in range(count):
            nxt = (index + 1) % count
            cutter_faces.append((index * 2, nxt * 2, nxt * 2 + 1, index * 2 + 1))
        triangles = geometry.tessellate_polygon([points])
        if not triangles:
            self.report({'ERROR'}, "Contour fill failed; redraw without self-intersections")
            return {'CANCELLED'}
        def point_index(point):
            if isinstance(point, int):
                return point
            return min(range(count), key=lambda index: (points[index] - point).length_squared)
        for triangle in triangles:
            indices = [point_index(point) for point in triangle]
            cutter_faces.append(tuple(index * 2 for index in indices))
            cutter_faces.append(tuple(index * 2 + 1 for index in reversed(indices)))
        cutter_mesh = bpy.data.meshes.new("GSMB_LocalRibbonCutterMesh")
        cutter_mesh.from_pydata(cutter_vertices, [], cutter_faces)
        cutter_mesh.update()
        cutter = bpy.data.objects.new("GSMB_LocalRibbonCutter", cutter_mesh)
        context.scene.collection.objects.link(cutter)
        cutter.matrix_world = source.matrix_world.copy()
        result = _make_optimized_copy(context, source, "RIBBON_CUT")
        result_name = result.name
        modifier = result.modifiers.new("GSMB Contour Membrane Boolean", 'BOOLEAN')
        modifier.operation = 'DIFFERENCE'
        modifier.solver = 'EXACT'
        modifier.object = cutter
        try:
            bpy.ops.object.modifier_apply(modifier=modifier.name)
        except RuntimeError as exc:
            bpy.data.objects.remove(cutter, do_unlink=True)
            bpy.data.objects.remove(result, do_unlink=True)
            self.report({'ERROR'}, f"Contour Membrane Boolean failed: {exc}")
            return {'CANCELLED'}
        bpy.data.objects.remove(cutter, do_unlink=True)
        source.hide_set(True)
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        separate_result = bpy.ops.mesh.separate(type='LOOSE')
        bpy.ops.object.mode_set(mode='OBJECT')
        pieces = [obj for obj in context.selected_objects if obj.type == 'MESH']
        substantial = [obj for obj in pieces if len(obj.data.polygons) >= 20]
        if 'FINISHED' not in separate_result or len(substantial) < 2:
            for piece in list(pieces):
                if piece != source and piece.name.startswith(result_name):
                    bpy.data.objects.remove(piece, do_unlink=True)
            source.hide_set(False)
            context.scene["gsmb_cut_status"] = "Contour membrane did not create two substantial pieces; original restored"
            self.report({'ERROR'}, context.scene["gsmb_cut_status"])
            return {'CANCELLED'}
        for index, piece in enumerate(sorted(substantial, key=lambda obj: len(obj.data.polygons), reverse=True), 1):
            piece.name = f"{source.name}_RibbonPiece_{index:02d}"
        if guide != source:
            guide.hide_set(True)
        context.scene["gsmb_cut_status"] = (
            f"Contour membrane split ready: {len(substantial)} pieces; original hidden and preserved"
        )
        self.report({'INFO'}, context.scene["gsmb_cut_status"])
        return {'FINISHED'}

    def _finish(self, context, restore):
        if getattr(self, "timer", None):
            context.window_manager.event_timer_remove(self.timer)
            self.timer = None
        if restore:
            self.arm.location = self.start_location
            for name, rotation in self.original_rotations.items():
                bone = self.arm.pose.bones.get(name)
                if bone:
                    bone.rotation_mode = 'XYZ'
                    bone.rotation_euler = rotation
        context.window.cursor_set('DEFAULT')
        context.workspace.status_text_set(None)
        context.scene["gsmb_last_analysis"] = "Mouse follow test exited"
        context.scene["gsmb_runtime_stats"] = "Idle"
        context.view_layer.update()
        if context.area:
            context.area.tag_redraw()


class GSMB_PT_main(bpy.types.Panel):
    bl_label = "Secondary Motion Builder"
    bl_idname = "GSMB_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Secondary Motion"

    def draw(self, context):
        layout = self.layout
        layout.operator("gsmb.build_test_asset", icon='OUTLINER_OB_ARMATURE')
        layout.operator("gsmb.analyze_scene", icon='VIEWZOOM')
        layout.operator("gsmb.generate_secondary_rig", icon='BONE_DATA')
        layout.separator()
        layout.operator("gsmb.create_motion_test", icon='PLAY')
        layout.operator("gsmb.mouse_follow_test", icon='MOUSE_MOVE')
        box = layout.box()
        box.label(text="MVP status")
        box.label(text=context.scene.get("gsmb_last_analysis", "Not analyzed"))
        stats = layout.box()
        stats.label(text="Realtime Spring Physics")
        stats.prop(context.scene, "gsmb_stiffness")
        stats.prop(context.scene, "gsmb_damping")
        stats.prop(context.scene, "gsmb_inertia")
        stats.prop(context.scene, "gsmb_gravity")
        stats.prop(context.scene, "gsmb_angle_limit")
        stats.prop(context.scene, "gsmb_collision_radius")
        stats.label(text=context.scene.get("gsmb_runtime_stats", "Idle"))
        optimizer = layout.box()
        optimizer.label(text="Game Mesh Optimizer")
        optimizer.operator("gsmb.analyze_mesh_quality", icon='VIEWZOOM')
        optimizer.prop(context.scene, "gsmb_optimize_target_tris")
        optimizer.operator("gsmb.create_game_optimized_copy", icon='MOD_DECIM')
        optimizer.prop(context.scene, "gsmb_retopo_target_faces")
        optimizer.operator("gsmb.create_quad_retopo_copy", icon='MOD_REMESH')
        optimizer.prop(context.scene, "gsmb_optimize_hide_source")
        optimizer.label(text=context.scene.get("gsmb_optimize_status", "Original mesh is always preserved"))
        cutter = layout.box()
        cutter.label(text="One-Click Garment Cutter")
        primary = cutter.row()
        primary.scale_y = 1.6
        primary.operator("gsmb.one_click_contour_cut", text="DRAW LOOP & EXTRACT", icon='MOD_BOOLEAN')
        cutter.label(text="LMB points • MMB view • Enter closes loop")
        cutter.label(text="Then click the piece you want to extract")
        cutter.operator("gsmb.replay_saved_cut", icon='FILE_REFRESH')
        cutter.prop(context.scene, "gsmb_show_cut_advanced", text="Show Advanced / Diagnostics")
        if context.scene.gsmb_show_cut_advanced:
            cutter.prop(context.scene, "gsmb_cut_mode", expand=True)
            cutter.prop(context.scene, "gsmb_cut_method", expand=True)
            cutter.operator("gsmb.surface_loop_cut", text="Legacy Draw Tool", icon='TOOL_SETTINGS')
            cutter.prop(context.scene, "gsmb_cut_through")
            repair = cutter.operator(
                "gsmb.separate_exact_region",
                text="Diagnostic: Repair Knife Edges",
                icon='MOD_SMOOTH',
            )
            repair.repair_only = True
            split = cutter.operator(
                "gsmb.separate_exact_region",
                text="Diagnostic: Edge Split",
                icon='UNLINKED',
            )
            # Blender remembers hidden operator values from the last invocation.
            # Never allow the Split button to inherit Repair mode.
            split.repair_only = False
            cutter.prop(context.scene, "gsmb_ribbon_width")
            cutter.operator(
                "gsmb.local_ribbon_split",
                text="Diagnostic: Stored-Seam Membrane Split",
                icon='MOD_BOOLEAN',
            )
            cutter.separator()
            clear_row = cutter.row()
            clear_row.alert = True
            clear_row.operator(
                "gsmb.clear_knife_lines",
                text="Reset / Delete Knife Lines…",
                icon='TRASH',
            )
        cutter.label(text=context.scene.get("gsmb_cut_status", "Select a mesh to begin"))


classes = (
    GSMB_OT_build_test_asset,
    GSMB_OT_analyze_scene,
    GSMB_OT_generate_secondary_rig,
    GSMB_OT_create_motion_test,
    GSMB_OT_mouse_follow_test,
    GSMB_OT_surface_loop_cut,
    GSMB_OT_one_click_contour_cut,
    GSMB_OT_replay_saved_cut,
    GSMB_OT_separate_exact_region,
    GSMB_OT_clear_knife_lines,
    GSMB_OT_analyze_mesh_quality,
    GSMB_OT_create_game_optimized_copy,
    GSMB_OT_create_quad_retopo_copy,
    GSMB_OT_local_ribbon_split,
    GSMB_PT_main,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.gsmb_stiffness = bpy.props.FloatProperty(name="Stiffness", default=24.0, min=1.0, max=80.0)
    bpy.types.Scene.gsmb_damping = bpy.props.FloatProperty(name="Damping", default=7.5, min=0.1, max=30.0)
    bpy.types.Scene.gsmb_inertia = bpy.props.FloatProperty(name="Inertia", default=0.8, min=0.0, max=2.0)
    bpy.types.Scene.gsmb_gravity = bpy.props.FloatProperty(name="Gravity", default=0.35, min=0.0, max=2.0)
    bpy.types.Scene.gsmb_angle_limit = bpy.props.FloatProperty(name="Angle Limit", default=0.55, min=0.05, max=1.4, subtype='ANGLE')
    bpy.types.Scene.gsmb_collision_radius = bpy.props.FloatProperty(name="Bone Radius", default=0.035, min=0.005, max=0.2, subtype='DISTANCE')
    bpy.types.Scene.gsmb_cut_through = bpy.props.BoolProperty(
        name="Cut Through Thickness",
        description="Experimental screen-ray cut; may hit unrelated surfaces in combined AI meshes",
        default=False,
    )
    bpy.types.Scene.gsmb_optimize_target_tris = bpy.props.IntProperty(
        name="Safe Target Tris",
        description="Triangle target for the UV-preserving game copy",
        default=80000,
        min=1000,
        max=2000000,
    )
    bpy.types.Scene.gsmb_retopo_target_faces = bpy.props.IntProperty(
        name="Quad Target Faces",
        description="Approximate face target for QuadriFlow retopology",
        default=40000,
        min=1000,
        max=500000,
    )
    bpy.types.Scene.gsmb_optimize_hide_source = bpy.props.BoolProperty(
        name="Hide Original After Build",
        default=True,
    )
    bpy.types.Scene.gsmb_ribbon_depth = bpy.props.FloatProperty(
        name="Local Cut Depth",
        description="How far the local cutter extends along both surface-normal directions",
        default=0.03,
        min=0.001,
        max=0.25,
        subtype='DISTANCE',
    )
    bpy.types.Scene.gsmb_ribbon_width = bpy.props.FloatProperty(
        name="Cut Gap Width",
        description="Half-width of the narrow removable band around the drawn seam",
        default=0.001,
        min=0.00005,
        max=0.02,
        subtype='DISTANCE',
    )
    bpy.types.Scene.gsmb_show_cut_advanced = bpy.props.BoolProperty(
        name="Show Advanced / Diagnostics",
        default=False,
    )
    bpy.types.Scene.gsmb_cut_mode = bpy.props.EnumProperty(
        name="Path",
        items=(("LINE", "Straight", "Connect clicked points with straight segments"), ("CURVE", "Smooth", "Smooth the closed path before cutting")),
        default="LINE",
    )
    bpy.types.Scene.gsmb_cut_method = bpy.props.EnumProperty(
        name="Method",
        items=(
            ("EXACT", "Exact Draw", "Follow the drawn garment seam through faces; draw + Enter, then split seam"),
            ("SURFACE", "Approx Topology", "Approximate existing mesh edges; not for exact garment seams"),
            ("PROJECT", "View Project", "Project the final loop from the current view"),
        ),
        default="EXACT",
    )
    production.register()
    hero_rig.register()


def unregister():
    hero_rig.unregister()
    production.unregister()
    for name in ("gsmb_stiffness", "gsmb_damping", "gsmb_inertia", "gsmb_gravity", "gsmb_angle_limit", "gsmb_collision_radius", "gsmb_cut_through", "gsmb_optimize_target_tris", "gsmb_retopo_target_faces", "gsmb_optimize_hide_source", "gsmb_ribbon_depth", "gsmb_ribbon_width", "gsmb_show_cut_advanced", "gsmb_cut_mode", "gsmb_cut_method"):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
