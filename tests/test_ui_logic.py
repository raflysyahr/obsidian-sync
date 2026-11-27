import unittest

from ui.logic import (build_status_lines, explain_error, fit_status_lines, format_duration,
                      layout_buttons, mask_url, next_step, parse_interval, shorten,
                      validate_remote, wrap_lines)


def make_view(**overrides):
    view = {
        "vault": "/vault", "repository": "Ready", "branch": "main",
        "remote": "https://github.com/u/r.git", "changes": 0, "auto_enabled": False,
        "interval": 300, "last_sync_at": "2026-11-26 10:00:00 WIB",
        "last_sync_status": "Success", "last_sync_message": "", "next_in": "",
    }
    view.update(overrides)
    return view


class TextHelpersTests(unittest.TestCase):
    def test_shorten_keeps_both_ends_of_paths(self):
        text = "/storage/emulated/0/Documents/Obsidian/MyVault"
        result = shorten(text, 20)
        self.assertEqual(len(result), 20)
        self.assertTrue(result.startswith("/sto"))
        self.assertTrue(result.endswith("MyVault"))
        self.assertIn("…", result)

    def test_shorten_leaves_short_text_and_handles_tiny_widths(self):
        self.assertEqual(shorten("abc", 10), "abc")
        self.assertEqual(shorten("abcdef", 3), "abc")
        self.assertEqual(shorten("abcdef", 0), "")
        self.assertEqual(shorten("abcdefghij", 6, "end"), "abcde…")

    def test_mask_url_hides_credentials(self):
        self.assertEqual(mask_url("https://me:tok3n@github.com/a/b.git"),
                         "https://***@github.com/a/b.git")
        self.assertEqual(mask_url("git@github.com:a/b.git"), "git@github.com:a/b.git")

    def test_format_duration(self):
        self.assertEqual(format_duration(45), "45s")
        self.assertEqual(format_duration(300), "5m")
        self.assertEqual(format_duration(1000), "16m 40s")
        self.assertEqual(format_duration(7200), "2h")
        self.assertEqual(format_duration(5400), "1h 30m")

    def test_wrap_lines_handles_newlines_and_literal_backslash_n(self):
        self.assertEqual(wrap_lines("a\\nb", 20), ["a", "b"])
        self.assertEqual(wrap_lines("one\n\ntwo", 20), ["one", "", "two"])
        self.assertTrue(all(len(line) <= 10 for line in wrap_lines("x" * 35, 10)))


class InputValidationTests(unittest.TestCase):
    def test_parse_interval_accepts_units(self):
        self.assertEqual(parse_interval("300"), (300, None))
        self.assertEqual(parse_interval("5m"), (300, None))
        self.assertEqual(parse_interval(" 1H "), (3600, None))

    def test_parse_interval_rejects_bad_values(self):
        for raw in ("", "abc", "30", "90000", "-5", "1.5m"):
            seconds, error = parse_interval(raw)
            self.assertIsNone(seconds, raw)
            self.assertTrue(error, raw)

    def test_validate_remote_matches_git_engine_rules(self):
        self.assertIsNone(validate_remote("https://github.com/u/r.git"))
        self.assertIsNone(validate_remote("git@github.com:u/r.git"))
        self.assertIsNone(validate_remote("ssh://git@github.com/u/r.git"))
        self.assertTrue(validate_remote(""))
        self.assertTrue(validate_remote("github.com/u/r"))
        self.assertTrue(validate_remote("https://user:tok@github.com/u/r.git"))
        self.assertTrue(validate_remote("https://github.com/u/r .git"))


class GuidanceTests(unittest.TestCase):
    def test_next_step_walks_through_setup(self):
        self.assertEqual(next_step(make_view(vault="(not configured)"))[0], "V")
        self.assertEqual(next_step(make_view(repository="Not initialized"))[0], "I")
        self.assertEqual(next_step(make_view(remote="(not configured)"))[0], "R")
        self.assertEqual(next_step(make_view(last_sync_at="Never", changes=3))[0], "C")
        self.assertEqual(next_step(make_view(last_sync_at="Never", changes=0))[0], "P")
        self.assertEqual(next_step(make_view(changes=2))[0], "N")
        self.assertEqual(next_step(make_view())[2], "ok")
        self.assertIsNone(next_step(make_view(repository="Checking…")))

    def test_explain_error_gives_actionable_tips(self):
        self.assertIn("commit", explain_error("Vault has local changes. Commit ...").lower())
        self.assertIn("login", explain_error("fatal: could not read Username for x").lower())
        self.assertIn("Press R", explain_error("GitHub remote 'origin' is not configured."))
        self.assertIn("git config --global user.name",
                      explain_error("*** Please tell me who you are."))
        self.assertEqual(explain_error("something unrelated"), "")


class StatusLineTests(unittest.TestCase):
    def flatten(self, lines):
        """Accept raw (priority, segments) rows or already-fitted segment lists."""
        rows = [row[1] if isinstance(row, tuple) and isinstance(row[0], int) else row
                for row in lines]
        return ["".join(text for text, _ in segments) for segments in rows]

    def test_status_lines_show_git_remote_and_auto_state(self):
        view = make_view(changes=3, auto_enabled=True, next_in="2m 05s")
        text = "\n".join(self.flatten(fit_status_lines(build_status_lines(view, 80), 10)))
        self.assertIn("Ready · main · 3 changes", text)
        self.assertIn("ON · every 5m · next in 2m 05s", text)
        self.assertIn("https://github.com/u/r.git", text)

    def test_credentials_never_appear_in_status(self):
        view = make_view(remote="https://me:secret@github.com/u/r.git")
        text = "\n".join(self.flatten(build_status_lines(view, 80)))
        self.assertNotIn("secret", text)

    def test_small_screens_keep_the_most_important_rows(self):
        lines = build_status_lines(make_view(), 40)
        kept = self.flatten(fit_status_lines(lines, 2))
        self.assertEqual(len(kept), 2)
        self.assertTrue(kept[0].startswith("Git"))
        self.assertTrue(kept[1].startswith("Auto"))
        self.assertEqual(fit_status_lines(lines, 0), [])

    def test_error_message_row_only_after_failed_sync(self):
        failed = make_view(last_sync_status="Error", last_sync_message="boom")
        self.assertTrue(any("boom" in row for row in self.flatten(build_status_lines(failed, 80))))
        self.assertFalse(any("boom" in row for row in
                             self.flatten(build_status_lines(make_view(last_sync_message="boom"), 80))))


class ButtonLayoutTests(unittest.TestCase):
    def test_everything_fits_on_one_page(self):
        pages = layout_buttons([6, 6, 6], avail=40)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0], [(0, 0, 0), (0, 7, 1), (0, 14, 2)])

    def test_wraps_to_second_row_then_pages_with_room_for_more_button(self):
        widths = [8] * 12
        pages = layout_buttons(widths, avail=30, rows=2)
        self.assertGreater(len(pages), 1)
        placed = sorted(i for page in pages for _, _, i in page)
        self.assertEqual(placed, list(range(12)))          # nothing lost
        for page in pages:
            for row, x, index in page:
                limit = 30 - (4 if row == 1 else 0)
                self.assertLessEqual(x + widths[index], limit)

    def test_oversized_button_is_still_placed(self):
        pages = layout_buttons([50], avail=20)
        self.assertEqual(pages, [[(0, 0, 0)]])


if __name__ == "__main__":
    unittest.main()
