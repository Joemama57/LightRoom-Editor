local LrDialogs = import "LrDialogs"
local LrTasks = import "LrTasks"
local Bridge = require "Bridge"

LrTasks.startAsyncTask(function()
	if Bridge.isRunning() then
		LrDialogs.message(
			"Match Look Bridge is running",
			"Folder: " .. Bridge.root
				.. "\n\nIn Claude Code, select the photos (graded one active) and run /match-look."
		)
	else
		local choice = LrDialogs.confirm(
			"Match Look Bridge is not running",
			"Start it now?" .. (Bridge.lastError and ("\n\nLast error: " .. Bridge.lastError) or ""),
			"Start"
		)
		if choice == "ok" then
			LrTasks.startAsyncTask(Bridge.run, "Match Look Bridge")
		end
	end
end)
