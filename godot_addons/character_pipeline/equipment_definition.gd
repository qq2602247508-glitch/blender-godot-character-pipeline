@tool
class_name GSMBEquipmentDefinition
extends Resource
## Data-only description of one equippable character part.

enum Kind {
	RIGID,
	SHARED_SKINNED,
	DYNAMIC,
}

@export var id: StringName
@export var slot: StringName
@export var kind: Kind = Kind.SHARED_SKINNED
@export var scene: PackedScene
@export_file("*.json") var secondary_motion_profile: String
@export var attachment_bone: StringName = &"Head"
@export var hide_body_regions: PackedStringArray
@export var incompatible_slots: PackedStringArray
