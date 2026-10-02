local Bridge = require "Bridge"

-- Never let shutdown raise: Lightroom then marks the plug-in as broken
-- ("may not work") even though the next load is fine.
pcall(Bridge.stop)
