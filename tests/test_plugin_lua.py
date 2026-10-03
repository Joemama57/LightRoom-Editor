"""Run the Lightroom plugin's Lua in a Lua 5.1 interpreter against a mocked SDK.

Lightroom itself can't run here, so the SDK modules (LrApplication, LrFileUtils,
...) are small fakes. This checks the plugin's own logic: JSON, the request
loop, and each command, end to end with the Python bridge client.
"""

import json
import os
import shutil
import threading
import time
from pathlib import Path

import pytest

lupa = pytest.importorskip("lupa.lua51")

PLUGIN = Path(__file__).resolve().parent.parent / "MatchLook.lrplugin"

MOCK_SDK = r"""
local py = ...
local mods = {}

-- Lightroom's plug-in sandbox has no os.rename/remove/time/execute.
os.rename, os.remove, os.time, os.execute = nil, nil, nil, nil

mods.LrDate = { currentTime = function() return py.now() end }

mods.LrPathUtils = {
  getStandardFilePath = function(which) return py.home end,
  child = function(a, b) return a .. "/" .. b end,
  parent = function(p) return (p:gsub("/[^/]*$", "")) end,
  leafName = function(p) return (p:match("[^/]*$")) end,
  extension = function(p) return p:match("%.([^./]*)$") or "" end,
}

mods.LrFileUtils = {
  createAllDirectories = function(p) py.makedirs(p) end,
  exists = function(p) return py.exists(p) end,
  delete = function(p) py.remove(p); return true end,
  move = function(a, b)
    if py.exists(b) then return false end  -- like Lightroom: never overwrites
    py.move(a, b); return true
  end,
  files = function(dir)
    local list = py.listdir(dir)
    local i = 0
    return function() i = i + 1; return list[i] end
  end,
}

mods.LrTasks = {
  pcall = pcall,
  sleep = function(s) py.sleep(s) end,
  startAsyncTask = function(fn) fn() end,
}

-- A tiny catalog
local photos, byId = {}, {}
local function makePhoto(id, name, fmt, settings)
  local p = { localIdentifier = id, settings = settings, label = nil, snapshots = {} }
  function p:getRawMetadata(k)
    if k == "fileFormat" then return fmt elseif k == "isVideo" then return false
    elseif k == "dateTimeOriginal" then return 750000000 + self.localIdentifier
    elseif k == "dimensions" then return { width = 4032, height = 3024 } end
  end
  function p:getFormattedMetadata(k)
    if k == "fileName" then return name elseif k == "cameraModel" then return "Sony A7 IV" end
  end
  function p:getDevelopSettings()
    local copy = {}
    for k, v in pairs(self.settings) do copy[k] = v end
    return copy
  end
  function p:applyDevelopSettings(s, historyName)
    assert(py.in_write_access(), "applyDevelopSettings outside write access")
    self.historyName = historyName
    -- Like Lightroom, silently ignore a value it doesn't accept.
    for k, v in pairs(s) do if v ~= "Bogus" then self.settings[k] = v end end
  end
  function p:createDevelopSnapshot(n, update) table.insert(self.snapshots, n) end
  function p:setRawMetadata(k, v)
    assert(py.in_write_access(), "setRawMetadata outside write access")
    if k == "colorNameForLabel" then self.label = v end
  end
  photos[#photos + 1] = p
  byId[id] = p
  return p
end
makePhoto(11, "A.ARW", "RAW", { Temperature = 5500, Tint = 3, ToneCurvePV2012 = { 0, 0, 255, 255 }, CameraProfile = "Adobe Standard" })
makePhoto(12, "B.jpg", "JPG", { Temperature = 0, Tint = 0 })

local catalog = { selected = { photos[1], photos[2] }, active = photos[1] }
function catalog:getTargetPhoto() return self.active end
function catalog:getTargetPhotos() return self.selected end
function catalog:getPath() return "/Catalog.lrcat" end
function catalog:getPhotoByLocalId(id) return byId[id] end
function catalog:withWriteAccessDo(name, fn, opts)
  py.set_write_access(true)
  local ok, err = pcall(fn)
  py.set_write_access(false)
  if not ok then error(err) end
end

mods.LrApplication = { activeCatalog = function() return catalog end }

-- Develop module: createNewMask appends an AI correction to the selected photo,
-- like Lightroom does once the selection has computed.
local view = { module = "library" }
mods.LrApplicationView = { switchToModule = function(m) view.module = m end }
mods.LrDevelopController = {
  goToMasking = function() end,
  createNewMask = function(kind, subtype)
    assert(view.module == "develop", "createNewMask outside Develop")
    local photo = catalog.active
    local list = photo.settings.MaskGroupBasedCorrections or {}
    if subtype == "nothing" then return nil end
    list[#list + 1] = { What = "Correction", CorrectionMasks = { { MaskSubType = subtype } }, LocalTemperature = 0 }
    photo.settings.MaskGroupBasedCorrections = list
    return nil  -- like Lightroom: no id even on success
  end,
}
function catalog:setSelectedPhotos(active, list) self.active = active; self.selected = list end

mods.LrExportSession = function(args)
  local session = {}
  function session:renditions()
    local i = 0
    return function()
      i = i + 1
      local photo = args.photosToExport[i]
      if not photo then return nil end
      local dir = args.exportSettings.LR_export_destinationPathPrefix
      return i, {
        photo = photo,
        waitForRender = function()
          local path = dir .. "/" .. photo:getFormattedMetadata("fileName") .. ".jpg"
          py.write(path, "jpeg-bytes-" .. photo.localIdentifier)
          return true, path
        end,
      }
    end
  end
  return session
end

function import(name) return assert(mods[name], "no mock for " .. name) end
return catalog, byId
"""


