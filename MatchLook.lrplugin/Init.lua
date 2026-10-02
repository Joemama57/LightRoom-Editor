local LrTasks = import "LrTasks"
local Bridge = require "Bridge"

LrTasks.startAsyncTask(Bridge.run, "Match Look Bridge")
