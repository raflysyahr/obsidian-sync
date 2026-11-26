import curses
import time
from pathlib import Path
from ui.components import safe_addstr, draw_frame
from core.dashboard_status import build_dashboard_snapshot

MENU = ["Configure Vault path", "Initialize Git repository", "Git status",
        "Configure GitHub remote", "Commit local changes", "Push to GitHub",
        "Pull from GitHub (safe)", "Toggle auto-sync", "Set auto-sync interval",
        "Create Vault backup", "Verify latest backup", "Restore backup to new folder",
        "Security check", "Environment check", "About", "Quit"]

def dashboard(stdscr, config):
    selected = 0
    next_auto_sync = time.monotonic() + max(60, int(config.data.get("auto_sync_interval_seconds", 300)))
    stdscr.timeout(1000)
    while True:
        draw_frame(stdscr, "OBSIDIAN SYNC  v0.6.0")
        h, w = stdscr.getmaxyx()
        snapshot = build_dashboard_snapshot(config.data)
        interval = snapshot["interval"]
        safe_addstr(stdscr, 3, 3, "VAULT", curses.A_BOLD)
        safe_addstr(stdscr, 4, 3, snapshot["vault"])
        safe_addstr(stdscr, 6, 3,
                    f"Repository: {snapshot['repository']}   Branch: {snapshot['branch']}")
        safe_addstr(stdscr, 7, 3, f"Remote: {snapshot['remote']}")
        change_label = "unknown" if snapshot["changes"] is None else str(snapshot["changes"])
        safe_addstr(stdscr, 8, 3, f"Local changes: {change_label}")
        auto_label = f"ON / every {snapshot['interval']}s" if snapshot["auto_enabled"] else "OFF"
        safe_addstr(stdscr, 9, 3, f"Auto-sync: {auto_label}")
        safe_addstr(stdscr, 10, 3,
                    f"Last sync: {snapshot['last_sync_at']} [{snapshot['last_sync_status']}]")
        if snapshot["last_sync_message"]:
            safe_addstr(stdscr, 11, 3, snapshot["last_sync_message"].replace("\n", " | "))
        safe_addstr(stdscr, 13, 3, "MAIN MENU", curses.A_BOLD)
        # Keep the menu usable on small Android terminal sizes.
        visible_rows = max(1, h - 16)
        start = min(max(0, selected - visible_rows + 1), max(0, len(MENU) - visible_rows))
        for row, menu_index in enumerate(range(start, min(len(MENU), start + visible_rows))):
            attr = curses.A_REVERSE if menu_index == selected else curses.A_NORMAL
            safe_addstr(stdscr, 14 + row, 5, MENU[menu_index], attr)
        stdscr.refresh()
        key = stdscr.getch()
        if snapshot["auto_enabled"] and time.monotonic() >= next_auto_sync:
            try:
                result = _sync_manager(config).auto_sync(
                    backup=config.data.get("backup_before_pull", True)
                )
                _record_sync(config, "Success", result)
                message(stdscr, "AUTO-SYNC COMPLETED\n" + result)
            except Exception as exc:
                _record_sync(config, "Error", str(exc))
                message(stdscr, f"AUTO-SYNC STOPPED\n{exc}")
            next_auto_sync = time.monotonic() + snapshot["interval"]
        if key in (curses.KEY_UP, ord("k")):
            selected = (selected - 1) % len(MENU)
        elif key in (curses.KEY_DOWN, ord("j")):
            selected = (selected + 1) % len(MENU)
        elif key in (10, 13, curses.KEY_ENTER):
            if selected == 0:
                configure_vault(stdscr, config)
            elif selected == 1:
                initialize_git(stdscr, config)
            elif selected == 2:
                show_git_status(stdscr, config)
            elif selected == 3:
                configure_remote(stdscr, config)
            elif selected == 4:
                commit_changes(stdscr, config)
            elif selected == 5:
                run_sync_action(stdscr, config, "push")
            elif selected == 6:
                run_sync_action(stdscr, config, "pull")
            elif selected == 7:
                enabled = not bool(config.data.get("auto_sync_enabled", False))
                config.set("auto_sync_enabled", enabled)
                next_auto_sync = time.monotonic() + interval
                message(stdscr, "Auto-sync " + ("enabled." if enabled else "disabled."))
            elif selected == 8:
                configure_auto_interval(stdscr, config)
                interval = max(60, int(config.data.get("auto_sync_interval_seconds", 300)))
                next_auto_sync = time.monotonic() + interval
            elif selected == 9:
                create_vault_backup(stdscr, config)
            elif selected == 10:
                verify_latest_backup(stdscr, config)
            elif selected == 11:
                restore_backup_ui(stdscr, config)
            elif selected == 12:
                show_security_check(stdscr, config)
            elif selected == 13:
                show_environment(stdscr)
            elif selected == 14:
                message(stdscr, "Obsidian Sync v0.6.0 - security and recovery tools.")
            else:
                return
        elif key in (ord("q"), ord("Q")):
            return

