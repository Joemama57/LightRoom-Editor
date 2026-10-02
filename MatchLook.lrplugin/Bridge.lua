--[[
File-based request loop. Each request is inbox/<id>.json:
    {"id": "...", "command": "render", "params": {...}}
and gets a reply in outbox/<id>.json:
    {"id": "...", "ok": true, "result": ...}   or   {"id": "...", "ok": false, "error": "..."}
]]

local LrApplication = import "LrApplication"
local LrExportSession = import "LrExportSession"
local LrFileUtils = import "LrFileUtils"
local LrPathUtils = import "LrPathUtils"
local LrTasks = import "LrTasks"

local Json = require "Json"

local Bridge = {
	handled = 0,
	lastError = nil,
}

local POLL_SECONDS = 0.2
local WRITE_TIMEOUT = { timeout = 60 }

local home = LrPathUtils.getStandardFilePath("home")
Bridge.root = LrPathUtils.child(LrPathUtils.child(home, ".matchlook"), "bridge")
local inbox = LrPathUtils.child(Bridge.root, "inbox")
local outbox = LrPathUtils.child(Bridge.root, "outbox")
local renderDir = LrPathUtils.child(Bridge.root, "renders")

local running = false
local stopRequested = false
-- Photos seen in the last get_selection, as a fallback for catalog lookups.
local photoCache = {}

local function readFile(path)
	local f = assert(io.open(path, "rb"))
	local text = f:read("*a")
	f:close()
	return text
end

local function writeAtomic(path, text)
	local tmp = path .. ".tmp"
	local f = assert(io.open(tmp, "wb"))
	f:write(text)
	f:close()
	assert(os.rename(tmp, path))
end

local function photoId(photo)
	return tostring(photo.localIdentifier)
end

local function findPhoto(id)
	local catalog = LrApplication.activeCatalog()
	-- getPhotoByLocalId isn't in every SDK version, so guard the lookup.
	local ok, photo = LrTasks.pcall(function()
		return catalog:getPhotoByLocalId(tonumber(id))
	end)
	photo = ok and photo or nil
	photo = photo or photoCache[id]
	if not photo then
		error("photo " .. tostring(id) .. " not found; re-run get_selection")
	end
	return photo
end

local function eachItem(params, fn)
	for _, item in ipairs(params.items or {}) do
		fn(findPhoto(item.id), item)
	end
end

-- Commands -------------------------------------------------------------------

local commands = {}

function commands.ping()
	return { plugin = "Match Look Bridge", version = "0.1.0", catalog = LrApplication.activeCatalog():getPath() }
end

function commands.get_selection()
	local catalog = LrApplication.activeCatalog()
	local active = catalog:getTargetPhoto()
	local photos = {}
	photoCache = {}
	-- With nothing selected getTargetPhotos() returns the whole filmstrip; don't act on that.
	if active then
		for _, photo in ipairs(catalog:getTargetPhotos()) do
			if not photo:getRawMetadata("isVideo") then
				local id = photoId(photo)
				photoCache[id] = photo
				photos[#photos + 1] = {
					id = id,
					fileName = photo:getFormattedMetadata("fileName"),
					fileFormat = photo:getRawMetadata("fileFormat"),
					cameraModel = photo:getFormattedMetadata("cameraModel"),
					settings = photo:getDevelopSettings(),
				}
			end
		end
	end
	return { active = active and photoId(active) or nil, photos = photos }
end

function commands.apply_settings(params)
	local n = 0
	LrApplication.activeCatalog():withWriteAccessDo("Match Look", function()
		eachItem(params, function(photo, item)
			photo:applyDevelopSettings(item.settings)
			n = n + 1
		end)
	end, WRITE_TIMEOUT)
	return { applied = n }
end

