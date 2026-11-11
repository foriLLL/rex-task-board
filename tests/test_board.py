import os
import re
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "rex-task-board", "scripts"))
import board  # noqa: E402

ANSI = re.compile(r"\033\[[0-9;?]*[A-Za-z]")


def plain(lines):
    return [ANSI.sub("", l) for l in lines]


class FakeReader:
    def __init__(self, texts):
        self.prompts = [{"id": str(n), "at": datetime.now(), "t": t} for n, t in enumerate(texts)]

    def poll(self):
        return self.prompts


STATE = {"title": "T", "lang": "zh", "attention": [], "phases": [{"name": "p", "items": [{"s": "done", "t": "a"}]}]}


class FrameTest(unittest.TestCase):
    def test_history_docks_to_bottom_within_ratio(self):
        view = board.View()
        lines = plain(view.frame(STATE, FakeReader([f"q{n}" for n in range(30)]), 36, 40, 0))
        self.assertEqual(len(lines), 40)
        header = next(i for i, l in enumerate(lines) if "提问 30" in l)
        self.assertEqual(40 - header, int(40 * 0.3))  # header + rows fill exactly 30%
        self.assertIn("q29", lines[-1])               # newest prompt sits on the last row
        self.assertIn("↑", lines[header])

    def test_short_history_takes_only_its_rows(self):
        lines = plain(board.View().frame(STATE, FakeReader(["only"]), 36, 40, 0))
        self.assertIn("提问 1", lines[-2])
        self.assertIn("only", lines[-1])

    def test_no_history_no_section(self):
        lines = plain(board.View().frame(STATE, None, 36, 40, 0))
        self.assertFalse(any("提问" in l for l in lines))

    def test_scroll_up_holds_position_when_new_prompts_arrive(self):
        view = board.View()
        reader = FakeReader([f"q{n}" for n in range(30)])
        view.frame(STATE, reader, 36, 40, 0)
        view.scroll("history", 5)
        before = plain(view.frame(STATE, reader, 36, 40, 0))[-1]
        reader.prompts.append({"id": "x", "at": datetime.now(), "t": "new"})
        after = plain(view.frame(STATE, reader, 36, 40, 0))
        self.assertEqual(after[-1], before)
        view.scroll("history", -10 ** 6)  # back to the newest
        self.assertIn("new", plain(view.frame(STATE, reader, 36, 40, 0))[-1])

    def test_long_prompt_wraps_then_cuts(self):
        lines = plain(board.View().frame(STATE, FakeReader(["很长" * 60]), 36, 40, 0))
        self.assertTrue(lines[-1].rstrip().endswith("…"))
        self.assertTrue(lines[-2].lstrip().startswith(datetime.now().strftime("%H:%M")))

    def test_notes_replace_prompt_text(self):
        state = dict(STATE, query_notes={"0": "short label"})
        lines = plain(board.View().frame(state, FakeReader(["raw prompt"]), 36, 40, 0))
        self.assertIn("short label", lines[-1])

    def test_tall_task_area_scrolls_under_fixed_header(self):
        items = [{"s": "todo", "t": f"i{n}"} for n in range(60)]
        items[0]["s"] = "run"
        state = dict(STATE, attention=[{"k": "watch", "t": "注意"}],
                     phases=[{"name": "p", "items": items}])
        view = board.View()
        lines = plain(view.frame(state, FakeReader(["q"]), 36, 30, 0))
        self.assertTrue(any("还有" in l and "↓" in l for l in lines))
        view.scroll("top", -6)
        lines = plain(view.frame(state, FakeReader(["q"]), 36, 30, 0))
        self.assertTrue(any("↑ 还有" in l for l in lines))
        self.assertTrue(any("注意" in l for l in lines))  # the 需要你 box stays put


def phase(name, n, s="done", note=True):
    items = [{"s": s, "t": f"{name}{k}"} for k in range(n)]
    if note:
        for it in items:
            it["note"] = "n"
    return {"name": name, "items": items}


def is_edge_row(line):
    return line.strip().startswith("╰") or line.strip() == "▼"


def view_top(lines):
    """Index of the first row under the ↑ hint."""
    return next(i for i, l in enumerate(lines) if "↑ 还有" in l) + 1


