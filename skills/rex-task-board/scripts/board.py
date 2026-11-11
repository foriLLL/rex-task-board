#!/usr/bin/env python3
"""Render a task board from a JSON state file, redrawn twice a second.

Usage: board.py STATE_JSON

The agent edits the state file; this only draws it. Layout, top to bottom:
title and clock, an overall progress bar, a "Needs you" box for decisions and
things to watch, one box per phase joined by arrows, an optional log tail,
and, docked to the bottom, the user's past prompts read from the agent's
session transcript (at most 30% of the pane). Running items get a spinner and
elapsed time. When the board is taller than its area it compacts step by step
(see render) and, if it still does not fit, the phases scroll under a fixed
header and follow the phase with work in progress.

Mouse: the wheel scrolls the area under the pointer; clicking a folded phase
or an "N done" line opens it in full, clicking a phase's title folds it,
and clicking a prompt cut with "…" shows it in full (click again to cut it
back). Keys: arrows, j/k, PgUp/PgDn and g/G scroll the prompt history. The
history follows the newest prompt unless scrolled up.

Interface text is English or Chinese: the state's "lang" ("en" or "zh")
decides, else the locale.

The script re-executes itself when its own file changes, so restyling never
needs the pane reopened.
"""
import json
import os
import re
import select
import sys
import time
import unicodedata
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import history  # noqa: E402

try:
    import termios
    import tty
except ImportError:  # not a POSIX terminal: draw without input
    termios = tty = None

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
GREEN, YELLOW, MAGENTA, GRAY, RED, CYAN = ("\033[32m", "\033[33m", "\033[35m", "\033[90m",
                                           "\033[31m", "\033[36m")
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
ITEM = {"done": ("✓", GREEN), "run": (None, YELLOW), "todo": ("·", GRAY),
        "ask": ("?", MAGENTA), "fail": ("✗", RED), "skip": ("-", GRAY)}
PHASE = {"done": GREEN, "run": YELLOW, "todo": GRAY, "ask": MAGENTA, "fail": RED}
ATTENTION = {"decide": ("?", MAGENTA), "watch": ("!", YELLOW), "block": ("✗", RED)}
TICK = 0.5          # seconds between redraws
WHEEL_STEP = 3      # lines per wheel notch
HISTORY_RATIO = 0.3  # default cap of the prompt area, as a share of the pane
HISTORY_WRAP = 2     # default lines per prompt before it is cut with "…"

# Interface text. The state's "lang" picks one; without it the locale does.
TEXT = {
    "en": {"title": "Task board", "needs_you": "Needs you", "to_decide": "{n} to decide",
           "heads_up": "Heads-up", "log": "Log", "prompts": "Prompts", "below": "↓ {n} more lines",
           "above": "↑ {n} more lines", "done_items": "{n} done", "skipped": ", {n} skipped",
           "unreadable": "Cannot read the board state",
           "phase": {"done": "done", "run": "running", "todo": "to do", "ask": "your call", "fail": "failed"}},
    "zh": {"title": "任务看板", "needs_you": "需要你", "to_decide": "{n} 项待决策",
           "heads_up": "需关注", "log": "日志", "prompts": "提问", "below": "↓ 还有 {n} 行",
           "above": "↑ 还有 {n} 行", "done_items": "{n} 项已完成", "skipped": "，{n} 项跳过",
           "unreadable": "看板状态读取失败",
           "phase": {"done": "完成", "run": "进行中", "todo": "待办", "ask": "待你定", "fail": "失败"}},
}
T = TEXT["en"]


def pick_lang(state, env=None):
    """The state's "lang" if it names a known one, else zh for a Chinese locale, else en."""
    lang = str(state.get("lang") or "").lower()[:2]
    if lang in TEXT:
        return lang
    env = os.environ if env is None else env
    locale = env.get("LC_ALL") or env.get("LC_MESSAGES") or env.get("LANG") or ""
    return "zh" if locale.lower().startswith("zh") else "en"


