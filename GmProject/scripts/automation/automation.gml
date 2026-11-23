/// CppSeparate StringType automation_job_file()
function automation_job_file()
{
	return ""
}

/// CppSeparate StringType automation_result_file()
function automation_result_file()
{
	return ""
}

/// CppSeparate BoolType automation_result_write(StringType)
function automation_result_write(json)
{
	return false
}

/// CppSeparate BoolType automation_surface_save(IntType, StringType)
function automation_surface_save(surf, fn)
{
	surface_save(surf, fn)
	return file_exists(fn)
}

/// CppSeparate StringType automation_path_canonical(StringType)
function automation_path_canonical(path)
{
	return path
}

/// CppSeparate StringType automation_stage_create(StringType, BoolType)
function automation_stage_create(destination, directory)
{
	return ""
}

/// CppSeparate BoolType automation_publish(StringType, StringType, BoolType)
function automation_publish(staged, destination, overwrite)
{
	return false
}

function automation_startup()
{
	globalvar automation_active, automation_stage, automation_job_path, automation_job, automation_types, automation_steps;
	globalvar automation_step, automation_frame, automation_output_dir, automation_overwrite, automation_mode;
	globalvar automation_aliases, automation_alias_list, automation_alias_kind, automation_alias_asset, automation_part_cache;
	globalvar automation_claims, automation_step_results, automation_artifacts, automation_surface, automation_staged;
	globalvar automation_sequence_files, automation_characters, automation_blocks, automation_open_file;
	globalvar automation_error_code, automation_error_message, automation_error_step, automation_dialog_error;

	enum e_automation_stage
	{
		BOOT,
		LOADING,
		RUNNING,
		DONE
	}

	automation_job_path = automation_job_file()
	automation_active = (automation_job_path != "")
	automation_stage = e_automation_stage.BOOT
	automation_job = null
	automation_types = null
	automation_steps = null
	automation_step = 0
	automation_frame = 0
	automation_output_dir = ""
	automation_overwrite = false
	automation_mode = "new"
	automation_open_file = ""
	automation_aliases = ds_map_create()
	automation_alias_list = ds_list_create()
	automation_alias_kind = ds_map_create()
	automation_alias_asset = ds_map_create()
	automation_part_cache = ds_map_create()
	automation_claims = ds_list_create()
	automation_step_results = ds_list_create()
	automation_artifacts = ds_list_create()
	automation_sequence_files = ds_list_create()
	automation_characters = ds_list_create()
	automation_blocks = ds_list_create()
	automation_surface = null
	automation_staged = ""
	automation_error_code = ""
	automation_error_message = ""
	automation_error_step = -1
	automation_dialog_error = ""

	if (automation_active)
		log("Automation job", automation_job_path)
}

function automation_update()
{
	if (automation_stage = e_automation_stage.DONE)
		return 0

	app_update_minecraft_resources()

	if (automation_stage = e_automation_stage.BOOT)
	{
		if (window_state != "startup")
			return 0

		window_state = ""
		window_busy = "automation"

		if (!automation_begin())
			automation_finish()
		return 0
	}

	if (automation_stage = e_automation_stage.LOADING)
	{
		if (popup = popup_loading || ds_priority_size(load_queue) > 0)
			return 0

		window_busy = "automation"

		if (!automation_bind())
			automation_finish()
		else
			automation_stage = e_automation_stage.RUNNING
		return 0
	}

	if (automation_step >= ds_list_size(automation_steps))
	{
		automation_finish()
		return 0
	}

	if (!automation_run_step(automation_steps[|automation_step]))
	{
		automation_error_step = automation_step
		automation_finish()
	}
}

function automation_fail(code, message)
{
	if (automation_error_code = "")
	{
		automation_error_code = code
		automation_error_message = message
	}
	log("Automation error", code, message)
	return false
}

function automation_event_game_end()
{
	if (automation_stage != e_automation_stage.DONE)
	{
		if (startup_error)
			automation_fail("STARTUP_FAILED", "Startup failed" + (automation_dialog_error != "" ? ": " + automation_dialog_error : ""))
		else if (window_state = "load_assets")
			automation_fail("ASSETS_UNAVAILABLE", "Minecraft assets could not be loaded" + (automation_dialog_error != "" ? ": " + automation_dialog_error : ""))
		else
			automation_fail("CANCELLED", "Application closed before the job finished")
		automation_write_result()
	}

	log("Closing...")
	return true
}

function automation_finish()
{
	automation_write_result()
	game_end()
}

function automation_write_result()
{
	automation_stage = e_automation_stage.DONE

	if (surface_exists(automation_surface))
		surface_free(automation_surface)

	if (automation_staged != "")
	{
		if (file_exists_lib(automation_staged))
			directory_delete_lib(automation_staged)
		automation_staged = ""
	}

	var ok, json, jobid;
	ok = (automation_error_code = "")
	jobid = "null"
	if (ds_map_valid(automation_job) && is_string(automation_job[?"id"]))
		jobid = automation_json_string(automation_job[?"id"])

	json = "{\"schema\":\"mineimator.result/1\",\"id\":" + jobid + ",\"status\":" + (ok ? "\"ok\"" : "\"error\"")
	json += ",\"application\":{\"version\":" + automation_json_string(mineimator_version_full) + ",\"projectFormat\":" + string(project_format)
	json += ",\"assetVersion\":" + automation_json_string(string(setting_minecraft_assets_version)) + ",\"trial\":" + automation_json_bool(trial_version) + "}"
	json += ",\"outputDirectory\":" + (automation_output_dir != "" ? automation_json_string(automation_output_dir) : "null")
	json += ",\"steps\":" + automation_json_join(automation_step_results, "[", "]")
	json += ",\"artifacts\":" + automation_json_join(automation_artifacts, "[", "]")

	if (!ok)
	{
		json += ",\"error\":{\"code\":" + automation_json_string(automation_error_code) + ",\"message\":" + automation_json_string(automation_error_message)
		json += ",\"step\":" + (automation_error_step >= 0 ? string(automation_error_step) : "null")
		if (automation_dialog_error != "")
			json += ",\"appError\":" + automation_json_string(automation_dialog_error)
		json += "}"
	}
	json += "}"

	if (!automation_result_write(json))
		log("Automation could not write result", automation_result_file())
}

