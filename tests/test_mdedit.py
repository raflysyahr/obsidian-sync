import os
import tempfile
import unittest
from pathlib import Path

from core import noteio
from core.mdedit import (Doc, autoformat_lines, line_styles, locate, parse_block, position_at,
                         render_preview, strip_inline, wrap_rows)

TEXT = "Rapat mingguan tim produk\nHasil utama kecepatan rilis naik karena review dipercepat.\n"


def doc_with(text=TEXT):
    return Doc(text)


def select_words(doc, row, first, count):
    """Select `count` words of `row` starting at word index `first` (touch simulation)."""
    line = doc.lines[row]
    words = list(__import__("re").finditer(r"\S+", line))
    doc.anchor = (row, words[first].start())
    doc.head = (row, words[first + count - 1].end())


class SelectionTests(unittest.TestCase):
    def test_tap_selects_word_and_buttons_extend_by_words(self):
        d = doc_with()
        self.assertTrue(d.select_word_at(1, 14))  # inside "kecepatan"
        self.assertEqual(d.selected_text(), "kecepatan")
        d.extend_right()
        d.extend_right()
        self.assertEqual(d.selected_text(), "kecepatan rilis naik")
        d.extend_left()
        self.assertEqual(d.selected_text(), "utama kecepatan rilis naik")

    def test_extend_crosses_lines(self):
        d = doc_with()
        d.select_word_at(0, 20)  # "produk"
        d.extend_right()
        self.assertEqual(d.selected_text(), "produk\nHasil")
        d.extend_left()
        self.assertEqual(d.selected_text(), "tim produk\nHasil")

    def test_range_tap_covers_both_words(self):
        d = doc_with()
        d.select_word_at(1, 0)
        self.assertTrue(d.extend_to_word(1, 22))  # somewhere in "rilis"
        self.assertTrue(d.selected_text().startswith("Hasil utama"))
        self.assertTrue(d.selected_text().endswith("kecepatan rilis") or "rilis" in d.selected_text())

    def test_tap_on_whitespace_only_places_cursor(self):
        d = Doc("a  b\n")
        self.assertFalse(d.select_word_at(0, 2))
        self.assertIsNone(d.selection)
        # tapping the space right after a word still picks that word (fat-finger friendly)
        self.assertTrue(d.select_word_at(0, 1))
        self.assertEqual(d.selected_text(), "a")

    def test_sentence_line_paragraph_all(self):
        d = Doc("Satu dua. Tiga empat! Lima?\n\nBaris A\nBaris B\n")
        d.select_sentence(0, 12)
        self.assertEqual(d.selected_text(), "Tiga empat!")
        d.select_paragraph(3)
        self.assertEqual(d.selected_text(), "Baris A\nBaris B")
        d.select_line(0)
        self.assertEqual(d.selected_text(), "Satu dua. Tiga empat! Lima?")
        d.select_all()
        self.assertEqual(d.selected_text(), "Satu dua. Tiga empat! Lima?\n\nBaris A\nBaris B")

    def test_decimal_point_does_not_split_sentence(self):
        d = Doc("Harga 3.5 juta naik. Besok turun.\n")
        d.select_sentence(0, 3)
        self.assertEqual(d.selected_text(), "Harga 3.5 juta naik.")

    def test_step_word_and_line(self):
        d = doc_with()
        d.select_word_at(0, 0)
        d.step_word(1)
        self.assertEqual(d.selected_text(), "mingguan")
        d.step_word(-1)
        self.assertEqual(d.selected_text(), "Rapat")
        d.step_line(1)
        self.assertEqual(d.head[0], 1)


