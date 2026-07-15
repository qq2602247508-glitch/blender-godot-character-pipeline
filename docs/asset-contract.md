# Character asset contract

## Canonical humanoid

- Project identifier: `HERO_RIG_V1`.
- Blender input adapters may accept Mixamo or Hunyuan names.
- Godot imports must map and rename humanoid bones to `SkeletonProfileHumanoid` names.
- Human animation clips never contain equipment secondary-bone tracks.
- Human animation assets are imported as shared `AnimationLibrary` resources.

## Equipment classes

### Rigid

Weapons, glasses, hats, backpacks, and other undeformed accessories use `BoneAttachment3D`.

### Shared-skeleton skinned

Shirts, trousers, gloves, shoes, and short rigid hairstyles bind directly to the main `Skeleton3D`. They carry no animation library.

### Dynamic

Long hair, skirts, coats, capes, braids, and tails contain an equipment-local skeleton. The local skeleton includes copied humanoid anchor bones plus equipment-specific `GSMB_*` chains.

Complex layered hair may be authored before the humanoid rig exists. Each
`GSMB_HAIR_GUIDES` curve records a source mesh, root vertex, tip vertex, and an
editable surface path. After rigging, Hair / From Guides converts those curves
to equipment bones and weights every vertex to its closest guide chain. Simple
ponytails may continue to use automatic distribution.

Required Godot hierarchy:

```text
Main Skeleton3D
  GSMBDynamicEquipment (RetargetModifier3D)
    Equipment Skeleton3D
      MeshInstance3D
      GSMB_SpringBone (created/configured from JSON)
```

## Blender export outputs

Each dynamic item produces:

- one equipment GLB containing its mesh, Skin, humanoid anchor bones, and `GSMB_*` bones;
- one `*.secondary_motion.json` profile;
- one equipment definition resource in the game project.

The Blender add-on's **Export Godot Equipment Package** button writes the GLB
and JSON pair with the same asset identifier. In Godot, create a
`GSMBEquipmentDefinition` resource, assign its imported scene and JSON path,
then pass it to `GSMBEquipmentManager.equip()`.

## Runtime equipment manager

Attach `GSMBEquipmentManager` under the character and set `main_skeleton_path`.
Definitions choose one of three paths automatically:

- `RIGID`: creates a `BoneAttachment3D` on `attachment_bone`;
- `SHARED_SKINNED`: instances the scene and points every `MeshInstance3D` to
  the main skeleton;
- `DYNAMIC`: places `GSMBDynamicEquipment` directly under the main skeleton,
  loads the secondary-motion JSON, and resets its spring simulation.

`hide_body_regions` is reference-counted, so overlapping equipment does not
reveal a body section until every item covering that section is removed.

The generated equipment rig automatically renames its five required anchors to
`Head`, `Chest`, `Hips`, `LeftUpperLeg`, and `RightUpperLeg`. The source character
is never renamed. JSON preserves source names for diagnostics while using Godot
humanoid names for collider bindings.

## Validation gates

- All mesh vertices are weighted.
- No vertex exceeds four bone influences.
- All secondary chain roots and ends exist.
- Every collider is bone-parented in Blender.
- Armature scale is applied.
- Main and equipment humanoid bones are renamed to the same Godot profile names.
- Dynamic equipment is reset when equipped to avoid inherited spring velocity.

## Body visibility

Each equipment definition records occupied slots and hidden body regions. A minimum region vocabulary is:

```text
head, chest, belly, hip,
upper_arm_l, upper_arm_r,
lower_arm_l, lower_arm_r,
hand_l, hand_r,
upper_leg_l, upper_leg_r,
lower_leg_l, lower_leg_r,
foot_l, foot_r
```