class PyHelpers:
    def __init__(self, home):
        self.home = str(home)
        self.write_access = False
        self.stop_at = None
        self.bridge_module = None

    def now(self):
        return time.time() - 978307200  # seconds since 2001, like LrDate

    def makedirs(self, p):
        os.makedirs(p, exist_ok=True)

    def exists(self, p):
        return os.path.exists(p)

    def remove(self, p):
        os.remove(p)

    def move(self, a, b):
        shutil.move(a, b)

    def listdir(self, d, _lua=None):
        return self._lua.table_from([os.path.join(d, f) for f in sorted(os.listdir(d))])

    def write(self, path, text):
        Path(path).write_text(text)

    def sleep(self, s):
        # Lightroom-side waits (mask polling, settling) don't need real time here.
        time.sleep(min(s, 0.02))
        if self.stop_at and time.monotonic() > self.stop_at:
            self.bridge_module.stop()

    def in_write_access(self):
        return self.write_access

    def set_write_access(self, v):
        self.write_access = v


@pytest.fixture
def lua_env(tmp_path):
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    lua.execute(f"package.path = '{PLUGIN}/?.lua;' .. package.path")
    py = PyHelpers(tmp_path)
    py._lua = lua
    catalog, by_id = lua.execute(MOCK_SDK, py)
    return lua, py, catalog, by_id


def test_json_round_trip(lua_env):
    lua, *_ = lua_env
    Json = lua.require("Json")
    if isinstance(Json, tuple):
        Json = Json[0]
    doc = {
        "id": "abc", "n": 5500, "f": -0.25, "big": 1e20, "t": True, "fl": False,
        "arr": [0, 10, 64.5, 255], "nested": {"k": ["x", {"y": 1}]}, "empty": {},
        "s": 'quote " backslash \\ newline \n tab \t unicode é € 日本 😀',
    }
    lua_value = Json.decode(json.dumps(doc))
    back = json.loads(Json.encode(lua_value))
    assert back == doc
    assert json.loads(Json.encode(Json.decode('"\\u00e9\\ud83d\\ude00"'))) == "é😀"
    with pytest.raises(Exception):
        Json.decode('{"a": 1,}')