class BlockTests(unittest.TestCase):
    def test_three_words_become_heading_rest_stays_paragraph(self):
        d = doc_with()
        select_words(d, 1, 2, 3)  # "kecepatan rilis naik"
        d.set_block("heading", 2)
        self.assertEqual(d.lines, [
            "Rapat mingguan tim produk", "Hasil utama", "",
            "## kecepatan rilis naik", "", "karena review dipercepat."])
        # selection now covers the heading TEXT, not the '## ' marker
        self.assertEqual(d.selected_text(), "kecepatan rilis naik")

    def test_whole_line_heading_toggle_and_level_change(self):
        d = doc_with()
        d.select_line(0)
        d.set_block("heading", 1)
        self.assertEqual(d.lines[0], "# Rapat mingguan tim produk")
        d.set_block("heading", 3)
        self.assertEqual(d.lines[0], "### Rapat mingguan tim produk")
        d.set_block("heading", 3)  # same level again -> back to paragraph
        self.assertEqual(d.lines[0], "Rapat mingguan tim produk")

    def test_paragraph_strips_prefix_and_joins_lines(self):
        d = Doc("## Judul\n- satu\n- dua\n")
        d.select_all()
        d.set_block("para")
        self.assertEqual(d.lines, ["Judul satu dua"])

    def test_bullets_ordered_tasks_quote_toggle(self):
        d = Doc("alpha\nbeta\ngamma\n")
        d.select_all()
        d.set_block("bullet")
        self.assertEqual(d.lines, ["- alpha", "- beta", "- gamma"])
        d.set_block("ordered")
        self.assertEqual(d.lines, ["1. alpha", "2. beta", "3. gamma"])
        d.set_block("task")
        self.assertEqual(d.lines, ["- [ ] alpha", "- [ ] beta", "- [ ] gamma"])
        d.set_block("task")  # toggle off
        self.assertEqual(d.lines, ["alpha", "beta", "gamma"])
        d.set_block("quote")
        self.assertEqual(d.lines, ["> alpha", "> beta", "> gamma"])
        d.set_block("quote")
        self.assertEqual(d.lines, ["alpha", "beta", "gamma"])

    def test_partial_selection_inside_list_item_converts_cleanly(self):
        d = Doc("- item satu dua\n")
        select_words(d, 0, 2, 1)  # "dua"... words are '-', 'item', 'satu', 'dua'
        d.anchor, d.head = (0, 12), (0, 15)
        d.set_block("heading", 2)
        self.assertEqual(d.lines, ["- item satu", "", "## dua"])

    def test_task_check_toggle(self):
        d = Doc("- [ ] a\n- [x] b\nplain\n")
        d.select_all()
        self.assertEqual(d.toggle_task_check(), 2)
        self.assertEqual(d.lines, ["- [x] a", "- [ ] b", "plain"])

    def test_code_block_wrap_and_unwrap(self):
        d = Doc("print(1)\nprint(2)\n")
        d.select_all()
        d.set_block("code")
        self.assertEqual(d.lines, ["```", "print(1)", "print(2)", "```"])
        d.select_all()
        d.set_block("code")
        self.assertEqual(d.lines, ["print(1)", "print(2)"])

    def test_callout_and_hr(self):
        d = Doc("catatan penting\n")
        d.select_line(0)
        d.set_block("callout")
        self.assertEqual(d.lines, ["> [!note]", "> catatan penting"])
        d2 = Doc("a\nb\n")
        d2.collapse((0, 0))
        d2.insert_hr()
        self.assertEqual(d2.lines, ["a", "", "---", "", "b"])

    def test_indent_outdent_and_move_lines(self):
        d = Doc("- a\n- b\n- c\n")
        d.select_line(1)
        d.indent(1)
        self.assertEqual(d.lines[1], "    - b")
        d.indent(-1)
        self.assertEqual(d.lines[1], "- b")
        d.move_lines(1)
        self.assertEqual(d.lines, ["- a", "- c", "- b"])
        d.move_lines(-1)
        d.move_lines(-1)
        self.assertEqual(d.lines, ["- b", "- a", "- c"])
        self.assertFalse(d.move_lines(-1))


class InlineTests(unittest.TestCase):
    def test_bold_italic_toggle_and_combination(self):
        d = Doc("halo dunia\n")
        d.select_word_at(0, 1)
        d.toggle_inline("**")
        self.assertEqual(d.lines[0], "**halo** dunia")
        d.toggle_inline("*")
        self.assertEqual(d.lines[0], "***halo*** dunia")
        d.toggle_inline("**")
        self.assertEqual(d.lines[0], "*halo* dunia")
        d.toggle_inline("*")
        self.assertEqual(d.lines[0], "halo dunia")

    def test_multi_word_and_multi_line_and_code(self):
        d = Doc("satu dua\ntiga empat\n")
        d.set_selection((0, 5), (1, 4))
        d.toggle_inline("==")
        self.assertEqual(d.lines, ["satu ==dua==", "==tiga== empat"])
        d.toggle_inline("==")
        self.assertEqual(d.lines, ["satu dua", "tiga empat"])
        d.select_word_at(0, 0)
        d.toggle_inline("`")
        self.assertEqual(d.lines[0], "`satu` dua")

    def test_wikilink_and_link(self):
        d = Doc("lihat Catatan Harian\n")
        d.set_selection((0, 6), (0, 20))
        d.toggle_inline("[[", "]]")
        self.assertEqual(d.lines[0], "lihat [[Catatan Harian]]")
        d.toggle_inline("[[", "]]")
        self.assertEqual(d.lines[0], "lihat Catatan Harian")
        d.select_word_at(0, 0)
        d.toggle_link("https://x.id")
        self.assertEqual(d.lines[0], "[lihat](https://x.id) Catatan Harian")
        self.assertTrue(d.selection_is_link())
        d.toggle_link()
        self.assertEqual(d.lines[0], "lihat Catatan Harian")

    def test_no_word_no_change(self):
        d = Doc("\n")
        self.assertFalse(d.toggle_inline("**"))

    def test_clear_format(self):
        d = Doc("## **Judul** dan `kode` [[Nota]]\n- *miring* ~~coret~~\n")
        d.select_all()
        d.clear_format()
        self.assertEqual(d.lines, ["Judul dan kode Nota", "miring coret"])


