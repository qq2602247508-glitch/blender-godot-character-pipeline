"""Real-asset regression using Quaternius' CC0 Ultimate Modular Women pack.

Environment variables:
    GSMB_REAL_MODEL   Path to Witch.gltf.
    GSMB_REAL_OUTPUT  Directory for generated GLB, JSON, and diagnostic blend.
"""

import json
import os
import pathlib
import sys

import bpy


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "blender_addons"))

import game_secondary_motion_builder as addon  # noqa: E402


MODEL_PATH = pathlib.Path(
    os.environ.get("GSMB_REAL_MODEL", "/Users/inagi/codex/900-杂项/Witch.gltf")
)
OUTPUT_ROOT = pathlib.Path(
    os.environ.get(
        "GSMB_REAL_OUTPUT",
        "/Users/inagi/codex/900-杂项/quaternius_witch_test_output",
    )
)


def extract_skirt(body):
    """Non-destructively isolate the lower central dress faces for testing."""
    selected = []
    for polygon in body.data.polygons:
        center = body.matrix_world @ polygon.center
        if center.z < 1.31 and abs(center.x) < 0.46:
            selected.append(polygon)
    used = sorted({index for polygon in selected for index in polygon.vertices})
    remap = {old: new for new, old in enumerate(used)}
    vertices = [body.data.vertices[index].co.copy() for index in used]
    faces = [[remap[index] for index in polygon.vertices] for polygon in selected]
    data = bpy.data.meshes.new("WitchSkirt_Test_Mesh")
    data.from_pydata(vertices, [], faces)
    data.update()
    skirt = bpy.data.objects.new("WitchSkirt_Test", data)
    body.users_collection[0].objects.link(skirt)
    skirt.matrix_world = body.matrix_world.copy()
    for material in body.data.materials:
        data.materials.append(material)
    for new_polygon, old_polygon in zip(data.polygons, selected):
        new_polygon.material_index = old_polygon.material_index
    return skirt


def main():
    assert MODEL_PATH.exists(), f"Missing real model: {MODEL_PATH}"
    addon.register()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    assert bpy.ops.import_scene.gltf(filepath=str(MODEL_PATH)) == {"FINISHED"}

    source = bpy.data.objects["CharacterArmature"]
    body = bpy.data.objects["Witch_Body"]
    skirt = extract_skirt(body)
    assert len(source.data.bones) == 62
    assert len(bpy.data.actions) == 24
    assert len(skirt.data.vertices) > 0

    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    source.select_set(True)
    skirt.select_set(True)
    bpy.context.view_layer.objects.active = skirt

    scene = bpy.context.scene
    scene.gsmb_prod_armature = source
    scene.gsmb_prod_asset_id = "quaternius_witch_skirt"
    scene.gsmb_prod_equipment_type = "SKIRT"
    scene.gsmb_prod_chain_count = 8
    scene.gsmb_prod_bones_per_chain = 3
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    scene.gsmb_prod_package_dir = str(OUTPUT_ROOT)
    scene.gsmb_prod_export_path = str(
        OUTPUT_ROOT / "quaternius_witch_skirt.secondary_motion.json"
    )

    assert bpy.ops.gsmb.generate_production_equipment() == {"FINISHED"}
    assert bpy.ops.gsmb.validate_production_equipment() == {"FINISHED"}
    assert bpy.ops.gsmb.export_dynamic_equipment_package() == {"FINISHED"}
    equipment = scene.gsmb_prod_equipment_armature
    profile_path = OUTPUT_ROOT / "quaternius_witch_skirt.secondary_motion.json"
    glb_path = OUTPUT_ROOT / "quaternius_witch_skirt.glb"
    with profile_path.open("r", encoding="utf-8") as handle:
        profile = json.load(handle)

    result = {
        "source_bones": len(source.data.bones),
        "source_actions": len(bpy.data.actions),
        "skirt_vertices": len(skirt.data.vertices),
        "skirt_polygons": len(skirt.data.polygons),
        "dynamic_bones": sum(
            1 for bone in equipment.data.bones if bone.get("gsmb_secondary")
        ),
        "chains": len(profile["chains"]),
        "colliders": len(profile["colliders"]),
        "max_influences": max(len(vertex.groups) for vertex in skirt.data.vertices),
        "unweighted": sum(1 for vertex in skirt.data.vertices if not vertex.groups),
        "glb_bytes": glb_path.stat().st_size,
        "profile_bytes": profile_path.stat().st_size,
    }
    assert result["dynamic_bones"] == 24
    assert result["chains"] == 8
    assert result["colliders"] == 5
    assert result["max_influences"] <= 4
    assert result["unweighted"] == 0
    bpy.ops.wm.save_as_mainfile(
        filepath=str(OUTPUT_ROOT / "quaternius_witch_pipeline_test.blend")
    )
    print("GSMB_REAL_MODEL_SMOKE_OK " + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