def run_bridge_with_client(lua_env, client_fn, seconds=3.0):
    """Run the Lua request loop on this thread while a Python client talks to it."""
    lua, py, *_ = lua_env
    from engine.bridge import Bridge as Client

    bridge = lua.require("Bridge")
    if isinstance(bridge, tuple):
        bridge = bridge[0]
    py.bridge_module = bridge
    py.stop_at = time.monotonic() + seconds

    client = Client(root=Path(py.home) / ".matchlook" / "bridge", timeout=seconds, poll=0.02)
    out = {}

    def worker():
        try:
            out["result"] = client_fn(client)
        except Exception as e:  # surfaced to the test below
            out["error"] = e
        finally:
            py.stop_at = time.monotonic()  # let the Lua loop exit

    t = threading.Thread(target=worker)
    t.start()
    bridge.run()
    t.join()
    if "error" in out:
        raise out["error"]
    return out["result"]


def test_bridge_commands_end_to_end(lua_env, tmp_path):
    lua, py, catalog, by_id = lua_env
    renders = tmp_path / "out"

    def client_fn(c):
        results = {"ping": c.ping(), "selection": c.get_selection()}
        results["apply"] = c.apply_settings([{"id": "12", "settings": {
            "Temperature": 12.5, "WhiteBalance": "Custom", "ToneCurvePV2012": [0, 5, 255, 250]}}])
        results["render"] = c.render([{"id": "11", "path": str(renders / "a.jpg")},
                                      {"id": "12", "path": str(renders / "sub" / "b.jpg")}], size=512)
        results["snapshot"] = c.snapshot([{"id": "12", "name": "Before Match Look"}])
        results["label"] = c.set_label([{"id": "12", "label": "yellow"}])
        return results

    r = run_bridge_with_client(lua_env, client_fn)

    assert r["ping"]["plugin"] == "Match Look Bridge"
    sel = r["selection"]
    assert sel["active"] == "11"
    assert [p["id"] for p in sel["photos"]] == ["11", "12"]
    a = sel["photos"][0]
    assert a["fileName"] == "A.ARW" and a["fileFormat"] == "RAW"
    assert a["captureTime"] == 750000011 and a["dimensions"] == {"width": 4032, "height": 3024}
    assert a["settings"]["ToneCurvePV2012"] == [0, 0, 255, 255]

    b = by_id[12]
    assert b.settings.Temperature == 12.5 and b.settings.WhiteBalance == "Custom"
    assert list(b.settings.ToneCurvePV2012.values()) == [0, 5, 255, 250]
    assert r["apply"] == {"applied": 1, "not_taken": {}}  # empty Lua table encodes as {}
    assert b.historyName == "Match Look"

    assert r["render"] == [str(renders / "a.jpg"), str(renders / "sub" / "b.jpg")]
    assert (renders / "sub" / "b.jpg").read_text() == "jpeg-bytes-12"

    assert r["snapshot"]["created"] == 1 and list(b.snapshots.values()) == ["Before Match Look"]
    assert b.label == "yellow"
    # Nothing left behind in the shared folder.
    root = Path(py.home) / ".matchlook" / "bridge"
    assert list((root / "inbox").iterdir()) == [] and list((root / "outbox").iterdir()) == []


def test_bridge_reports_errors(lua_env):
    from engine.bridge import BridgeError

    def client_fn(c):
        errors = []
        for call in (lambda: c.call("nope"), lambda: c.apply_settings([{"id": "999", "settings": {}}])):
            try:
                call()
            except BridgeError as e:
                errors.append(str(e))
        return errors

    errors = run_bridge_with_client(lua_env, client_fn)
    assert "unknown command nope" in errors[0]
    assert "photo 999 not found" in errors[1]


