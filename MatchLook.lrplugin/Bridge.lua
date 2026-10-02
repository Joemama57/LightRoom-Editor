--[[
File-based request loop. Each request is inbox/<id>.json:
    {"id": "...", "command": "render", "params": {...}}
and gets a reply in outbox/<id>.json:
    {"id": "...", "ok": true, "result": ...}   or   {"id": "...", "ok": false, "error": "..."}
]]

local LrApplication = import "LrApplication"
local LrApplicationView = import "LrApplicationView"
local LrDate = import "LrDate"
local LrDevelopController = import "LrDevelopController"
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

local heartbeatPath = LrPathUtils.child(Bridge.root, "heartbeat")
local stopPath = LrPathUtils.child(Bridge.root, "stop")
local HEARTBEAT_SECONDS = 2
local STALE_SECONDS = 6
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
	-- Lightroom's Lua has no os.rename; LrFileUtils.move won't overwrite, so clear the target first.
	if LrFileUtils.exists(path) then
		LrFileUtils.delete(path)
	end
	assert(LrFileUtils.move(tmp, path))
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

-- Lightroom silently ignores settings it doesn't accept (seen with malformed
-- CameraProfile values in LrC-AVG's tests on LrC 15.5.1), so read every write
-- back and report scalar values that didn't take.
local function notTaken(wanted, got)
	local missed = {}
	for key, value in pairs(wanted) do
		local actual = got[key]
		local ok
		if type(value) == "number" then
			ok = type(actual) == "number" and math.abs(actual - value) <= 0.011
		elseif type(value) == "table" then
			ok = true -- curves and nested looks: not compared
		else
			ok = actual == value
		end
		if not ok then
			missed[#missed + 1] = { key = key, wanted = value, got = actual }
		end
	end
	return missed
end

function commands.apply_settings(params)
	local catalog = LrApplication.activeCatalog()
	local applied = {}
	catalog:withWriteAccessDo("Match Look", function()
		eachItem(params, function(photo, item)
			-- The second argument names the step in the History panel.
			photo:applyDevelopSettings(item.settings, params.history_name or "Match Look")
			applied[#applied + 1] = { photo = photo, item = item }
		end)
	end, WRITE_TIMEOUT)

	local notTakenList = {}
	for _, entry in ipairs(applied) do
		for _, miss in ipairs(notTaken(entry.item.settings, entry.photo:getDevelopSettings())) do
			miss.id = entry.item.id
			notTakenList[#notTakenList + 1] = miss
		end
	end
	return { applied = #applied, not_taken = notTakenList }
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

-- Photos that no longer exist (deleted, or another catalog is open) are skipped.
function commands.get_settings(params)
	local out = {}
	for _, item in ipairs(params.items or {}) do
		local ok, photo = LrTasks.pcall(findPhoto, item.id)
		if ok then
			out[#out + 1] = { id = item.id, settings = photo:getDevelopSettings() }
		end
	end
	return out
end

-- Experimental: AI masks ---------------------------------------------------------
--
-- Creating an AI mask (Select Subject / Sky / ...) is only possible through
-- LrDevelopController, which drives the Develop module on screen: the photo has
-- to be selected and Develop open. The approach and its workarounds follow
-- par4987/lightroom-mcp's HandlerAIMasks.lua (MIT, Copyright (c) 2026 Marcin
-- Skalski), which found that:
--   * createNewMask can return nil while the AI selection is still computing,
--     so success is detected by polling for the new correction;
--   * a catalog write fails after the many yields of that sequence, so the
--     mask's sliders are written from a fresh task;
--   * local sliders are stored as a fraction of their -100..100 range, except
--     LocalExposure2012, which is in stops.
-- Our correction is found again later by its CorrectionName.

local MASK_KINDS = { subject = true, sky = true, background = true, people = true, objects = true, landscape = true }
local MASK_POLL_ATTEMPTS = 10
local MASK_POLL_SECONDS = 1.5

local function corrections(photo)
	local value = photo:getDevelopSettings().MaskGroupBasedCorrections
	return type(value) == "table" and value or {}
end

local function findCorrection(list, name)
	for i, c in ipairs(list) do
		if type(c) == "table" and c.CorrectionName == name then
			return i, c
		end
	end
	return nil
end

local function writeInFreshTask(fn)
	local done, ok, err = false, false, nil
	LrTasks.startAsyncTask(function()
		ok, err = LrTasks.pcall(fn)
		done = true
	end)
	for _ = 1, 200 do
		if done then
			break
		end
		LrTasks.sleep(0.05)
	end
	if not done then
		error("mask write did not finish within 10 s")
	end
	if not ok then
		error(err)
	end
end

function commands.mask_adjust(params)
	local kind = params.kind
	if not MASK_KINDS[kind] then
		error("unknown mask kind " .. tostring(kind))
	end
	local catalog = LrApplication.activeCatalog()
	local photo = findPhoto(params.id)
	local name = "Match Look " .. kind
	local index = findCorrection(corrections(photo), name)
	local created = false

	if not index then
		-- Remember the selection so it can be put back afterwards.
		local previousActive = catalog:getTargetPhoto()
		local previousSelection = catalog:getTargetPhotos()

		LrApplicationView.switchToModule("develop")
		LrTasks.sleep(0.5)
		catalog:setSelectedPhotos(photo, { photo })
		LrTasks.sleep(0.3)
		LrTasks.pcall(function()
			LrDevelopController.goToMasking()
		end)
		LrTasks.sleep(1.0)

		local before = #corrections(photo)
		local ok, err = LrTasks.pcall(function()
			return LrDevelopController.createNewMask("aiSelection", kind)
		end)
		if not ok then
			error("createNewMask failed (this Lightroom version may not support it): " .. tostring(err))
		end
		for _ = 1, MASK_POLL_ATTEMPTS do
			local n = #corrections(photo)
			if n > before then
				index = n
				created = true
				break
			end
			LrTasks.sleep(MASK_POLL_SECONDS)
		end

		if previousActive then
			LrTasks.pcall(function()
				catalog:setSelectedPhotos(previousActive, previousSelection)
			end)
		end
		if not index then
			error("Lightroom didn't create a " .. kind .. " mask (nothing detected, or AI masking unavailable)")
		end
	end

	writeInFreshTask(function()
		catalog:withWriteAccessDo("Match Look mask", function()
			local list = corrections(photo)
			local c = list[index]
			if type(c) ~= "table" then
				error("the new mask's correction disappeared")
			end
			c.CorrectionName = name
			for key, value in pairs(params.values or {}) do
				c[key] = (key == "LocalExposure2012") and value or value / 100
			end
			photo:applyDevelopSettings(
				{ EnableMaskGroupBasedCorrections = true, MaskGroupBasedCorrections = list },
				"Match Look mask"
			)
		end, WRITE_TIMEOUT)
	end)

	local _, ours = findCorrection(corrections(photo), name)
	local values = {}
	if ours then
		for key in pairs(params.values or {}) do
			local v = ours[key]
			values[key] = (type(v) == "number" and key ~= "LocalExposure2012") and v * 100 or v
		end
	end
	return { created = created, found = ours ~= nil, values = values }
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

local function heartbeatAge()
	local ok, text = pcall(readFile, heartbeatPath)
	local stamp = ok and tonumber(text)
	return stamp and (LrDate.currentTime() - stamp) or math.huge
end

function Bridge.run()
	-- Another loop (e.g. from a menu script with its own copy of this module)
	-- is already serving the inbox: never run two.
	if running or heartbeatAge() < STALE_SECONDS then
		return
	end
	running, stopRequested = true, false
	LrFileUtils.createAllDirectories(inbox)
	LrFileUtils.createAllDirectories(outbox)
	if LrFileUtils.exists(stopPath) then
		LrFileUtils.delete(stopPath)
	end

	local lastBeat = 0
	while not stopRequested and not LrFileUtils.exists(stopPath) do
		if LrDate.currentTime() - lastBeat >= HEARTBEAT_SECONDS then
			lastBeat = LrDate.currentTime()
			writeAtomic(heartbeatPath, tostring(lastBeat))
		end
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
	if LrFileUtils.exists(stopPath) then
		LrFileUtils.delete(stopPath)
	end
	if LrFileUtils.exists(heartbeatPath) then
		LrFileUtils.delete(heartbeatPath)
	end
end

-- A stop file on disk reaches the loop even when it runs in another copy of this module.
function Bridge.stop()
	stopRequested = true
	if running or Bridge.isRunning() then
		writeAtomic(stopPath, "stop")
	end
end

-- Read from disk so any script can tell, whichever copy of the module runs the loop.
function Bridge.isRunning()
	return heartbeatAge() < STALE_SECONDS
end

return Bridge
