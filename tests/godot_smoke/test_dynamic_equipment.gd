extends SceneTree

const DynamicEquipment = preload("res://godot_addons/character_pipeline/dynamic_equipment.gd")


func _init() -> void:
	call_deferred("_run")


func _run() -> void:
	var host := Node3D.new()
	root.add_child(host)
	var source := _make_source_skeleton()
	host.add_child(source)
	var dynamic: RetargetModifier3D = DynamicEquipment.new()
	dynamic.name = "DynamicSkirt"
	dynamic.configure_on_ready = false
	source.add_child(dynamic)
	var equipment := _make_equipment_skeleton()
	dynamic.add_child(equipment)
	await process_frame

	if not _assert(dynamic.configure_from_profile("res://tests/godot_smoke/skirt_profile.json"), "profile should configure"): return
	if not _assert(dynamic.profile is SkeletonProfileHumanoid, "humanoid retarget profile should be assigned"): return
	var simulator := equipment.get_node_or_null("GSMB_SpringBone") as SpringBoneSimulator3D
	if not _assert(simulator != null, "spring simulator should be created"): return
	if not _assert(simulator.setting_count == 1, "one spring chain should be configured"): return
	if not _assert(simulator.get_child_count() == 2, "two colliders should be created"): return
	if not _assert(simulator.get_root_bone_name(0) == "GSMB_Skirt_01_01", "root bone should match profile"): return
	if not _assert(simulator.get_end_bone_name(0) == "GSMB_Skirt_01_03", "end bone should match profile"): return
	if not _assert(dynamic.get_parent() == source and equipment.get_parent() == dynamic, "retarget hierarchy should be source -> modifier -> equipment"): return

	var source_hips := source.find_bone("Hips")
	var target_hips := equipment.find_bone("Hips")
	var before := equipment.get_bone_pose_position(target_hips)
	print("SOURCE before=", source.get_bone_pose_position(source_hips))
	source.set_bone_pose_position(source_hips, Vector3(0.2, 0.0, 0.0))
	print("SOURCE authored=", source.get_bone_pose_position(source_hips))
	await process_frame
	var after := equipment.get_bone_pose_position(target_hips)
	print("RETARGET before=", before, " after=", after)
	if not _assert(is_equal_approx(after.x - before.x, 0.2), "retarget should copy Hips X delta"): return
	print("GSMB_GODOT_SMOKE_OK chains=1 colliders=2 retarget_delta=0.2")
	quit(0)


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