function commands.render(params)
	local size = params.size or 1024
	local photos, wanted = {}, {}
	eachItem(params, function(photo, item)
		photos[#photos + 1] = photo
		wanted[photoId(photo)] = item.path
	end)
	if #photos == 0 then
		return {}
	end
	LrFileUtils.createAllDirectories(renderDir)

	local session = LrExportSession({
		photosToExport = photos,
		exportSettings = {
			LR_export_destinationType = "specificFolder",
			LR_export_destinationPathPrefix = renderDir,
			LR_export_useSubfolder = false,
			LR_collisionHandling = "rename",
			LR_renamingTokensOn = false,
			LR_format = "JPEG",
			LR_jpeg_quality = 0.9,
			LR_export_colorSpace = "sRGB",
			LR_size_doConstrain = true,
			LR_size_resizeType = "longEdge",
			LR_size_maxWidth = size,
			LR_size_maxHeight = size,
			LR_size_units = "pixels",
			LR_size_resolution = 72,
			LR_size_doNotEnlarge = true,
			LR_outputSharpeningOn = false,
			LR_minimizeEmbeddedMetadata = true,
			LR_removeLocationMetadata = true,
			LR_includeVideoFiles = false,
			LR_useWatermark = false,
			LR_reimportExportedPhoto = false,
		},
	})

	local out = {}
	for _, rendition in session:renditions() do
		local ok, pathOrMessage = rendition:waitForRender()
		if not ok then
			error("render failed for " .. rendition.photo:getFormattedMetadata("fileName") .. ": " .. tostring(pathOrMessage))
		end
		local dest = wanted[photoId(rendition.photo)]
		LrFileUtils.createAllDirectories(LrPathUtils.parent(dest))
		if LrFileUtils.exists(dest) then
			LrFileUtils.delete(dest)
		end
		assert(LrFileUtils.move(pathOrMessage, dest))
		out[#out + 1] = dest
	end
	return out
end

function commands.snapshot(params)
	local n, warning = 0, nil
	LrApplication.activeCatalog():withWriteAccessDo("Match Look snapshot", function()
		eachItem(params, function(photo, item)
			local ok = LrTasks.pcall(function()
				photo:createDevelopSnapshot(item.name, true)
			end)
			if ok then
				n = n + 1
			else
				warning = "This Lightroom version can't create snapshots from plug-ins; use the History panel to undo."
			end
		end)
	end, WRITE_TIMEOUT)
	return { created = n, warning = warning }
end

function commands.set_label(params)
	local n = 0
	LrApplication.activeCatalog():withWriteAccessDo("Match Look label", function()
		eachItem(params, function(photo, item)
			photo:setRawMetadata("colorNameForLabel", item.label)
			n = n + 1
		end)
	end, WRITE_TIMEOUT)
	return { labeled = n }
end

-- Loop -----------------------------------------------------------------------

local function handle(path)
	local text = readFile(path)
	LrFileUtils.delete(path)

	local reqOk, request = pcall(Json.decode, text)
	local reply
	if not reqOk then
		reply = { ok = false, error = "bad request: " .. tostring(request) }
	else
		local fn = commands[request.command]
		if not fn then
			reply = { id = request.id, ok = false, error = "unknown command " .. tostring(request.command) }
		else
			-- LrTasks.pcall, not pcall: export and catalog writes yield.
			local ok, result = LrTasks.pcall(fn, request.params or {})
			reply = { id = request.id, ok = ok }
			if ok then
				reply.result = result
			else
				reply.error = tostring(result)
				Bridge.lastError = reply.error
			end
		end
	end

	local name = LrPathUtils.leafName(path)
	writeAtomic(LrPathUtils.child(outbox, name), Json.encode(reply))
	Bridge.handled = Bridge.handled + 1
end

function Bridge.run()
	if running then
		return
	end
	running, stopRequested = true, false
	LrFileUtils.createAllDirectories(inbox)
	LrFileUtils.createAllDirectories(outbox)

	while not stopRequested do
		local requests = {}
		for path in LrFileUtils.files(inbox) do
			if LrPathUtils.extension(path) == "json" then
				requests[#requests + 1] = path
			end
		end
		table.sort(requests)
		for _, path in ipairs(requests) do
			local ok, err = LrTasks.pcall(handle, path)
			if not ok then
				Bridge.lastError = tostring(err)
			end
		end
		LrTasks.sleep(POLL_SECONDS)
	end
	running = false
end

function Bridge.stop()
	stopRequested = true
end

function Bridge.isRunning()
	return running
end

return Bridge
