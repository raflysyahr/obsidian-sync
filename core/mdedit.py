"""Pure Markdown editing engine for the touch editor (no curses, fully unit-testable).

Positions are (row, col) tuples; a selection is the half-open range [start, end).
The editor only ever *adds or removes Markdown markers* and re-flows whitespace
between blocks: the words you wrote are never rewritten.
"""
import re
from collections import namedtuple

MAX_UNDO = 200
WORD = re.compile(r"\S+")
SENTENCE = re.compile(r"\S.*?(?:[.!?]+[\"')\]]*(?=\s|$)|$)")

HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
TASK = re.compile(r"^(\s*)[-*+]\s+\[([ xX])\]\s+(.*)$")
BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
ORDERED = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
QUOTE = re.compile(r"^(\s*)>\s?(.*)$")
HR = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
PREFIX_ONLY = re.compile(r"^\s*(?:#{1,6}|[-*+]|\d+[.)]|>)?\s*(?:\[[ xX]\])?\s*$")
LIST_KINDS = ("bullet", "ordered", "task")

Block = namedtuple("Block", "kind level indent content prefix")


def parse_block(line):
    """Classify one line. `prefix` is the column where the content starts."""
    if not line.strip():
        return Block("blank", 0, "", "", 0)
    if HR.match(line):
        return Block("hr", 0, "", line.strip(), 0)
    if FENCE.match(line):
        return Block("fence", 0, "", line.strip(), 0)
    m = HEADING.match(line)
    if m:
        return Block("heading", len(m.group(1)), "", m.group(2).strip(), m.start(2))
    m = TASK.match(line)
    if m:
        return Block("task", 1 if m.group(2) in "xX" else 0, m.group(1), m.group(3).strip(),
                     m.start(3))
    m = BULLET.match(line)
    if m:
        return Block("bullet", 0, m.group(1), m.group(2).strip(), m.start(2))
    m = ORDERED.match(line)
    if m:
        return Block("ordered", 0, m.group(1), m.group(3).strip(), m.start(3))
    m = QUOTE.match(line)
    if m:
        return Block("quote", 0, m.group(1), m.group(2).strip(), m.start(2))
    return Block("para", 0, line[:len(line) - len(line.lstrip())], line.strip(),
                 len(line) - len(line.lstrip()))


def _word_span(line, col):
    """Span of the word containing col (or ending exactly at col)."""
    for m in WORD.finditer(line):
        if m.start() <= col < m.end():
            return m.span()
    for m in WORD.finditer(line):
        if m.end() == col:
            return m.span()
    return None


def _is_wrapped(text, open_, close):
    if len(text) <= len(open_) + len(close):
        return False
    if not (text.startswith(open_) and text.endswith(close)):
        return False
    if open_ == "*" == close:  # italic must not be confused with bold
        return not text.startswith("**") or text.startswith("***")
    return True


def _outside(line, s, e, open_, close):
    lo, lc = len(open_), len(close)
    if s < lo or line[s - lo:s] != open_ or line[e:e + lc] != close:
        return False
    if open_ == "*" == close and (line[s - 2:s] == "**" or line[e:e + 2] == "**"):
        return False
    return True


_INLINE_STRIP = [
    (re.compile(r"\*\*\*(.+?)\*\*\*"), r"\1"), (re.compile(r"\*\*(.+?)\*\*"), r"\1"),
    (re.compile(r"__(.+?)__"), r"\1"), (re.compile(r"(?<!\*)\*(?!\*)(.+?)\*"), r"\1"),
    (re.compile(r"~~(.+?)~~"), r"\1"), (re.compile(r"==(.+?)=="), r"\1"),
    (re.compile(r"`(.+?)`"), r"\1"), (re.compile(r"\[\[(?:[^\]|]*\|)?(.+?)\]\]"), r"\1"),
    (re.compile(r"\[(.+?)\]\((.*?)\)"), r"\1"),
]


def strip_inline(text):
    for pattern, repl in _INLINE_STRIP:
        text = pattern.sub(repl, text)
    return text