/// CppSeparate StringType automation_json_string(StringType)
function automation_json_string(str)
{
	return "\"" + json_string_encode(str) + "\""
}

function automation_json_bool(val)
{
	return (val ? "true" : "false")
}

function automation_json_join(list, open, close)
{
	var str = open;
	for (var i = 0; i < ds_list_size(list); i++)
		str += (i > 0 ? "," : "") + list[|i]
	return str + close
}

function automation_json_type(map, key)
{
	var types = automation_types[?map];
	if (!ds_map_valid(types) || !ds_map_exists(types, key))
		return null
	return types[?key]
}

function automation_is_object(val)
{
	return (is_real(val) && ds_map_exists(automation_types, val))
}

function automation_check_keys(map, allowed, where)
{
	var key = ds_map_find_first(map);
	while (!is_undefined(key))
	{
		if (!array_contains(allowed, key))
			return automation_fail("INVALID_REQUEST", "Unknown field \"" + string(key) + "\" in " + where)
		key = ds_map_find_next(map, key)
	}
	return true
}

function automation_get_string(map, key, where, required)
{
	var type = automation_json_type(map, key);
	if (type = null)
	{
		if (required)
			return automation_fail("INVALID_REQUEST", "Missing string field \"" + key + "\" in " + where)
		return true
	}
	if (type != e_json_type.STRING)
		return automation_fail("INVALID_REQUEST", "Field \"" + key + "\" in " + where + " must be a string")
	return true
}

function automation_get_number(map, key, where, required, minval, maxval, integer)
{
	var type = automation_json_type(map, key);
	if (type = null)
	{
		if (required)
			return automation_fail("INVALID_REQUEST", "Missing number field \"" + key + "\" in " + where)
		return true
	}
	if (type != e_json_type.NUMBER)
		return automation_fail("INVALID_REQUEST", "Field \"" + key + "\" in " + where + " must be a number")

	var val = map[?key];
	if (!(val >= minval && val <= maxval))
		return automation_fail("INVALID_REQUEST", "Field \"" + key + "\" in " + where + " must be between " + string_decimals(minval) + " and " + string_decimals(maxval))
	if (integer && floor(val) != val)
		return automation_fail("INVALID_REQUEST", "Field \"" + key + "\" in " + where + " must be an integer")
	return true
}

function automation_is_name(str, maxlen)
{
	if (string_length(str) < 1 || string_length(str) > maxlen)
		return false

	for (var i = 1; i <= string_length(str); i++)
	{
		var c = ord(string_char_at(str, i));
		if (!((c >= ord("a") && c <= ord("z")) || (c >= ord("A") && c <= ord("Z")) || (c >= ord("0") && c <= ord("9")) || c = ord("_") || c = ord("-")))
			return false
	}
	return true
}

function automation_path_clean(path)
{
	var clean = string_replace_all(path, "\\", "/");
	while (string_length(clean) > 1 && string_char_at(clean, string_length(clean)) = "/")
		clean = string_copy(clean, 1, string_length(clean) - 1)
	return clean
}

function automation_path_is_absolute(path)
{
	return (string_char_at(path, 1) = "/" || (string_char_at(path, 2) = ":" && string_char_at(path, 3) = "/"))
}

function automation_path_resolve(path)
{
	var clean = automation_path_clean(path);
	if (automation_path_is_absolute(clean))
		return automation_path_canonical(clean)
	return automation_path_canonical(automation_path_clean(filename_dir(automation_job_path)) + "/" + clean)
}

function automation_path_has_parent_segment(path)
{
	var segments = string_split(path, "/");
	for (var i = 0; i < array_length(segments); i++)
		if (segments[i] = "..")
			return true
	return false
}

function automation_relative_valid(path)
{
	if (string_length(path) < 1 || string_length(path) > 200 || string_char_at(path, 1) = "/")
		return false

	var segments = string_split(path, "/");
	for (var i = 0; i < array_length(segments); i++)
	{
		var seg = segments[i];
		if (seg = "" || seg = "." || seg = ".." || string_char_at(seg, 1) = ".")
			return false

		for (var j = 1; j <= string_length(seg); j++)
		{
			var c = ord(string_char_at(seg, j));
			if (!((c >= ord("a") && c <= ord("z")) || (c >= ord("A") && c <= ord("Z")) || (c >= ord("0") && c <= ord("9")) || c = ord("_") || c = ord("-") || c = ord(".") || c = ord(" ")))
				return false
		}
	}
	return true
}

function automation_path_inside(path, dir)
{
	path = string_lower(automation_path_canonical(path))
	dir = string_lower(automation_path_canonical(dir))
	if (path = "" || dir = "")
		return false
	if (path = dir)
		return true
	if (string_char_at(dir, string_length(dir)) != "/")
		dir += "/"
	return (string_copy(path, 1, string_length(dir)) = dir)
}

function automation_claim_output(map, key, ext, where)
{
	if (!automation_get_string(map, key, where, true))
		return false

	var rel = string_replace_all(map[?key], "\\", "/");
	if (!automation_relative_valid(rel))
		return automation_fail("INVALID_REQUEST", "Output path \"" + map[?key] + "\" in " + where + " must be relative to outputDirectory, without \"..\", hidden or empty segments")

	if (ext != "" && string_lower(filename_ext(rel)) != ext)
		return automation_fail("INVALID_REQUEST", "Output path \"" + rel + "\" in " + where + " must end with " + ext)

	var path = automation_path_canonical(automation_output_dir + "/" + rel);
	if (path = "" || !automation_path_inside(path, automation_output_dir))
		return automation_fail("INVALID_REQUEST", "Output path resolves outside outputDirectory")

	for (var i = 0; i < ds_list_size(automation_claims); i++)
		if (automation_path_inside(path, automation_claims[|i]) || automation_path_inside(automation_claims[|i], path))
			return automation_fail("INVALID_REQUEST", "Output path \"" + rel + "\" in " + where + " collides with another output")

	if (automation_path_inside(automation_job_path, path) || automation_path_inside(automation_result_file(), path) ||
		automation_path_inside(path, automation_path_clean(file_directory)) || automation_path_inside(automation_path_clean(file_directory), path) ||
		(automation_open_file != "" && automation_path_inside(automation_open_file, path)))
		return automation_fail("INVALID_REQUEST", "Output path \"" + rel + "\" in " + where + " collides with the job, result, input or temporary files")

	if ((!automation_overwrite || ext = "") && file_exists_lib(path))
		return automation_fail("OUTPUT_EXISTS", "Output \"" + path + "\" already exists")

	map[?key] = rel
	ds_list_add(automation_claims, path)
	return true
}