def row_of(lines, text):
    return next(i for i, l in enumerate(lines) if text in l) + 1  # 1-based screen row


class FoldClickTest(unittest.TestCase):
    STATE = {"title": "T", "lang": "zh", "attention": [],
             "phases": [phase("A", 4), phase("B", 4), phase("C", 2, "todo")]}

    def test_folds_oldest_first_only_as_needed(self):
        lines = plain(board.View().frame(self.STATE, None, 36, 28, 0))
        self.assertTrue(any("▸ 1 A" in l for l in lines))
        self.assertTrue(any("╭─ 2 B" in l for l in lines))  # still fits: B stays open

    def test_click_opens_folded_and_folds_open(self):
        view = board.View()
        lines = plain(view.frame(self.STATE, None, 36, 28, 0))
        self.assertTrue(view.click(row_of(lines, "▸ 1 A")))
        lines = plain(view.frame(self.STATE, None, 36, 28, 0))
        self.assertTrue(any("╭─ 1 A" in l for l in lines))   # opened, even if it now overflows
        self.assertTrue(view.click(row_of(lines, "╭─ 1 A")))
        lines = plain(view.frame(self.STATE, None, 36, 28, 0))
        self.assertTrue(any("▸ 1 A" in l for l in lines))
        self.assertTrue(view.click(row_of(lines, "╭─ 3 C")))  # unfinished phases fold too
        lines = plain(view.frame(self.STATE, None, 36, 28, 0))
        self.assertTrue(any("▸ 3 C" in l and "0/2" in l for l in lines))

    def test_click_prompt_shows_it_in_full(self):
        view = board.View()
        reader = FakeReader(["短", "很长" * 40, "最后"])
        lines = plain(view.frame(STATE, reader, 36, 40, 0))
        cut = next(i for i, l in enumerate(lines) if l.rstrip().endswith("…")) + 1
        self.assertTrue(view.click(cut))
        lines = plain(view.frame(STATE, reader, 36, 40, 0))
        self.assertFalse(any(l.rstrip().endswith("…") for l in lines[view.split:]))
        self.assertFalse(view.click(row_of(lines, "最后")))  # nothing hidden: nothing to toggle
        self.assertTrue(view.click(row_of(lines, "很长很长")))
        lines = plain(view.frame(STATE, reader, 36, 40, 0))
        self.assertTrue(any(l.rstrip().endswith("…") for l in lines))


class CompactTest(unittest.TestCase):
    def long_phase(self):
        items = [{"s": "done", "t": f"d{n}", "note": "n"} for n in range(13)]
        items.insert(8, {"s": "todo", "t": "left", "note": "waiting"})
        return {"name": "big", "items": items}

    def test_finished_items_collapse_before_anything_scrolls(self):
        state = dict(STATE, phases=[phase("A", 3), self.long_phase(), phase("B", 2, "done", False),
                                    {"name": "now", "items": [{"s": "run", "t": "working"}]}])
        view = board.View()
        lines = plain(view.frame(state, FakeReader(["q"]), 36, 24, 0))
        text = "\n".join(lines)
        self.assertIn("13 项已完成", text)
        self.assertIn("left", text)
        self.assertIn("waiting", text)        # unfinished items keep their notes
        self.assertIn("working", text)        # the running phase is fully on screen
        self.assertNotIn("还有", text)
        view.click(row_of(lines, "13 项已完成"))
        text = "\n".join(plain(view.frame(state, FakeReader(["q"]), 36, 60, 0)))
        self.assertIn("d12", text)

    def test_follows_running_phase_and_never_strands_a_title(self):
        first = {"name": "first", "items": [{"s": "done", "t": "x"}] + [{"s": "todo", "t": f"t{n}"} for n in range(40)]}
        state = dict(STATE, phases=[first, {"name": "now", "items": [{"s": "run", "t": "working"}]}])
        view = board.View()
        view.pinned = {"0:first": True}  # opened by hand, so it cannot fold away
        lines = plain(view.frame(state, None, 36, 20, 0))
        self.assertTrue(any("working" in l for l in lines))
        self.assertTrue(any("↑ 还有" in l for l in lines))
        self.assertFalse(is_edge_row(lines[view_top(lines)]))
        tall = dict(STATE, phases=[first, {"name": "next", "items": [{"s": "todo", "t": "later"}]}])
        view = board.View()
        view.pinned = {"0:first": True}
        lines = plain(view.frame(tall, None, 36, 20, 0))
        hint = next(l for l in lines if "↓ 还有" in l)
        self.assertIn("2 next", hint)        # the hint names what is hidden
        self.assertFalse(any("╭─ 2 next" in l for l in lines))
        above = lines[lines.index(hint) - 1]
        self.assertFalse(above.strip() == "▼")  # no arrow pointing at nothing

    def test_history_marks_prompt_cut_off_above(self):
        view = board.View()
        reader = FakeReader([f"a{n}" for n in range(4)] + ["很长" * 15] + [f"q{n}" for n in range(6)])
        lines = plain(view.frame(STATE, reader, 36, 27, 0))  # 7 rows show 12: the cut lands in the long one
        body = lines[view.split:]
        self.assertRegex(body[0], r"^ \d\d:\d\d …")  # its time and a leading … stand in for the hidden start
        self.assertIn("q0", body[1])


