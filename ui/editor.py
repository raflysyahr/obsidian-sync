"""Touch-first Markdown editor: tap words to select, tap toolbar buttons to format."""
import curses
import os
from datetime import datetime
from pathlib import Path

from core import noteio
from core.mdedit import Doc, line_styles, locate, position_at, render_preview, wrap_rows
from ui import pages
from ui.components import draw_frame, safe_addstr, style
from ui.logic import layout_buttons, shorten

MIN_W, MIN_H = 36, 12
TABS = ["Pilih", "Blok", "Teks", "Edit", "Alat", "File"]
BUTTONS = {
    "Pilih": [("Kata", "sel_word"), ("←+", "ext_left"), ("+→", "ext_right"),
              ("Kalimat", "sel_sentence"), ("Baris", "sel_line"), ("Para", "sel_para"),
              ("Semua", "sel_all"), ("Rentang", "range"), ("Batal", "sel_clear"),
              ("←", "left"), ("→", "right"), ("↑", "up"), ("↓", "down")],
    "Blok": [("H1", "h1"), ("H2", "h2"), ("H3", "h3"), ("¶", "para"), ("•", "bullet"),
             ("1.", "ordered"), ("[ ]", "task"), ("[x]", "check"), (">", "quote"),
             ("```", "code"), ("Note", "callout"), ("---", "hr")],
    "Teks": [("B", "bold"), ("I", "italic"), ("S", "strike"), ("==", "mark"), ("`", "icode"),
             ("[[ ]]", "wiki"), ("Link", "link"), ("Bersih", "clear")],
    "Edit": [("Undo", "undo"), ("Redo", "redo"), ("Salin", "copy"), ("Potong", "cut"),
             ("Tempel", "paste"), ("Hapus", "delete"), ("▲", "move_up"), ("▼", "move_down"),
             ("→|", "indent"), ("|←", "outdent"), ("Baris+", "newline")],
    "Alat": [("Gabung", "join"), ("→List", "tolist"), ("→1.2.", "tonum"), ("Rapikan", "tidy"),
             ("Ganti", "replace"), ("Tebak", "guess"), ("Ketik", "typing"), ("Preview", "preview")],
    "File": [("Simpan", "save"), ("Simpan sbg", "saveas"), ("Buka", "open"), ("Info", "info"),
             ("Keluar", "quit")],
}
TITLE_BUTTONS = [("Simpan", "save"), ("Keluar", "quit")]


# --------------------------------------------------------------------------
# note picker
# --------------------------------------------------------------------------
def pick_note(stdscr, vault):
    """Return a Path, "new", or None. Everything is tappable."""
    notes = noteio.list_notes(vault) if vault else []
    entries = [("+ Teks baru (ketik / tempel)", "new"), ("> Ketik path file…", "path")]
    for note in notes:
        entries.append((os.path.relpath(note, vault), note))
    selected = top = 0
    try:
        while True:
            pages.ensure_size(stdscr)
            h, w = stdscr.getmaxyx()
            rows = max(1, h - 6)
            top = min(max(0, selected - rows + 1) if selected >= top + rows else top, selected)
            draw_frame(stdscr, "PILIH CATATAN", "↑/↓ Pilih   Enter / ketuk Buka   Esc Batal")
            for k in range(rows):
                index = top + k
                if index >= len(entries):
                    break
                text = shorten(entries[index][0], w - 8)
                if index == selected:
                    safe_addstr(stdscr, 3 + k, 3, " " * max(0, w - 7), style("sel"))
                    safe_addstr(stdscr, 3 + k, 4, text, style("sel"))
                else:
                    safe_addstr(stdscr, 3 + k, 4, text, style("accent") if index < 2 else 0)
            safe_addstr(stdscr, h - 3, 3, f"{len(notes)} catatan (.md / .txt) di Vault", style("dim"))
            stdscr.refresh()
            stdscr.timeout(-1)
            kind, value = pages.read_event(stdscr)
            choice = None
            if kind == "wheel":
                selected = min(max(0, selected + value * 2), len(entries) - 1)
            elif kind == "click":
                index = top + value[0] - 3
                if 3 <= value[0] < 3 + rows and 0 <= index < len(entries):
                    selected, choice = index, index
            elif kind == "key":
                if value in (curses.KEY_UP, "k"):
                    selected = max(0, selected - 1)
                elif value in (curses.KEY_DOWN, "j"):
                    selected = min(len(entries) - 1, selected + 1)
                elif value in pages.ENTER_KEYS:
                    choice = selected
                elif value in ("\x1b", "q", "Q"):
                    return None
            if choice is None:
                continue
            target = entries[choice][1]
            if target != "path":
                return target
            raw = pages.prompt(stdscr, "BUKA FILE", "Path file (absolut, atau relatif ke Vault):",
                               validate=lambda v: None if v else "Isi path file.")
            if raw:
                path = Path(raw).expanduser()
                if not path.is_absolute() and vault:
                    path = vault / path
                if path.is_file():
                    return path
                pages.message(stdscr, f"File tidak ditemukan:\n{path}", "err")
    finally:
        pages.restore_idle(stdscr)


