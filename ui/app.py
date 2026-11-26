import curses
from ui.dashboard import dashboard

def _run(stdscr, config):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    dashboard(stdscr, config)

def run_app(config):
    curses.wrapper(_run, config)