function automation_kind_type(kind)
{
	switch (kind)
	{
		case "camera":		return e_tl_type.CAMERA
		case "point_light":	return e_tl_type.POINT_LIGHT
		case "block":		return e_tl_type.BLOCK
		case "character":	return e_tl_type.CHARACTER
		case "cube":		return e_tl_type.CUBE
	}
	return null
}

function automation_kind_of(type)
{
	switch (type)
	{
		case e_tl_type.CAMERA:		return "camera"
		case e_tl_type.POINT_LIGHT:	return "point_light"
		case e_tl_type.BLOCK:		return "block"
		case e_tl_type.CHARACTER:	return "character"
		case e_tl_type.CUBE:		return "cube"
		case e_tl_type.BODYPART:	return "bodypart"
	}
	return ""
}

function automation_value_list()
{
	return ["POS_X", "POS_Y", "POS_Z", "ROT_X", "ROT_Y", "ROT_Z", "SCA_X", "SCA_Y", "SCA_Z", "BEND_ANGLE_X", "BEND_ANGLE_Y", "BEND_ANGLE_Z",
			"ALPHA", "VISIBLE", "CAM_FOV", "CAM_ROTATE", "CAM_ROTATE_DISTANCE", "CAM_ROTATE_ANGLE_XY", "CAM_ROTATE_ANGLE_Z", "LIGHT_STRENGTH", "LIGHT_RANGE"]
}

function automation_value_is_bool(name)
{
	return (name = "VISIBLE" || name = "CAM_ROTATE")
}

function automation_value_supported(name, kind, bendable)
{
	switch (name)
	{
		case "POS_X": case "POS_Y": case "POS_Z": case "VISIBLE":
			return (kind != "")
		case "ROT_X": case "ROT_Y": case "ROT_Z":
			return (kind != "" && kind != "point_light")
		case "SCA_X": case "SCA_Y": case "SCA_Z":
			return (kind = "block" || kind = "character" || kind = "cube" || kind = "bodypart")
		case "ALPHA":
			return (kind = "block" || kind = "character" || kind = "cube" || kind = "bodypart")
		case "BEND_ANGLE_X": case "BEND_ANGLE_Y": case "BEND_ANGLE_Z":
			return (kind = "bodypart" && bendable)
		case "CAM_FOV": case "CAM_ROTATE": case "CAM_ROTATE_DISTANCE": case "CAM_ROTATE_ANGLE_XY": case "CAM_ROTATE_ANGLE_Z":
			return (kind = "camera")
		case "LIGHT_STRENGTH": case "LIGHT_RANGE":
			return (kind = "point_light")
	}
	return false
}

function automation_begin()
{
	automation_types = ds_int_map_create()
	automation_job = json_load(automation_job_path, automation_types)
	if (!ds_map_valid(automation_job))
		return automation_fail("INVALID_REQUEST", "Job file is not a JSON object: " + string(json_error))

	var job = automation_job;
	if (!automation_check_keys(job, ["schema", "id", "outputDirectory", "overwrite", "project", "steps"], "job"))
		return false

	if (!automation_get_string(job, "schema", "job", true) || !automation_get_string(job, "id", "job", true) || !automation_get_string(job, "outputDirectory", "job", true))
		return false

	if (job[?"schema"] != "mineimator.job/1")
		return automation_fail("UNSUPPORTED", "Unsupported schema \"" + job[?"schema"] + "\", expected \"mineimator.job/1\"")

	if (!automation_is_name(job[?"id"], 64))
		return automation_fail("INVALID_REQUEST", "Job id must be 1-64 letters, digits, \"_\" or \"-\"")

	if (automation_json_type(job, "overwrite") != null)
	{
		if (automation_json_type(job, "overwrite") != e_json_type.BOOL)
			return automation_fail("INVALID_REQUEST", "Field \"overwrite\" must be a boolean")
		automation_overwrite = job[?"overwrite"]
	}

	if (automation_path_has_parent_segment(automation_path_clean(job[?"outputDirectory"])))
		return automation_fail("INVALID_REQUEST", "outputDirectory must not contain \"..\"")
	automation_output_dir = automation_path_resolve(job[?"outputDirectory"])
	if (automation_output_dir = "")
		return automation_fail("INVALID_REQUEST", "outputDirectory could not be resolved")

	if (automation_json_type(job, "steps") != e_json_type.ARRAY)
		return automation_fail("INVALID_REQUEST", "Field \"steps\" must be an array")
	automation_steps = job[?"steps"]
	if (ds_list_size(automation_steps) > 500)
		return automation_fail("INVALID_REQUEST", "A job may contain at most 500 steps")

	if (!automation_check_project())
		return false

	for (var c = 0; c < ds_list_size(mc_assets.char_list); c++)
		ds_list_add(automation_characters, mc_assets.char_list[|c].name)

	for (var b = 0; b < ds_list_size(mc_assets.block_list); b++)
		if (!mc_assets.block_list[|b].timeline || mc_assets.block_list[|b].tl_model_name = "" || mc_assets.block_list[|b].model_double)
			ds_list_add(automation_blocks, mc_assets.block_list[|b].name)

	var frames = 0;
	for (var s = 0; s < ds_list_size(automation_steps); s++)
	{
		automation_error_step = s
		if (!automation_check_step(automation_steps[|s], "step " + string(s)))
			return false

		var step = automation_steps[|s];
		frames += (step[?"op"] = "render.sequence" ? step[?"count"] : (step[?"op"] = "render.image" ? 1 : 0))
		if (frames > 10000)
			return automation_fail("INVALID_REQUEST", "A job may render at most 10000 frames")
	}
	automation_error_step = -1

	if (!directory_exists_lib(automation_output_dir) && !directory_create_lib(automation_output_dir))
		return automation_fail("OUTPUT_WRITE_FAILED", "Could not create outputDirectory " + automation_output_dir)

	if (automation_mode = "new")
		automation_new_project()
	else
	{
		var dir = file_directory + "automation_open/";
		var copy = dir + filename_name(automation_open_file);
		directory_create_lib(dir)
		if (!file_copy_lib(automation_open_file, copy) || !file_exists_lib(copy))
			return automation_fail("PROJECT_LOAD_FAILED", "Could not copy " + automation_open_file + " to a working copy")

		if (!project_load(copy))
			return automation_fail("PROJECT_LOAD_FAILED", "Could not open " + automation_open_file + (automation_dialog_error != "" ? " (" + automation_dialog_error + ")" : ""))

		project_folder = automation_output_dir
		project_file = ""
	}

	automation_stage = e_automation_stage.LOADING
	return true
}

