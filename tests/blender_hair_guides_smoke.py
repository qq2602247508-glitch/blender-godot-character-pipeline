"""Headless regression for the semi-automatic root-to-tip hair guide workflow."""

import json
import math
import pathlib
import sys

import bpy
import bmesh


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "blender_addons"))

import game_secondary_motion_builder as addon  # noqa: E402


def build_rig(scene):
    data = bpy.data.armatures.new("GuideHero_Data")
    armature = bpy.data.objects.new("GuideHero", data)
    scene.collection.objects.link(armature)
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    specs = (
        ("GameRoot", (0, 0, 0), (0, 0, 0.9), None),
        ("mixamorig:Hips", (0, 0, 0.9), (0, 0, 1.1), "GameRoot"),
        ("mixamorig:Spine2", (0, 0, 1.1), (0, 0, 1.55), "mixamorig:Hips"),
        ("mixamorig:Head", (0, 0, 1.55), (0, 0, 1.9), "mixamorig:Spine2"),
        ("LeftUpperLeg", (0.1, 0, 0.95), (0.1, 0, 0.5), "mixamorig:Hips"),
        ("RightUpperLeg", (-0.1, 0, 0.95), (-0.1, 0, 0.5), "mixamorig:Hips"),
    )
    for name, head, tail, parent in specs:
        bone = data.edit_bones.new(name)
        bone.head, bone.tail = head, tail
        if parent:
            bone.parent = data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    return armature


def build_curved_hair(scene):
    vertices = []
    faces = []
    rows = 10
    for row in range(rows):
        down = row / (rows - 1)
        center_y = 0.08 * math.sin(down * math.pi)
        z = 1.78 - 0.7 * down
        vertices.extend(((-0.035, center_y, z), (0.035, center_y, z)))
        if row:
            first = (row - 1) * 2
            faces.append((first, first + 1, first + 3, first + 2))
    data = bpy.data.meshes.new("GuideHair_Mesh")
    data.from_pydata(vertices, [], faces)
    hair = bpy.data.objects.new("GuideHair", data)
    scene.collection.objects.link(hair)
    return hair, rows


def select_edit_vertices(obj, indices):
    mesh = bmesh.from_edit_mesh(obj.data)
    mesh.verts.ensure_lookup_table()
    for vertex in mesh.verts:
        vertex.select = vertex.index in indices
    bmesh.update_edit_mesh(obj.data)


def main():
    addon.register()
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    rig = build_rig(scene)
    hair, rows = build_curved_hair(scene)

    bpy.context.view_layer.objects.active = hair
    hair.select_set(True)
    rig.select_set(False)
    bpy.ops.object.mode_set(mode="EDIT")
    select_edit_vertices(hair, {0, 1})
    assert bpy.ops.gsmb.capture_hair_root() == {"FINISHED"}
    select_edit_vertices(hair, {(rows - 1) * 2, (rows - 1) * 2 + 1})
    assert bpy.ops.gsmb.create_hair_guide() == {"FINISHED"}
    bpy.ops.object.mode_set(mode="OBJECT")

    guides = [obj for obj in bpy.data.objects if obj.get("gsmb_hair_guide")]
    assert len(guides) == 1
    assert len(guides[0].data.splines[0].points) == scene.gsmb_hair_guide_points

    bpy.ops.object.select_all(action="DESELECT")
    rig.select_set(True)
    hair.select_set(True)
    bpy.context.view_layer.objects.active = hair
    scene.gsmb_prod_armature = rig
    scene.gsmb_prod_asset_id = "guide_hair_smoke"
    scene.gsmb_prod_equipment_type = "HAIR"
    scene.gsmb_hair_build_mode = "GUIDES"
    scene.gsmb_prod_bones_per_chain = 4
    assert bpy.ops.gsmb.generate_production_equipment() == {"FINISHED"}
    equipment = scene.gsmb_prod_equipment_armature
    assert sum(1 for bone in equipment.data.bones if bone.get("gsmb_secondary")) == 4
    assert "Head" in equipment.data.bones
    assert sum(1 for vertex in hair.data.vertices if not vertex.groups) == 0
    assert max(len(vertex.groups) for vertex in hair.data.vertices) <= 4
    assert bpy.ops.gsmb.validate_production_equipment() == {"FINISHED"}
    assert len(json.loads(equipment["gsmb_collider_names"])) == 5
    print("GSMB_HAIR_GUIDES_SMOKE_OK guides=1 bones=4 unweighted=0 max_influences=4")


if __name__ == "__main__":
    main()
