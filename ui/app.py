import curses
import os

from ui.components import init_colors
from ui.dashboard import dashboard


def _run(stdscr, config):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    init_colors()
    dashboard(stdscr, config)


def run_app(config):
    # Make Esc respond immediately instead of waiting for a possible escape sequence.
    os.environ.setdefault("ESCDELAY", "25")
    curses.wrapper(_run, config)
