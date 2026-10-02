--[[
Match Look Bridge: lets Claude Code drive Lightroom Classic for /match-look.

The plugin itself makes no decisions. It watches ~/.matchlook/bridge/inbox for
JSON requests (read the selection, apply settings, render a preview, take a
snapshot, set a label) and writes replies to ~/.matchlook/bridge/outbox.
]]

return {
	LrSdkVersion = 10.0,
	LrSdkMinimumVersion = 6.0,
	LrToolkitIdentifier = "com.matchlook.bridge",
	LrPluginName = "Match Look Bridge",
	LrPluginInfoUrl = "https://github.com/Joemama57/LightRoom-Editor",

	LrInitPlugin = "Init.lua",
	LrShutdownPlugin = "Shutdown.lua",
	LrForceInitPlugin = true,

	LrLibraryMenuItems = {
		{ title = "Match Look Bridge Status", file = "Status.lua" },
	},
	LrExportMenuItems = {
		{ title = "Match Look Bridge Status", file = "Status.lua" },
	},

	VERSION = { major = 0, minor = 1, revision = 0 },
}
