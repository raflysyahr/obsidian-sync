"""Main dashboard: status panel, menu with hotkeys, and all user actions."""
import curses
import platform
import shutil
import subprocess
import sys
import threading
import time
from collections import namedtuple
from datetime import datetime
from pathlib import Path

from core.dashboard_status import build_dashboard_snapshot
from core.environment import check_environment
from core.git_engine import GitEngine, GitError
from core.recovery import (RecoveryError, create_backup, list_backups, restore_to_new_directory,
                           security_report, verify_backup)
from core.sync_manager import SyncError, SyncManager
from core.noteio import NoteError
from ui import editor, pages
from ui.components import draw_frame, safe_addstr, style
from ui.logic import (NOT_CONFIGURED, build_status_lines, explain_error, fit_status_lines,
                      format_duration, mask_url, next_step, parse_interval, shorten,
                      validate_remote)

VERSION = "0.6.0"
TICK_MS = 1000          # idle wake-up: auto-sync timer, countdown, flash expiry
REFRESH_SECONDS = 5.0   # how often the Git status is re-read in the background
ERRORS = (GitError, SyncError, RecoveryError, NoteError, ValueError, OSError)

Item = namedtuple("Item", "key label desc")

MENU = [
    Item("V", "Vault folder", "Choose the folder that contains your Obsidian Vault."),
    Item("I", "Initialize Git", "Create a local Git repository inside the Vault (nothing is committed)."),
    Item("R", "GitHub remote", "Set the GitHub repository URL (HTTPS or SSH, no tokens)."),
    Item("S", "Git status", "List new, changed and deleted notes."),
    Item("C", "Commit changes", "Save all Vault changes as a local commit."),
    Item("P", "Push to GitHub", "Upload your commits to GitHub."),
    Item("L", "Pull from GitHub", "Download remote changes (fast-forward only, backup first)."),
    Item("N", "Sync now", "Commit, pull and push in one step."),
    Item("A", "Auto-sync", "Turn scheduled syncing on or off (runs while this app is open)."),
    Item("T", "Auto-sync interval", "How often auto-sync runs (1 minute to 24 hours)."),
    Item("W", "Markdown editor", "Format notes by tapping words and toolbar buttons (touch-first)."),
    Item("B", "Create backup", "Make a verified ZIP backup of the Vault."),
    Item("F", "Verify backup", "Check the newest backup against its checksums."),
    Item("O", "Restore backup", "Restore the newest backup into a NEW folder."),
    Item("U", "Security check", "Look for credentials and risky permissions."),
    Item("E", "Environment", "Show Python, Git and configuration details."),
    Item("H", "Help & About", "Keys, workflow and safety rules."),
    Item("Q", "Quit", "Leave Obsidian Sync."),
]
KEYS = {item.key: index for index, item in enumerate(MENU)}


# --------------------------------------------------------------------------
# background Git status
# --------------------------------------------------------------------------
class StatusRefresher:
    """Reads Git status in a worker thread so a slow Vault never freezes the UI."""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._snapshot = None
        self._taken = 0.0
        self._generation = 1
        self._seen = 0
        self.loaded = False

    def invalidate(self):
        with self._lock:
            self._generation += 1
            self.loaded = False

    def poll(self, settings):
        with self._lock:
            stale = (self._seen != self._generation
                     or time.monotonic() - self._taken >= REFRESH_SECONDS)
            busy = self._thread is not None and self._thread.is_alive()
            if not stale or busy:
                return
            generation = self._generation
        self._thread = threading.Thread(
            target=self._work, args=(dict(settings), generation), daemon=True)
        self._thread.start()

    def _work(self, settings, generation):
        try:
            snapshot = build_dashboard_snapshot(settings)
        except Exception as exc:  # never let the worker die silently
            snapshot = build_dashboard_snapshot({**settings, "vault_path": ""})
            snapshot.update(repository="Unavailable", last_sync_message=str(exc))
        with self._lock:
            self._snapshot = snapshot
            self._taken = time.monotonic()
            self._seen = generation
            self.loaded = generation == self._generation

    def wait_idle(self, timeout=3.0):
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)

    def latest(self):
        with self._lock:
            return (dict(self._snapshot) if self._snapshot else None), self.loaded


