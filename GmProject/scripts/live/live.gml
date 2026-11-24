/// CppSeparate BoolType live_server_start()
function live_server_start()
{
	return false
}

/// CppSeparate void live_server_stop()
function live_server_stop()
{
}

/// CppSeparate StringType live_server_session()
function live_server_session()
{
	return ""
}

/// CppSeparate IntType live_request_next(BoolType, IntType)
function live_request_next(ready, typemap)
{
	return -1
}

/// CppSeparate void live_request_reply(StringType)
function live_request_reply(json)
{
}

function live_startup()
{
	globalvar live_active, live_start_pending, live_revision, live_generation, live_busy_seen;

	live_active = false
	live_start_pending = (!automation_active && environment_get_variable("MINEIMATOR_LIVE_SESSION") = "1")
	live_revision = 0
	live_generation = 1
	live_busy_seen = false
}

function action_live_toggle()
{
	live_set(!live_active)
}

function live_set(enable)
{
	if (enable = live_active)
		return 0

	if (!enable)
	{
		live_server_stop()
		live_active = false
		toast_new(e_toast.INFO, text_get("alertagenteditingoff"))
		return 0
	}

	live_active = live_server_start()
	if (live_active)
		toast_new(e_toast.POSITIVE, text_get("alertagenteditingon"))
	else
		toast_new(e_toast.NEGATIVE, text_get("alertagenteditingfailed"))
}

function live_ready()
{
	return (window_state = "" && window_busy = "" && !textbox_isediting && popup = null && !mouse_left &&
			!keyboard_check(vk_anykey) && !keyboard_check_pressed(vk_anykey) && ds_priority_size(load_queue) = 0)
}

function live_update()
{
	if (live_start_pending)
	{
		live_start_pending = false
		live_set(true)
	}

	if (!live_active)
		return 0

	var ready = live_ready();
	if (!ready)
		live_busy_seen = true
	else if (live_busy_seen)
	{
		live_busy_seen = false
		live_revision++
	}

	var types, request;
	types = ds_int_map_create()
	request = live_request_next(ready, types)
	if (request < 0)
	{
		ds_map_destroy(types)
		return 0
	}

	automation_types = types
	automation_error_code = ""
	automation_error_message = ""

	var reply = live_handle(request);
	if (reply = "")
		reply = "{\"status\":\"error\",\"error\":{\"code\":" + automation_json_string(automation_error_code) + ",\"message\":" + automation_json_string(automation_error_message) + "}," + live_state_json() + "}"
	live_request_reply(reply)

	automation_types = null
	ds_map_destroy(request)
	ds_map_destroy(types)
}

function live_state_json()
{
	return "\"documentGeneration\":" + string(live_generation) + ",\"revision\":" + string(live_revision)
}

function live_handle(request)
{
	if (!automation_get_string(request, "op", "request", true))
		return ""

	switch (request[?"op"])
	{
		case "hello":
			return (automation_check_keys(request, ["op"], "hello") ? live_hello() : "")

		case "inspect":
		{
			if (!automation_check_keys(request, ["op", "frame", "selectedOnly"], "inspect") ||
				!automation_get_number(request, "frame", "inspect", false, 0, 100000, false))
				return ""
			if (ds_map_exists(request, "selectedOnly") && automation_json_type(request, "selectedOnly") != e_json_type.BOOL)
			{
				automation_fail("INVALID_REQUEST", "Field \"selectedOnly\" in inspect must be a boolean")
				return ""
			}
			return live_inspect(is_undefined(request[?"frame"]) ? timeline_marker : request[?"frame"], request[?"selectedOnly"] = true)
		}

		case "apply":
			return live_apply(request)
	}

	automation_fail("UNSUPPORTED", "Unsupported op \"" + request[?"op"] + "\", supported ops are hello, inspect, apply")
	return ""
}

function live_project_json()
{
	return "{\"name\":" + automation_json_string(project_name) + ",\"file\":" + (project_file != "" ? automation_json_string(project_file) : "null") +
		   ",\"tempo\":" + string(project_tempo) + ",\"frame\":" + string_decimals(timeline_marker) + ",\"length\":" + string(timeline_length) +
		   ",\"unsavedChanges\":" + automation_json_bool(project_changed) + "}"
}