class EditingTests(unittest.TestCase):
    def test_undo_redo_roundtrip(self):
        d = doc_with()
        before = list(d.lines)
        d.select_line(0)
        d.set_block("heading", 1)
        self.assertTrue(d.dirty)
        self.assertTrue(d.undo())
        self.assertEqual(d.lines, before)
        self.assertFalse(d.dirty)
        self.assertTrue(d.redo())
        self.assertEqual(d.lines[0], "# Rapat mingguan tim produk")
        self.assertFalse(Doc("x").undo())

    def test_typing_coalesces_into_one_undo_step(self):
        d = Doc("")
        for ch in "halo":
            d.insert_text(ch, coalesce=True)
        self.assertEqual(d.lines, ["halo"])
        d.undo()
        self.assertEqual(d.lines, [""])

    def test_insert_multiline_paste_and_backspace_join(self):
        d = Doc("ab\n")
        d.collapse((0, 1))
        d.insert_text("X\nY\nZ")
        self.assertEqual(d.lines, ["aX", "Y", "Zb"])
        self.assertEqual(d.head, (2, 1))
        d.collapse((1, 0))
        d.backspace()
        self.assertEqual(d.lines, ["aXY", "Zb"])

    def test_copy_cut_paste_and_delete(self):
        d = Doc("satu dua tiga\n")
        d.set_selection((0, 5), (0, 8))
        d.cut()
        self.assertEqual(d.lines[0], "satu  tiga")
        d.collapse((0, 0))
        d.paste()
        self.assertEqual(d.lines[0], "duasatu  tiga")
        d.collapse((0, 0))
        d.delete_selection()  # no selection: removes the line
        self.assertEqual(d.lines, [""])

    def test_replace_all_whole_doc_and_selection(self):
        d = Doc("a b a\nc a\n")
        self.assertEqual(d.replace_all("a", "Z"), 3)
        self.assertEqual(d.lines, ["Z b Z", "c Z"])
        d.set_selection((0, 0), (0, 5))
        self.assertEqual(d.replace_all("Z", "q"), 2)
        self.assertEqual(d.lines, ["q b q", "c Z"])

    def test_join_lines_and_sentences_to_list(self):
        d = Doc("kalimat yang\nterpotong rapi.\n\nparagraf dua\nterpotong.\n")
        d.select_all()
        d.join_lines()
        self.assertEqual(d.lines, ["kalimat yang terpotong rapi.", "", "paragraf dua terpotong."])
        d2 = Doc("Satu dua. Tiga empat! Lima enam.\n")
        d2.select_line(0)
        d2.sentences_to_list()
        self.assertEqual(d2.lines, ["- Satu dua.", "- Tiga empat!", "- Lima enam."])
        d2.select_all()
        d2.sentences_to_list(ordered=True)
        self.assertEqual(d2.lines[0], "1. Satu dua.")

    def test_tidy(self):
        d = Doc("# Judul   besar  \ntext  dengan   spasi\n\n\n\nsisa\n```\nkeep   this\n```\n")
        self.assertTrue(d.tidy())
        self.assertEqual(d.lines, ["# Judul besar", "", "text dengan spasi", "", "sisa",
                                   "```", "keep   this", "```"])
        self.assertEqual(d.tidy(), 0)

    def test_text_roundtrip_and_empty(self):
        self.assertEqual(Doc("a\nb\n").text(), "a\nb\n")
        self.assertEqual(Doc("").text(), "")
        self.assertEqual(Doc("a\r\nb\r\n").lines, ["a", "b"])
        self.assertEqual(Doc("a\tb").lines, ["a    b"])

    def test_words_are_never_changed_by_formatting(self):
        d = Doc(TEXT)
        original = " ".join(TEXT.split())
        d.select_all()
        for op in (lambda: d.set_block("bullet"), lambda: d.toggle_inline("**"),
                   lambda: d.set_block("quote"), lambda: d.clear_format(),
                   lambda: d.set_block("heading", 2)):
            op()
        plain = " ".join(strip_inline(" ".join(d.lines)).replace("#", "").replace(">", "").split())
        self.assertEqual(plain, original)