function automation_check_project()
{
	var project = automation_job[?"project"];
	if (automation_json_type(automation_job, "project") != e_json_type.OBJECT)
		return automation_fail("INVALID_REQUEST", "Field \"project\" must be an object")

	if (!automation_get_string(project, "mode", "project", true))
		return false

	automation_mode = project[?"mode"]
	if (automation_mode = "open")
	{
		if (!automation_check_keys(project, ["mode", "path"], "project") || !automation_get_string(project, "path", "project", true))
			return false

		automation_open_file = automation_path_resolve(project[?"path"])
		if (string_lower(filename_ext(automation_open_file)) != ".miproject")
			return automation_fail("UNSUPPORTED", "Only current-format .miproject files can be opened")
		if (!file_exists_lib(automation_open_file))
			return automation_fail("PROJECT_LOAD_FAILED", "Project not found: " + automation_open_file)

		return true
	}

	if (automation_mode != "new")
		return automation_fail("INVALID_REQUEST", "project.mode must be \"new\" or \"open\"")

	if (!automation_check_keys(project, ["mode", "name", "tempo", "width", "height", "seed"], "project") ||
		!automation_get_string(project, "name", "project", false) ||
		!automation_get_number(project, "tempo", "project", false, 1, 240, true) ||
		!automation_get_number(project, "width", "project", false, 16, 4096, true) ||
		!automation_get_number(project, "height", "project", false, 16, 4096, true) ||
		!automation_get_number(project, "seed", "project", false, 0, 65535, true))
		return false

	return true
}

function automation_new_project()
{
	var project = automation_job[?"project"];

	project_reset()
	action_load_render_settings(render_default_file)

	project_name = (is_string(project[?"name"]) ? project[?"name"] : automation_job[?"id"])
	project_folder = automation_output_dir
	project_file = ""

	if (is_real(project[?"tempo"]))
		project_tempo = project[?"tempo"]
	if (is_real(project[?"width"]))
		project_video_width = project[?"width"]
	if (is_real(project[?"height"]))
		project_video_height = project[?"height"]
	project_video_template = find_videotemplate(project_video_width, project_video_height)
	if (is_real(project[?"seed"]))
	{
		random_set_seed(project[?"seed"])
		save_id_seed = random_get_seed()
	}
}

function automation_check_step(step, where)
{
	if (!automation_is_object(step))
		return automation_fail("INVALID_REQUEST", where + " must be an object")

	if (!automation_get_string(step, "op", where, true))
		return false

	switch (step[?"op"])
	{
		case "catalog":
		{
			if (!automation_check_keys(step, ["op", "kind", "query", "asset", "limit"], where) ||
				!automation_get_string(step, "kind", where, true) ||
				!automation_get_string(step, "query", where, false) ||
				!automation_get_string(step, "asset", where, false) ||
				!automation_get_number(step, "limit", where, false, 1, 1000, true))
				return false

			var kind = step[?"kind"];
			if (kind != "kinds" && kind != "characters" && kind != "blocks" && kind != "values" && kind != "transitions" && kind != "parts")
				return automation_fail("INVALID_REQUEST", "catalog kind must be one of kinds, characters, blocks, values, transitions, parts")

			if (kind = "parts")
			{
				if (!automation_get_string(step, "asset", where, true))
					return false
				if (ds_list_find_index(automation_characters, step[?"asset"]) < 0)
					return automation_fail("ASSET_NOT_FOUND", "Unknown character \"" + step[?"asset"] + "\"")
			}
			else if (!is_undefined(step[?"asset"]))
				return automation_fail("INVALID_REQUEST", "Field \"asset\" is only valid for catalog kind parts")

			return true
		}

		case "create":
		{
			if (!automation_check_keys(step, ["op", "id", "kind", "asset"], where) ||
				!automation_get_string(step, "id", where, true) ||
				!automation_get_string(step, "kind", where, true) ||
				!automation_get_string(step, "asset", where, false))
				return false

			var alias = step[?"id"];
			if (!automation_is_name(alias, 40))
				return automation_fail("INVALID_REQUEST", "Object id \"" + alias + "\" must be 1-40 letters, digits, \"_\" or \"-\"")

			var kind = step[?"kind"];
			if (automation_kind_type(kind) = null)
				return automation_fail("UNSUPPORTED", "Unsupported kind \"" + kind + "\", supported kinds are camera, point_light, block, character, cube")

			var asset = step[?"asset"];
			if (kind = "character" || kind = "block")
			{
				if (!is_string(asset))
					return automation_fail("INVALID_REQUEST", "Kind " + kind + " requires an asset, see catalog")
				if (ds_list_find_index(kind = "character" ? automation_characters : automation_blocks, asset) < 0)
					return automation_fail("ASSET_NOT_FOUND", "Unknown " + kind + " asset \"" + asset + "\"")
			}
			else if (!is_undefined(asset))
				return automation_fail("INVALID_REQUEST", "Kind " + kind + " does not take an asset")

			return true
		}

		case "keyframe.set":
		{
			if (!automation_check_keys(step, ["op", "target", "frame", "values", "transition"], where) ||
				!automation_get_string(step, "target", where, true) ||
				!automation_get_number(step, "frame", where, true, 0, 100000, true) ||
				!automation_get_string(step, "transition", where, false))
				return false

			if (is_string(step[?"transition"]) && ds_list_find_index(transition_list, step[?"transition"]) < 0)
				return automation_fail("INVALID_REQUEST", "Unknown transition \"" + step[?"transition"] + "\", see catalog")

			if (automation_json_type(step, "values") != e_json_type.OBJECT)
				return automation_fail("INVALID_REQUEST", "Field \"values\" in " + where + " must be an object")

			var values, names, key;
			values = step[?"values"]
			names = automation_value_list()
			key = ds_map_find_first(values)
			while (!is_undefined(key))
			{
				if (!array_contains(names, key))
					return automation_fail("UNSUPPORTED", "Unsupported value \"" + string(key) + "\", see catalog kind values")

				if (automation_value_is_bool(key))
				{
					if (automation_json_type(values, key) != e_json_type.BOOL)
						return automation_fail("INVALID_REQUEST", "Value " + key + " must be a boolean")
				}
				else if (!automation_get_number(values, key, where + " values", true, -1000000, 1000000, false))
					return false

				key = ds_map_find_next(values, key)
			}

			if (ds_map_size(values) = 0 && !is_string(step[?"transition"]))
				return automation_fail("INVALID_REQUEST", where + " must set at least one value or a transition")

			return true
		}

		case "inspect":
			return (automation_check_keys(step, ["op", "frame"], where) && automation_get_number(step, "frame", where, true, 0, 100000, false))

		case "save":
			return (automation_check_keys(step, ["op", "path"], where) && automation_claim_output(step, "path", ".miproject", where))

		case "render.image":
		{
			if (!automation_check_keys(step, ["op", "camera", "frame", "path", "quality"], where) ||
				!automation_get_string(step, "camera", where, true) ||
				!automation_get_number(step, "frame", where, true, 0, 100000, false) ||
				!automation_check_quality(step, where))
				return false
			return automation_claim_output(step, "path", ".png", where)
		}

		case "render.sequence":
		{
			if (!automation_check_keys(step, ["op", "camera", "start", "count", "fps", "path", "quality"], where) ||
				!automation_get_string(step, "camera", where, true) ||
				!automation_get_number(step, "start", where, true, 0, 100000, false) ||
				!automation_get_number(step, "count", where, true, 1, 2000, true) ||
				!automation_get_number(step, "fps", where, true, 1, 120, false) ||
				!automation_check_quality(step, where))
				return false
			return automation_claim_output(step, "path", "", where)
		}
	}

	return automation_fail("UNSUPPORTED", "Unsupported op \"" + step[?"op"] + "\", supported ops are catalog, create, keyframe.set, inspect, save, render.image, render.sequence")
}

