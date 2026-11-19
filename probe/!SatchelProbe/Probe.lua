-- Satchel Probe: records, per play session, which addons loaded, which Lua errors and
-- blocked actions each one caused, and saves it to SatchelProbeDB for Satchel's report
-- (probe/report.py). The "!" in the folder name makes it load before other addons, so
-- errors raised while they load are caught too.
--
-- It never changes another addon's state. By default it also hides Lua errors: it records
-- each one but doesn't pass it on to the handler that was already in place, so no error
-- window opens. "/probe hide combat" hides them only in combat, and "/probe hide off" passes
-- every error on as before. Hidden errors are in the saved report either way.
local ADDON = ...

local MAX_SESSIONS = 10
local MAX_ERRORS = 300
local MAX_MESSAGE = 400
local MAX_STACK = 1500
local HIDE_MODES = { all = true, combat = true, off = true }

local session = {
	started = time and time() or 0,
	errors = {},
	blocked = {},
	addons = {},
}
local seen = {}

-- Saved in SatchelProbeDB.settings once the probe's saved variables load. Until then (and for
-- anyone who never changed it) every error is hidden.
local settings
local inCombat = false
local hidden = { count = 0, messages = {}, unique = 0 }   -- since the last notice
local noticeShown = false

local function Say(text)
	if DEFAULT_CHAT_FRAME then
		DEFAULT_CHAT_FRAME:AddMessage("|cff66ccffSatchel Probe|r: " .. text)
	end
end

local function HideMode()
	local mode = settings and settings.hide
	return HIDE_MODES[mode] and mode or "all"
end

local function InCombat()
	if inCombat then return true end
	return InCombatLockdown ~= nil and InCombatLockdown() and true or false
end

local function Hiding(combat)
	local mode = HideMode()
	return mode == "all" or (mode == "combat" and combat)
end

-- "Interface/AddOns/Foo/Bar.lua:12: ..." -> "Foo". WoW shortens long paths
-- ("...erface/AddOns/Foo/..."), so only "AddOns/<folder>/" is required. In a stack,
-- Blizzard's own frames and error collectors (BugGrabber) are skipped.
local function AddonFromText(text, isStack)
	if type(text) ~= "string" then return nil end
	if not isStack then
		return text:match("[Aa]dd[Oo]ns[/\\]([^/\\:%]]+)[/\\]")
	end
	for line in text:gmatch("[^\n]+") do
		local folder = line:match("[Aa]dd[Oo]ns[/\\]([^/\\:%]]+)[/\\]")
		if folder and not folder:find("^Blizzard_") and not line:find("BugGrabber", 1, true)
				and folder ~= "!SatchelProbe" then
			return folder
		end
	end
end

