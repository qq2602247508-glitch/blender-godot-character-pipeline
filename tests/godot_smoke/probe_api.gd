extends SceneTree


func _init() -> void:
	for inspected_class in [
		"RetargetModifier3D",
		"SpringBoneSimulator3D",
		"SpringBoneCollisionSphere3D",
		"SpringBoneCollisionCapsule3D",
	]:
		print("CLASS ", inspected_class, " exists=", ClassDB.class_exists(inspected_class))
		var methods: Array = ClassDB.class_get_method_list(inspected_class, true)
		var selected: Array[String] = []
		for method in methods:
			var method_name := String(method.name)
			if method_name.begins_with("set_") or method_name in ["clear_settings"]:
				selected.append(method_name)
		selected.sort()
		print("METHODS ", inspected_class, " ", selected)
	var humanoid := SkeletonProfileHumanoid.new()
	var humanoid_bones: Array[String] = []
	for index in humanoid.bone_size:
		humanoid_bones.append(humanoid.get_bone_name(index))
	print("HUMANOID_BONES ", humanoid_bones)
	quit()
