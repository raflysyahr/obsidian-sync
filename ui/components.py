import curses

from ui.logic import shorten  # noqa: F401  (re-exported for convenience)

STYLES = {}


def init_colors():
    """Define named styles; falls back to bold/dim when the terminal has no colors."""
    STYLES.clear()
    STYLES.update({
        "text": curses.A_NORMAL, "head": curses.A_BOLD, "dim": curses.A_DIM,
        "title": curses.A_BOLD, "ok": curses.A_BOLD, "warn": curses.A_BOLD,
        "err": curses.A_BOLD, "key": curses.A_BOLD, "accent": curses.A_BOLD,
        "sel": curses.A_REVERSE,
    })
    try:
        if not curses.has_colors():
            return
        curses.start_color()
        curses.use_default_colors()
        palette = [("title", curses.COLOR_CYAN, curses.A_BOLD),
                   ("ok", curses.COLOR_GREEN, 0),
                   ("warn", curses.COLOR_YELLOW, 0),
                   ("err", curses.COLOR_RED, curses.A_BOLD),
                   ("key", curses.COLOR_YELLOW, curses.A_BOLD),
                   ("accent", curses.COLOR_CYAN, 0)]
        for number, (name, color, extra) in enumerate(palette, start=1):
            curses.init_pair(number, color, -1)
            STYLES[name] = curses.color_pair(number) | extra
    except curses.error:
        pass


def style(name):
    return STYLES.get(name, curses.A_NORMAL)


def safe_addstr(win, y, x, text, attr=0):
    height, width = win.getmaxyx()
    if y < 0 or y >= height or x >= width:
        return
    try:
        win.addnstr(y, max(0, x), str(text), max(0, width - max(0, x) - 1), attr)
    except curses.error:
        pass


def draw_frame(stdscr, title, hint=None):
    """Clear the screen and draw border, title bar and footer hint.

    This never calls refresh(): callers draw their content and refresh once,
    otherwise the half-drawn frame is flushed to the terminal and flickers.
    """
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    stdscr.box()
    safe_addstr(stdscr, 1, 2, f" {title} ", style("title"))
    try:
        stdscr.hline(2, 1, curses.ACS_HLINE, max(0, w - 2))
        stdscr.addch(2, 0, curses.ACS_LTEE)
        stdscr.addch(2, w - 1, curses.ACS_RTEE)
    except curses.error:
        pass
    if hint:
        safe_addstr(stdscr, h - 2, 2, shorten(hint, w - 5, "end"), style("dim"))
