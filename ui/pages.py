"""Reusable full-screen "pages" for the Obsidian Sync TUI.

Every page follows the same rules:
  * It draws a complete frame, then refreshes exactly once.
  * It waits with a *blocking* read, so the dashboard's idle timer can never
    close it or cut input short.
  * It redraws on terminal resize and shows a friendly notice when the
    terminal is too small.
"""
import contextlib
import curses
import threading
import time

from ui.components import draw_frame, safe_addstr, style
from ui.logic import explain_error, shorten, wrap_lines

MIN_W, MIN_H = 30, 10

_idle_timeout = -1
_mouse_wanted = False

ENTER_KEYS = ("\n", "\r", curses.KEY_ENTER)
BACKSPACE_KEYS = ("\x7f", "\b", curses.KEY_BACKSPACE)


# --------------------------------------------------------------------------
# input plumbing
# --------------------------------------------------------------------------
def set_idle_timeout(milliseconds):
    """Timeout the dashboard uses while idle; restored after every blocking page."""
    global _idle_timeout
    _idle_timeout = milliseconds


def _mouse_mask():
    mask = getattr(curses, "BUTTON1_CLICKED", 0) | getattr(curses, "BUTTON4_PRESSED", 0)
    return mask | getattr(curses, "BUTTON5_PRESSED", 0)


def set_mouse(enabled):
    """Turn tap/scroll support on or off (off keeps native copy/paste working)."""
    global _mouse_wanted
    _mouse_wanted = bool(enabled)
    try:
        curses.mousemask(_mouse_mask() if enabled else 0)
    except (curses.error, AttributeError):
        pass


def mouse_enabled():
    return _mouse_wanted


@contextlib.contextmanager
def _mouse_suspended():
    """Disable mouse reporting while typing so long-press paste keeps working."""
    was_on = _mouse_wanted
    if was_on:
        try:
            curses.mousemask(0)
        except curses.error:
            pass
    try:
        yield
    finally:
        if was_on:
            try:
                curses.mousemask(_mouse_mask())
            except curses.error:
                pass


@contextlib.contextmanager
def blocking(stdscr):
    stdscr.timeout(-1)
    try:
        yield
    finally:
        stdscr.timeout(_idle_timeout)


def read_event(stdscr):
    """Return (kind, value): key / click / wheel / resize / none (timeout)."""
    try:
        key = stdscr.get_wch()
    except curses.error:
        return ("none", None)
    if key == curses.KEY_RESIZE:
        return ("resize", None)
    if key == curses.KEY_MOUSE:
        try:
            _, x, y, _, state = curses.getmouse()
        except curses.error:
            return ("none", None)
        if state & getattr(curses, "BUTTON4_PRESSED", 0):
            return ("wheel", -1)
        if state & getattr(curses, "BUTTON5_PRESSED", 0):
            return ("wheel", 1)
        clicks = (getattr(curses, "BUTTON1_CLICKED", 0)
                  | getattr(curses, "BUTTON1_DOUBLE_CLICKED", 0)
                  | getattr(curses, "BUTTON1_RELEASED", 0))
        if state & clicks:
            return ("click", (y, x))
        return ("none", None)
    return ("key", key)


def _set_cursor(visibility):
    try:
        curses.curs_set(visibility)
    except curses.error:
        pass


def restore_idle(stdscr):
    """Put back the dashboard's idle timeout after a page that managed its own."""
    stdscr.timeout(_idle_timeout)


def ensure_size(stdscr, min_w=MIN_W, min_h=MIN_H):
    """Block until the terminal is large enough (press Q to continue anyway)."""
    while True:
        h, w = stdscr.getmaxyx()
        if h >= min_h and w >= min_w:
            return
        stdscr.erase()
        safe_addstr(stdscr, 0, 0, f"Terminal too small ({w}x{h}).", style("warn"))
        safe_addstr(stdscr, 1, 0, f"Enlarge to {min_w}x{min_h}+ or press Q.")
        stdscr.refresh()
        with blocking(stdscr):
            kind, value = read_event(stdscr)
        if kind == "key" and value in ("q", "Q"):
            return


# --------------------------------------------------------------------------
# read-only pages
# --------------------------------------------------------------------------
def _wrap_items(items, width):
    rows = []
    for item in items:
        text, name = (item, "text") if isinstance(item, str) else item
        for line in wrap_lines(text, width):
            rows.append((line, name))
    return rows