function automation_check_quality(step, where)
{
	if (!automation_get_string(step, "quality", where, false))
		return false
	if (is_string(step[?"quality"]) && step[?"quality"] != "shaded")
		return automation_fail("UNSUPPORTED", "Only quality \"shaded\" is supported")
	return true
}

function automation_sequence_frame(step, index)
{
	return step[?"start"] + (index * project_tempo) / step[?"fps"]
}

function automation_add_alias(alias, tl, kind, asset)
{
	automation_aliases[?alias] = tl
	automation_alias_kind[?alias] = kind
	automation_alias_asset[?alias] = asset
	ds_list_add(automation_alias_list, alias)
}

function automation_bind()
{
	if (automation_mode = "open")
	{
		var counts = ds_map_create();
		for (var i = 0; i < ds_list_size(project_timeline_list); i++)
		{
			var tl = project_timeline_list[|i];
			if (tl.part_of = null && automation_is_name(tl.name, 40))
				counts[?tl.name] = (is_undefined(counts[?tl.name]) ? 1 : counts[?tl.name] + 1)
		}

		for (var i = 0; i < ds_list_size(project_timeline_list); i++)
		{
			var tl = project_timeline_list[|i];
			if (tl.part_of = null && automation_is_name(tl.name, 40) && counts[?tl.name] = 1)
				automation_add_alias(tl.name, tl, automation_kind_of(tl.type), automation_tl_asset(tl))
		}
		ds_map_destroy(counts)
	}

	var known = ds_map_create();
	ds_map_copy(known, automation_alias_kind)

	for (var s = 0; s < ds_list_size(automation_steps); s++)
	{
		var step, where;
		step = automation_steps[|s]
		where = "step " + string(s)
		automation_error_step = s

		switch (step[?"op"])
		{
			case "create":
			{
				if (ds_map_exists(known, step[?"id"]))
				{
					ds_map_destroy(known)
					return automation_fail("DUPLICATE_ID", "Object id \"" + step[?"id"] + "\" is already used")
				}
				known[?step[?"id"]] = step[?"kind"]
				automation_alias_asset[?step[?"id"]] = step[?"asset"]
				break
			}

			case "keyframe.set":
			{
				var target = automation_check_target(known, step[?"target"]);
				if (target = null)
				{
					ds_map_destroy(known)
					return false
				}

				var values, key;
				values = step[?"values"]
				key = ds_map_find_first(values)
				while (!is_undefined(key))
				{
					if (!automation_value_supported(key, target[0], target[1]))
					{
						ds_map_destroy(known)
						return automation_fail("UNSUPPORTED", "Value " + key + " is not supported on " + step[?"target"] + " (" + (target[0] = "" ? "unsupported kind" : target[0]) + ")")
					}
					key = ds_map_find_next(values, key)
				}
				break
			}

			case "render.image":
			case "render.sequence":
			{
				if (known[?step[?"camera"]] != "camera")
				{
					ds_map_destroy(known)
					return automation_fail("CAMERA_REQUIRED", "\"" + step[?"camera"] + "\" in " + where + " is not a camera created or opened earlier")
				}

				if (step[?"op"] = "render.sequence" && automation_sequence_frame(step, step[?"count"] - 1) > 100000)
				{
					ds_map_destroy(known)
					return automation_fail("INVALID_REQUEST", where + " samples past frame 100000")
				}
				break
			}
		}
	}

	ds_map_destroy(known)
	automation_error_step = -1
	return true
}

function automation_tl_asset(tl)
{
	if (tl.temp = null)
		return ""
	if (tl.type = e_tl_type.CHARACTER)
		return tl.temp.model_name
	if (tl.type = e_tl_type.BLOCK)
		return tl.temp.block_name
	return ""
}