# --------------------------------------------------------------------------
# wrapping / geometry helpers (used by the UI to map taps to text positions)
# --------------------------------------------------------------------------
def wrap_rows(lines, width):
    """Soft-wrap lines into visual rows: [(row, col_start, col_end)]."""
    width = max(4, width)
    rows = []
    for r, line in enumerate(lines):
        if not line:
            rows.append((r, 0, 0))
            continue
        start = 0
        while start < len(line):
            end = min(len(line), start + width)
            if end < len(line):
                cut = line.rfind(" ", start, end)
                if cut > start:
                    end = cut + 1
            rows.append((r, start, end))
            start = end
    return rows


def locate(rows, row, col):
    """Index of the visual row that shows (row, col)."""
    last = 0
    for i, (r, s, e) in enumerate(rows):
        if r == row:
            last = i
            if s <= col < e:
                return i
    return last


def position_at(rows, lines, visual_row, x):
    """Document position for a tap at visual row / column offset x."""
    if not rows:
        return (0, 0)
    visual_row = min(max(visual_row, 0), len(rows) - 1)
    r, s, e = rows[visual_row]
    limit = e if e == len(lines[r]) else max(s, e - 1)
    return (r, min(s + max(0, x), limit))


def line_styles(lines):
    """Per-line (style_name, marker_prefix_len) for syntax colouring."""
    out, in_code = [], False
    for line in lines:
        if FENCE.match(line):
            in_code = not in_code
            out.append(("accent", 0))
            continue
        if in_code:
            out.append(("accent", 0))
            continue
        b = parse_block(line)
        if b.kind == "heading":
            out.append(("title", 0))
        elif b.kind in LIST_KINDS:
            out.append(("text", b.prefix))
        elif b.kind == "quote":
            out.append(("ok", 0))
        elif b.kind == "hr":
            out.append(("dim", 0))
        else:
            out.append(("text", 0))
    return out


def render_preview(lines):
    """Readable preview rows [(text, style)] with the markers resolved."""
    out, in_code = [], False
    for line in lines:
        if FENCE.match(line):
            in_code = not in_code
            continue
        if in_code:
            out.append(("  │ " + line, "accent"))
            continue
        b = parse_block(line)
        text = strip_inline(b.content)
        if b.kind == "heading":
            out.append((text.upper() if b.level == 1 else text, "title" if b.level <= 2 else "head"))
            if b.level == 1:
                out.append(("═" * min(len(text), 40), "dim"))
        elif b.kind == "bullet":
            out.append((f"{b.indent}• {text}", "text"))
        elif b.kind == "ordered":
            number = ORDERED.match(line).group(2)
            out.append((f"{b.indent}{number}. {text}", "text"))
        elif b.kind == "task":
            out.append((f"{b.indent}{'☑' if b.level else '☐'} {text}", "ok" if b.level else "text"))
        elif b.kind == "quote":
            out.append((f"▎ {text}", "ok"))
        elif b.kind == "hr":
            out.append(("─" * 30, "dim"))
        elif b.kind == "blank":
            out.append(("", "text"))
        else:
            out.append((strip_inline(line.strip()), "text"))
    return out


def autoformat_lines(lines, whole_document=True):
    """Conservative rule-based guesses. Returns (new_lines, change_count)."""
    out, changes, in_code = [], 0, False
    first = next((i for i, l in enumerate(lines) if l.strip()), None)
    for i, line in enumerate(lines):
        if FENCE.match(line):
            in_code = not in_code
            out.append(line)
            continue
        if in_code:
            out.append(line)
            continue
        new = line
        m = re.match(r"^(\s*)[•·▪●○◦‣⁃–—]\s+(\S.*)$", line) or re.match(r"^(\s*)\*\s+(\S.*)$", line)
        if m and not HR.match(line):
            new = f"{m.group(1)}- {m.group(2)}"
        else:
            m = re.match(r"^(\s*)(\d+)\)\s+(\S.*)$", line)
            if m:
                new = f"{m.group(1)}{m.group(2)}. {m.group(3)}"
            else:
                new = _guess_heading(lines, i, first if whole_document else None) or line
        if new != line:
            changes += 1
        out.append(new)
    return out, changes


_KEYWORD_HEADING = re.compile(r"^(bab|bagian|chapter|section|pasal|lampiran)\s+[\divxlcIVXLC]+\b", re.I)


