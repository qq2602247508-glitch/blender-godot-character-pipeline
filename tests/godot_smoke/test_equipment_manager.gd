extends SceneTree

const DynamicEquipment = preload("res://godot_addons/character_pipeline/dynamic_equipment.gd")
const EquipmentDefinition = preload("res://godot_addons/character_pipeline/equipment_definition.gd")
const EquipmentManager = preload("res://godot_addons/character_pipeline/equipment_manager.gd")


func _init() -> void:
	call_deferred("_run")


func _run() -> void:
	var character := Node3D.new()
	character.name = "Character"
	root.add_child(character)
	var skeleton := _make_source_skeleton()
	character.add_child(skeleton)
	var hip_region := Node3D.new()
	hip_region.name = "BodyHip"
	character.add_child(hip_region)
	var manager: Node = EquipmentManager.new()
	manager.name = "EquipmentManager"
	manager.main_skeleton_path = NodePath("../MainSkeleton")
	manager.body_region_paths = {&"hip": NodePath("../BodyHip")}
	character.add_child(manager)
	await process_frame

	var definition: Resource = EquipmentDefinition.new()
	definition.id = &"smoke_skirt"
	definition.slot = &"lower_body"
	definition.kind = 2
	definition.scene = _make_dynamic_scene()
	definition.secondary_motion_profile = "res://tests/godot_smoke/skirt_profile.json"
	definition.hide_body_regions = PackedStringArray(["hip"])

	var instance: Node = manager.equip(definition)
	if not _assert(instance != null, "dynamic equipment should equip"): return
	if not _assert(instance.get_parent() == skeleton, "dynamic root should be a direct child of main skeleton"): return
	if not _assert(not hip_region.visible, "occupied body region should be hidden"): return
	var equipment_skeleton := instance.get_node("EquipmentSkeleton") as Skeleton3D
	if not _assert(equipment_skeleton.get_node_or_null("GSMB_SpringBone") is SpringBoneSimulator3D, "spring simulator should be configured"): return
	if not _assert(manager.get_equipped(&"lower_body") == definition, "slot should report its definition"): return

	manager.unequip(&"lower_body")
	await process_frame
	if not _assert(hip_region.visible, "body region should be restored after unequip"): return
	if not _assert(manager.get_equipped(&"lower_body") == null, "slot should be empty after unequip"): return
	print("GSMB_EQUIPMENT_MANAGER_SMOKE_OK slot=lower_body hidden_region=hip")
	character.queue_free()
	definition = null
	await process_frame
	quit(0)


func _make_dynamic_scene() -> PackedScene:
	var dynamic: RetargetModifier3D = DynamicEquipment.new()
	dynamic.name = "DynamicSkirt"
	dynamic.configure_on_ready = false
	var equipment := _make_equipment_skeleton()
	dynamic.add_child(equipment)
	equipment.owner = dynamic
	var packed := PackedScene.new()
	var result := packed.pack(dynamic)
	if result != OK:
		push_error("Could not pack dynamic smoke scene")
	dynamic.free()
	return packed


func _make_source_skeleton() -> Skeleton3D:
	var skeleton := Skeleton3D.new()
	skeleton.name = "MainSkeleton"
	_add_bone(skeleton, "Root", -1, Vector3.ZERO)
	_add_bone(skeleton, "Hips", 0, Vector3(0.0, 1.0, 0.0))
	_add_bone(skeleton, "Chest", 1, Vector3(0.0, 0.4, 0.0))
	_add_bone(skeleton, "Head", 2, Vector3(0.0, 0.4, 0.0))
	return skeleton


func _make_equipment_skeleton() -> Skeleton3D:
	var skeleton := Skeleton3D.new()
	skeleton.name = "EquipmentSkeleton"
	_add_bone(skeleton, "Root", -1, Vector3.ZERO)
	_add_bone(skeleton, "Hips", 0, Vector3(0.0, 1.0, 0.0))
	_add_bone(skeleton, "Chest", 1, Vector3(0.0, 0.4, 0.0))
	_add_bone(skeleton, "Head", 2, Vector3(0.0, 0.4, 0.0))
	_add_bone(skeleton, "GSMB_Skirt_01_01", 1, Vector3(0.2, 0.0, 0.0))
	_add_bone(skeleton, "GSMB_Skirt_01_02", 4, Vector3(0.0, -0.25, 0.0))
	_add_bone(skeleton, "GSMB_Skirt_01_03", 5, Vector3(0.0, -0.25, 0.0))
	return skeleton


func _add_bone(skeleton: Skeleton3D, bone_name: String, parent: int, rest_position: Vector3) -> void:
	var index := skeleton.get_bone_count()
	skeleton.add_bone(bone_name)
	skeleton.set_bone_parent(index, parent)
	var rest := Transform3D.IDENTITY
	rest.origin = rest_position
	skeleton.set_bone_rest(index, rest)


func _assert(condition: bool, message: String) -> bool:
	if condition:
		return true
	push_error("SMOKE ASSERTION FAILED: " + message)
	quit(1)
	return false