function automation_check_target(known, target)
{
	var slash, alias, part;
	slash = string_pos("/", target)
	alias = (slash > 0 ? string_copy(target, 1, slash - 1) : target)
	part = (slash > 0 ? string_delete(target, 1, slash) : "")

	if (!ds_map_exists(known, alias))
	{
		automation_fail("OBJECT_NOT_FOUND", "Unknown target \"" + target + "\", create it in an earlier step")
		return null
	}

	if (part = "")
	{
		if (known[?alias] = "")
		{
			automation_fail("UNSUPPORTED", "Target \"" + target + "\" has an unsupported kind")
			return null
		}
		return [known[?alias], false]
	}

	var modelpart = automation_find_part(alias, part);
	if (modelpart = null)
	{
		automation_fail("OBJECT_NOT_FOUND", "Unknown part \"" + part + "\" of \"" + alias + "\", see catalog kind parts")
		return null
	}
	return ["bodypart", modelpart.bend_part != null]
}

function automation_find_part(alias, partname)
{
	var tl = automation_aliases[?alias];
	if (!is_undefined(tl))
	{
		with (tl)
		{
			var parttl = tl_part_find(partname);
			if (parttl != null && parttl.model_part != null)
				return parttl.model_part
		}
		return null
	}

	var parts = automation_parts(automation_alias_asset[?alias]);
	if (parts = null)
		return null

	for (var p = 0; p < ds_list_size(parts); p++)
		if (parts[|p].name = partname)
			return parts[|p]
	return null
}

function automation_parts(asset)
{
	if (!is_string(asset) || ds_list_find_index(automation_characters, asset) < 0)
		return null

	if (ds_map_exists(automation_part_cache, asset))
		return automation_part_cache[?asset]

	var parts = ds_list_create();
	automation_bench_character(asset)
	with (bench_settings)
	{
		if (model_file != null)
		{
			for (var p = 0; p < ds_list_size(model_file.file_part_list); p++)
			{
				var part = model_file.file_part_list[|p];
				if (model_hide_list = null || ds_list_find_index(model_hide_list, part.name) = -1)
					ds_list_add(parts, part)
			}
		}
	}
	automation_part_cache[?asset] = parts
	return parts
}

function automation_bench_character(asset)
{
	with (bench_settings)
	{
		type = e_tl_type.CHARACTER
		model_tex = mc_res
		model_tex_material = mc_res
		model_tex_normal = mc_res
		model_name = ""
	}
	action_bench_model_name(asset)
}

function automation_target(target)
{
	var slash = string_pos("/", target);
	if (slash = 0)
		return automation_aliases[?target]

	var tl = automation_aliases[?string_copy(target, 1, slash - 1)];
	with (tl)
		return tl_part_find(string_delete(target, 1, slash))
	return null
}

function automation_refresh(frame)
{
	timeline_marker = frame
	timeline_marker_previous = -1
	render_samples = -1
	app_update_animate()
	app_update_animate()
}

function automation_run_step(step)
{
	var result = "{\"index\":" + string(automation_step) + ",\"op\":" + automation_json_string(step[?"op"]);

	switch (step[?"op"])
	{
		case "catalog":
			result += automation_catalog(step)
			break

		case "create":
		{
			var fields = automation_create(step);
			if (fields = "")
				return false
			result += fields
			break
		}

		case "keyframe.set":
		{
			var tl = automation_target(step[?"target"]);
			if (is_undefined(tl) || tl = null)
				return automation_fail("OBJECT_NOT_FOUND", "Target \"" + step[?"target"] + "\" was not found")

			var created = automation_keyframe_set(tl, step[?"frame"], step[?"values"], (is_string(step[?"transition"]) ? step[?"transition"] : ""));
			result += ",\"target\":" + automation_json_string(step[?"target"]) + ",\"frame\":" + string_decimals(step[?"frame"]) + ",\"created\":" + automation_json_bool(created)
			break
		}

		case "inspect":
			result += ",\"frame\":" + string_decimals(step[?"frame"]) + ",\"timelines\":" + automation_inspect(step[?"frame"])
			break

		case "save":
		{
			var path = automation_save(step[?"path"]);
			if (path = "")
				return false
			result += ",\"path\":" + automation_json_string(path)
			break
		}

		case "render.image":
		{
			var path, cam;
			path = automation_output_dir + "/" + step[?"path"]
			cam = automation_aliases[?step[?"camera"]]
			if (!automation_overwrite && file_exists_lib(path))
				return automation_fail("OUTPUT_EXISTS", "Output \"" + path + "\" already exists")

			directory_create_lib(filename_dir(path))
			if (!automation_render(cam, step[?"frame"], path))
				return automation_fail("RENDER_FAILED", "Could not render or save " + path)

			var artifact = "{\"kind\":\"image\",\"path\":" + automation_json_string(path) + ",\"camera\":" + automation_json_string(step[?"camera"]) +
						   ",\"frame\":" + string_decimals(step[?"frame"]) + ",\"width\":" + string(render_width) + ",\"height\":" + string(render_height) +
						   ",\"watermark\":" + automation_json_bool(render_watermark) + "}";
			ds_list_add(automation_artifacts, artifact)
			result += ",\"path\":" + automation_json_string(path)
			break
		}

		case "render.sequence":
		{
			if (!automation_render_sequence(step))
				return false
			if (automation_frame < step[?"count"])
				return true

			result += ",\"path\":" + automation_json_string(automation_output_dir + "/" + step[?"path"]) + ",\"count\":" + string(step[?"count"])
			automation_frame = 0
			break
		}
	}

	ds_list_add(automation_step_results, result + "}")
	automation_step++
	return true
}