def _guess_heading(lines, i, first):
    text = lines[i].strip()
    if not text or len(text) > 60 or parse_block(lines[i]).kind != "para":
        return None
    if text[-1] in ".,;!?":
        prev_blank = i == 0 or not lines[i - 1].strip()
        if not (text.endswith(":") and prev_blank):
            return None
    prev_blank = i == 0 or not lines[i - 1].strip()
    if not prev_blank:
        return None
    letters = [ch for ch in text if ch.isalpha()]
    if text.endswith(":") and len(text) > 1:
        return "### " + text[:-1].strip()
    if i == first and first is not None:
        return "# " + text
    if len(letters) >= 3 and text.upper() == text:
        return "## " + text
    if _KEYWORD_HEADING.match(text):
        return "## " + text
    return None


# --------------------------------------------------------------------------
# the document
# --------------------------------------------------------------------------
class Doc:
    def __init__(self, text=""):
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
        lines = text.split("\n")
        if len(lines) > 1 and lines[-1] == "":
            lines.pop()
        self.lines = lines or [""]
        self.anchor = None
        self.head = (0, 0)
        self.clipboard = ""
        self._undo, self._redo, self._last = [], [], ""
        self._saved = list(self.lines)

    # ---- state -----------------------------------------------------------
    @property
    def dirty(self):
        return self.lines != self._saved

    def mark_saved(self):
        self._saved = list(self.lines)

    def text(self):
        return "" if self.lines == [""] else "\n".join(self.lines) + "\n"

    def _state(self):
        return (list(self.lines), self.anchor, self.head)

    def _snap(self, coalesce=False):
        if not (coalesce and self._last == "type"):
            self._undo.append(self._state())
            del self._undo[:-MAX_UNDO]
        self._redo.clear()
        self._last = "type" if coalesce else ""

    def undo(self):
        if not self._undo:
            return False
        self._redo.append(self._state())
        self.lines, self.anchor, self.head = self._undo.pop()
        self._last = ""
        return True

    def redo(self):
        if not self._redo:
            return False
        self._undo.append(self._state())
        self.lines, self.anchor, self.head = self._redo.pop()
        self._last = ""
        return True

    # ---- selection -------------------------------------------------------
    def clamp(self, pos):
        r = min(max(pos[0], 0), len(self.lines) - 1)
        return (r, min(max(pos[1], 0), len(self.lines[r])))

    @property
    def selection(self):
        if self.anchor is None or self.anchor == self.head:
            return None
        return (min(self.anchor, self.head), max(self.anchor, self.head))

    def collapse(self, pos):
        self.anchor, self.head = None, self.clamp(pos)

    def set_selection(self, start, end):
        self.anchor, self.head = self.clamp(start), self.clamp(end)

    def selected_text(self):
        sel = self.selection
        if not sel:
            return ""
        (r1, c1), (r2, c2) = sel
        if r1 == r2:
            return self.lines[r1][c1:c2]
        return "\n".join([self.lines[r1][c1:]] + self.lines[r1 + 1:r2] + [self.lines[r2][:c2]])

    def select_word_at(self, row, col, nearest=False):
        row = min(max(row, 0), len(self.lines) - 1)
        line = self.lines[row]
        span = _word_span(line, col)
        if span is None and nearest:
            nxt = [m.span() for m in WORD.finditer(line) if m.start() >= col]
            prv = [m.span() for m in WORD.finditer(line) if m.end() <= col]
            span = nxt[0] if nxt else (prv[-1] if prv else None)
        if span is None:
            self.collapse((row, col))
            return False
        self.anchor, self.head = (row, span[0]), (row, span[1])
        return True

    def extend_to_word(self, row, col):
        """Range tap: grow the selection so it also covers the tapped word."""
        line = self.lines[min(max(row, 0), len(self.lines) - 1)]
        span = _word_span(line, col)
        sel = self.selection
        if span is None:
            return False
        if sel is None:
            return self.select_word_at(row, col)
        start, end = min(sel[0], (row, span[0])), max(sel[1], (row, span[1]))
        self.anchor, self.head = start, end
        return True

    def _next_word_end(self, pos):
        r, c = pos
        while r < len(self.lines):
            for m in WORD.finditer(self.lines[r]):
                if m.end() > c:
                    return (r, m.end())
            r, c = r + 1, 0
        return None

    def _prev_word_start(self, pos):
        r, c = pos
        while r >= 0:
            best = None
            for m in WORD.finditer(self.lines[r]):
                if m.start() < c:
                    best = m.start()
            if best is not None:
                return (r, best)
            r -= 1
            c = len(self.lines[r]) if r >= 0 else 0
        return None

    def _ensure_selection(self):
        if self.selection:
            return True
        return self.select_word_at(*self.head, nearest=True)

    def extend_right(self):
        if not self._ensure_selection():
            return False
        start, end = self.selection
        nxt = self._next_word_end(end)
        if nxt is None:
            return False
        self.anchor, self.head = start, nxt
        return True

    def extend_left(self):
        if not self._ensure_selection():
            return False
        start, end = self.selection
        prv = self._prev_word_start(start)
        if prv is None:
            return False
        self.anchor, self.head = prv, end
        return True

    def step_word(self, direction):
        """Select the next/previous word (touch-friendly cursor movement)."""
        base = self.selection
        if direction > 0:
            nxt = self._next_word_end(base[1] if base else self.head)
            if nxt is None:
                return False
            span = _word_span(self.lines[nxt[0]], nxt[1] - 1)
        else:
            prv = self._prev_word_start(base[0] if base else self.head)
            if prv is None:
                return False
            span = _word_span(self.lines[prv[0]], prv[1])
        row = nxt[0] if direction > 0 else prv[0]
        self.anchor, self.head = (row, span[0]), (row, span[1])
        return True

    def step_line(self, direction):
        row = min(max(self.head[0] + direction, 0), len(self.lines) - 1)
        if row == self.head[0] and direction:
            return False
        self.select_word_at(row, self.head[1], nearest=True)
        return True

    def select_line(self, row=None):
        row = self.head[0] if row is None else row
        self.anchor, self.head = (row, 0), (row, len(self.lines[row]))

    def select_sentence(self, row=None, col=None):
        row = self.head[0] if row is None else row
        col = self.head[1] if col is None else col
        for m in SENTENCE.finditer(self.lines[row]):
            if m.start() <= col <= m.end():
                self.anchor, self.head = (row, m.start()), (row, m.end())
                return True
        return False

    def select_paragraph(self, row=None):
        row = self.head[0] if row is None else row
        if not self.lines[row].strip():
            return False
        r1 = r2 = row
        while r1 > 0 and self.lines[r1 - 1].strip():
            r1 -= 1
        while r2 < len(self.lines) - 1 and self.lines[r2 + 1].strip():
            r2 += 1
        self.anchor, self.head = (r1, 0), (r2, len(self.lines[r2]))
        return True

    def select_all(self):
        last = len(self.lines) - 1
        self.anchor, self.head = (0, 0), (last, len(self.lines[last]))

    # ---- block operations ------------------------------------------------
    def _target(self):
        sel = self.selection
        if sel:
            return sel
        r = self.head[0]
        return ((r, 0), (r, len(self.lines[r])))

    def _rows(self):
        (r1, _), (r2, c2) = self._target()
        if r2 > r1 and c2 == 0:
            r2 -= 1
        return r1, r2

    def _split_target(self):
        (r1, c1), (r2, c2) = self._target()
        while r2 > r1 and c2 == 0:
            r2 -= 1
            c2 = len(self.lines[r2])
        L = self.lines
        before = L[r1][:c1]
        if PREFIX_ONLY.match(before):
            # Only a block marker ("- ", "## ") precedes the selection: the marker is
            # part of the block being converted, so treat the line as selected whole.
            before, c1 = "", 0
        sel = [L[r1][c1:c2]] if r1 == r2 else [L[r1][c1:]] + L[r1 + 1:r2] + [L[r2][:c2]]
        return r1, r2, before.rstrip(), sel, L[r2][c2:].lstrip()

    def _apply_block(self, r1, r2, before, new, after, inner=False):
        out = []
        if before:
            out += [before, ""]
        start = len(out)
        out += new
        if after:
            out += ["", after]
        self._snap()
        self.lines[r1:r2 + 1] = out
        first, last = r1 + start, r1 + start + len(new) - 1
        if inner and len(new) > 2:
            first, last = first + 1, last - 1
            self.anchor, self.head = (first, 0), (last, len(self.lines[last]))
        else:
            self.anchor = (first, parse_block(self.lines[first]).prefix)
            self.head = (last, len(self.lines[last]))
        return True

    def set_block(self, kind, level=1):
        r1, r2, before, sel, after = self._split_target()
        nonblank = [l for l in sel if l.strip()]
        if not nonblank:
            return False
        blocks = [parse_block(l) for l in nonblank]
        contents = [b.content for b in blocks]
        if kind == "code":
            fenced = (len(nonblank) >= 2 and FENCE.match(nonblank[0])
                      and FENCE.match(nonblank[-1]))
            if fenced:
                return self._apply_block(r1, r2, before, nonblank[1:-1] or [""], after)
            return self._apply_block(r1, r2, before, ["```"] + sel + ["```"], after, inner=True)
        off = False
        if kind == "heading":
            off = len(blocks) == 1 and blocks[0].kind == "heading" and blocks[0].level == level
        elif kind in ("bullet", "ordered", "task", "quote"):
            off = all(b.kind == kind for b in blocks)
        if kind == "para" or (off and kind == "heading"):
            new = [" ".join(contents)]
        elif off:
            new = contents
        elif kind == "heading":
            new = ["#" * level + " " + " ".join(contents)]
        elif kind == "quote":
            new = [f"> {c}" for c in contents]
        elif kind == "callout":
            new = ["> [!note]"] + [f"> {c}" for c in contents]
        else:
            new = []
            for i, b in enumerate(blocks, start=1):
                ind = b.indent if b.kind in LIST_KINDS else ""
                if kind == "bullet":
                    new.append(f"{ind}- {b.content}")
                elif kind == "ordered":
                    new.append(f"{ind}{i}. {b.content}")
                else:
                    box = "x" if (b.kind == "task" and b.level) else " "
                    new.append(f"{ind}- [{box}] {b.content}")
        return self._apply_block(r1, r2, before, new, after)

    def toggle_task_check(self):
        r1, r2 = self._rows()
        count = 0
        self._snap()
        for r in range(r1, r2 + 1):
            m = TASK.match(self.lines[r])
            if m:
                flip = " " if m.group(2) in "xX" else "x"
                line = self.lines[r]
                at = line.index("[", len(m.group(1)))
                self.lines[r] = line[:at + 1] + flip + line[at + 2:]
                count += 1
        if not count:
            self._undo.pop()
        return count

    def insert_hr(self):
        _, r2 = self._rows()
        new = []
        if self.lines[r2].strip():
            new.append("")
        new.append("---")
        if r2 + 1 >= len(self.lines) or self.lines[r2 + 1].strip():
            new.append("")
        self._snap()
        self.lines[r2 + 1:r2 + 1] = new
        self.collapse((r2 + len(new), 0))
        return True

    def sentences_to_list(self, ordered=False):
        r1, r2, before, sel, after = self._split_target()
        text = " ".join(parse_block(l).content for l in sel if l.strip())
        items = [m.group(0).strip() for m in SENTENCE.finditer(text) if m.group(0).strip()]
        if not items:
            return False
        new = [f"{i}. {s}" if ordered else f"- {s}" for i, s in enumerate(items, start=1)]
        return self._apply_block(r1, r2, before, new, after)

    def join_lines(self):
        """Merge hard-wrapped lines into paragraphs (blank lines stay as separators)."""
        if not self.selection:
            self.select_paragraph()
        r1, r2 = self._rows()
        out, group = [], []
        for line in self.lines[r1:r2 + 1] + [""]:
            if line.strip():
                group.append(line.strip())
            else:
                if group:
                    out.append(" ".join(group))
                    group = []
                if line == "" and out and out[-1] != "":
                    out.append("")
        while out and out[-1] == "":
            out.pop()
        removed = (r2 - r1 + 1) - len(out)
        if removed <= 0:
            return 0
        self._snap()
        self.lines[r1:r2 + 1] = out
        self.anchor, self.head = (r1, 0), (r1 + len(out) - 1, len(self.lines[r1 + len(out) - 1]))
        return removed

    def clear_format(self):
        r1, r2 = self._rows()
        self._snap()
        changed = 0
        for r in range(r1, r2 + 1):
            line = self.lines[r]
            b = parse_block(line)
            new = line if b.kind in ("blank", "fence", "hr") else strip_inline(b.content)
            if new != line:
                self.lines[r] = new
                changed += 1
        if not changed:
            self._undo.pop()
        else:
            self.anchor, self.head = (r1, 0), (r2, len(self.lines[r2]))
        return changed

    def indent(self, delta):
        r1, r2 = self._rows()
        self._snap()
        for r in range(r1, r2 + 1):
            if not self.lines[r].strip():
                continue
            if delta > 0:
                self.lines[r] = "    " + self.lines[r]
            else:
                cut = len(self.lines[r]) - len(self.lines[r].lstrip(" "))
                self.lines[r] = self.lines[r][min(4, cut):]
        self.anchor, self.head = (r1, 0), (r2, len(self.lines[r2]))
        return True

    def move_lines(self, delta):
        r1, r2 = self._rows()
        if (delta < 0 and r1 == 0) or (delta > 0 and r2 >= len(self.lines) - 1):
            return False
        self._snap()
        block = self.lines[r1:r2 + 1]
        if delta < 0:
            other = self.lines[r1 - 1]
            self.lines[r1 - 1:r2 + 1] = block + [other]
        else:
            other = self.lines[r2 + 1]
            self.lines[r1:r2 + 2] = [other] + block
        shift = -1 if delta < 0 else 1
        self.head = (self.head[0] + shift, self.head[1])
        if self.anchor is not None:
            self.anchor = (self.anchor[0] + shift, self.anchor[1])
        return True

    def tidy(self):
        """Trim trailing spaces, collapse repeated spaces/blank lines, space out headings."""
        if self.selection:
            r1, r2 = self._rows()
        else:
            r1, r2 = 0, len(self.lines) - 1
        out, in_code = [], False
        for line in self.lines[r1:r2 + 1]:
            if FENCE.match(line):
                in_code = not in_code
                out.append(line.rstrip())
                continue
            if in_code:
                out.append(line)
                continue
            lead = line[:len(line) - len(line.lstrip(" "))]
            body = re.sub(r" {2,}", " ", line.strip())
            line = (lead + body) if body else ""
            if not line and out and out[-1] == "":
                continue
            if out and out[-1] != "" and parse_block(out[-1]).kind == "heading" and line:
                out.append("")
            if line and parse_block(line).kind == "heading" and out and out[-1] != "":
                out.append("")
            out.append(line)
        if out == self.lines[r1:r2 + 1]:
            return 0
        changed = abs(len(out) - (r2 - r1 + 1)) or 1
        self._snap()
        self.lines[r1:r2 + 1] = out
        self.collapse(self.head)
        return changed

    def autoformat(self):
        if self.selection:
            r1, r2 = self._rows()
        else:
            r1, r2 = 0, len(self.lines) - 1
        whole = r1 == 0
        new, changes = autoformat_lines(self.lines[r1:r2 + 1], whole_document=whole)
        if changes:
            self._snap()
            self.lines[r1:r2 + 1] = new
            self.collapse(self.head)
        return changes

    # ---- inline operations -----------------------------------------------
    def _inline_segments(self):
        sel = self.selection
        if sel is None:
            r, c = self.head
            span = _word_span(self.lines[r], c)
            if not span:
                return []
            sel = ((r, span[0]), (r, span[1]))
        (r1, c1), (r2, c2) = sel
        segs = []
        for r in range(r1, r2 + 1):
            line = self.lines[r]
            s, e = (c1 if r == r1 else 0), (c2 if r == r2 else len(line))
            while s < e and line[s].isspace():
                s += 1
            while e > s and line[e - 1].isspace():
                e -= 1
            if e > s:
                segs.append((r, s, e))
        return segs

    def toggle_inline(self, open_, close=None):
        close = open_ if close is None else close
        segs = self._inline_segments()
        if not segs:
            return False
        L = self.lines
        wrapped = [_is_wrapped(L[r][s:e], open_, close) for r, s, e in segs]
        outside = [_outside(L[r], s, e, open_, close) for r, s, e in segs]
        unwrap = all(w or o for w, o in zip(wrapped, outside))
        self._snap()
        lo, lc = len(open_), len(close)
        new_segs = []
        for (r, s, e), w, o in zip(segs, wrapped, outside):
            line = L[r]
            if unwrap and w:
                L[r] = line[:s] + line[s + lo:e - lc] + line[e:]
                new_segs.append((r, s, e - lo - lc))
            elif unwrap and o:
                L[r] = line[:s - lo] + line[s:e] + line[e + lc:]
                new_segs.append((r, s - lo, e - lo))
            else:
                L[r] = line[:s] + open_ + line[s:e] + close + line[e:]
                new_segs.append((r, s, e + lo + lc))
        self.anchor = (new_segs[0][0], new_segs[0][1])
        self.head = (new_segs[-1][0], new_segs[-1][2])
        return True

    def selection_is_link(self):
        segs = self._inline_segments()
        return len(segs) == 1 and bool(re.fullmatch(r"\[.+\]\(.*\)", self.lines[segs[0][0]][segs[0][1]:segs[0][2]]))

    def toggle_link(self, url=""):
        segs = self._inline_segments()
        if len(segs) != 1:
            return False
        r, s, e = segs[0]
        text = self.lines[r][s:e]
        m = re.fullmatch(r"\[(.+)\]\((.*)\)", text)
        self._snap()
        new = m.group(1) if m else f"[{text}]({url})"
        self.lines[r] = self.lines[r][:s] + new + self.lines[r][e:]
        self.anchor, self.head = (r, s), (r, s + len(new))
        return True

    # ---- plain editing ----------------------------------------------------
    def _delete_range(self, start, end):
        (r1, c1), (r2, c2) = start, end
        self.lines[r1:r2 + 1] = [self.lines[r1][:c1] + self.lines[r2][c2:]]
        self.collapse((r1, c1))

    def insert_text(self, text, coalesce=False):
        self._snap(coalesce)
        sel = self.selection
        if sel:
            self._delete_range(*sel)
        r, c = self.head
        parts = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ").split("\n")
        line = self.lines[r]
        tail = line[c:]
        if len(parts) == 1:
            self.lines[r] = line[:c] + parts[0] + tail
            self.collapse((r, c + len(parts[0])))
        else:
            self.lines[r:r + 1] = [line[:c] + parts[0]] + parts[1:-1] + [parts[-1] + tail]
            self.collapse((r + len(parts) - 1, len(parts[-1])))
        return True

    def backspace(self):
        self._snap(True)
        sel = self.selection
        if sel:
            self._delete_range(*sel)
            return True
        r, c = self.head
        if c > 0:
            self.lines[r] = self.lines[r][:c - 1] + self.lines[r][c:]
            self.collapse((r, c - 1))
        elif r > 0:
            prev = self.lines[r - 1]
            self.lines[r - 1:r + 1] = [prev + self.lines[r]]
            self.collapse((r - 1, len(prev)))
        else:
            self._undo.pop()
            return False
        return True

    def delete_selection(self):
        self._snap()
        sel = self.selection
        if sel:
            self._delete_range(*sel)
        elif len(self.lines) > 1:
            r = self.head[0]
            del self.lines[r]
            self.collapse((min(r, len(self.lines) - 1), 0))
        else:
            self.lines[0] = ""
            self.collapse((0, 0))
        return True

    def copy(self):
        self.clipboard = self.selected_text() or self.lines[self.head[0]]
        return len(self.clipboard)

    def cut(self):
        self.copy()
        return self.delete_selection()

    def paste(self):
        if not self.clipboard:
            return False
        return self.insert_text(self.clipboard)

    def replace_all(self, find, repl):
        if not find:
            return 0
        sel = self.selection
        if sel:
            text = self.selected_text()
            count = text.count(find)
            if count:
                self.insert_text(text.replace(find, repl))
            return count
        body = "\n".join(self.lines)
        count = body.count(find)
        if count:
            self._snap()
            self.lines = body.replace(find, repl).split("\n")
            self.collapse(self.head)
        return count

    def stats(self):
        text = "\n".join(self.lines)
        return {"lines": len(self.lines), "words": len(WORD.findall(text)), "chars": len(text),
                "headings": sum(1 for l in self.lines if parse_block(l).kind == "heading")}