class App:
    def __init__(self, stdscr, config):
        self.stdscr = stdscr
        self.config = config
        self.status = StatusRefresher()
        self.selected = 0
        self.moved = False
        self.autoselect = True
        self.mouse = True
        self.flash_text = ""
        self.flash_kind = "ok"
        self.flash_until = 0.0
        self.menu_geometry = (0, 0, 0)  # first visible row y, first index, visible count
        self.next_auto = time.monotonic() + self.interval()

    def interval(self):
        try:
            return max(60, int(self.config.data.get("auto_sync_interval_seconds", 300)))
        except (TypeError, ValueError):
            return 300

    def reset_timer(self):
        self.next_auto = time.monotonic() + self.interval()

    def flash(self, text, kind="ok", seconds=6):
        self.flash_text, self.flash_kind = str(text), kind
        self.flash_until = time.monotonic() + seconds

    def view(self):
        """Merge the (possibly stale) Git snapshot with live settings."""
        cfg = self.config.data
        snapshot, loaded = self.status.latest()
        view = snapshot or build_dashboard_snapshot({**cfg, "vault_path": ""})
        view["vault"] = str(cfg.get("vault_path") or "") or NOT_CONFIGURED
        view["auto_enabled"] = bool(cfg.get("auto_sync_enabled", False))
        view["interval"] = self.interval()
        view["last_sync_at"] = str(cfg.get("last_sync_at", "Never"))
        view["last_sync_status"] = str(cfg.get("last_sync_status", "No sync yet"))
        if cfg.get("last_sync_message"):
            view["last_sync_message"] = str(cfg["last_sync_message"])
        if not loaded and view["vault"] != NOT_CONFIGURED:
            view["repository"] = "Checking…"
        remaining = max(0, int(self.next_auto - time.monotonic()))
        view["next_in"] = format_duration(remaining) if view["auto_enabled"] else ""
        return view


# --------------------------------------------------------------------------
# drawing
# --------------------------------------------------------------------------
def _draw(app, view):
    stdscr = app.stdscr
    h, w = stdscr.getmaxyx()
    draw_frame(stdscr, f"OBSIDIAN SYNC  v{VERSION}",
               "↑/↓ Move   Enter Open   ? Help   Q Quit")
    now = time.monotonic()
    row = 3

    # one guidance row: a flash message wins over the "next step" suggestion
    if app.flash_text and now < app.flash_until:
        icon = {"ok": "✓ ", "err": "✗ ", "warn": "! "}.get(app.flash_kind, "")
        safe_addstr(stdscr, row, 3, shorten(icon + app.flash_text, w - 6, "end"),
                    style(app.flash_kind))
    else:
        step = next_step(view)
        if step:
            safe_addstr(stdscr, row, 3, shorten("▶ " + step[1], w - 6, "end"), style(step[2]))
    row += 1

    # status rows: as many as fit, keeping at least 4 menu rows
    room = (h - 4) - row + 1 - 1 - 4
    for segments in fit_status_lines(build_status_lines(view, w), room):
        x = 3
        for text, name in segments:
            safe_addstr(stdscr, row, x, text, style(name))
            x += len(text)
        row += 1

    # menu
    visible = max(1, (h - 4) - (row + 1) + 1)
    first = min(max(0, app.selected - visible + 1), max(0, len(MENU) - visible))
    header = "─ MENU "
    safe_addstr(stdscr, row, 3, header + "─" * max(0, w - 6 - len(header)), style("dim"))
    if len(MENU) > visible:
        counter = f" {app.selected + 1}/{len(MENU)} "
        safe_addstr(stdscr, row, max(3, w - 3 - len(counter)), counter, style("dim"))
    row += 1
    app.menu_geometry = (row, first, visible)
    for offset, index in enumerate(range(first, min(len(MENU), first + visible))):
        item = MENU[index]
        label = item.label
        if item.key == "A":
            label = "Turn auto-sync " + ("OFF" if view["auto_enabled"] else "ON")
        y = row + offset
        if index == app.selected:
            safe_addstr(stdscr, y, 3, " " * max(0, w - 7), style("sel"))
            safe_addstr(stdscr, y, 4, item.key, style("sel") | curses.A_BOLD)
            safe_addstr(stdscr, y, 7, shorten(label, w - 12, "end"), style("sel"))
        else:
            safe_addstr(stdscr, y, 4, item.key, style("key"))
            safe_addstr(stdscr, y, 7, shorten(label, w - 12, "end"))
    safe_addstr(stdscr, h - 3, 3, shorten(MENU[app.selected].desc, w - 6, "end"), style("dim"))
    stdscr.refresh()


