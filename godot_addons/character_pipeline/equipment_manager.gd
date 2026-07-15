@tool
class_name GSMBEquipmentManager
extends Node
## Equips rigid, shared-skeleton, and dynamic character parts.
##
## Node paths are relative to this manager. Body regions should point to
## Node3D or CanvasItem nodes whose visibility can safely be toggled.

signal equipped(slot: StringName, definition: Resource, instance: Node)
signal unequipped(slot: StringName)
signal equip_failed(message: String)

@export_node_path("Skeleton3D") var main_skeleton_path: NodePath
@export var regular_equipment_root_path: NodePath
@export var body_region_paths: Dictionary = {}

var _equipped: Dictionary = {}
var _region_hide_counts: Dictionary = {}


func equip(definition: Resource) -> Node:
	if definition == null:
		return _fail("Equipment definition is null")
	var slot := StringName(definition.get("slot"))
	var packed_scene := definition.get("scene") as PackedScene
	if slot.is_empty():
		return _fail("Equipment definition has no slot")
	if packed_scene == null:
		return _fail("Equipment '%s' has no PackedScene" % definition.get("id"))
	var main_skeleton := _main_skeleton()
	if main_skeleton == null:
		return _fail("Main Skeleton3D path is invalid: %s" % main_skeleton_path)

	for incompatible_slot in definition.get("incompatible_slots"):
		unequip(StringName(incompatible_slot))
	unequip(slot)

	var instance := packed_scene.instantiate()
	var wrapper: Node = instance
	var kind := int(definition.get("kind"))
	match kind:
		0:
			var attachment := BoneAttachment3D.new()
			attachment.name = "GSMB_%s_Attachment" % slot
			attachment.bone_name = StringName(definition.get("attachment_bone"))
			main_skeleton.add_child(attachment)
			attachment.add_child(instance)
			wrapper = attachment
		1:
			var regular_root := _regular_equipment_root()
			regular_root.add_child(instance)
			_bind_meshes_to_main_skeleton(instance, main_skeleton)
		2:
			if not instance is RetargetModifier3D or not instance.has_method("configure_from_profile"):
				instance.free()
				return _fail("Dynamic equipment scene root must be GSMBDynamicEquipment")
			main_skeleton.add_child(instance)
			var profile_path := String(definition.get("secondary_motion_profile"))
			instance.set("profile_path", profile_path)
			instance.set("configure_on_ready", false)
			if not instance.call("configure_from_profile", profile_path):
				main_skeleton.remove_child(instance)
				instance.free()
				return _fail("Dynamic equipment profile failed: %s" % profile_path)
			instance.call("reset_secondary_motion")
		_:
			instance.free()
			return _fail("Unsupported equipment kind: %d" % kind)

	var hidden_regions := PackedStringArray(definition.get("hide_body_regions"))
	_apply_hidden_regions(hidden_regions, 1)
	_equipped[slot] = {
		"definition": definition,
		"instance": instance,
		"wrapper": wrapper,
		"hidden_regions": hidden_regions,
	}
	equipped.emit(slot, definition, instance)
	return instance


func unequip(slot: StringName) -> void:
	if not _equipped.has(slot):
		return
	var record: Dictionary = _equipped[slot]
	_apply_hidden_regions(record.get("hidden_regions", PackedStringArray()), -1)
	var wrapper := record.get("wrapper") as Node
	if is_instance_valid(wrapper):
		wrapper.queue_free()
	_equipped.erase(slot)
	unequipped.emit(slot)


func unequip_all() -> void:
	for slot in _equipped.keys().duplicate():
		unequip(StringName(slot))


func get_equipped(slot: StringName) -> Resource:
	if not _equipped.has(slot):
		return null
	return _equipped[slot].get("definition") as Resource


func _main_skeleton() -> Skeleton3D:
	return get_node_or_null(main_skeleton_path) as Skeleton3D


func _regular_equipment_root() -> Node:
	if regular_equipment_root_path.is_empty():
		return self
	var root := get_node_or_null(regular_equipment_root_path)
	return root if root != null else self


func _bind_meshes_to_main_skeleton(root: Node, main_skeleton: Skeleton3D) -> void:
	if root is MeshInstance3D:
		root.skeleton = root.get_path_to(main_skeleton)
	for child in root.get_children():
		_bind_meshes_to_main_skeleton(child, main_skeleton)


func _apply_hidden_regions(regions: PackedStringArray, delta: int) -> void:
	for region in regions:
		var key := StringName(region)
		var count: int = max(0, int(_region_hide_counts.get(key, 0)) + delta)
		_region_hide_counts[key] = count
		var path: Variant = body_region_paths.get(key, body_region_paths.get(String(key), NodePath()))
		var node := get_node_or_null(NodePath(path))
		if node is Node3D or node is CanvasItem:
			node.set("visible", count == 0)


func _fail(message: String) -> Node:
	push_error(message)
	equip_failed.emit(message)
	return null