function automation_catalog(step)
{
	var kind, query, limit, items, total;
	kind = step[?"kind"]
	query = (is_string(step[?"query"]) ? string_lower(step[?"query"]) : "")
	limit = (is_real(step[?"limit"]) ? step[?"limit"] : 100)
	items = ds_list_create()
	total = 0

	var names = null;
	if (kind = "kinds")
		names = ["camera", "point_light", "block", "character", "cube"]
	else if (kind = "values")
		names = automation_value_list()

	if (names != null)
	{
		for (var i = 0; i < array_length(names); i++)
		{
			if (query != "" && !string_contains(string_lower(names[i]), query))
				continue
			total++
			if (ds_list_size(items) >= limit)
				continue

			if (kind = "values")
			{
				var kinds = "";
				var allkinds = ["camera", "point_light", "block", "character", "cube", "bodypart"];
				for (var k = 0; k < array_length(allkinds); k++)
					if (automation_value_supported(names[i], allkinds[k], true))
						kinds += (kinds = "" ? "" : ",") + automation_json_string(allkinds[k])
				ds_list_add(items, "{\"name\":" + automation_json_string(names[i]) + ",\"type\":" + (automation_value_is_bool(names[i]) ? "\"bool\"" : "\"number\"") + ",\"kinds\":[" + kinds + "]}")
			}
			else
				ds_list_add(items, automation_json_string(names[i]))
		}
	}
	else
	{
		var list, partlist;
		list = null
		partlist = null
		if (kind = "characters")
			list = automation_characters
		else if (kind = "blocks")
			list = automation_blocks
		else if (kind = "transitions")
			list = transition_list
		else
			partlist = automation_parts(step[?"asset"])

		var size = (partlist != null ? ds_list_size(partlist) : ds_list_size(list));
		for (var i = 0; i < size; i++)
		{
			var name, item;
			if (partlist != null)
			{
				var part = partlist[|i];
				name = part.name
				item = "{\"name\":" + automation_json_string(name) + ",\"target\":" + automation_json_string("<id>/" + name) + ",\"bend\":" + automation_json_bool(part.bend_part != null) + "}"
			}
			else
			{
				name = list[|i]
				if (kind = "characters")
					item = "{\"name\":" + automation_json_string(name) + ",\"label\":" + automation_json_string(minecraft_asset_get_name("model", name)) + "}"
				else if (kind = "blocks")
					item = "{\"name\":" + automation_json_string(name) + ",\"label\":" + automation_json_string(minecraft_asset_get_name("block", mc_assets.block_name_map[?name].name)) + "}"
				else
					item = automation_json_string(name)
			}

			if (query != "" && !string_contains(string_lower(name), query) && !string_contains(string_lower(item), query))
				continue
			total++
			if (ds_list_size(items) < limit)
				ds_list_add(items, item)
		}
	}

	var fields = ",\"kind\":" + automation_json_string(kind) + ",\"total\":" + string(total) + ",\"items\":" + automation_json_join(items, "[", "]");
	ds_list_destroy(items)
	return fields
}

function automation_create(step)
{
	var alias, kind, asset;
	alias = step[?"id"]
	kind = step[?"kind"]
	asset = (is_string(step[?"asset"]) ? step[?"asset"] : "")

	switch (kind)
	{
		case "camera":
			bench_settings.type = e_tl_type.CAMERA
			break

		case "point_light":
			bench_settings.type = e_tl_type.LIGHT_SOURCE
			bench_settings.light_type = e_tl_type.POINT_LIGHT
			break

		case "character":
			automation_bench_character(asset)
			break

		case "block":
			bench_settings.type = e_tl_type.BLOCK
			bench_settings.block_name = ""
			action_bench_block_name(asset)
			break

		case "cube":
			bench_settings.type = e_tl_type.SHAPE
			bench_settings.shape_type = e_shape_type.CUBE
			with (bench_settings)
				temp_update_shape()
			break
	}

	action_bench_create()
	app_stop_place()
	window_busy = "automation"

	var hobj, tl;
	hobj = history[0]
	tl = null
	if (type_is_timeline(bench_settings.type))
		tl = save_id_find(hobj.spawn_save_id[0])
	else
	{
		var template = save_id_find(hobj.spawn_save_id[0]);
		with (obj_timeline)
			if (temp = template && part_of = null)
				tl = id
	}

	if (tl = null || automation_kind_of(tl.type) != kind)
	{
		automation_fail("INTERNAL", "Native create did not produce a " + kind + " timeline")
		return ""
	}

	with (tl)
	{
		name = alias
		tl_update_display_name()
	}
	automation_add_alias(alias, tl, kind, asset)
	project_changed = true
	automation_refresh(timeline_marker)

	var parts = "";
	if (tl.part_list != null)
	{
		for (var p = 0; p < ds_list_size(tl.part_list); p++)
		{
			var part = tl.part_list[|p];
			parts += (parts = "" ? "" : ",") + "{\"id\":" + automation_json_string(alias + "/" + part.model_part_name) +
					 ",\"parent\":" + automation_json_string(part.parent = tl ? alias : alias + "/" + part.parent.model_part_name) + "}"
		}
	}

	return ",\"id\":" + automation_json_string(alias) + ",\"saveId\":" + automation_json_string(tl.save_id) + ",\"kind\":" + automation_json_string(kind) +
		   ",\"asset\":" + (asset != "" ? automation_json_string(asset) : "null") + ",\"parts\":[" + parts + "]"
}

function automation_keyframe_set(tl, frame, values, transition)
{
	var kf = null;
	for (var k = 0; k < ds_list_size(tl.keyframe_list); k++)
		if (tl.keyframe_list[|k].position = frame)
			kf = tl.keyframe_list[|k]

	var created = (kf = null);
	if (created)
	{
		automation_refresh(frame)
		with (tl)
			kf = tl_keyframe_add(frame)
	}

	var key = ds_map_find_first(values);
	while (!is_undefined(key))
	{
		var vid = ds_list_find_index(value_name_list, key);
		if (automation_value_is_bool(key))
			kf.value[vid] = (values[?key] ? true : false)
		else
			kf.value[vid] = tl_value_clamp(vid, values[?key])
		key = ds_map_find_next(values, key)
	}

	if (transition != "")
		kf.value[e_value.TRANSITION] = transition

	tl.update_matrix = true
	tl_update_length()
	project_changed = true
	automation_refresh(frame)

	return created
}