# --------------------------------------------------------------------------
# helpers shared by actions
# --------------------------------------------------------------------------
def _vault(config):
    vault = config.data.get("vault_path", "")
    if not vault:
        raise GitError("Configure the Vault path first.")
    return vault


def _git_engine(config):
    return GitEngine(_vault(config))


def _sync_manager(config):
    if not config.data.get("vault_path", ""):
        raise SyncError("Configure the Vault path first.")
    return SyncManager(config.data["vault_path"], config.data.get("branch", "main"),
                       config.data.get("exclude", []))


def _record_sync(config, status, detail):
    """Persist a short, non-secret summary of the latest sync attempt."""
    config.data["last_sync_at"] = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    config.data["last_sync_status"] = str(status)
    config.data["last_sync_message"] = str(detail).replace("\r", " ").replace("\n", " | ")[:240]
    config.save()


def _fail(app, exc):
    pages.message(app.stdscr, str(exc), "err")


# --------------------------------------------------------------------------
# actions
# --------------------------------------------------------------------------
def configure_vault(app):
    def check(value):
        path = Path(value).expanduser()
        if not path.exists():
            return "That folder does not exist."
        return None if path.is_dir() else "That path is not a folder."

    raw = pages.prompt(app.stdscr, "VAULT FOLDER", "Absolute path to your Obsidian Vault:",
                       initial=app.config.data.get("vault_path", ""), validate=check)
    if raw is None:
        return
    path = Path(raw).expanduser().resolve()
    app.config.set("vault_path", str(path))
    app.flash(f"Vault saved: {path}")


def initialize_git(app):
    try:
        engine = _git_engine(app.config)
        branch = app.config.data.get("branch", "main")
        result = pages.run_task(app.stdscr, "GIT", "Initializing repository…",
                                engine.initialize, branch)
    except ERRORS as exc:
        return _fail(app, exc)
    pages.message(app.stdscr, result, "ok")


def configure_remote(app):
    current = app.view()["remote"]
    initial = "" if current == NOT_CONFIGURED or mask_url(current) != current else current
    url = pages.prompt(app.stdscr, "GITHUB REMOTE",
                       "Paste the repository URL (HTTPS or SSH).\nExample: https://github.com/you/vault.git",
                       initial=initial, validate=validate_remote)
    if url is None:
        return
    try:
        result = _git_engine(app.config).set_remote(url)
        app.config.set("remote_url", url)
    except ERRORS as exc:
        return _fail(app, exc)
    app.flash(result)


def show_git_status(app):
    try:
        status = pages.run_task(app.stdscr, "GIT STATUS", "Reading repository…",
                                _git_engine(app.config).status)
    except ERRORS as exc:
        return _fail(app, exc)
    labels = {"??": ("new", "ok"), "A": ("added", "ok"), "M": ("modified", "warn"),
              "D": ("deleted", "err"), "R": ("renamed", "warn")}
    counts = {}
    rows = []
    for entry in status["changes"]:
        code = entry["code"].strip()
        name, name_style = labels.get(code[:1] if code != "??" else code, ("changed", "text"))
        counts[name] = counts.get(name, 0) + 1
        rows.append((f"{name:<9}{entry['path']}", name_style))
    items = [(f"Branch  {status['branch']}", "head"),
             (f"Remote  {mask_url(status['remote']) or 'not set'}", "text"), ("", "text")]
    if rows:
        summary = ", ".join(f"{count} {name}" for name, count in counts.items())
        items += [(f"{len(rows)} changed: {summary}", "head"), ("", "text")] + rows
    else:
        items.append(("Working tree clean. Nothing to commit.", "ok"))
    pages.show_page(app.stdscr, "GIT STATUS", items)