class LangTest(unittest.TestCase):
    def test_lang_from_state_then_locale(self):
        self.assertEqual(board.pick_lang({"lang": "zh-CN"}, {}), "zh")
        self.assertEqual(board.pick_lang({"lang": "en"}, {"LANG": "zh_CN.UTF-8"}), "en")
        self.assertEqual(board.pick_lang({}, {"LANG": "zh_CN.UTF-8"}), "zh")
        self.assertEqual(board.pick_lang({}, {"LC_ALL": "C", "LANG": "zh_CN.UTF-8"}), "en")
        self.assertEqual(board.pick_lang({"lang": "fr"}, {}), "en")

    def test_english_labels(self):
        state = dict(STATE, lang="en", attention=[{"k": "decide", "t": "ship?"}],
                     phases=[{"name": "p", "items": [{"s": "done", "t": f"d{n}"} for n in range(9)]
                              + [{"s": "run", "t": "now"}]}])
        text = "\n".join(plain(board.View().frame(state, FakeReader(["q"]), 36, 14, 0)))
        for label in ("Needs you", "1 to decide", "running", "9 done", "Prompts 1"):
            self.assertIn(label, text)


class InputTest(unittest.TestCase):
    def test_left_press_clicks_release_does_not(self):
        view = board.View()
        hits = []
        view.click = lambda y: hits.append(y) or True
        board.handle_input(b"\x1b[<0;3;7M\x1b[<0;3;7m", view)
        board.handle_input(b"\x1b[M" + bytes([32, 35, 40]) + b"\x1b[M" + bytes([35, 35, 40]), view)
        self.assertEqual(hits, [7, 8])

    def test_wheel_scrolls_area_under_pointer(self):
        view = board.View()
        view.split = 30
        board.handle_input(b"\x1b[<64;5;35M", view)   # wheel up over history
        self.assertEqual(view.hist_off, board.WHEEL_STEP)
        board.handle_input(b"\x1b[<65;5;10M", view)   # wheel down over tasks
        self.assertEqual(view.top_off, board.WHEEL_STEP)
        board.handle_input(b"\x1b[<66;5;35M", view)   # horizontal wheel: ignored
        self.assertEqual(view.hist_off, board.WHEEL_STEP)

    def test_keys_scroll_history(self):
        view = board.View()
        board.handle_input(b"kk\x1b[A", view)
        self.assertEqual(view.hist_off, 3)
        board.handle_input(b"G", view)
        self.assertEqual(view.hist_off, 0)


class WrapTest(unittest.TestCase):
    def test_wrap_respects_display_width(self):
        rows = board.wrap("中文abc" * 10, 10, 3)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(board.width(r) <= 10 for r in rows))
        self.assertTrue(rows[-1].endswith("…"))
        self.assertEqual(board.wrap("short", 10, 2), ["short"])

    def test_wrap_breaks_between_words(self):
        self.assertEqual(board.wrap("add vector search behind a flag", 20, 3),
                         ["add vector search", "behind a flag"])
        self.assertEqual(board.wrap("see https://example.com/very/long/path", 12, 5),
                         ["see", "https://exam", "ple.com/very", "/long/path"])
        self.assertEqual(board.wrap("one two three four five", 9, 2), ["one two", "three…"])


if __name__ == "__main__":
    unittest.main()
