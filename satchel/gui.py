"""The Satchel desktop window."""

from __future__ import annotations

import ctypes
import json
import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, font, messagebox, simpledialog, ttk

from satchel import discover, wow
from satchel.catalog import CatalogItem, by_category, verified_ids
from satchel.download import Release, entry_id, install_release, page_url
from satchel.github import GitHub, parse_repo
from satchel.profiles import Profile, Profiles
from satchel.store import NotMarkedForForever, Row, SatchelError, Store, import_local_zip
from satchel.wowinterface import WoWInterface, parse_id

CONFIG_PATH = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Satchel" / "config.json"
ICON_PATH = Path(__file__).parent / "assets" / "satchel.ico"

# What the addon's own TOC files declare. Only testing in game makes an addon "Verified".
FOREVER_TEXT = {
    wow.STATUS_OK: "Declared",
    wow.STATUS_OUTDATED: "Older client",
    wow.STATUS_UNKNOWN: "Not declared",
}
SOURCE_TEXT = {
    "github": "GitHub",
    "wowinterface": "WoWInterface",
    "local": "ZIP file",
    "manual": "Not managed",
}
UPDATABLE_SOURCES = ("github", "wowinterface")
AUTO_UPDATE_EVERY_MS = 60 * 60 * 1000
AUTO_UPDATE_RETRY_MS = 60 * 1000  # When another task is running, try again shortly.
AUTO_UPDATE_START_MS = 3 * 1000  # First check after opening, once the window has settled.


def status_text(row: Row, updates: dict, update_errors: dict) -> str:
    """Whether the addon is on disk and current."""
    if row.status == "missing":
        return "Files missing (not on disk)"
    if row.status == "uninstalled":
        return "Uninstalled"
    if row.id in updates:
        return f"Update available: {updates[row.id].tag}"
    if row.id in update_errors:
        return "Couldn't check for updates"
    return "Installed"


def forever_text(row: Row, verified: set[str]) -> str:
    """Whether it works on Forever: tested in game, or only what its TOC files declare."""
    if row.status in ("missing", "uninstalled"):
        return "—"
    if row.managed and row.id in verified:
        return "Verified"
    return FOREVER_TEXT[row.status]


def load_config() -> dict:
    try:
        config = json.loads(CONFIG_PATH.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return config if isinstance(config, dict) else {}


def save_config(**changes) -> None:
    """Saves `changes` into config.json, keeping the settings it doesn't name."""
    config = load_config()
    config.update(changes)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config), "utf-8")


def load_saved_path() -> str | None:
    return load_config().get("forever_dir")


def save_path(path: Path) -> None:
    save_config(forever_dir=str(path))


def find_updates(store: Store, clients: dict) -> tuple[dict[str, Release], dict[str, str]]:
    """Asks each source for the latest release of every installed GitHub or WoWInterface addon."""
    updates, errors = {}, {}
    for entry in store.entries().values():
        if entry.source not in UPDATABLE_SOURCES or entry.uninstalled:
            continue
        try:
            release = clients[entry.source].latest(entry.repo)
        except SatchelError as exc:
            errors[entry.id] = f"{entry.name}: {exc}"
            continue
        if release.tag != entry.version:
            updates[entry.id] = release
    return updates, errors


def apply_updates(store: Store, pending: dict[str, Release]) -> tuple[list[str], dict[str, str]]:
    """Installs each pending release, returning the IDs that finished and the errors for the rest."""
    finished, failures = [], {}
    # Only addons the user already accepted as unmarked may stay unmarked.
    unmarked = {row.id for row in store.rows() if row.status == wow.STATUS_UNKNOWN}
    for addon_id, release in pending.items():
        try:
            install_release(store, release, allow_unverified=addon_id in unmarked)
            finished.append(addon_id)
        except SatchelError as exc:
            failures[addon_id] = str(exc)
    return finished, failures