def show_page(stdscr, title, items, closing="Press any key to return"):
    """Scrollable text page. items: str or (str, style_name)."""
    top = 0
    while True:
        ensure_size(stdscr)
        h, w = stdscr.getmaxyx()
        rows = _wrap_items(items, w - 6)
        view = max(1, h - 6)
        scrollable = len(rows) > view
        top = max(0, min(top, max(0, len(rows) - view)))
        hint = "↑/↓ PgUp/PgDn Scroll   Enter or Q Close" if scrollable else closing
        draw_frame(stdscr, title, hint)
        for offset, (line, name) in enumerate(rows[top:top + view]):
            safe_addstr(stdscr, 3 + offset, 3, line, style(name))
        if scrollable:
            label = f"{top + 1}-{min(top + view, len(rows))} of {len(rows)}"
            safe_addstr(stdscr, h - 3, max(3, w - len(label) - 3), label, style("dim"))
        stdscr.refresh()
        with blocking(stdscr):
            kind, value = read_event(stdscr)
        if kind in ("resize", "none"):
            continue
        if not scrollable:
            if kind == "wheel":
                continue
            return
        page = max(1, view - 1)
        if kind == "wheel":
            top += value * 2
        elif kind == "key":
            if value in ("q", "Q", "\x1b", curses.KEY_LEFT) or value in ENTER_KEYS:
                return
            if value in (curses.KEY_UP, "k"):
                top -= 1
            elif value in (curses.KEY_DOWN, "j"):
                top += 1
            elif value in (curses.KEY_PPAGE, "b"):
                top -= page
            elif value in (curses.KEY_NPAGE, " ", "f"):
                top += page
            elif value in (curses.KEY_HOME, "g"):
                top = 0
            elif value in (curses.KEY_END, "G"):
                top = len(rows)


_KIND = {
    "ok": ("Done", "✓ ", "ok"),
    "err": ("Error", "✗ ", "err"),
    "warn": ("Notice", "! ", "warn"),
    "info": ("Notice", "", "text"),
}


def message(stdscr, text, kind="info", title=None):
    """Result page. Errors automatically get a short, actionable tip."""
    default_title, icon, name = _KIND.get(kind, _KIND["info"])
    lines = str(text).replace("\\n", "\n").split("\n")
    items = [(icon + lines[0], name)] + [(line, "text") for line in lines[1:]]
    if kind == "err":
        tip = explain_error(text)
        if tip:
            items += [("", "text"), ("Tip: " + tip, "warn")]
    show_page(stdscr, title or default_title, items)


def confirm(stdscr, title, text, yes="Yes", default=False):
    """Yes/No question. Returns True only for an explicit yes (or default+Enter)."""
    yes_label, no_label = f"[Y] {yes}", "[N] Cancel"
    while True:
        ensure_size(stdscr)
        h, w = stdscr.getmaxyx()
        draw_frame(stdscr, title, "Y Yes   N No   Esc Cancel")
        rows = wrap_lines(text, w - 6)[:max(1, h - 9)]
        for offset, line in enumerate(rows):
            safe_addstr(stdscr, 4 + offset, 3, line)
        button_y = min(4 + len(rows) + 1, h - 4)
        yes_style = style("ok") | (curses.A_REVERSE if default else 0)
        no_style = style("err") | (0 if default else curses.A_REVERSE)
        safe_addstr(stdscr, button_y, 3, yes_label, yes_style)
        no_x = 3 + len(yes_label) + 4
        safe_addstr(stdscr, button_y, no_x, no_label, no_style)
        stdscr.refresh()
        with blocking(stdscr):
            kind, value = read_event(stdscr)
        if kind == "key":
            if value in ("y", "Y"):
                return True
            if value in ("n", "N", "q", "Q", "\x1b"):
                return False
            if value in ENTER_KEYS:
                return default
        elif kind == "click":
            y, x = value
            if y == button_y and 3 <= x < 3 + len(yes_label):
                return True
            if y == button_y and no_x <= x < no_x + len(no_label):
                return False


