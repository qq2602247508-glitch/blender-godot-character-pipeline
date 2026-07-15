@tool
class_name GSMBDynamicEquipment
extends RetargetModifier3D
## Runtime wrapper for a Blender-authored dynamic equipment Skeleton3D.
##
## Required scene tree:
##   Main Skeleton3D
##     GSMBDynamicEquipment (this node)
##       Equipment Skeleton3D
##         MeshInstance3D(s)
##
## The main and equipment humanoid bones must be renamed to the names from
## SkeletonProfileHumanoid during Godot import. GSMB_* secondary names remain
## untouched.

signal profile_configured(profile_data: Dictionary)
signal profile_failed(message: String)

@export_file("*.json") var profile_path: String
@export var configure_on_ready := true


func _ready() -> void:
	profile = SkeletonProfileHumanoid.new()
	set_position_enabled(true)
	set_rotation_enabled(true)
	set_scale_enabled(false)
	set_use_global_pose(false)
	if configure_on_ready and not profile_path.is_empty():
		configure_from_profile(profile_path)


func configure_from_profile(path: String = profile_path) -> bool:
	if path.is_empty():
		return _fail("Secondary-motion profile path is empty")
	if not FileAccess.file_exists(path):
		return _fail("Secondary-motion profile does not exist: %s" % path)
	var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(path))
	if not parsed is Dictionary:
		return _fail("Secondary-motion profile is not a JSON object: %s" % path)
	var data: Dictionary = parsed
	if data.get("schema", "") != "gsmb-secondary-motion-v1":
		return _fail("Unsupported secondary-motion schema: %s" % data.get("schema", "missing"))
	var equipment_skeleton := _equipment_skeleton()
	if equipment_skeleton == null:
		return _fail("GSMBDynamicEquipment needs one direct Skeleton3D child")

	profile_path = path
	profile = SkeletonProfileHumanoid.new()
	var retarget: Dictionary = data.get("retarget", {})
	set_position_enabled(bool(retarget.get("position", true)))
	set_rotation_enabled(bool(retarget.get("rotation", true)))
	set_scale_enabled(bool(retarget.get("scale", false)))
	set_use_global_pose(bool(retarget.get("use_global_pose", false)))

	var simulator := _ensure_simulator(equipment_skeleton)
	_clear_generated_colliders(simulator)
	_build_colliders(simulator, data.get("colliders", []))
	if not _configure_chains(simulator, equipment_skeleton, data.get("chains", [])):
		return false
	profile_configured.emit(data)
	return true


func _equipment_skeleton() -> Skeleton3D:
	for child in get_children():
		if child is Skeleton3D:
			return child
	return null


func _ensure_simulator(equipment_skeleton: Skeleton3D) -> SpringBoneSimulator3D:
	var existing := equipment_skeleton.get_node_or_null("GSMB_SpringBone")
	if existing is SpringBoneSimulator3D:
		return existing
	var simulator := SpringBoneSimulator3D.new()
	simulator.name = "GSMB_SpringBone"
	equipment_skeleton.add_child(simulator)
	_set_generated_owner(simulator)
	return simulator


func _clear_generated_colliders(simulator: SpringBoneSimulator3D) -> void:
	for child in simulator.get_children():
		if child.has_meta("gsmb_generated_collider"):
			simulator.remove_child(child)
			child.queue_free()


func _build_colliders(simulator: SpringBoneSimulator3D, entries: Array) -> void:
	for entry_value in entries:
		if not entry_value is Dictionary:
			continue
		var entry: Dictionary = entry_value
		var collider: SpringBoneCollision3D
		var radius := float(entry.get("radius", 0.05))
		if entry.get("shape", "sphere") == "capsule":
			var capsule := SpringBoneCollisionCapsule3D.new()
			capsule.radius = radius
			capsule.height = max(float(entry.get("height", radius * 2.0)), radius * 2.0)
			collider = capsule
		else:
			var sphere := SpringBoneCollisionSphere3D.new()
			sphere.radius = radius
			collider = sphere
		collider.name = _safe_node_name(String(entry.get("name", "GSMB_Collider")))
		collider.bone_name = String(entry.get("bone", ""))
		collider.position_offset = _vector3(entry.get("position_offset", [0.0, 0.0, 0.0]))
		collider.set_meta("gsmb_generated_collider", true)
		simulator.add_child(collider)
		_set_generated_owner(collider)


func _configure_chains(
	simulator: SpringBoneSimulator3D,
	equipment_skeleton: Skeleton3D,
	entries: Array,
) -> bool:
	simulator.clear_settings()
	simulator.setting_count = entries.size()
	for index in entries.size():
		var entry_value: Variant = entries[index]
		if not entry_value is Dictionary:
			return _fail("Chain %d is not a JSON object" % index)
		var entry: Dictionary = entry_value
		var root_name := String(entry.get("root", ""))
		var end_name := String(entry.get("end", ""))
		if equipment_skeleton.find_bone(root_name) < 0:
			return _fail("Equipment skeleton is missing spring root: %s" % root_name)
		if equipment_skeleton.find_bone(end_name) < 0:
			return _fail("Equipment skeleton is missing spring end: %s" % end_name)
		simulator.set_root_bone_name(index, root_name)
		simulator.set_end_bone_name(index, end_name)
		simulator.set_stiffness(index, float(entry.get("stiffness", 0.45)))
		simulator.set_drag(index, float(entry.get("drag", 0.7)))
		simulator.set_gravity(index, float(entry.get("gravity", 0.35)))
		simulator.set_gravity_direction(index, _vector3(entry.get("gravity_direction", [0.0, -1.0, 0.0])))
		simulator.set_radius(index, float(entry.get("radius", 0.035)))
		simulator.set_rotation_axis(index, SkeletonModifier3D.ROTATION_AXIS_ALL)
		simulator.set_enable_all_child_collisions(index, true)
	return true


func reset_secondary_motion() -> void:
	var equipment_skeleton := _equipment_skeleton()
	if equipment_skeleton == null:
		return
	var simulator := equipment_skeleton.get_node_or_null("GSMB_SpringBone")
	if simulator is SpringBoneSimulator3D:
		simulator.active = false
		simulator.active = true


func _set_generated_owner(node: Node) -> void:
	if Engine.is_editor_hint() and owner != null:
		node.owner = owner


func _vector3(value: Variant) -> Vector3:
	if value is Array and value.size() >= 3:
		return Vector3(float(value[0]), float(value[1]), float(value[2]))
	return Vector3.ZERO


func _safe_node_name(value: String) -> String:
	return value.replace(":", "_").replace("/", "_").replace(".", "_")


func _fail(message: String) -> bool:
	push_error(message)
	profile_failed.emit(message)
	return false
