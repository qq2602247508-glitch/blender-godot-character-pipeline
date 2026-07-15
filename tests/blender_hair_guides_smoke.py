"""Headless regression for the semi-automatic root-to-tip hair guide workflow."""

import json
import math
import pathlib
import sys

import bpy
import bmesh
from mathutils import Euler


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


def build_curved_hair(scene, name, x_offset):
    vertices = []
    faces = []
    rows = 10
    for row in range(rows):
        down = row / (rows - 1)
        center_y = 0.08 * math.sin(down * math.pi)
        z = 1.78 - 0.7 * down
        vertices.extend(((x_offset - 0.035, center_y, z), (x_offset + 0.035, center_y, z)))
        if row:
            first = (row - 1) * 2
            faces.append((first, first + 1, first + 3, first + 2))
    data = bpy.data.meshes.new(f"{name}_Mesh")
    data.from_pydata(vertices, [], faces)
    hair = bpy.data.objects.new(name, data)
    scene.collection.objects.link(hair)
    return hair, rows


def select_edit_vertices(obj, indices):
    mesh = bmesh.from_edit_mesh(obj.data)
    mesh.verts.ensure_lookup_table()
    for vertex in mesh.verts:
        vertex.select = vertex.index in indices
    bmesh.update_edit_mesh(obj.data)


def create_guide(scene, hair, rows, side=0):
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = hair
    hair.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    select_edit_vertices(hair, {side})
    assert bpy.ops.gsmb.capture_hair_root() == {"FINISHED"}
    select_edit_vertices(hair, {(rows - 1) * 2 + side})
    assert bpy.ops.gsmb.create_hair_guide() == {"FINISHED"}
    bpy.ops.object.mode_set(mode="OBJECT")


def preview_copy(scene, source):
    copy = source.copy()
    copy.data = source.data.copy()
    copy.name = f"GSMB_TEST_{source.name}"
    copy.location = (0.12, -0.07, 0.03)
    scene.collection.objects.link(copy)
    source.hide_viewport = True
    return copy


def max_edge_stretch(obj):
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    maximum = 1.0
    for edge in obj.data.edges:
        first, second = edge.vertices
        rest = (obj.data.vertices[first].co - obj.data.vertices[second].co).length
        posed = (mesh.vertices[first].co - mesh.vertices[second].co).length
        if rest > 1e-8:
            maximum = max(maximum, posed / rest)
    evaluated.to_mesh_clear()
    return maximum


def main():
    addon.register()
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    rig = build_rig(scene)
    source_a, rows_a = build_curved_hair(scene, "GuideHairA", -0.16)
    source_b, rows_b = build_curved_hair(scene, "GuideHairB", 0.16)
    create_guide(scene, source_a, rows_a, 0)
    create_guide(scene, source_a, rows_a, 1)
    create_guide(scene, source_b, rows_b)
    hair_a = preview_copy(scene, source_a)
    hair_b = preview_copy(scene, source_b)

    guides = [obj for obj in bpy.data.objects if obj.get("gsmb_hair_guide")]
    assert len(guides) == 3
    assert all(len(guide.data.splines[0].points) == scene.gsmb_hair_guide_points for guide in guides)

    bpy.ops.object.select_all(action="DESELECT")
    rig.select_set(True)
    hair_a.select_set(True)
    hair_b.select_set(True)
    bpy.context.view_layer.objects.active = hair_a
    scene.gsmb_prod_armature = rig
    scene.gsmb_prod_asset_id = "guide_hair_smoke"
    scene.gsmb_prod_equipment_type = "HAIR"
    scene.gsmb_hair_build_mode = "GUIDES"
    scene.gsmb_prod_bones_per_chain = 4
    assert bpy.ops.gsmb.generate_production_equipment() == {"FINISHED"}
    equipment = scene.gsmb_prod_equipment_armature
    assert sum(1 for bone in equipment.data.bones if bone.get("gsmb_secondary")) == 12
    assert "Head" in equipment.data.bones
    for hair in (hair_a, hair_b):
        assert sum(1 for vertex in hair.data.vertices if not vertex.groups) == 0
        assert max(len(vertex.groups) for vertex in hair.data.vertices) <= 4
    dynamic_a = {group.name for group in hair_a.vertex_groups if "_Hair_" in group.name}
    dynamic_b = {group.name for group in hair_b.vertex_groups if "_Hair_" in group.name}
    assert dynamic_a and dynamic_b and dynamic_a.isdisjoint(dynamic_b)
    assert len({name.split("_Hair_", 1)[1].split("_", 1)[0] for name in dynamic_a}) == 1
    assert hair_a["gsmb_source_mesh"] == "GuideHairA"
    assert hair_b["gsmb_source_mesh"] == "GuideHairB"
    assert bpy.ops.gsmb.validate_production_equipment() == {"FINISHED"}
    assert len(json.loads(equipment["gsmb_collider_names"])) == 5
    assert json.loads(equipment["gsmb_guide_sources"]) == ["GuideHairA", "GuideHairA", "GuideHairB"]
    chains = json.loads(equipment["gsmb_chain_manifest"])
    target_root = equipment.matrix_world.inverted() @ (
        hair_a.matrix_world @ hair_a.data.vertices[0].co
    )
    assert (equipment.data.bones[chains[0][0]].head_local - target_root).length < 1e-4
    for index, chain in enumerate(chains):
        angle = math.radians(25 if index % 2 == 0 else -25)
        for bone_name in chain:
            pose_bone = equipment.pose.bones[bone_name]
            pose_bone.rotation_mode = "XYZ"
            pose_bone.rotation_euler = Euler((0.0, angle, 0.0))
    bpy.context.view_layer.update()
    stretch = max(max_edge_stretch(hair_a), max_edge_stretch(hair_b))
    assert stretch < 1.35, stretch
    print(
        "GSMB_HAIR_GUIDES_SMOKE_OK guides=3 bones=12 isolated_sources=2 "
        f"max_influences=4 max_edge_stretch={stretch:.3f}"
    )


if __name__ == "__main__":
    main()