def test_no_selection_returns_no_photos(lua_env):
    lua, py, catalog, _ = lua_env
    catalog.active = None
    sel = run_bridge_with_client(lua_env, lambda c: c.get_selection())
    assert sel.get("active") is None
    assert sel["photos"] in ({}, [])


def test_apply_reports_settings_lightroom_ignored(lua_env):
    def client_fn(c):
        return c.apply_settings([{"id": "11", "settings": {"CameraProfile": "Bogus", "Exposure2012": 0.5}}])

    r = run_bridge_with_client(lua_env, client_fn)
    assert r["applied"] == 1
    assert r["not_taken"] == [{"id": "11", "key": "CameraProfile", "wanted": "Bogus", "got": "Adobe Standard"}]


def test_only_one_loop_runs_and_stop_file_ends_it(lua_env):
    lua, py, *_ = lua_env
    bridge = lua.require("Bridge")
    if isinstance(bridge, tuple):
        bridge = bridge[0]
    root = Path(py.home) / ".matchlook" / "bridge"
    seen = {}

    def sleep(s):
        # While the first loop runs: it reports running, and a second run() returns at once.
        seen["running"] = bridge.isRunning()
        bridge.run()  # must not start a nested loop
        seen["second_returned"] = True
        (root / "stop").write_text("stop")  # what Shutdown.lua does from another script

    py.sleep = sleep
    bridge.run()
    assert seen == {"running": True, "second_returned": True}
    assert not (root / "stop").exists() and not (root / "heartbeat").exists()
    assert not bridge.isRunning()


def test_get_settings_and_mask_adjust(lua_env):
    lua, py, catalog, by_id = lua_env

    def client_fn(c):
        first = c.mask_adjust("12", "subject", {"LocalTemperature": -10, "LocalExposure2012": 0.3})
        second = c.mask_adjust("12", "subject", {"LocalTemperature": -20, "LocalExposure2012": 0.3})
        (settings,) = c.get_settings([{"id": "12"}, {"id": "999"}])  # 999 doesn't exist: skipped
        return first, second, settings

    first, second, settings = run_bridge_with_client(lua_env, client_fn)
    assert first["created"] is True and first["found"] is True
    assert second["created"] is False  # found again by name, not re-created
    assert second["values"] == {"LocalTemperature": -20, "LocalExposure2012": 0.3}
    (mask,) = settings["settings"]["MaskGroupBasedCorrections"]
    assert mask["CorrectionName"] == "Match Look subject"
    assert mask["LocalTemperature"] == pytest.approx(-0.2)  # stored as a fraction of -100..100
    assert mask["LocalExposure2012"] == pytest.approx(0.3)  # stops, unscaled
    # The user's selection is put back afterwards.
    assert catalog.active.localIdentifier == 11


def test_mask_adjust_reports_no_detection(lua_env):
    from engine.bridge import BridgeError

    def client_fn(c):
        try:
            c.mask_adjust("12", "objects", {"LocalTemperature": -10})
        except BridgeError as e:
            return str(e)

    lua, py, *_ = lua_env
    # Make the AI selection find nothing, as on a photo without a subject.
    lua.execute("local m = import('LrDevelopController'); local orig = m.createNewMask; "
                "m.createNewMask = function(k, s) return orig(k, 'nothing') end")
    msg = run_bridge_with_client(lua_env, client_fn, seconds=10.0)
    assert "didn't create a objects mask" in msg


def test_get_settings_names_each_photo_and_survives_a_failing_one(lua_env):
    lua, py, catalog, by_id = lua_env
    lua.execute("""
      local p = ...
      function p:getDevelopSettings() error("photo of a closed catalog") end
    """, by_id[11])
    settings = run_bridge_with_client(lua_env, lambda c: c.get_settings([{"id": "11"}, {"id": "12"}]))
    assert [(s["id"], s["fileName"]) for s in settings] == [("12", "B.jpg")]