class AutoformatTests(unittest.TestCase):
    def test_bullets_numbers_and_headings(self):
        lines = ["Laporan Bulanan", "", "• satu", "– dua", "* tiga", "1) empat", "",
                 "RINGKASAN", "isi teks biasa.", "", "Bab II", "Catatan:", "",
                 "Kalimat panjang sekali yang jelas bukan judul karena diakhiri titik."]
        new, changes = autoformat_lines(lines)
        self.assertEqual(new[0], "# Laporan Bulanan")
        self.assertEqual(new[2:6], ["- satu", "- dua", "- tiga", "1. empat"])
        self.assertEqual(new[7], "## RINGKASAN")
        self.assertEqual(new[10], "## Bab II")
        self.assertEqual(new[11], "Catatan:")  # not preceded by blank line -> untouched
        self.assertEqual(new[-1], lines[-1])
        self.assertGreaterEqual(changes, 6)

    def test_code_fences_and_existing_markdown_untouched(self):
        lines = ["```", "• bukan list", "```", "# Sudah", "- ok"]
        self.assertEqual(autoformat_lines(lines), (lines, 0))


class GeometryTests(unittest.TestCase):
    def test_wrap_and_position_mapping(self):
        lines = ["satu dua tiga empat lima", "", "x"]
        rows = wrap_rows(lines, 10)
        self.assertEqual(rows[0], (0, 0, 9))  # "satu dua " (breaks after the space)
        self.assertEqual(rows[-1], (2, 0, 1))
        self.assertIn((1, 0, 0), rows)
        i = locate(rows, 0, 12)
        r, s, e = rows[i]
        self.assertTrue(s <= 12 < e)
        self.assertEqual(position_at(rows, lines, 0, 3), (0, 3))
        self.assertEqual(position_at(rows, lines, 99, 0), (2, 0))

    def test_parse_and_styles_and_preview(self):
        self.assertEqual(parse_block("## T").kind, "heading")
        self.assertEqual(parse_block("---").kind, "hr")
        self.assertEqual(parse_block("- [x] t").kind, "task")
        self.assertEqual(parse_block("3) x").kind, "ordered")
        styles = line_styles(["# h", "- a", "```", "code", "```", "> q"])
        self.assertEqual([s for s, _ in styles], ["title", "text", "accent", "accent", "accent", "ok"])
        preview = render_preview(["# Judul", "- **a**", "- [x] b", "> q"])
        texts = [t for t, _ in preview]
        self.assertIn("JUDUL", texts)
        self.assertIn("• a", texts)
        self.assertIn("☑ b", texts)


class NoteIoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_write_preserves_crlf_and_is_atomic(self):
        path = self.vault / "a.md"
        path.write_bytes("satu\r\ndua\r\n".encode())
        text, newline = noteio.read_note(path)
        self.assertEqual(newline, "\r\n")
        noteio.write_note(path, Doc(text).text(), newline)
        self.assertEqual(path.read_bytes(), b"satu\r\ndua\r\n")
        self.assertEqual([p.name for p in self.vault.iterdir()], ["a.md"])

    def test_rejects_binary_and_large_files(self):
        binary = self.vault / "b.md"
        binary.write_bytes(b"\xff\xfe\x00bad")
        with self.assertRaises(noteio.NoteError):
            noteio.read_note(binary)
        big = self.vault / "big.md"
        big.write_bytes(b"x" * (noteio.MAX_BYTES + 1))
        with self.assertRaises(noteio.NoteError):
            noteio.read_note(big)

    def test_backup_goes_outside_the_vault(self):
        note = self.vault / "n.md"
        note.write_text("isi lama\n")
        backup = noteio.backup_before_overwrite(note, self.vault)
        self.assertEqual(backup.read_text(), "isi lama\n")
        self.assertEqual(backup.parent, self.root / ".obsidian-sync-backups" / "edits")
        self.assertIsNone(noteio.backup_before_overwrite(self.vault / "none.md", self.vault))

    def test_list_notes_skips_hidden_folders(self):
        (self.vault / ".git").mkdir()
        (self.vault / ".git" / "x.md").write_text("x")
        (self.vault / "sub").mkdir()
        (self.vault / "sub" / "b.txt").write_text("b")
        (self.vault / "a.md").write_text("a")
        (self.vault / "img.png").write_bytes(b"\x89PNG")
        names = sorted(p.name for p in noteio.list_notes(self.vault))
        self.assertEqual(names, ["a.md", "b.txt"])

    def test_txt_saves_as_new_md_without_touching_original(self):
        txt = self.vault / "catatan.txt"
        txt.write_text("a")
        self.assertEqual(noteio.suggest_save_path(txt), self.vault / "catatan.md")
        (self.vault / "catatan.md").write_text("sudah ada")
        self.assertEqual(noteio.suggest_save_path(txt), self.vault / "catatan.formatted.md")
        md = self.vault / "x.md"
        self.assertEqual(noteio.suggest_save_path(md), md)


if __name__ == "__main__":
    unittest.main()