def commit_changes(app):
    default = f"Update notes {datetime.now():%Y-%m-%d %H:%M}"
    text = pages.prompt(app.stdscr, "COMMIT CHANGES",
                        "Commit message (Enter accepts the suggestion):", initial=default,
                        validate=lambda v: None if v else "Enter a commit message.")
    if text is None:
        return
    try:
        manager = _sync_manager(app.config)
        result = pages.run_task(app.stdscr, "COMMIT", "Saving changes…", manager.commit, text)
    except ERRORS as exc:
        return _fail(app, exc)
    app.flash(result, "ok" if "created" in result else "warn")


def run_sync_action(app, action):
    title = "PUSH" if action == "push" else "PULL"
    if action == "pull" and not pages.confirm(
            app.stdscr, "PULL FROM GITHUB",
            "Download remote changes into your Vault?\n"
            "Only fast-forward updates are applied"
            + (" and a backup is made first." if app.config.data.get("backup_before_pull", True)
               else "."), yes="Pull"):
        return
    try:
        manager = _sync_manager(app.config)
        if action == "push":
            result = pages.run_task(app.stdscr, title, "Uploading to GitHub…", manager.push)
        else:
            result = pages.run_task(app.stdscr, title, "Downloading from GitHub…", manager.pull,
                                    backup=app.config.data.get("backup_before_pull", True))
    except ERRORS as exc:
        _record_sync(app.config, "Error", str(exc))
        return _fail(app, exc)
    _record_sync(app.config, "Success", result)
    if action == "push":
        app.flash(result)
    else:
        pages.message(app.stdscr, result, "ok")


def sync_now(app):
    try:
        manager = _sync_manager(app.config)
        result = pages.run_task(
            app.stdscr, "SYNC NOW", "Committing, pulling and pushing…", manager.auto_sync,
            backup=app.config.data.get("backup_before_pull", True))
    except ERRORS as exc:
        _record_sync(app.config, "Error", str(exc))
        return _fail(app, exc)
    _record_sync(app.config, "Success", result)
    app.reset_timer()
    pages.message(app.stdscr, result, "ok", "Sync complete")


def toggle_auto(app):
    enabled = not bool(app.config.data.get("auto_sync_enabled", False))
    app.config.set("auto_sync_enabled", enabled)
    app.reset_timer()
    if not enabled:
        return app.flash("Auto-sync turned OFF.", "warn")
    if app.view()["remote"] == NOT_CONFIGURED:
        return app.flash("Auto-sync ON, but no GitHub remote is set yet (press R).", "warn")
    app.flash(f"Auto-sync ON: every {format_duration(app.interval())} while this app is open.")


def configure_interval(app):
    seconds = {}

    def check(value):
        parsed, problem = parse_interval(value)
        seconds["value"] = parsed
        return problem

    raw = pages.prompt(app.stdscr, "AUTO-SYNC INTERVAL",
                       "How often should auto-sync run?\nExamples: 300, 5m, 1h (1 minute to 24 hours)",
                       initial=str(app.interval()), validate=check)
    if raw is None:
        return
    app.config.set("auto_sync_interval_seconds", seconds["value"])
    app.reset_timer()
    app.flash(f"Auto-sync interval saved: {format_duration(seconds['value'])}.")


def _backup_vault(app):
    return Path(_vault(app.config)).expanduser().resolve()


def create_vault_backup(app):
    try:
        archive = pages.run_task(app.stdscr, "BACKUP", "Creating and verifying backup…",
                                 create_backup, _backup_vault(app))
    except ERRORS as exc:
        return _fail(app, exc)
    pages.message(app.stdscr, f"Backup created and verified:\n{archive}", "ok")


def verify_latest_backup(app):
    try:
        backups = list_backups(_backup_vault(app))
        if not backups:
            return pages.message(app.stdscr, "No backup found yet. Press B to create one.", "warn")
        info = pages.run_task(app.stdscr, "VERIFY", "Checking backup integrity…",
                              verify_backup, backups[0])
    except ERRORS as exc:
        return _fail(app, exc)
    pages.message(app.stdscr, f"Backup integrity OK\nFiles: {info['files']}\n"
                  f"Created: {info['created_at']}\n{info['archive']}", "ok")