# --------------------------------------------------------------------------
# the editor
# --------------------------------------------------------------------------
class Editor:
    def __init__(self, stdscr, vault):
        self.stdscr = stdscr
        self.vault = vault
        self.doc = Doc("")
        self.path = None
        self.newline = "\n"
        self.tab = 0
        self.page = {name: 0 for name in TABS}
        self.range_mode = False
        self.typing = False
        self.flash = ""
        self.top = 0
        self.follow = True
        self.hits = []
        self.rows = []
        self.text_rows = 1

    # ---- loading / saving -------------------------------------------------
    def new(self):
        self.doc, self.path, self.newline = Doc(""), None, "\n"
        self.typing = True
        self.flash = "Mode ketik aktif: ketik atau tempel teks."

    def load(self, path):
        text, newline = noteio.read_note(path)
        self.doc, self.path, self.newline = Doc(text), Path(path), newline
        self.top, self.typing = 0, False

    def _resolve(self, raw):
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (self.vault or Path.cwd()) / path
        return path if path.suffix else path.with_suffix(".md")

    def _write(self, target):
        try:
            if target.exists():
                noteio.backup_before_overwrite(target, self.vault)
            noteio.write_note(target, self.doc.text(), self.newline)
        except (OSError, noteio.NoteError) as exc:
            pages.message(self.stdscr, f"Gagal menyimpan:\n{exc}", "err")
            return False
        was_txt = self.path is not None and target != self.path
        self.path = target
        self.doc.mark_saved()
        self.flash = (f"Disimpan sebagai {target.name} (teks asli tidak diubah)." if was_txt
                      else f"Disimpan: {target.name}")
        return True

    def save(self):
        if self.path is None:
            return self.save_as()
        return self._write(noteio.suggest_save_path(self.path))

    def save_as(self):
        if self.path is not None:
            default = str(noteio.suggest_save_path(self.path))
        else:
            base = self.vault or Path.cwd()
            default = str(base / f"catatan-{datetime.now():%Y%m%d-%H%M}.md")
        raw = pages.prompt(self.stdscr, "SIMPAN SEBAGAI", "Nama / path file Markdown:",
                           initial=default, validate=lambda v: None if v else "Isi nama file.")
        if raw is None:
            return False
        target = self._resolve(raw)
        if target.exists() and target != self.path and not pages.confirm(
                self.stdscr, "TIMPA FILE?", f"{target.name} sudah ada. Timpa? (backup dibuat dulu)",
                yes="Timpa"):
            return False
        return self._write(target)

    def _discard_ok(self):
        if not self.doc.dirty:
            return True
        return pages.confirm(self.stdscr, "BUANG PERUBAHAN?",
                             "Ada perubahan yang belum disimpan. Buang?", yes="Buang")

    # ---- drawing ----------------------------------------------------------
    def _layout(self, h):
        trows = 2 if h >= 18 else 1
        y_status = h - 2
        y_btn = y_status - trows
        y_tabs = y_btn - 1
        y_line = y_tabs - 1
        return trows, y_status, y_btn, y_tabs, y_line

    def draw(self):
        stdscr, doc = self.stdscr, self.doc
        h, w = stdscr.getmaxyx()
        trows, y_status, y_btn, y_tabs, y_line = self._layout(h)
        name = self.path.name if self.path else "teks baru"
        draw_frame(stdscr, shorten(f"EDIT  {name}{' *' if doc.dirty else ''}", w - 22, "end"))
        self.hits = []

        x = w - 2 - sum(len(label) + 3 for label, _ in TITLE_BUTTONS)
        for label, action in TITLE_BUTTONS:
            text = f" {label} "
            safe_addstr(stdscr, 1, x, text, style("sel"))
            self.hits.append((1, x, x + len(text), "btn", action))
            x += len(text) + 1

        # text area
        th = max(1, y_line - 3)
        self.text_rows = th
        self.rows = rows = wrap_rows(doc.lines, w - 6)
        head_row = locate(rows, *doc.head)
        if self.follow:
            if head_row < self.top:
                self.top = head_row
            elif head_row >= self.top + th:
                self.top = head_row - th + 1
        self.top = max(0, min(self.top, max(0, len(rows) - th)))
        kinds = line_styles(doc.lines)
        sel = doc.selection
        hr, hc = doc.head
        if doc.lines == [""]:
            safe_addstr(stdscr, 3, 3, "Kosong. Buka File > Buka, atau Alat > Ketik lalu tempel teks.",
                        style("dim"))
        for k in range(th):
            vy = self.top + k
            if vy >= len(rows):
                break
            r, s, e = rows[vy]
            line, y = doc.lines[r], 3 + k
            seg = line[s:e]
            name_, prefix = kinds[r]
            if prefix > s:
                cut = min(prefix, e) - s
                safe_addstr(stdscr, y, 3, seg[:cut], style("key"))
                safe_addstr(stdscr, y, 3 + cut, seg[cut:], style(name_))
            else:
                safe_addstr(stdscr, y, 3, seg, style(name_))
            if sel and sel[0][0] <= r <= sel[1][0]:
                a = sel[0][1] if r == sel[0][0] else 0
                b = sel[1][1] if r == sel[1][0] else len(line)
                a, b = max(a, s), min(b, e)
                if b > a:
                    safe_addstr(stdscr, y, 3 + a - s, line[a:b], curses.A_REVERSE)
            elif not sel and r == hr and (s <= hc < e or hc == e == len(line)):
                char = line[hc] if hc < len(line) else " "
                safe_addstr(stdscr, y, 3 + hc - s, char, curses.A_REVERSE)
        if self.top > 0:
            safe_addstr(stdscr, 3, w - 3, "▲", style("dim"))
        if self.top + th < len(rows):
            safe_addstr(stdscr, 3 + th - 1, w - 3, "▼", style("dim"))

        # toolbar
        try:
            stdscr.hline(y_line, 1, curses.ACS_HLINE, max(0, w - 2))
        except curses.error:
            pass
        # tabs: padded labels when they fit, compact labels on narrow phone screens
        padded = [f" {n} " for n in TABS]
        labels = padded if sum(map(len, padded)) + len(TABS) - 1 <= w - 6 else list(TABS)
        x = 3
        for index, text in enumerate(labels):
            attr = style("sel") | curses.A_BOLD if index == self.tab else style("key")
            safe_addstr(stdscr, y_tabs, x, text, attr)
            self.hits.append((y_tabs, x, x + len(text) + 1, "tab", index))  # +1: easier to tap
            x += len(text) + 1

        spec = BUTTONS[TABS[self.tab]]
        labels = [f"[{label}]" for label, _ in spec]
        avail = w - 6
        layout = layout_buttons([len(t) for t in labels], avail, rows=trows)
        page_no = self.page[TABS[self.tab]] % len(layout)
        for row, bx, index in layout[page_no]:
            action = spec[index][1]
            active = (action == "range" and self.range_mode) or (action == "typing" and self.typing)
            attr = curses.A_REVERSE | curses.A_BOLD if active else style("accent")
            x0 = 3 + bx
            safe_addstr(stdscr, y_btn + row, x0, labels[index], attr)
            self.hits.append((y_btn + row, x0, x0 + len(labels[index]) + 1, "btn", action))  # +1 gap
        if len(layout) > 1:
            x0 = 3 + avail - 3
            safe_addstr(stdscr, y_btn + trows - 1, x0, f"[»{page_no + 1}/{len(layout)}]"[:6],
                        style("key"))
            self.hits.append((y_btn + trows - 1, x0, x0 + 6, "more", None))

        # status: message/selection info on the left, active modes always visible on the right
        flags = ("[RENTANG] " if self.range_mode else "") + ("[KETIK]" if self.typing else "")
        flags = flags.strip()
        room = w - 6 - (len(flags) + 1 if flags else 0)
        if self.flash:
            safe_addstr(stdscr, y_status, 3, shorten(self.flash, room, "end"), style("warn"))
        else:
            if sel:
                chosen = doc.selected_text()
                info = f"{len(chosen.split())} kata · {len(chosen)} huruf dipilih"
            else:
                info = f"Baris {hr + 1}/{len(doc.lines)}"
            safe_addstr(stdscr, y_status, 3, shorten(info, room, "end"), style("accent"))
        if flags:
            safe_addstr(stdscr, y_status, w - 3 - len(flags), flags, style("key") | curses.A_BOLD)
        stdscr.refresh()

    # ---- actions ----------------------------------------------------------
    def _need(self, ok, message="Tidak ada yang berubah."):
        if not ok:
            self.flash = message

    def act_sel_word(self):
        self._need(self.doc.select_word_at(*self.doc.head, nearest=True), "Tidak ada kata di sini.")

    def act_ext_left(self):
        self._need(self.doc.extend_left(), "Sudah di awal teks.")

    def act_ext_right(self):
        self._need(self.doc.extend_right(), "Sudah di akhir teks.")

    def act_sel_sentence(self):
        self._need(self.doc.select_sentence(), "Tidak ada kalimat di baris ini.")

    def act_sel_line(self):
        self.doc.select_line()

    def act_sel_para(self):
        self._need(self.doc.select_paragraph(), "Baris ini kosong.")

    def act_sel_all(self):
        self.doc.select_all()

    def act_range(self):
        self.range_mode = not self.range_mode
        if self.range_mode:
            self.flash = ("Rentang: ketuk kata AKHIR." if self.doc.selection
                          else "Rentang: ketuk kata awal, lalu kata akhir.")

    def act_sel_clear(self):
        self.doc.collapse(self.doc.head)
        self.range_mode = False

    def act_left(self):
        if self.typing:
            r, c = self.doc.head
            self.doc.collapse((r, c - 1) if c else (max(0, r - 1), 10 ** 6))
        else:
            self._need(self.doc.step_word(-1), "Sudah di awal.")

    def act_right(self):
        if self.typing:
            r, c = self.doc.head
            line = self.doc.lines[r]
            self.doc.collapse((r, c + 1) if c < len(line) else (min(len(self.doc.lines) - 1, r + 1), 0))
        else:
            self._need(self.doc.step_word(1), "Sudah di akhir.")

    def act_up(self):
        if self.typing:
            self.doc.collapse((self.doc.head[0] - 1, self.doc.head[1]))
        else:
            self._need(self.doc.step_line(-1), "Sudah di baris pertama.")

    def act_down(self):
        if self.typing:
            self.doc.collapse((self.doc.head[0] + 1, self.doc.head[1]))
        else:
            self._need(self.doc.step_line(1), "Sudah di baris terakhir.")

    def _block(self, kind, level=1):
        self._need(self.doc.set_block(kind, level), "Pilih kata atau baris yang berisi teks.")

    def act_h1(self): self._block("heading", 1)
    def act_h2(self): self._block("heading", 2)
    def act_h3(self): self._block("heading", 3)
    def act_para(self): self._block("para")
    def act_bullet(self): self._block("bullet")
    def act_ordered(self): self._block("ordered")
    def act_task(self): self._block("task")
    def act_quote(self): self._block("quote")
    def act_code(self): self._block("code")
    def act_callout(self): self._block("callout")

    def act_check(self):
        self._need(self.doc.toggle_task_check(), "Tidak ada checklist (- [ ]) di sini.")

    def act_hr(self):
        self.doc.insert_hr()

    def _inline(self, open_, close=None):
        self._need(self.doc.toggle_inline(open_, close), "Ketuk sebuah kata dulu.")

    def act_bold(self): self._inline("**")
    def act_italic(self): self._inline("*")
    def act_strike(self): self._inline("~~")
    def act_mark(self): self._inline("==")
    def act_icode(self): self._inline("`")
    def act_wiki(self): self._inline("[[", "]]")

    def act_link(self):
        if self.doc.selection_is_link():
            self.doc.toggle_link()
            return
        if not (self.doc.selection or self.doc.select_word_at(*self.doc.head)):
            self.flash = "Ketuk kata atau pilih teks untuk dijadikan link."
            return
        url = pages.prompt(self.stdscr, "LINK", "Alamat URL untuk teks terpilih:",
                           initial="https://", validate=lambda v: None if len(v) > 8 else "Isi URL.")
        if url:
            self._need(self.doc.toggle_link(url), "Link hanya untuk teks satu baris.")

    def act_clear(self):
        self._need(self.doc.clear_format(), "Tidak ada format untuk dihapus.")

    def act_undo(self): self._need(self.doc.undo(), "Tidak ada yang bisa di-undo.")
    def act_redo(self): self._need(self.doc.redo(), "Tidak ada yang bisa di-redo.")

    def act_copy(self):
        self.flash = f"{self.doc.copy()} karakter disalin."

    def act_cut(self):
        self.doc.cut()
        self.flash = "Dipotong. Ketuk posisi lalu Tempel."

    def act_paste(self): self._need(self.doc.paste(), "Papan klip kosong (Salin/Potong dulu).")
    def act_delete(self): self.doc.delete_selection()
    def act_move_up(self): self._need(self.doc.move_lines(-1), "Sudah di paling atas.")
    def act_move_down(self): self._need(self.doc.move_lines(1), "Sudah di paling bawah.")
    def act_indent(self): self.doc.indent(1)
    def act_outdent(self): self.doc.indent(-1)

    def act_newline(self):
        r = self.doc.head[0] if not self.doc.selection else self.doc.selection[1][0]
        self.doc.collapse((r, len(self.doc.lines[r])))
        self.doc.insert_text("\n")

    def act_join(self):
        n = self.doc.join_lines()
        self.flash = f"{n} baris digabung." if n else "Tidak ada baris terpotong."

    def act_tolist(self): self._need(self.doc.sentences_to_list(False), "Pilih kalimat dulu.")
    def act_tonum(self): self._need(self.doc.sentences_to_list(True), "Pilih kalimat dulu.")

    def act_tidy(self):
        n = self.doc.tidy()
        self.flash = "Teks dirapikan." if n else "Sudah rapi."

    def act_guess(self):
        n = self.doc.autoformat()
        self.flash = f"{n} perubahan otomatis (Undo bila kurang pas)." if n else "Tidak ada tebakan."

    def act_replace(self):
        find = pages.prompt(self.stdscr, "GANTI TEKS", "Cari teks:",
                            validate=lambda v: None if v else "Isi teks yang dicari.")
        if find is None:
            return
        repl = pages.prompt(self.stdscr, "GANTI TEKS", f"Ganti “{shorten(find, 20, 'end')}” dengan:")
        if repl is None:
            return
        scope = "di pilihan" if self.doc.selection else "di seluruh teks"
        self.flash = f"{self.doc.replace_all(find, repl)} penggantian {scope}."

    def act_typing(self):
        self.typing = not self.typing
        self.flash = ("Mode ketik ON: ketik / tempel teks. Ketuk posisi untuk memindah kursor."
                      if self.typing else "Mode ketik OFF.")

    def act_preview(self):
        pages.show_page(self.stdscr, "PREVIEW", render_preview(self.doc.lines))

    def act_save(self): self.save()
    def act_saveas(self): self.save_as()

    def act_info(self):
        s = self.doc.stats()
        pages.message(self.stdscr, f"{self.path or 'teks baru (belum disimpan)'}\n"
                      f"Baris: {s['lines']}\nKata: {s['words']}\nHuruf: {s['chars']}\n"
                      f"Heading: {s['headings']}", "info", "Info")

    def act_open(self):
        if not self._discard_ok():
            return
        choice = pick_note(self.stdscr, self.vault)
        if choice == "new":
            self.new()
        elif choice is not None:
            try:
                self.load(choice)
            except (noteio.NoteError, OSError) as exc:
                pages.message(self.stdscr, str(exc), "err")

    def act_quit(self):
        if self.doc.dirty:
            if pages.confirm(self.stdscr, "KELUAR", "Simpan perubahan sebelum keluar?", yes="Simpan"):
                if not self.save():
                    return None
            elif not pages.confirm(self.stdscr, "KELUAR", "Buang semua perubahan?", yes="Buang"):
                return None
        return "exit"

    # ---- events -----------------------------------------------------------
    def _run_action(self, name):
        return getattr(self, "act_" + name)()

    def _tap_text(self, y, x):
        vy = self.top + (y - 3)
        if not 0 <= vy < len(self.rows):
            return
        row, col = position_at(self.rows, self.doc.lines, vy, x - 3)
        if self.typing:
            self.doc.collapse((row, col))
        elif self.range_mode:
            self.doc.extend_to_word(row, col)
            self.range_mode = False
        else:
            self.doc.select_word_at(row, col)

    def on_click(self, y, x):
        for y0, x0, x1, kind, value in self.hits:
            if y == y0 and x0 <= x < x1:
                if kind == "tab":
                    self.tab = value
                elif kind == "more":
                    self.page[TABS[self.tab]] += 1
                else:
                    return self._run_action(value)
                return None
        if 3 <= y < 3 + self.text_rows:
            self._tap_text(y, x)
        return None

    def on_key(self, key):
        doc = self.doc
        if key == "\x13":
            return self._run_action("save")
        if key == "\x1a":
            return self._run_action("undo")
        if key == "\x19":
            return self._run_action("redo")
        if key == "\x1b":
            return self._run_action("quit")
        moves = {curses.KEY_UP: "up", curses.KEY_DOWN: "down",
                 curses.KEY_LEFT: "left", curses.KEY_RIGHT: "right"}
        if key in moves:
            return self._run_action(moves[key])
        if self.typing:
            if key in pages.ENTER_KEYS:
                doc.insert_text("\n", coalesce=True)
            elif key in pages.BACKSPACE_KEYS:
                doc.backspace()
            elif key == "\t":
                doc.insert_text("    ", coalesce=True)
            elif isinstance(key, str) and key.isprintable():
                doc.insert_text(key, coalesce=True)
        return None

    def run(self):
        pages.set_mouse(True)
        while True:
            pages.ensure_size(self.stdscr, MIN_W, MIN_H)
            self.draw()
            self.stdscr.timeout(-1)
            kind, value = pages.read_event(self.stdscr)
            if kind in ("none", "resize"):
                continue
            self.flash = ""
            result = None
            if kind == "wheel":
                self.top += value * 3
                self.follow = False
                continue
            self.follow = True
            if kind == "click":
                result = self.on_click(*value)
            elif kind == "key":
                result = self.on_key(value)
            if result == "exit":
                return


def run_editor(stdscr, vault):
    vault = Path(vault) if vault else None
    try:
        choice = pick_note(stdscr, vault)
        if choice is None:
            return
        editor = Editor(stdscr, vault)
        if choice == "new":
            editor.new()
        else:
            try:
                editor.load(choice)
            except (noteio.NoteError, OSError) as exc:
                pages.message(stdscr, str(exc), "err")
                return
        editor.run()
    finally:
        pages.restore_idle(stdscr)
