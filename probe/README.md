# Verifying candidates with Satchel Probe

An addon goes on Satchel's recommended list only after it has run on Forever and worked.
Declaring Forever support in its TOC makes it a *candidate*, nothing more.

## Procedure

1. Copy `probe/!SatchelProbe` into `<WoW>\Interface\AddOns\` (once).
2. In Satchel, open Recommended, tick "Show untested candidates", and install a batch.
   Keep batches small (about 5-10) and never mix UI replacements (GW2 UI, RealUI) with
   anything else, so a failure points at one addon.
3. Restart WoW fully (new folders aren't seen by `/reload`), log in, and use each addon's main
   features for a few minutes (see the checklist the batch came with). `/probe` shows a running
   summary in chat.
4. Log out or `/reload` so the game writes the probe's data, then run
   `python probe/report.py`.
5. An addon is verified when the report says PASS **and** its features worked. Set
   `verified="<date>: probe clean, <what was tried>"` on its entry in `satchel/catalog.py`, run
   the tests, and rebuild the exe. Remove addons that fail, or keep them as candidates with a note.

## Hiding errors

The probe records every Lua error but doesn't pass it on, so the game's error window (or an
error addon such as BugSack) stays quiet. It says so once per session in chat. To work
through them, log out or `/reload`, then run:

```
python probe/report.py --errors
```

That lists every distinct error across the saved sessions, grouped by addon with the most
frequent first, with how often it happened, how many of those were in combat, and the addon
stack frames that led to it. Add `--session 1` for the latest session only.

`/probe hide` changes what you see in game, and the setting is saved:

| Command | Lua errors |
| --- | --- |
| `/probe hide all` | Hidden (the default) |
| `/probe hide combat` | Hidden in combat, with a count when combat ends; shown otherwise |
| `/probe hide off` | Shown as normal |

They're recorded in every mode. The game's "blocked an action" messages (an addon calling a
protected function in combat) aren't Lua errors and still appear.

## Reading the report

The report treats folders that don't declare Forever (retail-only modules shipped in the same
package, such as BigWigs' Midnight raids) as expected not to load. Errors it can't tie to an
installed Satchel addon are listed separately: they may come from an addon's library, or from
the game's own UI.