def configure_vault(stdscr, config):
    curses.echo()
    curses.curs_set(1)
    draw_frame(stdscr, "CONFIGURE VAULT")
    safe_addstr(stdscr, 4, 3, "Absolute path to your Obsidian Vault:")
    safe_addstr(stdscr, 6, 3, "> ")
    stdscr.refresh()
    try:
        raw = stdscr.getstr(6, 5, 240).decode("utf-8", "replace").strip()
    finally:
        curses.noecho()
        curses.curs_set(0)
    if not raw:
        message(stdscr, "No path entered. Configuration unchanged.")
        return
    path = Path(raw).expanduser()
    if not path.exists() or not path.is_dir():
        message(stdscr, "That folder does not exist or is not a directory.")
        return
    config.set("vault_path", str(path.resolve()))
    message(stdscr, f"Vault path saved: {path.resolve()}")

def show_environment(stdscr):
    import shutil, sys
    git = shutil.which("git") or "not found"
    message(stdscr, f"Python: {sys.version.split()[0]} | Git: {git}")

def message(stdscr, text):
    draw_frame(stdscr, "NOTICE")
    safe_addstr(stdscr, 4, 3, text)
    safe_addstr(stdscr, 7, 3, "Press any key to return...")
    stdscr.refresh()
    stdscr.timeout(-1)
    stdscr.getch()
    stdscr.timeout(1000)


def _git_engine(config):
    from core.git_engine import GitEngine, GitError
    vault = config.data.get("vault_path", "")
    if not vault:
        raise GitError("Configure the Vault path first.")
    return GitEngine(vault)


def initialize_git(stdscr, config):
    from core.git_engine import GitError
    try:
        result = _git_engine(config).initialize(config.data.get("branch", "main"))
        message(stdscr, result)
    except GitError as exc:
        message(stdscr, f"Git error: {exc}")


def show_git_status(stdscr, config):
    from core.git_engine import GitError
    try:
        status = _git_engine(config).status()
    except GitError as exc:
        message(stdscr, f"Git error: {exc}")
        return
    draw_frame(stdscr, "GIT STATUS")
    safe_addstr(stdscr, 3, 3, f"Branch: {status['branch']}")
    safe_addstr(stdscr, 4, 3, f"Remote: {status['remote'] or '(not configured)'}")
    safe_addstr(stdscr, 6, 3, f"Changed/untracked files: {len(status['changes'])}", curses.A_BOLD)
    for index, item in enumerate(status["changes"][:max(0, curses.LINES - 11)]):
        safe_addstr(stdscr, 8 + index, 3, f"{item['code']} {item['path']}")
    if not status["changes"]:
        safe_addstr(stdscr, 8, 3, "Working tree clean")
    safe_addstr(stdscr, curses.LINES - 2, 3, "Press any key to return")
    stdscr.refresh()
    stdscr.getch()


def configure_remote(stdscr, config):
    from core.git_engine import GitError
    curses.echo()
    curses.curs_set(1)
    draw_frame(stdscr, "CONFIGURE GITHUB REMOTE")
    safe_addstr(stdscr, 3, 3, "Paste repository URL (HTTPS or SSH):")
    safe_addstr(stdscr, 5, 3, "> ")
    stdscr.refresh()
    try:
        url = stdscr.getstr(5, 5, 240).decode("utf-8", "replace").strip()
    finally:
        curses.noecho()
        curses.curs_set(0)
    if not url:
        message(stdscr, "No URL entered. Configuration unchanged.")
        return
    try:
        result = _git_engine(config).set_remote(url)
        config.set("remote_url", url)
        message(stdscr, result)
    except GitError as exc:
        message(stdscr, f"Git error: {exc}")



def _sync_manager(config):
    from core.sync_manager import SyncManager, SyncError
    vault = config.data.get("vault_path", "")
    if not vault:
        raise SyncError("Configure the Vault path first.")
    return SyncManager(
        vault,
        config.data.get("branch", "main"),
        config.data.get("exclude", []),
    )