function live_hello()
{
	var names, values, transitions;
	names = automation_value_list()
	values = ""
	for (var i = 0; i < array_length(names); i++)
		values += (i > 0 ? "," : "") + automation_json_string(names[i])

	transitions = ""
	for (var i = 0; i < ds_list_size(transition_list); i++)
		transitions += (i > 0 ? "," : "") + automation_json_string(transition_list[|i])

	return "{\"status\":\"ok\"," + live_state_json() + ",\"project\":" + live_project_json() +
		   ",\"ops\":[\"hello\",\"inspect\",\"apply\"],\"operations\":[\"keyframe.set\"],\"values\":[" + values + "],\"transitions\":[" + transitions + "]}"
}

function live_value_supported(tl, name)
{
	if (tl.lock)
		return false

	switch (name)
	{
		case "POS_X": case "POS_Y": case "POS_Z":
			return tl.value_type[e_value_type.TRANSFORM_POS]
		case "ROT_X": case "ROT_Y": case "ROT_Z":
			return tl.value_type[e_value_type.TRANSFORM_ROT]
		case "SCA_X": case "SCA_Y": case "SCA_Z":
			return tl.value_type[e_value_type.TRANSFORM_SCA]
		case "BEND_ANGLE_X": case "BEND_ANGLE_Y": case "BEND_ANGLE_Z":
			return tl.value_type[e_value_type.TRANSFORM_BEND]
		case "ALPHA":
			return tl.value_type[e_value_type.MATERIAL_COLOR]
		case "VISIBLE":
			return tl.value_type[e_value_type.TRANSFORM]
		case "CAM_FOV": case "CAM_ROTATE": case "CAM_ROTATE_DISTANCE": case "CAM_ROTATE_ANGLE_XY": case "CAM_ROTATE_ANGLE_Z":
			return tl.value_type[e_value_type.CAMERA]
		case "LIGHT_STRENGTH": case "LIGHT_RANGE":
			return tl.value_type[e_value_type.LIGHT]
	}
	return false
}

function live_values_json(tl, vals)
{
	var names, str;
	names = automation_value_list()
	str = ""
	for (var i = 0; i < array_length(names); i++)
	{
		if (!live_value_supported(tl, names[i]))
			continue

		var val = vals[ds_list_find_index(value_name_list, names[i])];
		str += (str = "" ? "" : ",") + automation_json_string(names[i]) + ":" + (automation_value_is_bool(names[i]) ? automation_json_bool(val) : string_decimals(val))
	}
	return "{" + str + "}"
}

function live_evaluate(frame)
{
	timeline_marker = frame
	with (obj_timeline)
		tl_update_values()
}

function live_inspect(frame, selectedonly)
{
	var marker, objects, selection;
	marker = timeline_marker
	live_evaluate(frame)

	objects = ""
	selection = ""
	for (var i = 0; i < ds_list_size(project_timeline_list); i++)
	{
		var tl = project_timeline_list[|i];
		if (tl.selected)
			selection += (selection = "" ? "" : ",") + automation_json_string(tl.save_id)
		if (selectedonly && !tl.selected)
			continue

		var editable, names, keyframes;
		editable = ""
		names = automation_value_list()
		for (var n = 0; n < array_length(names); n++)
			if (live_value_supported(tl, names[n]))
				editable += (editable = "" ? "" : ",") + automation_json_string(names[n])

		keyframes = ""
		for (var k = 0; k < ds_list_size(tl.keyframe_list); k++)
		{
			var kf = tl.keyframe_list[|k];
			keyframes += (k > 0 ? "," : "") + "{\"frame\":" + string(kf.position) + ",\"transition\":" + automation_json_string(kf.value[e_value.TRANSITION]) + ",\"values\":" + live_values_json(tl, kf.value) + "}"
		}

		objects += (objects = "" ? "" : ",") + "{\"ref\":" + automation_json_string(tl.save_id) + ",\"name\":" + automation_json_string(tl.display_name) +
				   ",\"type\":" + automation_json_string(tl_type_name_list[|tl.type]) +
				   ",\"parent\":" + (tl.parent != app ? automation_json_string(tl.parent.save_id) : "null") +
				   ",\"partOf\":" + (tl.part_of != null ? automation_json_string(tl.part_of.save_id) : "null") +
				   ",\"part\":" + (tl.part_of != null && tl.type = e_tl_type.BODYPART ? automation_json_string(tl.model_part_name) : "null") +
				   ",\"selected\":" + automation_json_bool(tl.selected) + ",\"hidden\":" + automation_json_bool(tl.hide) + ",\"locked\":" + automation_json_bool(tl.lock) +
				   ",\"editable\":[" + editable + "],\"values\":" + live_values_json(tl, tl.value) + ",\"keyframes\":[" + keyframes + "]}"
	}

	live_evaluate(marker)

	return "{\"status\":\"ok\"," + live_state_json() + ",\"project\":" + live_project_json() + ",\"frame\":" + string_decimals(frame) +
		   ",\"selection\":[" + selection + "],\"objects\":[" + objects + "]}"
}