class App(ttk.Frame):
    def __init__(self, root: tk.Tk, forever_dir: Path | None = None):
        super().__init__(root, padding=12)
        self.root = root
        self.store: Store | None = None
        self.clients = {"github": GitHub(), "wowinterface": WoWInterface()}
        self.updates: dict[str, Release] = {}
        self.update_errors: dict[str, str] = {}
        self.rows: dict[str, Row] = {}
        self.results: queue.Queue = queue.Queue()
        self.busy = False
        self.auto_update = tk.BooleanVar(value=load_config().get("auto_update") is True)
        self._auto_job: str | None = None

        root.title("Satchel")
        root.geometry("1000x560")
        root.minsize(970, 400)  # Wide enough for the whole row of buttons.
        self.pack(fill="both", expand=True)
        self._build()
        self.root.after(100, self._poll)
        self._open_install(forever_dir)
        if self.auto_update.get():
            self._schedule_auto_update(AUTO_UPDATE_START_MS)

    def _build(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x")
        # The button is packed first so that a long path can't push it out of view.
        self.change_button = ttk.Button(header, text="Change folder…", command=self.choose_folder)
        self.change_button.pack(side="right", padx=(8, 0))
        self.install_label = ttk.Label(header, text="Looking for World of Warcraft: Forever…")
        self.install_label.pack(side="left", fill="x", expand=True)

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(12, 8))
        self.recommended_button = ttk.Button(actions, text="Recommended…", command=self.show_recommended)
        self.add_button = ttk.Button(actions, text="Add from GitHub…", command=self.add_github)
        self.wowi_button = ttk.Button(actions, text="Add from WoWInterface…", command=self.add_wowinterface)
        self.zip_button = ttk.Button(actions, text="Import ZIP…", command=self.import_zip)
        self.check_button = ttk.Button(actions, text="Check for updates", command=self.check_updates)
        self.update_button = ttk.Button(actions, text="Update all", command=self.update_all)
        self.profiles_button = ttk.Button(actions, text="Profiles…", command=self.show_profiles)
        self.discover_button = ttk.Button(actions, text="Discover installed", command=self.discover_installed)
        for button in (
            self.profiles_button,
            self.discover_button,
            self.recommended_button,
            self.add_button,
            self.wowi_button,
            self.zip_button,
            self.check_button,
            self.update_button,
        ):
            button.pack(side="left", padx=(0, 6))
        # Always clickable: turning it on or off only changes the schedule, even mid-task.
        self.auto_check = ttk.Checkbutton(
            actions, text="Auto-update", variable=self.auto_update, command=self._toggle_auto_update
        )
        self.auto_check.pack(side="left", padx=(0, 6))

        table = ttk.Frame(self)
        table.pack(fill="both", expand=True)
        columns = ("version", "status", "forever", "source")
        self.tree = ttk.Treeview(table, columns=columns, selectmode="extended")
        self.tree.heading("#0", text="Addon", anchor="w")
        self.tree.heading("version", text="Version", anchor="w")
        self.tree.heading("status", text="Status", anchor="w")
        self.tree.heading("forever", text="Forever", anchor="w")
        self.tree.heading("source", text="Source", anchor="w")
        self.tree.column("#0", width=280, stretch=True)
        self.tree.column("version", width=150, stretch=False)
        self.tree.column("status", width=200, stretch=False)
        self.tree.column("forever", width=110, stretch=False)
        self.tree.column("source", width=110, stretch=False)
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._sync_buttons())

        # Per-addon actions live in a right-click menu (also Shift+F10 or the menu key).
        self.menu = tk.Menu(self.tree, tearoff=False)
        self.menu.add_command(label="Open page", command=self.open_selected_page)
        self.menu.add_separator()
        self.menu.add_command(label="Uninstall", command=self.uninstall_selected)
        self.menu.add_command(label="Reinstall", command=self.reinstall_selected)
        self.menu.add_separator()
        self.menu.add_command(label="Remove from List…", command=self.forget_selected)
        self.menu.add_command(label="Remove Local Files…", command=self.remove_selected)
        self.tree.bind("<Button-3>", self._show_menu)
        self.tree.bind("<Shift-F10>", self._show_menu_for_focus)
        self.tree.bind("<App>", self._show_menu_for_focus)

        self.status_label = ttk.Label(self, text="", wraplength=880, justify="left")
        self.status_label.pack(fill="x", pady=(8, 0))

    # Install selection --------------------------------------------------------

    def _open_install(self, forced: Path | None) -> None:
        candidates: list[Path] = [forced] if forced else []
        saved = load_saved_path()
        if not forced and saved:
            candidates.append(Path(saved))
        if not forced:
            candidates.extend(wow.find_installs())
        for candidate in candidates:
            try:
                self._use_install(candidate)
                return
            except (ValueError, OSError):
                continue
        self.install_label.configure(text="World of Warcraft: Forever was not found.")
        self.status_label.configure(text="Choose your _forever_ folder to get started.")
        self._sync_buttons()

    def _use_install(self, path: Path, remember: bool = False) -> None:
        install = wow.read_install(path)
        self.store = Store(install)
        self.updates.clear()
        self.update_errors.clear()
        self.install_label.configure(
            text=f"Client {install.version}  •  Interface {install.interface}  •  {install.path}"
        )
        if remember:
            save_path(path)
        self.refresh()

    def choose_folder(self) -> None:
        chosen = filedialog.askdirectory(title="Choose your _forever_ folder")
        if not chosen:
            return
        try:
            self._use_install(Path(chosen), remember=True)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Satchel", str(exc))

    # List ---------------------------------------------------------------------

    def refresh(self, message: str | None = None) -> None:
        if self.store is None:
            return
        try:
            rows = self.store.rows()
        except (SatchelError, OSError) as exc:
            messagebox.showerror("Satchel", str(exc))
            return
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        self.rows = {row.id: row for row in rows}
        verified = verified_ids()
        for row in rows:
            self.tree.insert(
                "",
                "end",
                iid=row.id,
                text=row.name,
                values=(row.version or "—", self._status_for(row), forever_text(row, verified),
                        SOURCE_TEXT[row.source]),
            )
        self.tree.selection_set([iid for iid in selected if iid in self.rows])
        if message is None:
            count = sum(_installed(row) for row in rows)
            message = (f"{count} addon{'' if count == 1 else 's'} installed. "
                       "Right-click an addon to open its page, uninstall it or remove it.")
            if not rows:
                message = "No addons yet. Open Recommended to pick some, or add one yourself."
        self.status_label.configure(text=message)
        self._sync_buttons()

    def _status_for(self, row: Row) -> str:
        return status_text(row, self.updates, self.update_errors)

    def _sync_buttons(self) -> None:
        ready = self.store is not None and not self.busy
        has_updatable = any(row.source in UPDATABLE_SOURCES for row in self.rows.values())
        states = {
            self.change_button: not self.busy,
            self.profiles_button: ready,
            self.discover_button: ready,
            self.recommended_button: ready,
            self.add_button: ready,
            self.wowi_button: ready,
            self.zip_button: ready,
            self.check_button: ready and has_updatable,
            self.update_button: ready and bool(self.updates),
        }
        for button, enabled in states.items():
            button.state(["!disabled"] if enabled else ["disabled"])

    def _selected_rows(self) -> list[Row]:
        return [self.rows[iid] for iid in self.tree.selection() if iid in self.rows]

    def _show_menu(self, event) -> str:
        iid = self.tree.identify_row(event.y)
        if not iid:
            return "break"
        # Right-clicking outside the selection acts on that addon alone, as in Explorer.
        if iid not in self.tree.selection():
            self.tree.selection_set(iid)
        self.tree.focus(iid)
        self._popup(event.x_root, event.y_root)
        return "break"

    def _show_menu_for_focus(self, _event) -> str:
        iid = self.tree.focus() or next(iter(self.tree.selection()), "")
        bbox = self.tree.bbox(iid) if iid else None
        if bbox:
            if iid not in self.tree.selection():
                self.tree.selection_set(iid)
            x, y, _width, height = bbox
            self._popup(self.tree.winfo_rootx() + x + 20, self.tree.winfo_rooty() + y + height)
        return "break"

    def _popup(self, x: int, y: int) -> None:
        rows = self._selected_rows()
        ready = self.store is not None and not self.busy
        states = {
            "Open page": self._selected_page() is not None,
            "Uninstall": ready and any(_installed(row) for row in rows),
            "Reinstall": ready and any(row.status == "uninstalled" for row in rows),
            "Remove from List…": ready and any(row.managed and row.status != "uninstalled" for row in rows),
            "Remove Local Files…": ready and any(row.status != "missing" for row in rows),
        }
        for label, enabled in states.items():
            self.menu.entryconfigure(label, state="normal" if enabled else "disabled")
        try:
            self.menu.tk_popup(x, y)
        finally:
            self.menu.grab_release()

    def _selected_page(self) -> str | None:
        selection = self.tree.selection()
        row = self.rows.get(selection[0]) if len(selection) == 1 else None
        return page_url(row.source, row.repo) if row else None

    def open_selected_page(self) -> None:
        url = self._selected_page()
        if url:
            webbrowser.open(url)

    # Background work ----------------------------------------------------------

    def _run(self, label: str, work, done) -> None:
        self.busy = True
        self.status_label.configure(text=label)
        self._sync_buttons()

        def target():
            try:
                self.results.put((done, work(), None))
            except Exception as exc:  # Reported to the user on the UI thread.
                self.results.put((done, None, exc))

        threading.Thread(target=target, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                done, value, error = self.results.get_nowait()
                self.busy = False
                done(value, error)
                self._sync_buttons()
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _show_error(self, error: Exception) -> None:
        self.refresh()
        text = str(error) if isinstance(error, SatchelError) else f"Unexpected error: {error}"
        messagebox.showerror("Satchel", text)

    def _finished_message(self, text: str) -> str:
        if wow.is_game_running():
            return f"{text} The game is running, so type /reload in game to load the change."
        return text

    # Actions ------------------------------------------------------------------

    def add_github(self) -> None:
        text = simpledialog.askstring("Add from GitHub", "GitHub repository (owner/name or URL):", parent=self.root)
        if not text:
            return
        try:
            repo = parse_repo(text)
        except SatchelError as exc:
            messagebox.showerror("Satchel", str(exc))
            return
        self._install_addon("github", repo, allow_unverified=False)

    def add_wowinterface(self) -> None:
        text = simpledialog.askstring(
            "Add from WoWInterface", "WoWInterface addon page URL or ID:", parent=self.root
        )
        if not text:
            return
        try:
            addon_id = parse_id(text)
        except SatchelError as exc:
            messagebox.showerror("Satchel", str(exc))
            return
        self._install_addon("wowinterface", addon_id, allow_unverified=False)

    def show_recommended(self, untested: bool = False) -> None:
        installed = self.store.entries()
        window = tk.Toplevel(self.root)
        window.title("Recommended addons" + (" and untested candidates" if untested else ""))
        window.geometry("760x560")
        window.transient(self.root)
        window.grab_set()

        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="Click an addon to tick it, or click a category to tick everything in it. "
            "Recommended addons have been run on Forever and found working. Untested candidates "
            "only declare Forever support; install them to test, not to rely on.",
            wraplength=730,
        ).pack(fill="x", pady=(0, 4))

        show_untested = tk.BooleanVar(value=untested)

        def reopen() -> None:
            window.destroy()
            self.show_recommended(untested=show_untested.get())

        ttk.Checkbutton(
            frame, text="Show untested candidates (for testing)", variable=show_untested, command=reopen
        ).pack(anchor="w", pady=(0, 8))

        buttons = ttk.Frame(frame)
        buttons.pack(side="bottom", fill="x", pady=(8, 0))
        table = ttk.Frame(frame)
        table.pack(fill="both", expand=True)
        tree = ttk.Treeview(table, columns=("source", "note"), selectmode="browse")
        tree.heading("#0", text="Addon", anchor="w")
        tree.heading("source", text="Source", anchor="w")
        tree.heading("note", text="Note", anchor="w")
        tree.column("#0", width=300, stretch=False)
        tree.column("source", width=110, stretch=False)
        tree.column("note", width=310, stretch=True)
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        bold = font.nametofont("TkDefaultFont").copy()
        bold.configure(weight="bold")
        window.bold_font = bold  # Tk drops the font if nothing else references it.
        tree.tag_configure("category", font=bold)

        items: dict[str, CatalogItem] = {}
        groups: dict[str, list[str]] = {}
        for category, members in by_category(include_untested=untested).items():
            node = f"category:{category}"
            tree.insert("", "end", iid=node, open=True, tags=("category",))
            groups[node] = []
            for item in members:
                iid = entry_id(item.source, item.repo)
                items[iid] = item
                groups[node].append(iid)
                note = "Already installed" if iid in installed else item.note
                if not item.verified:
                    note = f"Untested · {note}" if note else "Untested"
                tree.insert(node, "end", iid=iid, values=(SOURCE_TEXT[item.source], note))

        ticked: set[str] = set()

        def install() -> None:
            chosen = [item for iid, item in items.items() if iid in ticked]
            window.destroy()
            if chosen:
                self._install_items(chosen)

        install_button = ttk.Button(buttons, command=install)
        install_button.pack(side="right")
        ttk.Button(buttons, text="Close", command=window.destroy).pack(side="right", padx=(0, 6))

        def highlighted_page() -> str | None:
            selection = tree.selection()
            item = items.get(selection[0]) if selection else None
            return page_url(item.source, item.repo) if item else None

        def open_page() -> None:
            url = highlighted_page()
            if url:
                webbrowser.open(url)

        page_button = ttk.Button(buttons, text="Open page", command=open_page)
        page_button.pack(side="left")

        def render(_event=None) -> None:
            for node, members in groups.items():
                available = [iid for iid in members if iid not in installed]
                count = sum(iid in ticked for iid in available)
                name = node.removeprefix("category:")
                if not available:
                    tree.item(node, text=f"✓  {name}  (all installed)")
                else:
                    mark = "☑" if count == len(available) else "▣" if count else "☐"
                    tree.item(node, text=f"{mark}  {name}  ({count}/{len(available)})")
                for iid in members:
                    mark = "✓" if iid in installed else "☑" if iid in ticked else "☐"
                    tree.item(iid, text=f"{mark}  {items[iid].name}")
            install_button.configure(text=f"Install selected ({len(ticked)})")
            install_button.state(["!disabled"] if ticked else ["disabled"])
            page_button.state(["!disabled"] if highlighted_page() else ["disabled"])

        def toggle(iid: str) -> None:
            if iid in groups:
                available = {member for member in groups[iid] if member not in installed}
                if available <= ticked:
                    ticked.difference_update(available)
                else:
                    ticked.update(available)
            elif iid in items and iid not in installed:
                ticked.symmetric_difference_update({iid})
            render()

        def on_click(event) -> None:
            iid = tree.identify_row(event.y)
            # The arrow next to a category collapses it without changing what's ticked.
            if iid and "indicator" not in tree.identify_element(event.x, event.y):
                toggle(iid)

        def on_space(_event) -> None:
            if tree.focus():
                toggle(tree.focus())

        tree.bind("<ButtonRelease-1>", on_click)
        tree.bind("<space>", on_space)
        tree.bind("<<TreeviewSelect>>", render)
        render()

    # Discover -------------------------------------------------------------------

    def discover_installed(self) -> None:
        store, clients = self.store, self.clients

        def done(result: discover.Scan, error):
            if error:
                self._show_error(error)
                return
            self.refresh()
            total = len(self.rows)
            rescanned = f"Rescanned AddOns: {total} addon{'' if total == 1 else 's'}."
            if not result.found:
                detail = (" No unmanaged addon names a GitHub or WoWInterface release Satchel can use."
                          if result.unmatched else " Satchel already manages everything in AddOns.")
                self.refresh(rescanned + detail)
                return
            lines = []
            for found in result.found[:12]:
                where = SOURCE_TEXT[found.release.source]
                note = "" if found.up_to_date else f"; update available: {found.release.tag}"
                lines.append(f"• {found.name} ({', '.join(found.folders)}) from {where}{note}")
            if len(result.found) > 12:
                lines.append(f"…and {len(result.found) - 12} more")
            prompt = (
                f"Found {len(result.found)} installed addon{'' if len(result.found) == 1 else 's'} "
                f"that Satchel can manage:\n\n" + "\n".join(lines) + "\n\n"
                "Manage them? Nothing is downloaded or moved; Satchel starts tracking them so it can update them."
            )
            if not messagebox.askyesno("Satchel", prompt):
                self.refresh(rescanned)
                return
            adopted, failures = [], []
            for found in result.found:
                try:
                    discover.adopt(store, found)
                    adopted.append(found.name)
                    if not found.up_to_date:
                        self.updates[found.id] = found.release
                except (SatchelError, OSError) as exc:
                    failures.append(f"{found.name}: {exc}")
            message = f"Satchel now manages {len(adopted)} more addon{'' if len(adopted) == 1 else 's'}."
            if failures:
                message += " Not added: " + "; ".join(failures)
            self.refresh(message)

        self._run("Looking through AddOns…", lambda: discover.scan(store, clients), done)

    # Profiles -------------------------------------------------------------------

    def show_profiles(self) -> None:
        profiles = Profiles(self.store)
        window = tk.Toplevel(self.root)
        window.title("Profiles")
        window.geometry("620x380")
        window.transient(self.root)
        window.grab_set()

        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="A profile is a saved copy of your whole addon loadout. Restoring one puts your "
            "addons back exactly as they were saved; anything else goes to Satchel's backups. "
            "Addon settings are never changed.",
            wraplength=590,
        ).pack(fill="x", pady=(0, 8))

        buttons = ttk.Frame(frame)
        buttons.pack(side="bottom", fill="x", pady=(8, 0))
        tree = ttk.Treeview(frame, columns=("addons", "saved"), selectmode="browse", height=10)
        tree.heading("#0", text="Profile", anchor="w")
        tree.heading("addons", text="Addons", anchor="w")
        tree.heading("saved", text="Saved", anchor="w")
        tree.column("#0", width=300, stretch=True)
        tree.column("addons", width=80, stretch=False)
        tree.column("saved", width=170, stretch=False)
        tree.pack(fill="both", expand=True)

        listed: dict[str, Profile] = {}

        def load() -> None:
            tree.delete(*tree.get_children())
            listed.clear()
            for profile in profiles.list():
                iid = str(profile.path)
                listed[iid] = profile
                saved = time.strftime("%Y-%m-%d %H:%M", time.localtime(profile.created))
                tree.insert("", "end", iid=iid, text=profile.name, values=(profile.addon_count, saved))
            sync()

        def selected() -> Profile | None:
            selection = tree.selection()
            return listed.get(selection[0]) if selection else None

        def sync(_event=None) -> None:
            state = ["!disabled"] if selected() else ["disabled"]
            restore_button.state(state)
            delete_button.state(state)

        def save() -> None:
            name = simpledialog.askstring("Save profile", "Name for your current addon loadout:", parent=window)
            if not name or not name.strip():
                return
            if any(p.name.lower() == " ".join(name.split()).lower() for p in listed.values()):
                if not messagebox.askyesno("Satchel", f'Replace the profile "{name.strip()}"?', parent=window):
                    return
            window.destroy()
            self._save_profile(name)

        def restore() -> None:
            profile = selected()
            if profile is None:
                return
            try:
                plan = profiles.plan(profile)
            except (SatchelError, OSError) as exc:
                messagebox.showerror("Satchel", str(exc), parent=window)
                return
            if not plan.changes:
                messagebox.showinfo("Satchel", f'Your addons already match "{profile.name}".', parent=window)
                return
            message = (
                f'Restore "{profile.name}"?\n\n'
                f"{len(plan.add)} folder(s) will be copied in from the profile and {len(plan.keep)} stay as they are.\n"
                f"{len(plan.uninstall)} folder(s) the profile doesn't have will be uninstalled: their files are "
                "kept, and you can reinstall them from the right-click menu.\n"
                f"{len(plan.remove)} folder(s) the profile replaces with its own copy will move to Satchel's backups."
            )
            if messagebox.askyesno("Satchel", message, parent=window):
                window.destroy()
                self._restore_profile(profiles, profile)

        def delete() -> None:
            profile = selected()
            if profile and messagebox.askyesno("Satchel", f'Delete the profile "{profile.name}"?', parent=window):
                try:
                    profiles.delete(profile)
                except (SatchelError, OSError) as exc:
                    messagebox.showerror("Satchel", str(exc), parent=window)
                load()

        ttk.Button(buttons, text="Close", command=window.destroy).pack(side="right")
        delete_button = ttk.Button(buttons, text="Delete", command=delete)
        delete_button.pack(side="right", padx=(0, 6))
        restore_button = ttk.Button(buttons, text="Restore", command=restore)
        restore_button.pack(side="right", padx=(0, 6))
        ttk.Button(buttons, text="Save current as…", command=save).pack(side="left")
        tree.bind("<<TreeviewSelect>>", sync)
        load()

    def _save_profile(self, name: str) -> None:
        profiles = Profiles(self.store)

        def done(profile, error):
            if error:
                self._show_error(error)
            else:
                self.refresh(f'Saved profile "{profile.name}" with {profile.addon_count} addons.')

        self._run(f'Saving profile "{name.strip()}"…', lambda: profiles.save(name), done)

    def _restore_profile(self, profiles: Profiles, profile: Profile) -> None:
        def done(plan, error):
            if error:
                self._show_error(error)
                return
            text = (f'Restored "{profile.name}": {len(plan.add)} folder(s) copied in, '
                    f"{len(plan.uninstall)} uninstalled (files kept), {len(plan.remove)} replaced.")
            if wow.is_game_running():
                text += " Restart the game to load them (/reload doesn't pick up added or removed folders)."
            self.refresh(text)

        self._run(f'Restoring profile "{profile.name}"…', lambda: profiles.restore(profile), done)

    def _install_items(self, items: list[CatalogItem]) -> None:
        store, clients = self.store, self.clients

        def work():
            finished, failures = [], []
            for item in items:
                try:
                    install_release(store, clients[item.source].latest(item.repo))
                    finished.append(item.name)
                except SatchelError as exc:
                    failures.append(f"{item.name}: {exc}")
            return finished, failures

        def done(result, error):
            if error:
                self._show_error(error)
                return
            finished, failures = result
            message = f"Installed {len(finished)} addon{'' if len(finished) == 1 else 's'}."
            if failures:
                message += " Failed: " + "; ".join(failures)
            self.refresh(self._finished_message(message))

        self._run(f"Installing {len(items)} addon{'' if len(items) == 1 else 's'}…", work, done)

    def _install_addon(self, source: str, repo: str, allow_unverified: bool) -> None:
        store, client = self.store, self.clients[source]

        def work():
            return install_release(store, client.latest(repo), allow_unverified)

        def done(entry, error):
            if isinstance(error, NotMarkedForForever):
                self.refresh()
                if messagebox.askyesno("Satchel", f"{error}\n\nInstall it anyway?"):
                    self._install_addon(source, repo, allow_unverified=True)
            elif error:
                self._show_error(error)
            else:
                self.refresh(self._finished_message(f"Installed {entry.name} {entry.version}."))

        self._run(f"Installing {SOURCE_TEXT[source]} addon {repo}…", work, done)

    def import_zip(self) -> None:
        chosen = filedialog.askopenfilename(title="Choose an addon ZIP", filetypes=[("ZIP files", "*.zip")])
        if chosen:
            self._import(Path(chosen), allow_unverified=False)

    def _import(self, path: Path, allow_unverified: bool) -> None:
        store = self.store

        def done(entry, error):
            if isinstance(error, NotMarkedForForever):
                self.refresh()
                if messagebox.askyesno("Satchel", f"{error}\n\nInstall it anyway?"):
                    self._import(path, allow_unverified=True)
            elif error:
                self._show_error(error)
            else:
                self.refresh(self._finished_message(f"Installed {entry.name}."))

        self._run(f"Importing {path.name}…", lambda: import_local_zip(store, path, allow_unverified), done)

    def check_updates(self) -> None:
        store, clients = self.store, self.clients

        def work():
            return find_updates(store, clients)

        def done(result, error):
            if error:
                self._show_error(error)
                return
            self.updates, self.update_errors = result
            message = f"{len(self.updates)} update{'' if len(self.updates) == 1 else 's'} available."
            if self.update_errors:
                names = "; ".join(self.update_errors.values())
                message += f" Some checks failed: {names}"
            self.refresh(message)

        self._run("Checking for updates…", work, done)

    def update_all(self) -> None:
        store = self.store
        pending = dict(self.updates)

        def work():
            return apply_updates(store, pending)

        def done(result, error):
            if error:
                self._show_error(error)
                return
            finished, failures = result
            for addon_id in finished:
                self.updates.pop(addon_id, None)
            message = f"Updated {len(finished)} addon{'' if len(finished) == 1 else 's'}."
            if failures:
                message += " Failed: " + "; ".join(failures.values())
            self.refresh(self._finished_message(message))

        self._run("Updating addons…", work, done)

    # Auto-update ----------------------------------------------------------------

    def _toggle_auto_update(self) -> None:
        enabled = self.auto_update.get()
        try:
            save_config(auto_update=enabled)
        except OSError as exc:
            messagebox.showerror("Satchel", f"Couldn't save the auto-update setting: {exc}")
        if enabled:
            self._schedule_auto_update(0)
        else:
            self._cancel_auto_update()
            self.status_label.configure(text="Auto-update is off.")

    def _cancel_auto_update(self) -> None:
        if self._auto_job is not None:
            self.root.after_cancel(self._auto_job)
            self._auto_job = None

    def _schedule_auto_update(self, delay_ms: int) -> None:
        self._cancel_auto_update()
        self._auto_job = self.root.after(delay_ms, self._auto_update)

    def _auto_update(self) -> None:
        """Checks for updates and installs them, then schedules the next check in an hour."""
        self._auto_job = None
        if not self.auto_update.get():
            return
        if self.store is None:
            self._schedule_auto_update(AUTO_UPDATE_EVERY_MS)
            return
        if self.busy:
            self._schedule_auto_update(AUTO_UPDATE_RETRY_MS)
            return
        store, clients = self.store, self.clients

        def work():
            updates, errors = find_updates(store, clients)
            finished, failures = apply_updates(store, updates)
            return updates, errors, finished, failures

        def done(result, error):
            # Unattended, so problems go in the status line rather than a dialog.
            if self.auto_update.get():
                self._schedule_auto_update(AUTO_UPDATE_EVERY_MS)
            stamp = time.strftime("%H:%M")
            next_check = time.strftime("%H:%M", time.localtime(time.time() + AUTO_UPDATE_EVERY_MS / 1000))
            if error:
                text = str(error) if isinstance(error, SatchelError) else f"Unexpected error: {error}"
                self.refresh(f"Auto-update at {stamp} failed: {text} Next try at {next_check}.")
                return
            updates, errors, finished, failures = result
            self.updates = {addon_id: release for addon_id, release in updates.items() if addon_id not in finished}
            self.update_errors = errors
            if finished:
                message = f"Auto-update at {stamp}: updated {len(finished)} addon{'' if len(finished) == 1 else 's'}."
                message = self._finished_message(message)
            else:
                message = f"Auto-update at {stamp}: everything is up to date."
            if failures:
                message += " Failed: " + "; ".join(failures.values())
            if errors:
                message += " Some checks failed: " + "; ".join(errors.values())
            self.refresh(f"{message} Next check at {next_check}.")

        self._run("Auto-update: checking for updates…", work, done)

    def uninstall_selected(self) -> None:
        rows = [row for row in self._selected_rows() if _installed(row)]
        if rows:
            self._move_rows(rows, "Uninstalling", self.store.uninstall,
                            "Uninstalled {n}; the files are kept, so you can reinstall from the right-click menu.")

    def reinstall_selected(self) -> None:
        rows = [row for row in self._selected_rows() if row.status == "uninstalled"]
        if rows:
            self._move_rows(rows, "Reinstalling", self.store.reinstall, "Reinstalled {n}.")

    def _move_rows(self, rows: list[Row], verb: str, action, finished: str) -> None:
        """Runs `action` on each row in the background, reporting any that fail."""

        def work():
            failures = []
            for row in rows:
                try:
                    action(row)
                except (SatchelError, OSError) as exc:
                    failures.append(str(exc))
            return failures

        def done(failures, error):
            for row in rows:
                self.updates.pop(row.id, None)
                self.update_errors.pop(row.id, None)
            if error:
                self._show_error(error)
                return
            n = len(rows) - len(failures)
            message = finished.format(n=f"{n} addon{'' if n == 1 else 's'}")
            if failures:
                message += " Failed: " + " ".join(failures)
            self.refresh(self._finished_message(message))

        self._run(f"{verb} {len(rows)} addon{'' if len(rows) == 1 else 's'}…", work, done)

    def forget_selected(self) -> None:
        rows = [row for row in self._selected_rows() if row.managed and row.status != "uninstalled"]
        if not rows:
            return
        prompt = (
            f"Remove these addons from Satchel's list?\n\n{_name_list(rows)}\n\n"
            "Their files stay in AddOns, so they keep working in game. They'll show as "
            "Not managed, and Satchel won't update them."
        )
        if not messagebox.askyesno("Satchel", prompt):
            return
        store = self.store
        try:
            for row in rows:
                store.forget(row)
                self.updates.pop(row.id, None)
                self.update_errors.pop(row.id, None)
        except (SatchelError, OSError) as exc:
            self._show_error(exc)
            return
        self.refresh(f"Removed {len(rows)} addon{'' if len(rows) == 1 else 's'} from the list; the files are still in AddOns.")

    def remove_selected(self) -> None:
        rows = [row for row in self._selected_rows() if row.status != "missing"]
        if not rows:
            return
        prompt = (
            f"Remove these addons' files?\n\n{_name_list(rows)}\n\n"
            "Their folders move to Satchel's backups, and your in-game settings are kept."
        )
        if not messagebox.askyesno("Satchel", prompt):
            return
        store = self.store

        def done(_value, error):
            for row in rows:
                self.updates.pop(row.id, None)
                self.update_errors.pop(row.id, None)
            if error:
                self._show_error(error)
            else:
                self.refresh(self._finished_message(f"Removed {len(rows)} addon{'' if len(rows) == 1 else 's'}."))

        def work():
            for row in rows:
                store.remove(row)

        self._run("Removing addons…", work, done)


def _installed(row: Row) -> bool:
    return row.status not in ("missing", "uninstalled")


def _name_list(rows: list[Row], limit: int = 12) -> str:
    names = "\n".join(f"• {row.name}" for row in rows[:limit])
    if len(rows) > limit:
        names += f"\n…and {len(rows) - limit} more"
    return names


def apply_icon(root: tk.Tk) -> None:
    """Sets the icon for every window. A missing icon shouldn't stop the app."""
    try:
        root.iconbitmap(default=str(ICON_PATH))
    except tk.TclError:
        pass


def main(forever_dir: Path | None = None) -> None:
    # Without its own ID, Windows groups the app under Python's taskbar icon when run from source.
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Satchel.AddonManager")
    root = tk.Tk()
    apply_icon(root)
    root.report_callback_exception = lambda _type, value, _trace: messagebox.showerror(
        "Satchel", f"Unexpected error: {value}"
    )
    App(root, forever_dir)
    root.mainloop()
