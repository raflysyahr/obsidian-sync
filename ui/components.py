import curses

def safe_addstr(win, y, x, text, attr=0):
    height, width = win.getmaxyx()
    if y < 0 or y >= height or x >= width:
        return
    try:
        win.addnstr(y, max(0, x), str(text), max(0, width - max(0, x) - 1), attr)
    except curses.error:
        pass

def draw_frame(stdscr, title):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    stdscr.box()
    safe_addstr(stdscr, 1, 3, f" {title} ", curses.A_BOLD)
    safe_addstr(stdscr, h - 2, 2, "↑/↓ Navigate  Enter Select  Q Quit")
    stdscr.refresh()
