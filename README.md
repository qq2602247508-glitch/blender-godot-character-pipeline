# Blender Godot Character Pipeline

Production pipeline for AI-generated 3D game characters with one canonical humanoid animation skeleton, modular equipment, and runtime hair/skirt/cape secondary motion in Godot 4.5+.

## Architecture

```text
Hunyuan / Mixamo input
        -> HERO_RIG_V1
        -> shared AnimationLibrary

Rigid equipment
        -> BoneAttachment3D

Normal skinned clothing
        -> shared main Skeleton3D

Dynamic hair / skirt / cape
        -> equipment-local Skeleton3D
        -> RetargetModifier3D
        -> SpringBoneSimulator3D
```

## Repository layout

- `blender_addons/game_secondary_motion_builder/`: Blender authoring, weight generation, bone-bound collider proxies, and Godot JSON export.
- `godot_addons/character_pipeline/`: Godot retarget and spring-bone runtime setup.
- `tests/godot_smoke/`: headless Godot API and runtime smoke tests.
- `docs/`: asset conventions and workflow notes.

## Blender quick start

1. Install `blender_addons/game_secondary_motion_builder` as an addon.
2. In `3D View > Secondary Motion > Production Dynamic Equipment`, choose the source humanoid armature.
3. Select the armature and one or more hair/skirt meshes.
4. Build and validate the dynamic equipment rig.
5. Use **Export GLB + Godot Profile** to create a matched runtime package.

The production builder accepts Mixamo-like bone names and exports Godot `SkeletonProfileHumanoid` names for the retarget/collider layer. Use Godot's BoneMap/Bone Renamer on import so both main and equipment humanoid bones use `Root`, `Hips`, `Chest`, `Head`, `LeftUpperLeg`, and related profile names.

## Local Mixamo-like hero rig (MVP)

`3D View > Secondary Motion > Hero Auto-Rig` provides an offline landmark workflow:

1. Select the complete base character mesh and click **Start / Reset Landmark Rigging**.
2. From a front orthographic view, click pelvis, chest, neck, head, then the left shoulder/arm/leg landmarks shown in the Chinese status line. A live orange ring and center dot confirm the hit position; placed rings remain visible and the right side appears automatically. `Ctrl+Z` removes the last point and its mirror; the 3D Cursor button is the manual fallback.
3. Click **Build HERO_RIG_V2 + Fingers**. The generated deform skeleton has 54 bones, including three segments for all ten fingers.
4. Classify modular meshes as body, fixed replacement, rigid accessory, hair, skirt/cape, or ignored. **Bind Selected Parts** uses automatic weights for ordinary skinned parts and nearest-bone parenting for rigid accessories.
5. Keep one approved weighted base body. For later clothes and shoes, select the new parts and use **Transfer Body Weights to Clothes**, then correct only difficult joints in Weight Paint.
6. Hair and skirt/cape parts use editable `GSMB_FIXED` and `GSMB_DYNAMIC` vertex groups. Run **Auto Fixed / Dynamic Mask**, then override selected vertices with the Fixed/Dynamic buttons before sending the part to Production Dynamic Equipment.

The landmark generator deliberately has no mandatory Rigify dependency. Rigify can still be added later as a control-rig layer, while `HERO_RIG_V2` remains the stable deform/export skeleton.

## Godot scene contract

```text
Main Skeleton3D
  GSMBDynamicEquipment (RetargetModifier3D)
    Equipment Skeleton3D
      MeshInstance3D
      GSMB_SpringBone (generated at runtime)
```

Assign the Blender-exported JSON to `GSMBDynamicEquipment.profile_path`. The component creates and configures `SpringBoneSimulator3D` and its bone-bound sphere/capsule colliders.

For modular characters, add `GSMBEquipmentManager` below the character and
create one `GSMBEquipmentDefinition` resource per item. The manager handles
`BoneAttachment3D` accessories, meshes sharing the main skeleton, dynamic
equipment-local skeletons, incompatible slots, and reference-counted body-part
visibility.

## Tests

```bash
/Applications/Godot.app/Contents/MacOS/Godot \
  --headless --path . \
  --script tests/godot_smoke/test_dynamic_equipment.gd

/Applications/Godot.app/Contents/MacOS/Godot \
  --headless --path . \
  --script tests/godot_smoke/test_equipment_manager.gd

/Applications/Blender.app/Contents/MacOS/Blender \
  --background --factory-startup \
  --python tests/blender_smoke.py

/Applications/Blender.app/Contents/MacOS/Blender \
  --background --factory-startup \
  --python tests/blender_hair_guides_smoke.py

GSMB_REAL_MODEL=/path/to/Witch.gltf \
/Applications/Blender.app/Contents/MacOS/Blender \
  --background --factory-startup \
  --python tests/blender_real_model_smoke.py
```

The optional real-model regression uses Quaternius'
[CC0 Ultimate Modular Women](https://quaternius.com/packs/ultimatemodularwomen.html)
`Witch.gltf`. It non-destructively extracts a skirt test mesh from the modular
body and exercises the complete package export without committing third-party
asset files to this repository.

## Semi-automatic complex hair

Complex curls and layered AI hair can use editable surface guides before the
character has an armature. In Hair / From Guides mode:

1. Edit a hair part and select a small root region.
2. Click **Capture Selected Root**.
3. Select the matching tip region and click **Create Guide to Selected Tip**.
4. Edit the generated curve if needed and repeat for each major lock.
5. After the humanoid rig exists, build the hair equipment from those guides.

Simple ponytails can still use automatic distribution. Guide-driven weighting
assigns each vertex to the closest chain and keeps the four-influence limit.
