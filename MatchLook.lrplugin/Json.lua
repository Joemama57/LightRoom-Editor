--[[
Minimal JSON encode/decode for Lightroom's Lua 5.1.

Tables whose keys are exactly 1..n encode as arrays; any other non-empty table
encodes as an object. Empty tables encode as {}.
]]

local Json = {}

local escapes = {
	['"'] = '\\"', ["\\"] = "\\\\", ["\b"] = "\\b", ["\f"] = "\\f",
	["\n"] = "\\n", ["\r"] = "\\r", ["\t"] = "\\t",
}

-- Control bytes are listed explicitly: %c follows the C locale, and on macOS
-- in a UTF-8 locale it also matches 0x80-0x9F, which are continuation bytes
-- of characters such as emoji or "€", and escaping them corrupts the text.
local function encodeString(s)
	return '"' .. s:gsub('[%z\1-\31\127"\\]', function(c)
		return escapes[c] or string.format("\\u%04x", c:byte())
	end) .. '"'
end

local function isArray(t)
	local n = 0
	for _ in pairs(t) do
		n = n + 1
	end
	if n == 0 then
		return false
	end
	for i = 1, n do
		if t[i] == nil then
			return false
		end
	end
	return true
end

local encodeValue

local function encodeTable(t, seen)
	if seen[t] then
		error("JSON: circular reference")
	end
	seen[t] = true
	local parts = {}
	local out
	if isArray(t) then
		for i = 1, #t do
			parts[i] = encodeValue(t[i], seen)
		end
		out = "[" .. table.concat(parts, ",") .. "]"
	else
		for k, v in pairs(t) do
			parts[#parts + 1] = encodeString(tostring(k)) .. ":" .. encodeValue(v, seen)
		end
		out = "{" .. table.concat(parts, ",") .. "}"
	end
	seen[t] = nil
	return out
end

encodeValue = function(v, seen)
	local kind = type(v)
	if kind == "nil" then
		return "null"
	elseif kind == "boolean" then
		return tostring(v)
	elseif kind == "number" then
		if v ~= v or v == math.huge or v == -math.huge then
			return "null"
		end
		if v == math.floor(v) and math.abs(v) < 1e15 then
			return string.format("%d", v)
		end
		return string.format("%.14g", v)
	elseif kind == "string" then
		return encodeString(v)
	elseif kind == "table" then
		return encodeTable(v, seen)
	end
	return "null" -- functions, userdata: not representable
end

function Json.encode(v)
	return encodeValue(v, {})
end

-- Decoding ------------------------------------------------------------------

local function decodeError(str, i, msg)
	error(string.format("JSON: %s at position %d near '%s'", msg, i, str:sub(i, i + 10)))
end

local function skip(str, i)
	return str:find("[^ \t\r\n]", i) or #str + 1
end

local function utf8(cp)
	if cp < 0x80 then
		return string.char(cp)
	elseif cp < 0x800 then
		return string.char(0xC0 + math.floor(cp / 0x40), 0x80 + cp % 0x40)
	elseif cp < 0x10000 then
		return string.char(0xE0 + math.floor(cp / 0x1000), 0x80 + math.floor(cp / 0x40) % 0x40, 0x80 + cp % 0x40)
	end
	return string.char(
		0xF0 + math.floor(cp / 0x40000),
		0x80 + math.floor(cp / 0x1000) % 0x40,
		0x80 + math.floor(cp / 0x40) % 0x40,
		0x80 + cp % 0x40
	)
end

local unescapes = { b = "\b", f = "\f", n = "\n", r = "\r", t = "\t", ['"'] = '"', ["\\"] = "\\", ["/"] = "/" }

local decodeValue

local function decodeString(str, i)
	local parts = {}
	local j = i + 1
	while true do
		local c = str:sub(j, j)
		if c == "" then
			decodeError(str, i, "unterminated string")
		elseif c == '"' then
			return table.concat(parts), j + 1
		elseif c == "\\" then
			local e = str:sub(j + 1, j + 1)
			if e == "u" then
				local cp = tonumber(str:sub(j + 2, j + 5), 16)
				if not cp then
					decodeError(str, j, "bad \\u escape")
				end
				j = j + 6
				-- Surrogate pair
				if cp >= 0xD800 and cp <= 0xDBFF and str:sub(j, j + 1) == "\\u" then
					local lo = tonumber(str:sub(j + 2, j + 5), 16)
					if lo and lo >= 0xDC00 and lo <= 0xDFFF then
						cp = 0x10000 + (cp - 0xD800) * 0x400 + (lo - 0xDC00)
						j = j + 6
					end
				end
				parts[#parts + 1] = utf8(cp)
			elseif unescapes[e] then
				parts[#parts + 1] = unescapes[e]
				j = j + 2
			else
				decodeError(str, j, "bad escape")
			end
		else
			local k = str:find('["\\]', j) or #str + 1
			parts[#parts + 1] = str:sub(j, k - 1)
			j = k
		end
	end
end

local function decodeArray(str, i)
	local result = {}
	i = skip(str, i + 1)
	if str:sub(i, i) == "]" then
		return result, i + 1
	end
	while true do
		local v
		v, i = decodeValue(str, i)
		result[#result + 1] = v
		i = skip(str, i)
		local c = str:sub(i, i)
		if c == "]" then
			return result, i + 1
		elseif c ~= "," then
			decodeError(str, i, "expected ',' or ']'")
		end
		i = skip(str, i + 1)
	end
end

local function decodeObject(str, i)
	local result = {}
	i = skip(str, i + 1)
	if str:sub(i, i) == "}" then
		return result, i + 1
	end
	while true do
		if str:sub(i, i) ~= '"' then
			decodeError(str, i, "expected string key")
		end
		local key
		key, i = decodeString(str, i)
		i = skip(str, i)
		if str:sub(i, i) ~= ":" then
			decodeError(str, i, "expected ':'")
		end
		local v
		v, i = decodeValue(str, skip(str, i + 1))
		result[key] = v
		i = skip(str, i)
		local c = str:sub(i, i)
		if c == "}" then
			return result, i + 1
		elseif c ~= "," then
			decodeError(str, i, "expected ',' or '}'")
		end
		i = skip(str, i + 1)
	end
end

decodeValue = function(str, i)
	i = skip(str, i)
	local c = str:sub(i, i)
	if c == "{" then
		return decodeObject(str, i)
	elseif c == "[" then
		return decodeArray(str, i)
	elseif c == '"' then
		return decodeString(str, i)
	elseif str:sub(i, i + 3) == "true" then
		return true, i + 4
	elseif str:sub(i, i + 4) == "false" then
		return false, i + 5
	elseif str:sub(i, i + 3) == "null" then
		return nil, i + 4
	end
	local num = str:match("^-?%d+%.?%d*[eE]?[-+]?%d*", i)
	if num and #num > 0 then
		local n = tonumber(num)
		if n then
			return n, i + #num
		end
	end
	decodeError(str, i, "unexpected character")
end

function Json.decode(str)
	local v, i = decodeValue(str, 1)
	i = skip(str, i)
	if i <= #str then
		decodeError(str, i, "trailing characters")
	end
	return v
end

return Json
