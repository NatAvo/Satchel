import sys
from pathlib import Path

from lupa import luajit21

PROBE = Path(__file__).resolve().parents[1] / "probe"
sys.path.insert(0, str(PROBE))

import report  # noqa: E402

WOW_STUBS = r"""
handlers = {}
local current = function(msg) last_default = msg end
function geterrorhandler() return current end
function seterrorhandler(h) current = h; for _, hook in ipairs(handlers) do hook(h) end end
function hooksecurefunc(name, hook) assert(name == "seterrorhandler"); handlers[#handlers + 1] = hook end
function debugstack() return "Interface/AddOns/Stack/Owner.lua:3: in function <...>" end
function time() return 1000 end
function UnitName() return "Chishi" end
function GetRealmName() return "Ironhorn" end
function GetBuildInfo() return "1.60.1", "70862", "Nov 1 2026", 16001 end
local frames = {}
function CreateFrame()
    local f = { events = {} }
    function f:RegisterEvent(e) self.events[e] = true end
    function f:SetScript(_, fn) self.onEvent = fn end
    frames[#frames + 1] = f
    return f
end
function fire(event, ...) for _, f in ipairs(frames) do if f.events[event] then f.onEvent(f, event, ...) end end end
local list = {
    { "!SatchelProbe", true, true, nil }, { "Good", true, true, nil },
    { "Broken", true, true, nil }, { "Good_Retail", false, false, "INTERFACE_VERSION" },
    { "NeedsDep", false, false, "DEP_MISSING" }, { "Later", false, true, "DEMAND_LOADED" },
}
C_AddOns = {
    GetNumAddOns = function() return #list end,
    GetAddOnInfo = function(i) local a = list[i]; return a[1], a[1] .. " title", "", a[3], a[4] end,
    IsAddOnLoaded = function(name) for _, a in ipairs(list) do if a[1] == name then return a[2] end end end,
    GetAddOnEnableState = function() return 2 end,
    GetAddOnMetadata = function() return "1.0" end,
}
SlashCmdList = {}

-- Serialises plain data back to Lua, as the game does when it saves SavedVariables.
function dump(v, indent)
    indent = indent or ""
    if type(v) == "table" then
        local out = { "{\n" }
        for k, val in pairs(v) do
            local key = type(k) == "number" and ("[" .. k .. "]") or ("[" .. string.format("%q", k) .. "]")
            out[#out + 1] = indent .. "  " .. key .. " = " .. dump(val, indent .. "  ") .. ",\n"
        end
        return table.concat(out) .. indent .. "}"
    elseif type(v) == "string" then
        return string.format("%q", v)
    end
    return tostring(v)
end
"""


def load_probe(saved: str = "") -> luajit21.LuaRuntime:
    """Loads the probe; `saved` runs first, like SavedVariables from an earlier session."""
    rt = luajit21.LuaRuntime(unpack_returned_tuples=True)
    rt.execute(WOW_STUBS)
    rt.execute("chat = {}; DEFAULT_CHAT_FRAME = { AddMessage = function(_, text) chat[#chat + 1] = text end }")
    rt.execute(saved)
    code = (PROBE / "!SatchelProbe" / "Probe.lua").read_text("utf-8")
    rt.execute(code.replace("local ADDON = ...", "local ADDON = '!SatchelProbe'"))
    return rt


def run_probe(saved: str = "") -> luajit21.LuaRuntime:
    rt = load_probe(saved)
    rt.execute("""
        fire("ADDON_LOADED", "!SatchelProbe")
        geterrorhandler()("Interface/AddOns/Broken/Main.lua:10: attempt to index nil")
        geterrorhandler()("Interface/AddOns/Broken/Main.lua:10: attempt to index nil")
        -- A later addon installs its own handler; the probe must still see errors.
        seterrorhandler(function(msg) collector_saw = msg end)
        geterrorhandler()("no path in this message")
        fire("ADDON_ACTION_BLOCKED", "Broken", "CastSpellByName()")
        fire("PLAYER_LOGIN")
        fire("PLAYER_LOGOUT")
    """)
    return rt


def test_probe_records_errors_blocks_and_load_state():
    rt = run_probe()
    session = rt.eval("SatchelProbeDB.sessions[1]")
    errors = session.errors
    assert errors[1].addon == "Broken" and errors[1].count == 2
    assert errors[2].addon == "Stack"          # attributed from the stack when the message has no path
    assert rt.eval("collector_saw") is None and rt.eval("last_default") is None   # hidden by default
    assert session.blocked[1].action == "CastSpellByName()"
    assert session.addons["Good_Retail"].reason == "INTERFACE_VERSION"
    assert session.addons["Good"].loaded is True
    assert session.client == "1.60.1 70862 Nov 1 2026 16001" and session.character == "Chishi"


