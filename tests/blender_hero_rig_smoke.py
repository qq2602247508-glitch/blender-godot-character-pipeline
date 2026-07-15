"""Headless smoke test for HERO_RIG_V2 and modular-part authoring."""

import json
import pathlib
import sys

import bpy


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "blender_addons"))

import game_secondary_motion_builder as addon  # noqa: E402


def mesh_object(name, vertices, faces):
    data = bpy.data.meshes.new(name + "_Mesh")
    data.from_pydata(vertices, [], faces)
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    return obj


addon.register()
scene = bpy.context.scene
body = mesh_object(
    "BaseBody",
    [(-0.4, -0.15, 0), (0.4, -0.15, 0), (0.4, 0.15, 2), (-0.4, 0.15, 2)],
    [(0, 1, 2, 3)],
)
scene.gsmb_hero_target = body
points = {
    "pelvis": (0, 0, 1.0), "chest": (0, 0, 1.48), "neck": (0, 0, 1.68), "head": (0, 0, 1.95),
    "shoulder.L": (0.25, 0, 1.58), "elbow.L": (0.55, 0, 1.42),
    "wrist.L": (0.78, 0, 1.28), "hand.L": (0.92, 0, 1.25),
    "hip.L": (0.16, 0, 0.98), "knee.L": (0.17, 0, 0.52),
    "ankle.L": (0.17, 0, 0.08), "toe.L": (0.17, -0.15, 0.03),
}
scene["gsmb_hero_landmarks"] = json.dumps(points)
addon.hero_rig._sync_markers(scene, addon.hero_rig._points(scene))
assert bpy.data.objects.get("LM_shoulder.L_橙色圈")
assert bpy.data.objects.get("LM_shoulder.R_橙色圈")
assert bpy.data.objects.get("LM_shoulder.L_中心点")
assert len(bpy.data.collections["GSMB_HERO_LANDMARKS"].objects) == 40
assert bpy.ops.gsmb.build_hero_rig() == {"FINISHED"}
rig = scene.gsmb_hero_armature
required = {
    "root", "Hips", "Spine2", "Head", "UpperArm.L", "LowerLeg.R",
    "Thumb1.L", "Index3.L", "Middle2.R", "Ring3.R", "Pinky1.L",
}
assert required <= set(rig.data.bones.keys())
assert len(rig.data.bones) == 54

hair = mesh_object(
    "new_ponytail_hair",
    [(-0.1, 0, 1), (0.1, 0, 1), (0.1, 0, 2), (-0.1, 0, 2)],
    [(0, 1, 2, 3)],
)
bpy.ops.object.select_all(action="DESELECT")
hair.select_set(True)
bpy.context.view_layer.objects.active = hair
assert bpy.ops.gsmb.analyze_part_roles() == {"FINISHED"}
assert hair.gsmb_part_role == "HAIR"
assert bpy.ops.gsmb.auto_dynamic_mask() == {"FINISHED"}
assert hair.vertex_groups.get("GSMB_FIXED")
assert hair.vertex_groups.get("GSMB_DYNAMIC")
fixed = hair.vertex_groups["GSMB_FIXED"]
dynamic = hair.vertex_groups["GSMB_DYNAMIC"]
top = max(hair.data.vertices, key=lambda vertex: vertex.co.z)
bottom = min(hair.data.vertices, key=lambda vertex: vertex.co.z)
assert fixed.weight(top.index) > dynamic.weight(top.index)
assert dynamic.weight(bottom.index) > fixed.weight(bottom.index)

# New modular clothing can inherit the already-approved base-body weights.
hips = body.vertex_groups.new(name="Hips")
hips.add([vertex.index for vertex in body.data.vertices], 1.0, "REPLACE")
cloth = mesh_object(
    "new_jacket",
    [(-0.4, -0.14, 0), (0.4, -0.14, 0), (0.4, 0.14, 2), (-0.4, 0.14, 2)],
    [(0, 1, 2, 3)],
)
bpy.ops.object.select_all(action="DESELECT")
cloth.select_set(True)
bpy.context.view_layer.objects.active = cloth
assert bpy.ops.gsmb.transfer_body_weights() == {"FINISHED"}
assert cloth.vertex_groups.get("Hips")
assert min(cloth.vertex_groups["Hips"].weight(vertex.index) for vertex in cloth.data.vertices) > 0.99
assert any(modifier.type == "ARMATURE" and modifier.object == rig for modifier in cloth.modifiers)

removed = addon.hero_rig._undo_last_landmark(scene)
assert removed == "toe.L"
assert bpy.data.objects.get("LM_toe.L_橙色圈") is None
assert bpy.data.objects.get("LM_toe.R_橙色圈") is None

print(f"GSMB_HERO_RIG_SMOKE_OK bones={len(rig.data.bones)} roles=1 masks=2 transfer=1 mirrored_markers=20 undo=1")
