import curses
from pathlib import Path
from ui.components import safe_addstr, draw_frame

MENU = ["Configure Vault path", "Initialize Git repository", "Git status",
        "Configure GitHub remote", "Environment check", "About", "Quit"]

def dashboard(stdscr, config):
    selected = 0
    while True:
        draw_frame(stdscr, "OBSIDIAN SYNC  v0.1.0")
        h, w = stdscr.getmaxyx()
        vault = config.data.get("vault_path") or "(not configured)"
        safe_addstr(stdscr, 3, 3, "VAULT", curses.A_BOLD)
        safe_addstr(stdscr, 4, 3, vault)
        safe_addstr(stdscr, 6, 3, f"Branch: {config.data.get('branch', 'main')}")
        safe_addstr(stdscr, 7, 3, "Auto-sync: " + (
            "enabled" if config.data.get("auto_sync_enabled") else "disabled"
        ))
        safe_addstr(stdscr, 9, 3, "MAIN MENU", curses.A_BOLD)
        for i, item in enumerate(MENU):
            attr = curses.A_REVERSE if i == selected else curses.A_NORMAL
            safe_addstr(stdscr, 11 + i, 5, item, attr)
        stdscr.refresh()
        key = stdscr.getch()
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
                show_environment(stdscr)
            elif selected == 5:
                message(stdscr, "Obsidian Sync v0.2.0 - local Git engine.")
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
    stdscr.getch()


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