def test_report_reads_saved_variables_and_judges_each_addon(tmp_path):
    rt = run_probe()
    saved = tmp_path / "!SatchelProbe.lua"
    saved.write_text("SatchelProbeDB = " + rt.eval("dump(SatchelProbeDB)") + "\n", "utf-8")
    sessions = report.load_sessions(saved)
    assert len(sessions) == 1
    results = {r.name: r for r in report.evaluate(sessions[0], {
        "Good addon": ["Good", "Good_Retail"],
        "Broken addon": ["Broken"],
        "Needs a dependency": ["NeedsDep"],
        "Load on demand": ["Later"],
        "Never installed": ["Ghost"],
    })}
    assert results["Good addon"].verdict == "PASS" and results["Good addon"].skipped == ["Good_Retail"]
    assert results["Broken addon"].verdict == "FAIL"
    assert len(results["Broken addon"].errors) == 1 and len(results["Broken addon"].blocked) == 1
    assert results["Needs a dependency"].verdict == "FAIL"
    assert "DEP_MISSING" in results["Needs a dependency"].missing[0]
    assert results["Load on demand"].verdict == "NOT LOADED"
    assert results["Never installed"].verdict == "FAIL"


BUGGRABBER_STACK = (
    "[Interface/AddOns/Blizzard_SharedXMLBase/CallbackRegistry.lua]:213: in function 'TriggerEvent'\n"
    "[Interface/AddOns/Decursive/Libs/BugGrabber/BugGrabber.lua]:422: in function <...>\n"
    "[Interface/AddOns/HealBot/HealBot.lua]:1641: in function 'HealBot_ItemIdsInBag'\n"
)


def test_probe_attributes_shortened_paths_and_skips_blizzard_frames():
    rt = run_probe()
    rt.execute("""
        geterrorhandler()("...erface/AddOns/DisenchantBuddy/GetTooltipLineData.lua:29: attempt to index field 'API'")
        function debugstack() return [[%s]] end
        geterrorhandler()("Usage: local outObject = Mixin(object, ...)")
    """ % BUGGRABBER_STACK)
    errors = rt.eval("SatchelProbeDB.sessions[1].errors")
    owners = {errors[i].message: errors[i].addon for i in errors}
    assert owners["...erface/AddOns/DisenchantBuddy/GetTooltipLineData.lua:29: attempt to index field 'API'"] == "DisenchantBuddy"
    assert owners["Usage: local outObject = Mixin(object, ...)"] == "HealBot"


def test_report_reattributes_errors_recorded_by_an_older_probe():
    assert report.attribute({"message": "...erface/AddOns/DisenchantBuddy/X.lua:29: boom",
                             "addon": "Blizzard_SharedXMLBase"}) == "DisenchantBuddy"
    assert report.attribute({"message": "no path", "stack": BUGGRABBER_STACK,
                             "addon": "Blizzard_SharedXMLBase"}) == "HealBot"
    assert report.attribute({"message": "no path", "addon": "Old"}) == "Old"
    taint = "GetAuraDataByIndex(): Auras cannot be accessed when secret while tainted by 'Questie'\nLua Taint: Questie"
    assert report.attribute({"message": taint}) == "Questie"


def test_folders_hidden_from_forever_by_game_type_are_expected(tmp_path):
    def toc(folder, text):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / f"{folder}.toc").write_text(text)
    toc("Retail", "## Interface: 120105\n## AllowLoadGameType: tbc, wrath, standard\n")
    toc("Excluded", "## ExcludeLoadGameType: camelot\n")
    toc("Ours", "## AllowLoadGameType: camelot, standard\n")
    toc("Plain", "## Interface: 16001\n")
    (tmp_path / "LibOnly").mkdir()   # no TOC at all: not an addon the game lists
    from satchel import wow
    assert [wow.loads_on_forever(tmp_path / f) for f in ("Retail", "Excluded", "Ours", "Plain", "LibOnly")] \
        == [False, False, True, True, False]

    session = {"addons": {"Ours": {"loaded": True}}}
    [result] = report.evaluate(session, {"Pack": ["Ours", "Retail", "LibOnly", "Plain", "Gone"]}, tmp_path)
    assert result.skipped == ["Retail", "LibOnly"]
    assert "restart WoW" in result.missing[0] and "no longer on disk" in result.missing[1]