function automation_tl_json(tl, label, frame)
{
	var keyframes, values, names;
	keyframes = ""
	for (var k = 0; k < ds_list_size(tl.keyframe_list); k++)
		keyframes += (k > 0 ? "," : "") + string_decimals(tl.keyframe_list[|k].position)

	values = ""
	names = automation_value_list()
	var kind = automation_kind_of(tl.type);
	var bendable = (tl.model_part != null && tl.model_part.bend_part != null);
	for (var i = 0; i < array_length(names); i++)
	{
		if (!automation_value_supported(names[i], kind, bendable))
			continue

		var val = tl.value[ds_list_find_index(value_name_list, names[i])];
		values += (values = "" ? "" : ",") + automation_json_string(names[i]) + ":" + (automation_value_is_bool(names[i]) ? automation_json_bool(val) : string_decimals(val))
	}

	return "{\"id\":" + automation_json_string(label) + ",\"saveId\":" + automation_json_string(tl.save_id) + ",\"kind\":" + automation_json_string(kind = "" ? tl_type_name_list[|tl.type] : kind) +
		   ",\"keyframes\":[" + keyframes + "],\"values\":{" + values + "}"
}

function automation_inspect(frame)
{
	automation_refresh(frame)

	var list = "";
	for (var a = 0; a < ds_list_size(automation_alias_list); a++)
	{
		var alias, tl, asset, item, parts;
		alias = automation_alias_list[|a]
		tl = automation_aliases[?alias]
		asset = automation_tl_asset(tl)
		item = automation_tl_json(tl, alias, frame)
		item += ",\"asset\":" + (asset != "" ? automation_json_string(asset) : "null")
		item += ",\"parent\":" + (tl.parent != app && automation_is_name(tl.parent.name, 40) && automation_aliases[?tl.parent.name] = tl.parent ? automation_json_string(tl.parent.name) : "null")

		parts = ""
		if (tl.part_list != null)
		{
			for (var p = 0; p < ds_list_size(tl.part_list); p++)
			{
				var part = tl.part_list[|p];
				if (ds_list_size(part.keyframe_list) > 0)
					parts += (parts = "" ? "" : ",") + automation_tl_json(part, alias + "/" + part.model_part_name, frame) + "}"
			}
		}

		list += (a > 0 ? "," : "") + item + ",\"parts\":[" + parts + "]}"
	}
	return "[" + list + "]"
}

function automation_save(rel)
{
	var path, staged;
	path = automation_output_dir + "/" + string(rel)

	if (!automation_overwrite && file_exists_lib(path))
	{
		automation_fail("OUTPUT_EXISTS", "Output \"" + path + "\" already exists")
		return ""
	}

	staged = automation_stage_create(path, false)
	if (staged = "")
	{
		automation_fail("OUTPUT_WRITE_FAILED", "Could not create a temporary project file")
		return ""
	}

	project_folder = filename_dir(path)
	project_file = path
	project_save(staged)
	recent_add_wait = false

	var map = json_load(staged);
	var valid = (ds_map_valid(map) && map[?"format"] = project_format);
	if (ds_map_valid(map))
		ds_map_destroy(map)

	if (!valid || !automation_publish(staged, path, automation_overwrite) || !file_exists_lib(path) || file_exists_lib(staged))
	{
		file_delete_lib(staged)
		automation_fail("OUTPUT_WRITE_FAILED", "Could not write or verify project " + path)
		return ""
	}

	ds_list_add(automation_artifacts, "{\"kind\":\"project\",\"path\":" + automation_json_string(path) + "}")
	return path
}

function automation_render(cam, frame, fn)
{
	automation_refresh(frame)

	var prevview, prevhq, prevlights, prevparticles, preveffects, prevhidden, prevbackground, prevquality;
	prevview = render_view_current
	prevhq = popup_exportimage.high_quality
	prevlights = render_lights
	prevparticles = render_particles
	preveffects = render_effects
	prevhidden = render_hidden
	prevbackground = render_background
	prevquality = render_quality

	render_view_current = view_main
	render_active = "image"
	render_quality = e_view_mode.SHADED
	popup_exportimage.high_quality = false
	render_lights = true
	render_particles = true
	render_effects = true
	render_hidden = false
	render_background = true
	render_watermark = (popup_exportimage.watermark || trial_version)

	render_start(automation_surface, cam)
	render_low()
	automation_surface = render_done()

	var ok = automation_surface_save(automation_surface, fn);

	render_active = null
	render_view_current = prevview
	popup_exportimage.high_quality = prevhq
	render_lights = prevlights
	render_particles = prevparticles
	render_effects = preveffects
	render_hidden = prevhidden
	render_background = prevbackground
	render_quality = prevquality
	render_samples = -1

	return ok
}

function automation_render_sequence(step)
{
	var path = automation_output_dir + "/" + step[?"path"];

	if (automation_frame = 0)
	{
		if (file_exists_lib(path))
			return automation_fail("OUTPUT_EXISTS", "Output \"" + path + "\" already exists")

		automation_staged = automation_stage_create(path, true)
		if (automation_staged = "")
			return automation_fail("OUTPUT_WRITE_FAILED", "Could not create " + automation_staged)
		ds_list_clear(automation_sequence_files)
	}

	var frame, file, cam;
	frame = automation_sequence_frame(step, automation_frame)
	file = "frame_" + string_repeat("0", 6 - string_length(string(automation_frame))) + string(automation_frame) + ".png"
	cam = automation_aliases[?step[?"camera"]]

	if (!automation_render(cam, frame, automation_staged + "/" + file))
		return automation_fail("RENDER_FAILED", "Could not render or save frame " + string(automation_frame) + " of " + path)

	ds_list_add(automation_sequence_files, "{\"file\":" + automation_json_string(file) + ",\"frame\":" + string_decimals(frame) + "}")
	automation_frame++

	if (automation_frame < step[?"count"])
		return true

	if (!automation_publish(automation_staged, path, false) || !file_exists_lib(path + "/" + file))
		return automation_fail("OUTPUT_WRITE_FAILED", "Could not publish sequence " + path)
	automation_staged = ""

	var artifact = "{\"kind\":\"sequence\",\"path\":" + automation_json_string(path) + ",\"camera\":" + automation_json_string(step[?"camera"]) +
				   ",\"start\":" + string_decimals(step[?"start"]) + ",\"count\":" + string(step[?"count"]) + ",\"fps\":" + string_decimals(step[?"fps"]) +
				   ",\"width\":" + string(render_width) + ",\"height\":" + string(render_height) + ",\"watermark\":" + automation_json_bool(render_watermark) +
				   ",\"files\":" + automation_json_join(automation_sequence_files, "[", "]") + "}";
	ds_list_add(automation_artifacts, artifact)
	return true
}