function live_keyframe_at(tl, frame)
{
	for (var k = 0; k < ds_list_size(tl.keyframe_list); k++)
		if (tl.keyframe_list[|k].position = frame)
			return tl.keyframe_list[|k]
	return null
}

function live_check_operation(op, where, timelines)
{
	if (!automation_is_object(op))
		return automation_fail("INVALID_REQUEST", where + " must be an object")

	if (!automation_check_keys(op, ["op", "target", "frame", "values", "transition"], where) ||
		!automation_get_string(op, "op", where, true) ||
		!automation_get_string(op, "target", where, true) ||
		!automation_get_number(op, "frame", where, true, 0, 100000, true) ||
		!automation_get_string(op, "transition", where, false))
		return false

	if (op[?"op"] != "keyframe.set")
		return automation_fail("UNSUPPORTED", "Unsupported operation \"" + op[?"op"] + "\" in " + where + ", supported operations are keyframe.set")

	var tl = timelines[?op[?"target"]];
	if (is_undefined(tl))
		return automation_fail("OBJECT_NOT_FOUND", "No object with ref \"" + op[?"target"] + "\" in " + where + ", inspect again")
	if (tl.lock)
		return automation_fail("UNSUPPORTED", "Target \"" + tl.display_name + "\" in " + where + " is locked")

	if (is_string(op[?"transition"]) && ds_list_find_index(transition_list, op[?"transition"]) < 0)
		return automation_fail("INVALID_REQUEST", "Unknown transition \"" + op[?"transition"] + "\" in " + where + ", see hello")

	if (automation_json_type(op, "values") != e_json_type.OBJECT)
		return automation_fail("INVALID_REQUEST", "Field \"values\" in " + where + " must be an object")

	var values, names, key;
	values = op[?"values"]
	names = automation_value_list()
	key = ds_map_find_first(values)
	while (!is_undefined(key))
	{
		if (!array_contains(names, key))
			return automation_fail("UNSUPPORTED", "Unsupported value \"" + string(key) + "\" in " + where + ", see hello")
		if (!live_value_supported(tl, key))
			return automation_fail("UNSUPPORTED", "Value " + key + " is not editable on \"" + tl.display_name + "\" in " + where + ", see its editable list")

		if (automation_value_is_bool(key))
		{
			if (automation_json_type(values, key) != e_json_type.BOOL)
				return automation_fail("INVALID_REQUEST", "Value " + key + " in " + where + " must be a boolean")
		}
		else if (!automation_get_number(values, key, where + " values", true, -1000000, 1000000, false))
			return false

		key = ds_map_find_next(values, key)
	}

	if (ds_map_size(values) = 0 && !is_string(op[?"transition"]))
		return automation_fail("INVALID_REQUEST", where + " must set at least one value or a transition")

	return true
}

function live_apply(request)
{
	if (!automation_check_keys(request, ["op", "documentGeneration", "expectedRevision", "label", "operations"], "apply") ||
		!automation_get_number(request, "documentGeneration", "apply", true, 0, 1000000000, true) ||
		!automation_get_number(request, "expectedRevision", "apply", true, 0, 1000000000, true) ||
		!automation_get_string(request, "label", "apply", false))
		return ""

	if (automation_json_type(request, "operations") != e_json_type.ARRAY || ds_list_size(request[?"operations"]) < 1 || ds_list_size(request[?"operations"]) > 500)
	{
		automation_fail("INVALID_REQUEST", "Field \"operations\" in apply must be an array of 1 to 500 operations")
		return ""
	}

	var label = (is_string(request[?"label"]) ? string_copy(request[?"label"], 1, 80) : "");

	if (request[?"documentGeneration"] != live_generation)
	{
		automation_fail("DOCUMENT_CHANGED", "Another project was opened or created since document generation " + string(request[?"documentGeneration"]) + ", inspect again")
		return ""
	}

	if (request[?"expectedRevision"] != live_revision)
	{
		automation_fail("REVISION_CONFLICT", "The scene changed since revision " + string(request[?"expectedRevision"]) + ", it is now " + string(live_revision) + ". Inspect again and rebuild the patch")
		return ""
	}

	var timelines, ops;
	timelines = ds_map_create()
	for (var i = 0; i < ds_list_size(project_timeline_list); i++)
		timelines[?project_timeline_list[|i].save_id] = project_timeline_list[|i]

	ops = request[?"operations"]
	for (var i = 0; i < ds_list_size(ops); i++)
	{
		if (!live_check_operation(ops[|i], "operations[" + string(i) + "]", timelines))
		{
			ds_map_destroy(timelines)
			return ""
		}
	}

	var hobj, applied;
	hobj = history_set(action_live_patch)
	applied = live_patch_apply(hobj, ops, timelines)
	ds_map_destroy(timelines)
	live_patch_refresh()

	toast_new(e_toast.INFO, text_get("alertagentedit", (label != "" ? label : text_get("alertagenteditdefault")), text_control_name(keybinds[e_keybind.UNDO].keybind)))

	return "{\"status\":\"ok\"," + live_state_json() + ",\"applied\":[" + applied + "]}"
}