def cw(c):
    if unicodedata.combining(c) or c in "\u200b\u200d\ufe0f":
        return 0
    return 2 if unicodedata.east_asian_width(c) in "WF" else 1


def width(text):
    return sum(cw(c) for c in text)


def clip(text, limit):
    out, used = "", 0
    for c in text:
        if used + cw(c) > limit:
            return (out[:-1] + "…") if out else ""
        out, used = out + c, used + cw(c)
    return out


def pad(text, limit):
    text = clip(text, limit)
    return text + " " * (limit - width(text))


# A word is a run of non-space characters outside the CJK ranges; CJK
# characters and spaces are tokens of their own, so CJK text breaks anywhere.
TOKEN = re.compile(r"[^\s\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]+|\s|.", re.S)


def wrap(text, limit, max_lines):
    """Split text into at most max_lines rows of display width <= limit.

    Breaks between words where it can and inside a word only when the word
    is wider than a row. A cut text ends with "…".
    """
    rows, cur, used = [], "", 0
    for tok in TOKEN.findall(text):
        w = width(tok)
        if used + w <= limit:
            if cur or not tok.isspace():  # a row never starts with a space
                cur, used = cur + tok, used + w
            continue
        if cur:
            rows.append(cur.rstrip())
            cur, used = "", 0
        if tok.isspace():
            continue
        for c in tok if w > limit else [tok]:
            if used + width(c) > limit:
                rows.append(cur)
                cur, used = "", 0
            cur, used = cur + c, used + width(c)
    if cur or not rows:
        rows.append(cur.rstrip())
    if len(rows) > max_lines:
        rows = rows[:max_lines]
        rows[-1] = clip(rows[-1] + "…", limit)
    return rows


def elapsed(start):
    now = datetime.now()
    try:
        if "T" in start or "-" in start:
            t0 = datetime.fromisoformat(start.replace("Z", "+00:00"))
            if t0.tzinfo:
                t0 = t0.astimezone().replace(tzinfo=None)
        else:
            t0 = datetime.strptime(start, "%H:%M:%S").replace(
                year=now.year, month=now.month, day=now.day)
    except ValueError:
        return ""
    s = max(0, int((now - t0).total_seconds()))
    if s >= 3600:
        return f"{s // 3600}h{s % 3600 // 60:02d}m"
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def phase_state(items):
    states = [i.get("s", "todo") for i in items]
    if not states:
        return "todo"
    if "fail" in states:
        return "fail"
    if all(s in ("done", "skip") for s in states):
        return "done"
    if any(s in ("run", "done") for s in states):
        return "run"
    if all(s == "ask" for s in states):
        return "ask"
    return "todo"


def box_top(W, head, tail, border, tail_color):
    room = W - 3 - width(tail)
    if width(head) > room:
        head = clip(head.rstrip(), room - 1) + " "
    fill = max(W - 2 - width(head) - width(tail), 1)
    return f" {border}╭{BOLD}{head}{RESET}{border}{'─' * fill}{RESET}{tail_color}{BOLD}{tail[:-1]}{RESET}{border}─╮{RESET}"


def row(W, border, content, content_width):
    return f" {border}│{RESET} {content}{' ' * max(W - 4 - content_width, 0)} {border}│{RESET}"


def board_width(cols):
    return max(24, min(cols - 2, 40))


def phase_key(n, phase):
    return f"{n}:{phase.get('name', '')}"


ANSI = re.compile(r"\033\[[0-9;?]*[A-Za-z]")


def is_edge(line):
    """A box's bottom border or the arrow between boxes: never the first row shown."""
    bare = ANSI.sub("", line).strip()
    return bare.startswith("╰") or bare == "▼"


def key_label(key):
    n, _, name = key.partition(":")
    return f"{int(n) + 1} {name}"


def is_active(phase):
    return any(i.get("s") in ("run", "ask", "fail") for i in phase.get("items", []))


