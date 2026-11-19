# Satchel

A small addon manager for World of Warcraft: Forever. It has no account, overlay, ads, or background service, and it only runs while its window is open.

## Run it

Use `dist\Satchel.exe`, or run it from source with Python 3.12 or later:

```
python -m satchel
```

Satchel has no third-party runtime dependencies. It looks for `World of Warcraft\_forever_` on each drive. If it can't find your install, use **Change folder…**.

## Add addons

- **Recommended:** pick from addons that have been run on Forever and found working, grouped by category. Click an addon to tick it, or a category to tick everything in it. Tick **Show untested candidates** to also list addons that only declare Forever support; they're for testing (see `probe/README.md`). Satchel doesn't install dependencies, so plugins and skins say which addon they need. One entry is a third-party mirror and is labeled.
- **Add from GitHub:** enter `owner/name` or paste the repository URL. Satchel installs the release's Forever build and can update it later.
- **Add from WoWInterface:** paste an addon page's URL or enter its ID. Satchel can update it later. WoWInterface doesn't tag addons for Forever, so Satchel checks the package's TOC files and asks first if nothing declares support.
- **Import ZIP:** use this for an addon ZIP you downloaded yourself. To update it, import the newer ZIP.

Right-click an addon in the list (or press Shift+F10) for:

- **Open page:** the addon's GitHub or WoWInterface page. Addons from a ZIP and unmanaged folders have no known page. **Recommended** has its own Open page button, which uses the addon you clicked last.
- **Uninstall:** the addon's folders move out of `AddOns` to `_satchel\uninstalled`, so the game stops loading it. It stays on the list as Uninstalled, isn't checked for updates, and its in-game settings are kept.
- **Reinstall:** puts an uninstalled addon's kept folders back. If a folder with the same name has appeared in `AddOns` meanwhile, Satchel asks you to remove that one first. Installing the addon fresh instead replaces the kept copy, which moves to backups.
- **Remove from List:** Satchel stops managing the addon and won't update it. Its files stay in `AddOns`, so it keeps working in game, and it shows as Not managed.
- **Remove Local Files:** the addon's folders (or an uninstalled addon's kept folders) move to Satchel's backups and Satchel stops managing it.

Right-clicking an addon that isn't selected acts on that addon alone; select several first to act on all of them.

**Discover installed** rescans `AddOns`. If a folder Satchel doesn't manage names its own source in its TOC (`X-WoWI-ID`, or a GitHub or WoWInterface link such as `X-Website`), and that source has a release Satchel can install, Satchel offers to manage it. Nothing is downloaded or moved; Satchel starts tracking the folders so it can update them, and offers an update right away if the installed files are older than the latest release. Folders that name no usable source stay Not managed.

Existing folders in `AddOns` appear as **Not managed**. Satchel doesn't change them unless you remove one or install a package that contains the same folder. In either case, the old folder is backed up first.

## Updates

**Check for updates** asks GitHub and WoWInterface for each managed addon's latest release, and **Update all** installs the ones it found.

Tick **Auto-update** to have Satchel do both on its own: right away, and then every hour while its window is open. It installs updates the same way **Update all** does, so an addon that has stopped declaring Forever support is only updated if you already accepted it as Not declared. Results and any failures appear in the status line instead of a dialog. Satchel remembers the setting.

## Profiles

**Profiles** saves your whole addon loadout under a name: a copy of every folder in `AddOns`, managed or not, plus Satchel's records for them. Restoring a profile puts `AddOns` back exactly as it was saved. Addons the profile doesn't have are uninstalled, not deleted: their files are kept and they stay on the list, so you can switch profiles back and forth or reinstall one by itself. Folders the profile replaces with its own copy move to Satchel's backups, and folders that are already identical stay where they are. Profiles live in `_forever_\_satchel\profiles`. Addon settings in `WTF` aren't part of a profile and are never changed.

## How Forever support is decided

The client's Interface value comes from its version, so `1.60.1` becomes `16001`.

For GitHub releases, Satchel looks for a build in this order:

1. A build listed with the `forever` flavor in the release's `release.json`
2. A ZIP with `forever` or `camelot` in its name
3. A single ZIP with no other game flavor in its name, if the release has no `release.json` build list

Before installing from any source, Satchel checks the TOC files in the ZIP. The addon list shows the result in the **Forever** column:

| Forever | Meaning |
| --- | --- |
| Verified | It's on the recommended list: it has been run on Forever and found working |
| Declared | It has a `_Forever` or `_Camelot` TOC, or an Interface value for the current client or newer |
| Older client | It targets an older Forever client |
| Not declared | Nothing in it declares Forever support, so Satchel asks before installing it |

The **Status** column says whether the addon is on disk and current: Installed, Update available, Couldn't check for updates, Uninstalled (its files are kept in `_satchel\uninstalled`), or Files missing (Satchel lists it, but its folders aren't where they should be).

A package with several folders counts as supported if any one of them declares it, so some folders may still show as unsupported in the game's addon list. A status only shows what the addon's author declared. It doesn't prove the addon works in game.

## What it changes

- Replaced and removed addon folders move to `_forever_\_satchel\backups`, and the 10 newest backups are kept.
- Uninstalled addons are kept in `_forever_\_satchel\uninstalled` until you reinstall or remove them; they're never pruned.
- Satchel's list of installed addons is `_forever_\_satchel\manifest.json`.
- Saved profiles are full copies of `AddOns` in `_forever_\_satchel\profiles`. Delete ones you no longer need from the Profiles window.
- Settings are saved in `%LOCALAPPDATA%\Satchel\config.json`: a folder you choose by hand, and whether Auto-update is on. If it detects your folder automatically and you never tick Auto-update, Satchel doesn't save anything there.
- `WTF`, which holds your settings and SavedVariables, is never touched.

If an install or update fails while changing files, Satchel restores the previous folders. ZIPs with unsafe paths or symbolic links are rejected, and so are ZIPs that try to replace Blizzard's addons.

## Limits

- GitHub allows 60 unauthenticated API requests per hour. Each installed GitHub addon uses one when you check for updates. Set `GITHUB_TOKEN` before starting Satchel to raise the limit.
- Satchel doesn't integrate with CurseForge, by design. It exists as an alternative to that kind of manager.

## Develop

```
python -m pip install pytest pyinstaller
python -m pytest
python -m PyInstaller --noconsole --onefile --name Satchel --icon satchel\assets\satchel.ico --add-data "satchel\assets;satchel\assets" satchel\__main__.py
```

## App icon

`satchel\assets\satchel.ico` is original artwork.