def test_errors_are_hidden_everywhere_by_default_but_recorded():
    rt = load_probe()
    rt.execute("""
        fire("ADDON_LOADED", "!SatchelProbe")
        shown = {}
        seterrorhandler(function(msg) shown[#shown + 1] = msg end)
        geterrorhandler()("Interface/AddOns/Broken/Main.lua:1: boom")
        fire("PLAYER_REGEN_DISABLED")
        geterrorhandler()("Interface/AddOns/Broken/Main.lua:1: boom")
        geterrorhandler()("Interface/AddOns/Other/Fight.lua:9: bang")
        fire("PLAYER_REGEN_ENABLED")
        geterrorhandler()("Interface/AddOns/Broken/Main.lua:1: boom")
    """)
    assert len(rt.eval("shown")) == 0
    chat = list(rt.eval("chat").values())
    assert sum("Hiding Lua errors" in line for line in chat) == 1   # one notice, not one per error
    assert not any("during combat" in line for line in chat)         # no extra line after each fight
    errors = {e.message: e for e in rt.eval("SatchelProbeDB.sessions[1].errors").values()}
    boom = errors["Interface/AddOns/Broken/Main.lua:1: boom"]
    assert (boom.count, boom.combat) == (3, 1)
    assert errors["Interface/AddOns/Other/Fight.lua:9: bang"].combat == 1


def test_hide_modes_combat_and_off():
    rt = load_probe("SatchelProbeDB = { sessions = {}, settings = { holdInCombat = false } }")
    rt.execute("""
        fire("ADDON_LOADED", "!SatchelProbe")
        shown = {}
        seterrorhandler(function(msg) shown[#shown + 1] = msg end)
        SlashCmdList.SATCHELPROBE("hide combat")
        fire("PLAYER_REGEN_DISABLED")
        geterrorhandler()("Interface/AddOns/A/x.lua:1: in fight")
        geterrorhandler()("Interface/AddOns/A/x.lua:1: in fight")
        fire("PLAYER_REGEN_ENABLED")
        geterrorhandler()("Interface/AddOns/A/x.lua:2: after fight")
        SlashCmdList.SATCHELPROBE("hide off")
        fire("PLAYER_REGEN_DISABLED")
        geterrorhandler()("Interface/AddOns/A/x.lua:3: loud")
        fire("PLAYER_REGEN_ENABLED")
        SlashCmdList.SATCHELPROBE("hide nonsense")
    """)
    assert list(rt.eval("shown").values()) == ["Interface/AddOns/A/x.lua:2: after fight",
                                               "Interface/AddOns/A/x.lua:3: loud"]
    chat = list(rt.eval("chat").values())
    assert any("Hid 2 Lua error(s) during combat (1 different)" in line for line in chat)
    assert "/probe hide all" in chat[-1]                    # an unknown mode explains the choices
    settings = rt.eval("SatchelProbeDB.settings")
    assert settings.hide == "off" and settings.holdInCombat is None   # 1.1.0's setting is dropped


def test_errors_are_hidden_before_settings_load():
    rt = load_probe()
    rt.execute('geterrorhandler()("Interface/AddOns/Early/Load.lua:1: boom")')
    assert rt.eval("last_default") is None


def test_error_list_groups_by_addon_across_sessions():
    sessions = [
        {"errors": [{"message": "Interface/AddOns/A/a.lua:1: x", "count": 5, "combat": 2},
                    {"message": "no path", "count": 1, "stack": BUGGRABBER_STACK}]},
        {"errors": [{"message": "Interface/AddOns/A/a.lua:1: x", "count": 3},
                    {"message": "Interface/AddOns/B/b.lua:9: y", "count": 1}]},
    ]
    grouped = report.collect_errors(sessions)
    assert list(grouped) == ["A", "B", "HealBot"] or list(grouped) == ["A", "HealBot", "B"]
    first = grouped["A"][0]
    assert (first.count, first.combat, first.sessions) == (8, 2, 2)
    assert report.stack_lines(BUGGRABBER_STACK) == ["[Interface/AddOns/HealBot/HealBot.lua]:1641: in function 'HealBot_ItemIdsInBag'"]


def test_report_cannot_reach_the_system(tmp_path):
    saved = tmp_path / "!SatchelProbe.lua"
    saved.write_text('SatchelProbeDB = { sessions = { { leaked = (os ~= nil) or (io ~= nil) or (require ~= nil) } } }',
                     "utf-8")
    assert report.load_sessions(saved) == [{"leaked": False}]