local function Record(message, stack, combat)
	pcall(function()
		message = tostring(message):sub(1, MAX_MESSAGE)
		local entry = seen[message]
		if not entry then
			if #session.errors >= MAX_ERRORS then return end
			entry = {
				message = message,
				count = 0,
				combat = 0,   -- how many of `count` happened in combat
				addon = AddonFromText(message) or AddonFromText(stack, true)
					or message:match("tainted by '([^']+)'"),
				stack = type(stack) == "string" and stack:sub(1, MAX_STACK) or nil,
				first = time and time() or 0,
			}
			seen[message] = entry
			session.errors[#session.errors + 1] = entry
		end
		entry.count = entry.count + 1
		if combat then entry.combat = entry.combat + 1 end
	end)
end

local function CountHidden(message)
	pcall(function()
		local key = tostring(message):sub(1, MAX_MESSAGE)
		hidden.count = hidden.count + 1
		if not hidden.messages[key] then
			hidden.messages[key] = true
			hidden.unique = hidden.unique + 1
		end
	end)
end

local function ResetHidden()
	hidden = { count = 0, messages = {}, unique = 0 }
end

-- Wrap the current handler, and re-wrap whenever another addon installs its own,
-- so an error collector loaded later doesn't blind the probe.
local previous = geterrorhandler()
local handler
handler = function(message, ...)
	local combat = InCombat()
	Record(message, debugstack and debugstack(2) or nil, combat)
	if Hiding(combat) then
		CountHidden(message)
		-- One chat line per session (combat mode reports after each fight instead), never per error.
		if HideMode() == "all" and not noticeShown then
			noticeShown = true
			Say("Hiding Lua errors and recording them for Satchel. /probe lists them; /probe hide off shows them.")
		end
		return
	end
	if previous then return previous(message, ...) end
end
seterrorhandler(handler)
pcall(hooksecurefunc, "seterrorhandler", function(new)
	if new ~= handler then
		previous = new
		seterrorhandler(handler)
	end
end)

local function RecordBlocked(kind, addon, action)
	pcall(function()
		local key = kind .. "\0" .. tostring(addon) .. "\0" .. tostring(action)
		local entry = seen[key]
		if entry then
			entry.count = entry.count + 1
			return
		end
		entry = { kind = kind, addon = tostring(addon), action = tostring(action), count = 1 }
		seen[key] = entry
		session.blocked[#session.blocked + 1] = entry
	end)
end

local function Snapshot()
	local A = C_AddOns
	if not A then return end
	local player = UnitName and UnitName("player")
	local addons = {}
	for i = 1, A.GetNumAddOns() do
		local name, title, _, loadable, reason = A.GetAddOnInfo(i)
		if name then
			local okState, state = pcall(A.GetAddOnEnableState, name, player)
			local okVersion, version = pcall(A.GetAddOnMetadata, name, "Version")
			addons[name] = {
				title = title,
				loaded = A.IsAddOnLoaded(name) and true or false,
				loadable = loadable and true or false,
				reason = reason,
				enabled = okState and state or nil,
				version = okVersion and version or nil,
			}
		end
	end
	session.addons = addons
end

local function CombatEnded()
	inCombat = false
	if HideMode() == "combat" and hidden.count > 0 then
		Say(("Hid %d Lua error(s) during combat (%d different). /probe for a summary.")
			:format(hidden.count, hidden.unique))
	end
	ResetHidden()
end

local MODE_TEXT = {
	all = "hidden and recorded",
	combat = "hidden in combat, shown otherwise; all recorded",
	off = "shown and recorded",
}

local function Summary()
	local errorsBy, blockedBy = {}, {}
	for _, e in ipairs(session.errors) do
		local key = e.addon or "?"
		errorsBy[key] = (errorsBy[key] or 0) + e.count
	end
	for _, b in ipairs(session.blocked) do
		blockedBy[b.addon] = (blockedBy[b.addon] or 0) + b.count
	end
	Say(("%d different error(s), %d blocked action(s) this session."):format(#session.errors, #session.blocked))
	for addon, count in pairs(errorsBy) do Say(("  errors: %s x%d"):format(addon, count)) end
	for addon, count in pairs(blockedBy) do Say(("  blocked: %s x%d"):format(addon, count)) end
	Say("Lua errors are " .. MODE_TEXT[HideMode()] .. " (/probe hide all|combat|off).")
	Say("Log out or /reload to save the full report for Satchel.")
end

local frame = CreateFrame("Frame")
frame:RegisterEvent("ADDON_LOADED")
frame:RegisterEvent("PLAYER_LOGIN")
frame:RegisterEvent("PLAYER_LOGOUT")
frame:RegisterEvent("ADDON_ACTION_BLOCKED")
frame:RegisterEvent("ADDON_ACTION_FORBIDDEN")
frame:RegisterEvent("PLAYER_REGEN_DISABLED")
frame:RegisterEvent("PLAYER_REGEN_ENABLED")
frame:SetScript("OnEvent", function(_, event, arg1, arg2)
	if event == "PLAYER_REGEN_DISABLED" then
		inCombat = true
	elseif event == "PLAYER_REGEN_ENABLED" then
		CombatEnded()
	elseif event == "ADDON_LOADED" and arg1 == ADDON then
		if type(SatchelProbeDB) ~= "table" or type(SatchelProbeDB.sessions) ~= "table" then
			SatchelProbeDB = { sessions = {} }
		end
		if type(SatchelProbeDB.settings) ~= "table" then SatchelProbeDB.settings = {} end
		settings = SatchelProbeDB.settings
		settings.holdInCombat = nil   -- 1.1.0's setting; hiding everything is now the default
		local sessions = SatchelProbeDB.sessions
		sessions[#sessions + 1] = session
		while #sessions > MAX_SESSIONS do table.remove(sessions, 1) end
	elseif event == "PLAYER_LOGIN" then
		session.client = GetBuildInfo and table.concat({ GetBuildInfo() }, " ", 1, 4) or nil
		session.character = UnitName and UnitName("player") or nil
		session.realm = GetRealmName and GetRealmName() or nil
		Snapshot()
	elseif event == "PLAYER_LOGOUT" then
		-- Load-on-demand addons may have loaded since login.
		Snapshot()
		session.ended = time and time() or 0
	elseif event == "ADDON_ACTION_BLOCKED" or event == "ADDON_ACTION_FORBIDDEN" then
		RecordBlocked(event == "ADDON_ACTION_BLOCKED" and "blocked" or "forbidden", arg1, arg2)
	end
end)

SLASH_SATCHELPROBE1 = "/probe"
SlashCmdList.SATCHELPROBE = function(text)
	local command, value = (text or ""):lower():match("^%s*(%S*)%s*(%S*)")
	if command ~= "hide" then
		Summary()
		return
	end
	if not HIDE_MODES[value] then
		Say("Lua errors are " .. MODE_TEXT[HideMode()] .. ". Use /probe hide all, /probe hide combat or /probe hide off.")
		return
	end
	if not settings then
		Say("Settings aren't loaded yet; try again in a moment.")
		return
	end
	settings.hide = value
	ResetHidden()
	Say("Lua errors are now " .. MODE_TEXT[value] .. ".")
end