def item_lines(W, border, item, tick, with_note=True):
    inner = W - 4
    s = item.get("s", "todo")
    icon, color = ITEM.get(s, ("·", GRAY))
    if s == "run":
        icon = SPIN[tick % len(SPIN)]
    right = elapsed(item["start"]) if s == "run" and item.get("start") else ""
    room = inner - 2 - (width(right) + 1 if right else 0)
    text = clip(item.get("t", ""), room)
    style = BOLD if s == "run" else (GRAY if s in ("todo", "skip") else "")
    body = f"{color}{icon}{RESET} {style}{text}{RESET}"
    used = 2 + width(text)
    if right:
        body += " " * (inner - used - width(right)) + f"{YELLOW}{right}{RESET}"
        used = inner
    out = [row(W, border, body, used)]
    if with_note and item.get("note"):
        note = clip("  " + item["note"], inner)
        out.append(row(W, border, f"{GRAY}{note}{RESET}", width(note)))
    return out


def phase_lines(W, n, phase, mode, tick):
    """Draw one phase as (lines, targets).

    mode is "full", "lean" (finished items lose their notes), "brief"
    (finished items become one count line) or "fold" (one line). A target is
    (phase key, "open"|"fold"): what a click on that line does.
    """
    its = phase.get("items", [])
    ps = phase_state(its)
    label, lc = T["phase"][ps], PHASE[ps]
    border = {"done": GREEN, "todo": GRAY}.get(ps, lc)
    name = f"{n + 1} {phase.get('name', '')}"
    key = phase_key(n, phase)
    if mode == "fold":
        counted = [i for i in its if i.get("s") != "skip"]
        count = f"{sum(i.get('s') == 'done' for i in counted)}/{len(counted)}"
        text = clip(f"▸ {name}", W - 3 - width(count))
        color = GREEN if ps == "done" else lc
        gap = " " * max(W - 1 - width(text) - width(count), 1)
        return [f" {color}{text}{RESET}{gap}{GRAY}{count}{RESET}"], [(key, "open")]
    lines, targets = [box_top(W, f"─ {name} ", f" {label} ─", border, lc)], [(key, "fold")]
    finished = [i for i in its if i.get("s") in ("done", "skip")]
    summary_done = False
    for item in its:
        if mode == "brief" and item in finished:
            if not summary_done:
                done = sum(i.get("s") == "done" for i in finished)
                text = T["done_items"].format(n=done)
                if len(finished) > done:
                    text += T["skipped"].format(n=len(finished) - done)
                text = clip(text + " ▸", W - 6)
                lines.append(row(W, border, f"{GREEN}✓{RESET} {GRAY}{text}{RESET}", 2 + width(text)))
                targets.append((key, "open"))
                summary_done = True
            continue
        drawn = item_lines(W, border, item, tick, mode == "full" or item not in finished)
        lines += drawn
        targets += [None] * len(drawn)
    lines.append(f" {border}╰{'─' * (W - 2)}╯{RESET}")
    targets.append(None)
    return lines, targets


