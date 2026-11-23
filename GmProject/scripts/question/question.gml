/// question(text)
/// @arg text

function question(text)
{
	var answer;
	
	if (automation_active)
	{
		log("Automation declined question", text)
		return false
	}
	
	window_set_caption("Mine-imator")
	answer = show_question(text)
	window_set_caption("")
	
	return answer;
}
