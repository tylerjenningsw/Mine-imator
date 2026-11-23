/// error(name)
/// @arg name

function error(name)
{
	if (automation_active)
	{
		automation_dialog_error = name
		log("Automation suppressed error", name, text_get(name))
		return null
	}

	window_set_caption(text_get(name + "caption"))
	show_message(text_get(name))
	window_set_caption("")
	
	return null
}