def restore_backup_ui(app):
    try:
        vault = _backup_vault(app)
        backups = list_backups(vault)
        if not backups:
            return pages.message(app.stdscr, "No backup found yet. Press B to create one.", "warn")
        archive = backups[0]
        pages.run_task(app.stdscr, "RESTORE", "Verifying backup first…", verify_backup, archive)
        suggestion = vault.parent / f"{vault.name}-restored-{datetime.now():%Y%m%d}"

        def check(value):
            target = Path(value).expanduser().resolve()
            if target == vault or vault in target.parents:
                return "Choose a folder outside your current Vault."
            return None

        destination = pages.prompt(
            app.stdscr, "RESTORE BACKUP",
            "New empty destination folder (your Vault is never overwritten):",
            initial=str(suggestion), validate=check)
        if destination is None:
            return
        if not pages.confirm(app.stdscr, "RESTORE BACKUP",
                             f"Restore {archive.name}\ninto {destination}?", yes="Restore"):
            return
        restored = pages.run_task(app.stdscr, "RESTORE", "Restoring files…",
                                  restore_to_new_directory, archive, destination)
    except ERRORS as exc:
        return _fail(app, exc)
    pages.message(app.stdscr, f"Restore completed to new folder:\n{restored}", "ok")


def show_security_check(app):
    warnings = security_report(app.config.data.get("vault_path", ""), app.config.path)
    items = [("Security check", "head"), ("", "text")]
    items += [(f"• {warning}", "warn" if "appears" in warning or "accessible" in warning
               else "text") for warning in warnings]
    pages.show_page(app.stdscr, "SECURITY CHECK", items)


