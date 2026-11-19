"""Satchel's recommended addons, and the candidates being tested for that list.

Every entry's package declared Forever support when it was reviewed, but that only means
the author says it works. An addon is recommended only once it has been run in game and
found working; until then it is an untested candidate, shown only when asked for. Test
candidates with the SatchelProbe addon (see probe/README.md), then set `verified` to the
date and how it was checked.

A listed addon still goes through the normal checks each time it is installed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogItem:
    name: str
    category: str
    source: str
    repo: str
    note: str = ""
    verified: str = ""  # e.g. "2026-11-12: probe clean, features tried". Empty = untested.


QUESTING = "Questing"
INTERFACE = "Interface"
DUNGEONS = "Dungeons"
COMBAT = "Combat"
PROFESSIONS = "Professions & items"
SOCIAL = "Social"
ROLEPLAY = "Roleplay"
COSMETIC = "Cosmetic"

CATEGORIES = (QUESTING, INTERFACE, DUNGEONS, COMBAT, PROFESSIONS, SOCIAL, ROLEPLAY, COSMETIC)

IN_USE = "2026-11-12: in daily use on this install"

CATALOG = (
    # Questing
    CatalogItem("Questie", QUESTING, "github", "Questie/Questie", verified=IN_USE),
    CatalogItem("What's Training?", QUESTING, "github", "fusionpit/WhatsTraining"),
    CatalogItem("Forever Quest Tint", QUESTING, "github", "alexdia25/forever-quest-tint"),
    CatalogItem("AllTheThings", QUESTING, "github", "ATTWoWAddon/AllTheThings", "Collection and completion tracker"),
    CatalogItem("Carbonite", QUESTING, "github", "IrcDirk/Carbonite-All-in-One-Retail-Classic", "Maps, quests and notes"),
    CatalogItem("Syling Tracker", QUESTING, "github", "Skamer/Syling-Tracker", "Quest tracker"),
    CatalogItem("Immersion", QUESTING, "github", "seblindfors/Immersion", "Cinematic NPC dialogue"),
    # Interface
    CatalogItem("MinimapButtonButton", INTERFACE, "github", "syndenbock/MinimapButtonButton"),
    CatalogItem("Titan Panel", INTERFACE, "wowinterface", "8092", verified=IN_USE),
    CatalogItem("TipTac", INTERFACE, "github", "frozn/TipTac", "Tooltip enhancements"),
    CatalogItem("SexyMap", INTERFACE, "github", "funkydude/SexyMap", "Minimap customization"),
    CatalogItem("BlizzMove", INTERFACE, "github", "Kiatra/BlizzMove", "Move Blizzard windows"),
    CatalogItem("Dominos", INTERFACE, "github", "tullamods/Dominos", "Action bars"),
    CatalogItem("tullaRange", INTERFACE, "github", "tullamods/tullaRange", "Colors out-of-range buttons"),
    CatalogItem("IceHUD", INTERFACE, "github", "parnic/ice-hud", "Heads-up display"),
    CatalogItem("XLoot", INTERFACE, "github", "Xuerian/XLoot", "Loot frames"),
    CatalogItem("AdvancedInterfaceOptions", INTERFACE, "github", "Stanzilla/AdvancedInterfaceOptions", "Hidden settings"),
    CatalogItem("idTip", INTERFACE, "github", "silverwind/idTip", "Item and spell IDs in tooltips"),
    CatalogItem("BugSack", INTERFACE, "github", "funkydude/BugSack", "Collects Lua errors"),
    CatalogItem("LiteMount", INTERFACE, "github", "xod-wow/LiteMount", "Random mount picker"),
    CatalogItem("GW2 UI", INTERFACE, "github", "Mortalknight/GW2_UI", "Replaces the whole UI; test on its own"),
    CatalogItem("RealUI", INTERFACE, "github", "RealUI/RealUI", "Replaces the whole UI; test on its own"),
    # Dungeons
    CatalogItem(
        "DungeonJournal",
        DUNGEONS,
        "github",
        "andi-74/foreverdungeonjournal-mirror",
        "Third-party mirror, not published by the author",
        verified=IN_USE,
    ),
    CatalogItem(
        "Deadly Boss Mods",
        DUNGEONS,
        "github",
        "DeadlyBossMods/DeadlyBossMods",
        "Boss timers and warnings; add the DBM modules below for Forever content",
    ),
    CatalogItem("DBM: Vanilla raids", DUNGEONS, "github", "DeadlyBossMods/DBM-Vanilla", "Needs Deadly Boss Mods"),
    CatalogItem(
        "DBM: Dungeons",
        DUNGEONS,
        "github",
        "DeadlyBossMods/DBM-Dungeons",
        "Needs Deadly Boss Mods; includes the Forever dungeon module",
    ),
    CatalogItem("BigWigs", DUNGEONS, "github", "BigWigsMods/BigWigs", "Boss timers; alternative to DBM"),
    CatalogItem("LittleWigs", DUNGEONS, "github", "BigWigsMods/LittleWigs", "Needs BigWigs; has a Forever dungeon module"),
    # Combat
    CatalogItem("GTFO", COMBAT, "wowinterface", "17996", "Warns you when you stand in bad stuff"),
    CatalogItem("Decursive", COMBAT, "github", "2072/Decursive", "One-click cleansing for curses, poisons and more"),
    CatalogItem("ThreatClassic2", COMBAT, "github", "dfherr/ThreatClassic2", "Threat meter"),
    CatalogItem("Skada", COMBAT, "github", "zarnivoop/skada", "Damage meter"),
    CatalogItem("HealBot", COMBAT, "github", "davidmarshall15/Healbot", "Click-to-heal frames"),
    CatalogItem("Grid2", COMBAT, "github", "michaelnpsp/Grid2", "Raid frames"),
    CatalogItem("MaxDps", COMBAT, "github", "kaminaris/MaxDps", "Rotation helper"),
    CatalogItem("GSE", COMBAT, "github", "TimothyLuke/GSE-Advanced-Macro-Compiler", "Macro sequencer"),
    CatalogItem("ShamanPower", COMBAT, "github", "taubut/ShamanPower", "Totem management"),
    # Professions & items
    CatalogItem("Recipe Master", PROFESSIONS, "github", "BrenoLudgero/Recipe_Master", "Recipe tracker"),
    CatalogItem("DisenchantBuddy", PROFESSIONS, "github", "BreakBB/DisenchantBuddy", "Shows disenchant results"),
    CatalogItem("BagSync", PROFESSIONS, "github", "Xruptor/BagSync", "Items across your characters"),
    CatalogItem("RatingBuster", PROFESSIONS, "github", "raethkcj/RatingBuster", "Stat comparisons in tooltips"),
    # Social
    CatalogItem("Prat 3.0", SOCIAL, "wowinterface", "10783", "Chat enhancements"),
    CatalogItem("WIM (WoW Instant Messenger)", SOCIAL, "wowinterface", "5342", "Whispers in their own windows",
                verified=IN_USE),
    CatalogItem("xanChat", SOCIAL, "wowinterface", "18787", "Lightweight chat enhancements"),
    CatalogItem("Glass", SOCIAL, "github", "mixxorz/Glass", "Chat frame replacement"),
    CatalogItem("BetterFriendlist", SOCIAL, "github", "Hayato2846/BetterFriendlist", "Friends list replacement"),
    CatalogItem("Friend List Colors", SOCIAL, "wowinterface", "18256", "Customizes friends list colors"),
    CatalogItem("Chat Emotes", SOCIAL, "wowinterface", "26188", "Emotes in chat"),
    # Roleplay
    CatalogItem("Total RP 3", ROLEPLAY, "github", "Total-RP/Total-RP-3", "Character profiles"),
    CatalogItem("Total RP 3: Extended", ROLEPLAY, "github", "Total-RP/Total-RP-3-Extended", "Needs Total RP 3"),
    CatalogItem("Total RP 3: Unit Frames", ROLEPLAY, "wowinterface", "26477", "Needs Total RP 3"),
    CatalogItem("Total RP 3: RP Name in Quest Text", ROLEPLAY, "wowinterface", "26545", "Needs Total RP 3"),
    CatalogItem("MyRolePlay", ROLEPLAY, "wowinterface", "4990", "Character profiles", verified=IN_USE),
    # Cosmetic
    CatalogItem("Masque", COSMETIC, "github", "SFX-WoW/Masque", "Button skinning"),
    CatalogItem("Masque: Caith", COSMETIC, "wowinterface", "8869", "Skin, needs Masque"),
    CatalogItem("Masque: Cirque", COSMETIC, "wowinterface", "24410", "Skin, needs Masque"),
    CatalogItem("Masque: Darion", COSMETIC, "wowinterface", "26992", "Skin, needs Masque"),
    CatalogItem("Narcissus", COSMETIC, "github", "Peterodox/Narcissus", "Character screen and photo mode"),
    CatalogItem("Caerdon Wardrobe", COSMETIC, "wowinterface", "26357", "Shows item appearance status"),
    CatalogItem("Dresser", COSMETIC, "wowinterface", "9381", "Dressing room additions"),
)


def verified_ids() -> set[str]:
    """Manifest ids ("github:owner/name", "wowinterface:123") of verified addons."""
    return {f"{item.source}:{item.repo.lower()}" for item in CATALOG if item.verified}


def by_category(include_untested: bool = False) -> dict[str, list[CatalogItem]]:
    """Groups the catalog by category in CATEGORIES order, keeping list order within each.

    Only verified addons are included unless include_untested is set; empty categories are left out.
    """
    groups: dict[str, list[CatalogItem]] = {}
    for category in CATEGORIES:
        members = [
            item for item in CATALOG
            if item.category == category and (include_untested or item.verified)
        ]
        if members:
            groups[category] = members
    return groups