function live_patch_snapshot(after, n, kf)
{
	for (var v = 0; v < e_value.amount; v++)
	{
		if (after)
			patch_after[n, v] = tl_value_get_save_id(v, kf.value[v])
		else
			patch_before[n, v] = tl_value_get_save_id(v, kf.value[v])
	}
}

function live_patch_apply(hobj, ops, timelines)
{
	var marker, applied;
	marker = timeline_marker
	applied = ""
	hobj.patch_amount = 0

	for (var i = 0; i < ds_list_size(ops); i++)
	{
		var op, tl, frame, kf, n, created;
		op = ops[|i]
		tl = timelines[?op[?"target"]]
		frame = op[?"frame"]
		kf = live_keyframe_at(tl, frame)

		n = -1
		for (var p = 0; p < hobj.patch_amount; p++)
			if (hobj.patch_tl[p] = tl.save_id && hobj.patch_frame[p] = frame)
				n = p

		if (n < 0)
		{
			n = hobj.patch_amount
			hobj.patch_tl[n] = tl.save_id
			hobj.patch_frame[n] = frame
			hobj.patch_existed[n] = (kf != null)
			if (kf != null)
				with (hobj)
					live_patch_snapshot(false, n, kf)
			hobj.patch_amount++
		}

		created = (kf = null)
		if (created)
		{
			timeline_marker = frame
			with (tl)
			{
				tl_update_values()
				kf = tl_keyframe_add(frame)
			}
		}

		var values, key;
		values = op[?"values"]
		key = ds_map_find_first(values)
		while (!is_undefined(key))
		{
			var vid = ds_list_find_index(value_name_list, key);
			if (automation_value_is_bool(key))
				kf.value[vid] = (values[?key] ? true : false)
			else
				kf.value[vid] = tl_value_clamp(vid, values[?key])
			key = ds_map_find_next(values, key)
		}

		if (is_string(op[?"transition"]))
			kf.value[e_value.TRANSITION] = op[?"transition"]

		tl.update_matrix = true
		applied += (i > 0 ? "," : "") + "{\"target\":" + automation_json_string(tl.save_id) + ",\"frame\":" + string(frame) + ",\"created\":" + automation_json_bool(created) + "}"
	}

	for (var n = 0; n < hobj.patch_amount; n++)
	{
		var kf = live_keyframe_at(save_id_find(hobj.patch_tl[n]), hobj.patch_frame[n]);
		with (hobj)
			live_patch_snapshot(true, n, kf)
	}

	timeline_marker = marker
	return applied
}

function live_patch_write(after)
{
	for (var n = 0; n < patch_amount; n++)
	{
		var tl, kf;
		tl = save_id_find(patch_tl[n])
		if (tl = null)
			continue

		kf = live_keyframe_at(tl, patch_frame[n])
		if (!after && !patch_existed[n])
		{
			if (kf != null)
				with (kf)
					instance_destroy()
			tl.update_matrix = true
			continue
		}

		if (kf = null)
			with (tl)
				kf = tl_keyframe_add(other.patch_frame[n])

		for (var v = 0; v < e_value.amount; v++)
			kf.value[v] = tl_value_find_save_id(v, kf.value[v], (after ? patch_after[n, v] : patch_before[n, v]))
		tl.update_matrix = true
	}
}

function live_patch_refresh()
{
	with (obj_timeline)
		tl_update_values()

	tl_update_matrix()
	tl_update_length()
	app_update_tl_edit()
}

function action_live_patch()
{
	var redo = history_redo;
	with (history_data)
		live_patch_write(redo)

	live_patch_refresh()
}