def render(state, cols, rows, tick, pinned=None):
    """Lay out the task part of the board in at most rows lines if possible.

    Returns (head, body, targets, focus): head is the title, progress bar and
    "Needs you" box, which never scroll; body is the phases (and log tail);
    targets[i] is what a click on body line i does, or None; focus is the body
    line of the first phase with a running, waiting or failed item, or None.

    When the board is too tall it compacts step by step, stopping as soon as
    it fits: fold finished phases (oldest first), drop the notes of finished
    items, collapse finished items into a count line, fold phases not started
    yet, fold phases with nothing running, waiting or failed. pinned maps phase keys to True (always full) or False (always folded).
    """
    pinned = pinned or {}
    W = board_width(cols)
    inner = W - 4
    head = []
    title = state.get("title") or T["title"]
    head.append(f" {BOLD}{pad(title, W - 10)}{RESET}{GRAY}{datetime.now():%H:%M:%S}{RESET}")

    phases = state.get("phases") or []
    items = [i for p in phases for i in p.get("items", []) if i.get("s") != "skip"]
    done = sum(i.get("s") == "done" for i in items)
    total = len(items)
    if total:
        size = W - 12
        filled = round(size * done / total)
        head.append(f" {GREEN}{'█' * filled}{GRAY}{'░' * (size - filled)}{RESET} "
                    f"{BOLD}{round(100 * done / total):3d}%{RESET} {GRAY}{done}/{total}{RESET}")
    head.append("")

    asks = state.get("attention") or []
    if asks:
        need = sum(a.get("k") == "decide" for a in asks)
        tail = f" {T['to_decide'].format(n=need) if need else T['heads_up']} ─"
        head.append(box_top(W, f"─ {T['needs_you']} ", tail, MAGENTA, MAGENTA))
        for a in asks:
            icon, color = ATTENTION.get(a.get("k"), ("·", GRAY))
            text = clip(a.get("t", ""), inner - 2)
            head.append(row(W, MAGENTA, f"{color}{BOLD}{icon}{RESET} {BOLD}{text}{RESET}", 2 + width(text)))
            if a.get("note"):
                note = clip("  " + a["note"], inner)
                head.append(row(W, MAGENTA, f"{GRAY}{note}{RESET}", width(note)))
        head.append(f" {MAGENTA}╰{'─' * (W - 2)}╯{RESET}")
        head.append("")

    log_lines = []
    log = state.get("log")
    if log and os.path.exists(log):
        with open(log, encoding="utf-8", errors="replace") as fh:
            tail = [l.rstrip("\n") for l in fh.readlines() if l.strip()][-int(state.get("log_lines", 4)):]
        head_text = f"── {T['log']} "
        log_lines = ["", f" {GRAY}{head_text}{'─' * max(W - width(head_text), 1)}{RESET}"]
        log_lines += [f" {DIM}{clip(l, W - 1)}{RESET}" for l in tail]

    keys = [phase_key(n, p) for n, p in enumerate(phases)]
    states = [phase_state(p.get("items", [])) for p in phases]
    modes = {k: ("full" if pinned.get(k, True) else "fold") for k in keys}
    free = [n for n, k in enumerate(keys) if k not in pinned]
    steps = ([(n, "fold") for n in free if states[n] == "done"]
             + [(n, "lean") for n in free if states[n] != "done"]
             + [(n, "brief") for n in free if states[n] not in ("done", "todo")]
             + [(n, "fold") for n in free if states[n] == "todo"]
             + [(n, "fold") for n in free if states[n] not in ("done", "todo") and not is_active(phases[n])])

    def draw():
        lines, targets, focus = [], [], None
        for n, phase in enumerate(phases):
            mode = modes[keys[n]]
            if n and mode != "fold" and modes[keys[n - 1]] != "fold":
                lines.append(" " * (W // 2) + f"{GREEN if states[n - 1] == 'done' else GRAY}▼{RESET}")
                targets.append(None)
            if focus is None and is_active(phase):
                focus = len(lines)
            pl, pt = phase_lines(W, n, phase, mode, tick)
            lines += pl
            targets += pt
        return lines + log_lines, targets + [None] * len(log_lines), focus

    body, targets, focus = draw()
    for n, mode in steps:
        if len(head) + len(body) <= rows:
            break
        modes[keys[n]] = mode
        body, targets, focus = draw()
    return head, body, targets, focus


# ── prompt history ─────────────────────────────────────────────────────────


def stamp(at, today):
    """Five-column time label: HH:MM today, MM/DD on other days."""
    if isinstance(at, str):
        parsed = history.local_time(at) if ("T" in at or "-" in at) else None
        if parsed is None:
            return at[:5]
        at = parsed
    if not isinstance(at, datetime):
        return "     "
    return f"{at:%H:%M}" if at.date() == today else f"{at:%m/%d}"


def prompt_entries(state, reader):
    """Prompts to list, oldest first: [{"at", "t", "note"}]."""
    if reader is not None:
        notes = state.get("query_notes") or {}
        return [{"at": p["at"], "t": p["t"], "note": notes.get(p["id"])} for p in reader.poll()]
    return [{"at": q.get("at"), "t": q.get("t", ""), "note": None} for q in state.get("queries") or []]


def history_lines(entries, W, max_lines, opened=()):
    """All rows of the prompt list, newest at the bottom, as (lines, owners, cont).

    owners[i] is the index of the entry row i belongs to; entries in opened
    are shown in full instead of max_lines rows. cont[i] is a function that
    draws row i as the top row of the view, with the entry's time and a
    leading "…", for a prompt whose start is scrolled out of sight.
    """
    today = datetime.now().date()
    room = W - 7  # " HH:MM " prefix
    out, owners, cont = [], [], []
    for n, e in enumerate(entries):
        latest = n == len(entries) - 1
        text = re.sub(r"\s+", " ", e["note"] or e["t"]).strip()
        rows = wrap(text, room, 200 if n in opened else max_lines)
        style = (CYAN if e["note"] else "") + (BOLD if latest else "")
        when = stamp(e["at"], today)
        for k, r in enumerate(rows):
            lead = f"{GRAY}{when}{RESET}" if k == 0 else "     "
            out.append(f" {lead} {style}{r}{RESET}")
            owners.append(n)
            cont.append(lambda r=r, when=when, style=style:
                        f" {GRAY}{when}{RESET} {style}{clip('…' + r, room)}{RESET}")
    return out, owners, cont


def history_header(W, count, above, below):
    """Rule over the prompt list; ↑ when older prompts are hidden, ↓ when newer are."""
    marks = " ".join(m for m in ("↑" if above else "", "↓" if below else "") if m)
    head = f"── {T['prompts']} {count} "
    tail = f" {marks} ──" if marks else "──"
    fill = max(W - width(head) - width(tail), 1)
    return f" {GRAY}{head}{'─' * fill}{RESET}{YELLOW if below else GRAY}{tail}{RESET}"


class View:
    """Scroll positions, folds and click targets that survive redraws."""

    FOLLOW_AFTER = 30  # seconds after a manual scroll before the task area follows work again

    def __init__(self):
        self.top_off = 0      # rows scrolled down in the phase area
        self.top_touched = -1e9  # monotonic time of the last manual scroll there
        self.hist_off = 0     # rows scrolled up from the newest prompt
        self.hist_total = 0   # history rows last frame, to hold position on new prompts
        self.split = 0        # first screen row (1-based) of the history area
        self.pinned = {}      # phase key -> True (opened by click) / False (folded by click)
        self.opened = set()   # prompt indexes shown in full
        self.truncated = set()  # prompt indexes cut with "…" last frame
        self.targets = {}     # screen row -> ("phase", (key, action)) | ("prompt", index)
        self.anchor = None    # (prompt index, screen row) to keep in place after a toggle

    def frame(self, state, reader, cols, rows, tick):
        global T
        T = TEXT[pick_lang(state)]
        W = board_width(cols)
        self.targets = {}
        bottom = self.history_area(state, reader, W, rows)
        room = rows - len(bottom) - (1 if bottom else 0)  # a blank row sets the areas apart
        top = self.task_area(state, cols, W, room, tick)
        return top + [""] * (rows - len(bottom) - len(top)) + bottom

    def history_area(self, state, reader, W, rows):
        ratio = float(state.get("history_ratio", HISTORY_RATIO))
        per = max(1, int(state.get("history_wrap", HISTORY_WRAP)))
        entries = prompt_entries(state, reader)
        hist, owners, cont = history_lines(entries, W, per, self.opened)
        self.truncated = {owners[i] for i, l in enumerate(hist) if l.endswith("…" + RESET)}
        if self.hist_off and len(hist) > self.hist_total and not self.anchor:
            self.hist_off += len(hist) - self.hist_total
        self.hist_total = len(hist)
        if not hist:
            self.split = rows + 1
            return []
        height = min(len(hist) + 1, max(3, int(rows * ratio)))
        view = height - 1
        self.split = rows - height + 1
        if self.anchor and self.anchor[0] in owners:
            first = owners.index(self.anchor[0])
            self.hist_off = len(hist) - (first - (self.anchor[1] - self.split - 1) + view)
        self.anchor = None
        self.hist_off = max(0, min(self.hist_off, len(hist) - view))
        end = len(hist) - self.hist_off
        start = end - view
        shown = hist[start:end]
        if start and owners[start] == owners[start - 1]:  # a prompt cut off above
            shown[0] = cont[start]()
        for r in range(view):
            self.targets[self.split + 1 + r] = ("prompt", owners[start + r])
        return [history_header(W, len(entries), start, self.hist_off)] + shown

    def task_area(self, state, cols, W, room, tick):
        if room <= 0:
            return []
        head, body, marks, focus = render(state, cols, room, tick, self.pinned)
        if len(head) + 3 > room:  # tiny pane: let the header scroll too
            body, marks, focus = head + body, [None] * len(head) + marks, None
            head = []
        avail = room - len(head)
        if len(body) <= avail:
            self.top_off = 0
            window, wmarks = body, marks
        else:
            if focus is not None and time.monotonic() - self.top_touched > self.FOLLOW_AFTER:
                self.top_off = focus - 1  # keep the work in progress in view
            self.top_off = max(0, min(self.top_off, len(body) - avail))
            while self.top_off and self.top_off + 1 < len(body) and is_edge(body[self.top_off + 1]):
                self.top_off += 1  # row top_off becomes the ↑ hint; start on content
            window = body[self.top_off:self.top_off + avail]
            wmarks = marks[self.top_off:self.top_off + avail]
            below = max(0, len(body) - self.top_off - avail)
            if below:
                cut = len(window) - 1
                while cut > 0 and ((wmarks[cut - 1] and wmarks[cut - 1][1] == "fold")
                                   or ANSI.sub("", window[cut - 1]).strip() == "▼"):
                    cut -= 1  # never end on a phase title or an arrow into the hidden part
                below += len(window) - cut
                window, wmarks = window[:cut], wmarks[:cut]
                nxt = next((m[0] for m in marks[len(body) - below:] if m), None)
                hint = T["below"].format(n=below) + (f" · {key_label(nxt)}" if nxt else "")
                window.append(f" {GRAY}{clip(hint, W - 1)}{RESET}")
                wmarks.append(None)
            if self.top_off:
                window[0] = f" {GRAY}{clip(T['above'].format(n=self.top_off + 1), W - 1)}{RESET}"
                wmarks[0] = None
        for r, mark in enumerate(wmarks):
            if mark:
                self.targets[len(head) + r + 1] = ("phase", mark)
        return head + window

    def scroll(self, area, delta):
        """delta > 0 shows older/upper content."""
        if area == "history":
            self.hist_off = max(0, self.hist_off + delta)
        else:
            self.top_off = max(0, self.top_off - delta)
            self.top_touched = time.monotonic()

    def click(self, y):
        """Toggle what sits on screen row y; return True if anything changed."""
        kind, target = self.targets.get(y, (None, None))
        if kind == "phase":
            key, action = target
            self.pinned[key] = action == "open"
            return True
        if kind == "prompt" and (target in self.opened or target in self.truncated):
            self.opened ^= {target}
            self.anchor = (target, self._first_row(target, y))
            return True
        return False

    def _first_row(self, key, y):
        """Top visible row of prompt key: where its first line stays after a toggle."""
        rows = sorted(r for r, t in self.targets.items() if t == ("prompt", key))
        return rows[0] if rows else y


# ── terminal input ─────────────────────────────────────────────────────────

SGR_MOUSE = re.compile(rb"\x1b\[<(\d+);(\d+);(\d+)([Mm])")
KEYS = {b"\x1b[A": 1, b"\x1bOA": 1, b"k": 1, b"\x1b[B": -1, b"\x1bOB": -1, b"j": -1,
        b"\x1b[5~": 10, b"\x1b[6~": -10, b"g": 10 ** 6, b"\x1b[H": 10 ** 6, b"\x1b[1~": 10 ** 6,
        b"G": -10 ** 6, b"\x1b[F": -10 ** 6, b"\x1b[4~": -10 ** 6}


def mouse(view, button, y, press):
    """Apply one mouse report; return True if the view changed."""
    if button & 64:  # wheel: 64 up, 65 down; 66/67 are horizontal
        if button & 2:
            return False
        area = "history" if view.split and y >= view.split else "top"
        view.scroll(area, -WHEEL_STEP if button & 1 else WHEEL_STEP)
        return True
    if press and button & ~(4 | 8 | 16) == 0:  # left press, any modifier
        return view.click(y)
    return False


def handle_input(data, view):
    """Apply wheel, click and key events in data; return True if anything changed."""
    changed, i = False, 0
    while i < len(data):
        m = SGR_MOUSE.match(data, i)
        if m:
            changed |= mouse(view, int(m.group(1)), int(m.group(3)), m.group(4) == b"M")
            i = m.end()
            continue
        if data.startswith(b"\x1b[M", i) and i + 6 <= len(data):  # legacy X10 mouse report
            button = data[i + 3] - 32
            changed |= mouse(view, button, data[i + 5] - 32, button & 3 != 3)
            i += 6
            continue
        for seq, delta in KEYS.items():
            if data.startswith(seq, i):
                view.scroll("history", delta)
                changed, i = True, i + len(seq)
                break
        else:
            i += 1
    return changed


# Alternate screen, hidden cursor, and wheel reporting (SGR encoding).
ENTER = "\033[?1049h\033[?25l\033[?1000h\033[?1006h"
LEAVE = "\033[?1006l\033[?1000l\033[?25h\033[?1049l" + RESET


class Terminal:
    def __init__(self):
        self.fd, self.saved = None, None
        if termios and sys.stdin.isatty():
            self.fd = sys.stdin.fileno()
            self.saved = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
        sys.stdout.write(ENTER)
        sys.stdout.flush()

    def wait(self, timeout):
        """Bytes typed within timeout seconds, or b"" when none."""
        if self.fd is None:
            time.sleep(max(timeout, 0))
            return b""
        ready, _, _ = select.select([self.fd], [], [], max(timeout, 0))
        return os.read(self.fd, 4096) if ready else b""

    def restore(self):
        sys.stdout.write(LEAVE)
        sys.stdout.flush()
        if self.saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)


def terminal_size():
    # Ask the pane itself: an inherited COLUMNS goes stale once the pane resizes.
    try:
        s = os.get_terminal_size(sys.stdout.fileno())
        return s.columns, s.lines
    except OSError:
        return 36, 60


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: board.py STATE_JSON")
    state_path = os.path.abspath(sys.argv[1])
    me = os.path.abspath(__file__)
    mine = [me, os.path.join(os.path.dirname(me), "history.py")]
    born = [os.path.getmtime(p) for p in mine]
    term = Terminal()
    view, tick, last_good, reader, err = View(), 0, None, None, None
    next_tick = time.monotonic()
    try:
        while True:
            try:
                changed = [os.path.getmtime(p) for p in mine] != born
            except OSError:  # mid-update: the files are being replaced
                changed = False
            if changed:
                term.restore()
                os.execv(sys.executable, [sys.executable, "-I", me, state_path])
            cols, rows = terminal_size()
            try:
                with open(state_path, encoding="utf-8") as fh:
                    last_good = json.load(fh)
                err = None
            except (OSError, ValueError) as exc:  # mid-write or removed
                err = exc
            if last_good is None:
                lines = [f" {RED}{TEXT[pick_lang({})]['unreadable']}{RESET}", f" {clip(str(err), cols - 2)}"]
            else:
                source = last_good.get("transcript")
                if not source:
                    reader = None
                elif reader is None or reader.path != source:
                    reader = history.Reader(source)
                lines = view.frame(last_good, reader, cols, rows, tick)
            lines = lines[:rows]
            sys.stdout.write("\033[H" + "\r\n".join(l + "\033[K" for l in lines) + "\033[J")
            sys.stdout.flush()
            data = term.wait(next_tick - time.monotonic())
            if data:
                handle_input(data, view)
            else:
                tick += 1
                next_tick = time.monotonic() + TICK
    except KeyboardInterrupt:
        pass
    finally:
        term.restore()


if __name__ == "__main__":
    main()