def show_environment(app):
    git = shutil.which("git")
    git_version = ""
    if git:
        try:
            git_version = subprocess.run([git, "--version"], text=True, timeout=5,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE
                                         ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    problems = check_environment()
    items = [("Environment", "head"), ("", "text"),
             (f"Python    {sys.version.split()[0]} ({platform.system()})", "text"),
             (f"Git       {git_version or git or 'not found'}", "text" if git else "err"),
             (f"Settings  {app.config.path}", "text"),
             (f"Vault     {app.config.data.get('vault_path') or NOT_CONFIGURED}", "text"), ("", "text")]
    if problems:
        items += [(f"✗ {problem}", "err") for problem in problems]
    else:
        items.append(("✓ Environment looks good.", "ok"))
    pages.show_page(app.stdscr, "ENVIRONMENT", items)


def open_editor(app):
    try:
        vault = _vault(app.config)
        editor.run_editor(app.stdscr, vault)
    except ERRORS as exc:
        return _fail(app, exc)
    finally:
        pages.set_mouse(app.mouse)  # the editor needs touch; restore the user's choice


def show_help(app):
    items = [(f"Obsidian Sync v{VERSION}", "head"),
             ("Keep one Obsidian Vault in sync with GitHub from Termux.", "dim"), ("", "text"),
             ("MOVING AROUND", "head"),
             ("↑/↓ or j/k  move      Enter or →  open", "text"),
             ("Letter  jump to and run that item (the yellow key)", "text"),
             ("Touch  tap an item to select it, tap again to open; swipe to scroll", "text"),
             ("M  turn touch/mouse support on or off", "text"),
             ("Esc  cancel while typing      Q  close / quit", "text"), ("", "text"),
             ("FIRST-TIME WORKFLOW", "head"),
             ("1  V  choose your Vault folder", "text"),
             ("2  I  initialize Git", "text"),
             ("3  R  add your GitHub repository URL", "text"),
             ("4  C  commit, then P push (once, to publish)", "text"),
             ("After that: N syncs everything, or turn on auto-sync with A.", "text"), ("", "text"),
             ("MARKDOWN EDITOR (W)", "head"),
             ("Tap a word to select it. Use the toolbar tabs: Pilih (select), Blok (headings, "
              "lists), Teks (bold, italic), Edit, Alat, File. Selecting words then tapping H2 "
              "turns just those words into a heading.", "text"), ("", "text"),
             ("SAFETY", "head"),
             ("No force-push, reset or rebase. Pull is fast-forward only and "
              "makes a backup first. Tokens are never stored by this app.", "text")]
    pages.show_page(app.stdscr, "HELP & ABOUT", items)


HANDLERS = {
    "V": configure_vault, "I": initialize_git, "R": configure_remote, "S": show_git_status,
    "C": commit_changes, "P": lambda app: run_sync_action(app, "push"),
    "L": lambda app: run_sync_action(app, "pull"), "N": sync_now, "A": toggle_auto,
    "T": configure_interval, "W": open_editor, "B": create_vault_backup, "F": verify_latest_backup,
    "O": restore_backup_ui, "U": show_security_check, "E": show_environment, "H": show_help,
}


# --------------------------------------------------------------------------
# main loop
# --------------------------------------------------------------------------
def _run_auto_sync(app):
    """Scheduled sync: never blocks on a modal page; the result shows on the dashboard."""
    try:
        manager = _sync_manager(app.config)
        result = pages.run_task(
            app.stdscr, "AUTO-SYNC", "Scheduled sync is running…", manager.auto_sync,
            backup=app.config.data.get("backup_before_pull", True))
        _record_sync(app.config, "Success", result)
        app.flash(f"Auto-sync completed at {datetime.now():%H:%M:%S}.")
    except Exception as exc:  # keep the app alive whatever Git/network does
        _record_sync(app.config, "Error", str(exc))
        reason = explain_error(str(exc)) or str(exc)
        app.flash(f"Auto-sync stopped: {reason}", "err", seconds=30)
    app.reset_timer()
    app.status.invalidate()


def _activate(app, index):
    """Run a menu item. Returns True when the app should quit."""
    key = MENU[index].key
    if key == "Q":
        return True
    app.status.wait_idle()
    HANDLERS[key](app)
    app.status.invalidate()
    return False


def _toggle_mouse(app):
    app.mouse = not app.mouse
    pages.set_mouse(app.mouse)
    app.flash("Touch/mouse support " + ("ON" if app.mouse else "OFF (native copy/paste)"))


def _move(app, delta):
    app.selected = (app.selected + delta) % len(MENU)
    app.moved = True


def dashboard(stdscr, config):
    app = App(stdscr, config)
    pages.set_idle_timeout(TICK_MS)
    stdscr.timeout(TICK_MS)
    pages.set_mouse(True)
    drawn = None
    try:
        while True:
            pages.ensure_size(stdscr)
            app.status.poll(config.data)
            view = app.view()
            if app.autoselect and app.status.loaded:
                app.autoselect = False
                step = next_step(view)
                if step and not app.moved:
                    app.selected = KEYS[step[0]]
            h, w = stdscr.getmaxyx()
            flash = app.flash_text if time.monotonic() < app.flash_until else ""
            state = (app.selected, h, w, flash, tuple(sorted(view.items(), key=lambda kv: kv[0])))
            if state != drawn:
                _draw(app, view)
                drawn = state

            # wake up quickly while the first Git status is still loading
            stdscr.timeout(TICK_MS if app.status.loaded else 150)
            kind, value = pages.read_event(stdscr)
            index = None
            if kind == "wheel":
                _move(app, value)
            elif kind == "click":
                y, x = value
                top, first, visible = app.menu_geometry
                if top <= y < top + visible and first + (y - top) < len(MENU):
                    clicked = first + (y - top)
                    if clicked == app.selected:
                        index = clicked
                    else:
                        app.selected, app.moved = clicked, True
            elif kind == "key":
                if value in (curses.KEY_UP, "k"):
                    _move(app, -1)
                elif value in (curses.KEY_DOWN, "j"):
                    _move(app, 1)
                elif value == curses.KEY_HOME:
                    app.selected, app.moved = 0, True
                elif value == curses.KEY_END:
                    app.selected, app.moved = len(MENU) - 1, True
                elif value == curses.KEY_PPAGE:
                    app.selected, app.moved = max(0, app.selected - 5), True
                elif value == curses.KEY_NPAGE:
                    app.selected, app.moved = min(len(MENU) - 1, app.selected + 5), True
                elif value in pages.ENTER_KEYS or value == curses.KEY_RIGHT:
                    index = app.selected
                elif value == "?":
                    index = KEYS["H"]
                elif value in ("m", "M"):
                    _toggle_mouse(app)
                elif isinstance(value, str) and value.upper() in KEYS:
                    index = KEYS[value.upper()]
                    app.selected, app.moved = index, True
            if index is not None and _activate(app, index):
                return
            if app.config.data.get("auto_sync_enabled", False) and time.monotonic() >= app.next_auto:
                _run_auto_sync(app)
            if index is not None:
                drawn = None  # an action ran: repaint from scratch
    finally:
        pages.set_mouse(False)