def _read_line(stdscr, title, prompt, max_bytes=200):
    curses.echo()
    curses.curs_set(1)
    draw_frame(stdscr, title)
    safe_addstr(stdscr, 4, 3, prompt)
    safe_addstr(stdscr, 6, 3, "> ")
    stdscr.refresh()
    try:
        return stdscr.getstr(6, 5, max_bytes).decode("utf-8", "replace").strip()
    finally:
        curses.noecho()
        curses.curs_set(0)


def commit_changes(stdscr, config):
    from core.sync_manager import SyncError
    message_text = _read_line(stdscr, "COMMIT LOCAL CHANGES", "Commit message (one line):")
    if not message_text:
        message(stdscr, "Commit cancelled; no message entered.")
        return
    try:
        result = _sync_manager(config).commit(message_text)
        message(stdscr, result)
    except SyncError as exc:
        message(stdscr, f"Sync error: {exc}")


def run_sync_action(stdscr, config, action):
    from core.sync_manager import SyncError
    try:
        manager = _sync_manager(config)
        result = manager.push() if action == "push" else manager.pull(
            backup=config.data.get("backup_before_pull", True)
        )
        _record_sync(config, "Success", result)
        message(stdscr, result)
    except SyncError as exc:
        _record_sync(config, "Error", str(exc))
        message(stdscr, f"Sync error: {exc}")



def _record_sync(config, status, detail):
    """Persist a short, non-secret summary of the latest sync attempt."""
    from datetime import datetime
    config.set("last_sync_at", datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"))
    config.set("last_sync_status", str(status))
    # Avoid filling the dashboard with long Git diagnostics.
    config.set("last_sync_message", str(detail).replace("\r", " ").replace("\n", " | ")[:240])


def configure_auto_interval(stdscr, config):
    raw = _read_line(
        stdscr, "AUTO-SYNC INTERVAL",
        "Interval in seconds (minimum 60, maximum 86400):",
        max_bytes=8,
    )
    if not raw:
        message(stdscr, "Interval unchanged.")
        return
    try:
        seconds = int(raw)
    except ValueError:
        message(stdscr, "Enter a whole number of seconds.")
        return
    if not 60 <= seconds <= 86400:
        message(stdscr, "Interval must be between 60 and 86400 seconds.")
        return
    config.set("auto_sync_interval_seconds", seconds)
    message(stdscr, f"Auto-sync interval saved: {seconds} seconds.")



def _recovery_vault(config):
    vault = config.data.get("vault_path", "")
    if not vault:
        raise ValueError("Configure the Vault path first.")
    return Path(vault).expanduser().resolve()


def create_vault_backup(stdscr, config):
    from core.recovery import create_backup, RecoveryError
    try:
        archive = create_backup(_recovery_vault(config))
        message(stdscr, f"Backup created and verified:\\n{archive}")
    except (RecoveryError, ValueError) as exc:
        message(stdscr, f"Backup error: {exc}")


def verify_latest_backup(stdscr, config):
    from core.recovery import list_backups, verify_backup, RecoveryError
    try:
        backups = list_backups(_recovery_vault(config))
        if not backups:
            message(stdscr, "No backup archive found.")
            return
        info = verify_backup(backups[0])
        message(stdscr, f"Backup integrity OK\\nFiles: {info['files']}\\nCreated: {info['created_at']}\\n{info['archive']}")
    except (RecoveryError, ValueError) as exc:
        message(stdscr, f"Verification error: {exc}")


def restore_backup_ui(stdscr, config):
    from core.recovery import list_backups, verify_backup, restore_to_new_directory, RecoveryError
    try:
        backups = list_backups(_recovery_vault(config))
        if not backups:
            message(stdscr, "No backup archive found.")
            return
        archive = backups[0]
        verify_backup(archive)
        destination = _read_line(
            stdscr, "RESTORE BACKUP",
            "New empty destination path (never your existing Vault):",
            max_bytes=240,
        )
        if not destination:
            message(stdscr, "Restore cancelled.")
            return
        restored = restore_to_new_directory(archive, destination)
        message(stdscr, f"Restore completed to new folder:\\n{restored}")
    except (RecoveryError, ValueError, OSError) as exc:
        message(stdscr, f"Restore error: {exc}")


def show_security_check(stdscr, config):
    from core.recovery import security_report
    warnings = security_report(
        config.data.get("vault_path", ""),
        config.path,
    )
    message(stdscr, "SECURITY CHECK\\n\\n" + "\\n".join(f"- {item}" for item in warnings))