# --------------------------------------------------------------------------
# text input
# --------------------------------------------------------------------------
def prompt(stdscr, title, label, initial="", validate=None, max_len=240,
           hint="Enter Save   Esc Cancel   ←/→ Move   Ctrl+U Clear"):
    """Single-line editor. Returns the text, or None if the user cancelled.

    `validate(text) -> error string | None` keeps the page open until the
    value is acceptable. Esc/Ctrl+C always cancel without changing anything.
    """
    text = str(initial)[:max_len]
    pos = len(text)
    error = ""
    try:
        with _mouse_suspended(), blocking(stdscr):
            _set_cursor(1)
            while True:
                ensure_size(stdscr)
                h, w = stdscr.getmaxyx()
                draw_frame(stdscr, title, hint)
                rows = wrap_lines(label, w - 6)[:max(1, h - 10)]
                for offset, line in enumerate(rows):
                    safe_addstr(stdscr, 4 + offset, 3, line)
                field_y = min(4 + len(rows) + 1, h - 5)
                field_w = max(6, w - 9)
                start = max(0, pos - field_w + 1)
                safe_addstr(stdscr, field_y, 3, "› ", style("accent"))
                safe_addstr(stdscr, field_y, 5, text[start:start + field_w])
                if start > 0:
                    safe_addstr(stdscr, field_y, 4, "…", style("dim"))
                if error:
                    safe_addstr(stdscr, field_y + 2, 3, shorten(error, w - 6, "end"),
                                style("err"))
                try:
                    stdscr.move(field_y, min(5 + pos - start, w - 3))
                except curses.error:
                    pass
                stdscr.refresh()

                kind, key = read_event(stdscr)
                if kind != "key":
                    continue
                if key in ENTER_KEYS:
                    value = text.strip()
                    problem = validate(value) if validate else None
                    if problem:
                        error = problem
                        continue
                    return value
                if key == "\x1b":
                    return None
                error = ""
                if key in BACKSPACE_KEYS:
                    if pos:
                        text, pos = text[:pos - 1] + text[pos:], pos - 1
                elif key == curses.KEY_DC:
                    text = text[:pos] + text[pos + 1:]
                elif key == curses.KEY_LEFT:
                    pos = max(0, pos - 1)
                elif key == curses.KEY_RIGHT:
                    pos = min(len(text), pos + 1)
                elif key in (curses.KEY_HOME, "\x01"):
                    pos = 0
                elif key in (curses.KEY_END, "\x05"):
                    pos = len(text)
                elif key == "\x15":      # Ctrl+U: clear the line
                    text, pos = "", 0
                elif key == "\x0b":      # Ctrl+K: delete to end
                    text = text[:pos]
                elif key == "\x17":      # Ctrl+W: delete previous word
                    head = text[:pos].rstrip()
                    cut = head.rfind(" ") + 1
                    text, pos = text[:cut] + text[pos:], cut
                elif isinstance(key, str) and key.isprintable() and len(text) < max_len:
                    text, pos = text[:pos] + key + text[pos:], pos + 1
    except KeyboardInterrupt:
        return None
    finally:
        _set_cursor(0)


# --------------------------------------------------------------------------
# long-running work
# --------------------------------------------------------------------------
def run_task(stdscr, title, text, function, *args, **kwargs):
    """Run `function` in a worker thread while showing a spinner and elapsed time.

    The result is returned, or the worker's exception is re-raised here, so
    callers keep their normal try/except structure.
    """
    box = {}

    def worker():
        try:
            box["value"] = function(*args, **kwargs)
        except BaseException as exc:  # re-raised in the UI thread below
            box["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    started = time.monotonic()
    frame = 0
    stdscr.timeout(120)
    try:
        while thread.is_alive():
            h, w = stdscr.getmaxyx()
            draw_frame(stdscr, title, "Working… please wait")
            spinner = "|/-\\"[frame % 4]
            for offset, line in enumerate(wrap_lines(text, w - 8)[:max(1, h - 8)]):
                safe_addstr(stdscr, 4 + offset, 3, line)
            elapsed = int(time.monotonic() - started)
            safe_addstr(stdscr, min(h - 4, 6 + offset), 3,
                        f"{spinner}  {elapsed}s", style("accent"))
            stdscr.refresh()
            stdscr.getch()  # doubles as a 120 ms sleep; keypresses are ignored
            frame += 1
    finally:
        stdscr.timeout(_idle_timeout)
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")
