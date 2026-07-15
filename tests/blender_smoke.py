"""Headless Blender regression for the production dynamic-equipment builder."""

import json
import math
import pathlib
import sys

import bpy


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ADDON_ROOT = REPO_ROOT / "blender_addons"
OUTPUT_ROOT = REPO_ROOT / "tests" / "output"
sys.path.insert(0, str(ADDON_ROOT))

import game_secondary_motion_builder as addon  # noqa: E402


def build_mixamo_rig(scene):
    data = bpy.data.armatures.new("SmokeHero_Data")
    armature = bpy.data.objects.new("SmokeHero", data)
    scene.collection.objects.link(armature)
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    specs = (
        ("GameRoot", (0, 0, 0), (0, 0, 0.9), None),
        ("mixamorig:Hips", (0, 0, 0.9), (0, 0, 1.05), "GameRoot"),
        ("mixamorig:Spine", (0, 0, 1.05), (0, 0, 1.25), "mixamorig:Hips"),
        ("mixamorig:Spine1", (0, 0, 1.25), (0, 0, 1.42), "mixamorig:Spine"),
        ("mixamorig:Spine2", (0, 0, 1.42), (0, 0, 1.55), "mixamorig:Spine1"),
        ("mixamorig:Neck", (0, 0, 1.55), (0, 0, 1.65), "mixamorig:Spine2"),
        ("mixamorig:Head", (0, 0, 1.65), (0, 0, 1.88), "mixamorig:Neck"),
        ("mixamorig:LeftUpLeg", (0.11, 0, 0.95), (0.11, 0, 0.52), "mixamorig:Hips"),
        ("mixamorig:RightUpLeg", (-0.11, 0, 0.95), (-0.11, 0, 0.52), "mixamorig:Hips"),
    )
    for name, head, tail, parent in specs:
        bone = data.edit_bones.new(name)
        bone.head = head
        bone.tail = tail
        if parent:
            bone.parent = data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    return armature


def build_skirt(scene):
    segments, rings = 24, 6
    vertices, faces = [], []
    for ring in range(rings):
        down = ring / (rings - 1)
        radius = 0.27 + 0.18 * down
        z = 1.0 - 0.62 * down
        for segment in range(segments):
            angle = 2.0 * math.pi * segment / segments
            vertices.append((radius * math.cos(angle), radius * math.sin(angle), z))
    for ring in range(rings - 1):
        for segment in range(segments):
            a = ring * segments + segment
            b = ring * segments + (segment + 1) % segments
            c = (ring + 1) * segments + (segment + 1) % segments
            d = (ring + 1) * segments + segment
            faces.append((a, b, c, d))
    data = bpy.data.meshes.new("SmokeSkirt_Mesh")
    data.from_pydata(vertices, [], faces)
    mesh = bpy.data.objects.new("SmokeSkirt", data)
    scene.collection.objects.link(mesh)
    return mesh


def main():
    addon.register()
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    source = build_mixamo_rig(scene)
    skirt = build_skirt(scene)
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    source.select_set(True)
    skirt.select_set(True)
    bpy.context.view_layer.objects.active = skirt
    scene.gsmb_prod_armature = source
    scene.gsmb_prod_asset_id = "skirt_headless_smoke"
    scene.gsmb_prod_equipment_type = "SKIRT"
    scene.gsmb_prod_chain_count = 8
    scene.gsmb_prod_bones_per_chain = 3
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    scene.gsmb_prod_export_path = str(OUTPUT_ROOT / "skirt_headless_smoke.json")

    assert bpy.ops.gsmb.generate_production_equipment() == {"FINISHED"}
    equipment = scene.gsmb_prod_equipment_armature
    assert equipment is not None
    assert skirt.parent == equipment
    assert "Hips" in equipment.data.bones
    assert "mixamorig:Hips" not in equipment.data.bones
    assert sum(1 for bone in equipment.data.bones if bone.get("gsmb_secondary")) == 24
    assert max(len(vertex.groups) for vertex in skirt.data.vertices) <= 4
    assert bpy.ops.gsmb.validate_production_equipment() == {"FINISHED"}
    assert bpy.ops.gsmb.export_godot_profile() == {"FINISHED"}
    scene.gsmb_prod_package_dir = str(OUTPUT_ROOT)
    assert bpy.ops.gsmb.export_dynamic_equipment_package() == {"FINISHED"}
    assert (OUTPUT_ROOT / "skirt_headless_smoke.glb").stat().st_size > 0
    assert (OUTPUT_ROOT / "skirt_headless_smoke.secondary_motion.json").stat().st_size > 0

    with open(scene.gsmb_prod_export_path, "r", encoding="utf-8") as handle:
        profile = json.load(handle)
    assert profile["schema"] == "gsmb-secondary-motion-v1"
    assert len(profile["chains"]) == 8
    assert len(profile["colliders"]) == 5
    assert {entry["bone"] for entry in profile["colliders"]} == {
        "Head", "Chest", "Hips", "LeftUpperLeg", "RightUpperLeg",
    }
    hips_collider = next(
        obj for obj in scene.objects
        if obj.get("gsmb_collider_role") == "hips" and obj.parent == equipment
    )
    before = hips_collider.matrix_world.translation.copy()
    equipment.pose.bones["Hips"].location.x = 0.2
    bpy.context.view_layer.update()
    after = hips_collider.matrix_world.translation.copy()
    assert abs((after - before).x - 0.2) < 1e-5

    # Regression: a Kimodo-style source Action must not leave the independent
    # skirt rig static. The bake must also keep canonical anchors attached.
    equipment.pose.bones["Hips"].location.x = 0.0
    source.animation_data_create()
    action = bpy.data.actions.new("KIMODO_Smoke_Idle_Loop")
    source.animation_data.action = action
    for frame, angle in ((1, 0.0), (30, math.radians(5.0)), (60, 0.0)):
        source.pose.bones["mixamorig:Hips"].rotation_mode = "XYZ"
        source.pose.bones["mixamorig:Hips"].rotation_euler.z = angle
        source.pose.bones["mixamorig:Hips"].keyframe_insert("rotation_euler", frame=frame)
        source.pose.bones["mixamorig:Head"].rotation_mode = "XYZ"
        source.pose.bones["mixamorig:Head"].rotation_euler.x = -angle * 0.5
        source.pose.bones["mixamorig:Head"].keyframe_insert("rotation_euler", frame=frame)
    assert bpy.ops.gsmb.bake_action_secondary() == {"FINISHED"}
    secondary = equipment.animation_data.action
    assert secondary.get("gsmb_generated_secondary")
    assert secondary.get("gsmb_source_action") == action.name
    assert secondary.get("gsmb_dynamic_max_angle_deg") > 0.1
    assert secondary.get("gsmb_anchor_max_error_m") < 1e-3
    dynamic_curves = [
        curve for curve in secondary.fcurves
        if "GSMB_" in curve.data_path and curve.data_path.endswith("rotation_euler")
    ]
    assert dynamic_curves
    assert any(abs(curve.evaluate(30) - curve.evaluate(1)) > 1e-4 for curve in dynamic_curves)
    print("GSMB_BLENDER_SMOKE_OK chains=8 bones=24 colliders=5 kimodo_secondary=1")


if __name__ == "__main__":
    main()
