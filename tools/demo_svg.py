#!/usr/bin/env python3
"""Draw the board with demo data into an SVG for the README.

Usage: python3 tools/demo_svg.py en|zh OUT.svg

The frame comes from the real renderer (board.View), so the picture shows
exactly what the pane would; only the ANSI colours are mapped to SVG.
"""
import os
import re
import sys
from datetime import datetime, timedelta
from html import escape

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "rex-task-board", "scripts"))
import board  # noqa: E402

COLS, ROWS = 37, 60
TEXT_CUT = {"en": "Prompts", "zh": "提问"}
CELL_W, CELL_H, FONT = 8.6, 19, 14.5
PAD = 14
BG, FG = "#1e2128", "#d7dae0"
COLORS = {"31": "#e06c75", "32": "#98c379", "33": "#e5c07b", "35": "#c678dd", "36": "#56b6c2", "90": "#7f848e"}
SGR = re.compile(r"\033\[([0-9;]*)m")

DEMO = {
    "en": {
        "title": "Ship v2 search",
        "attention": [{"k": "decide", "t": "Enable for all users?", "note": "canary errors 0.1%"},
                      {"k": "watch", "t": "CI still running", "note": "about 6 min left"}],
        "phases": [
            {"name": "Prepare", "items": ["Read the old ranking", "Pick embedding model", "Write the plan"]},
            {"name": "Build", "items": [("done", "Vector index", "1.2M docs"), ("done", "Hybrid query API"),
                                        ("run", "Backfill embeddings"), ("todo", "Load test")]},
            {"name": "Release", "items": ["Canary 5%", "Full rollout"]},
        ],
        "prompts": ["add vector search behind a flag",
                    "use the existing embedding service and keep keyword search as the fallback when it times out",
                    "how long will the backfill take?",
                    "ship it to canary once the tests pass"],
    },
    "zh": {
        "title": "上线 v2 搜索",
        "attention": [{"k": "decide", "t": "要全量开放吗？", "note": "灰度错误率 0.1%"},
                      {"k": "watch", "t": "CI 还在跑", "note": "预计还要 6 分钟"}],
        "phases": [
            {"name": "准备", "items": ["读旧排序逻辑", "选向量模型", "写方案"]},
            {"name": "开发", "items": [("done", "向量索引", "120 万文档"), ("done", "混合检索接口"),
                                     ("run", "回填向量"), ("todo", "压测")]},
            {"name": "发布", "items": ["灰度 5%", "全量"]},
        ],
        "prompts": ["加一个向量搜索，先放在开关后面",
                    "用现有的向量服务，超时的时候退回关键词搜索，别影响线上",
                    "回填大概要多久？",
                    "测试过了就先灰度"],
    },
}


class Prompts:
    def __init__(self, texts):
        start = datetime.now() - timedelta(minutes=40)
        self.items = [{"id": str(n), "at": start + timedelta(minutes=9 * n), "t": t} for n, t in enumerate(texts)]

    def poll(self):
        return self.items


def demo_state(lang):
    d = DEMO[lang]
    phases = []
    for n, p in enumerate(d["phases"]):
        items = []
        for it in p["items"]:
            s, t, note = ("done" if n == 0 else "todo", it, None) if isinstance(it, str) else (it + (None,))[:3]
            item = {"s": s, "t": t}
            if note:
                item["note"] = note
            if s == "run":
                item["start"] = (datetime.now() - timedelta(minutes=4, seconds=12)).strftime("%H:%M:%S")
            items.append(item)
        phases.append({"name": p["name"], "items": items})
    return {"title": d["title"], "lang": lang, "attention": d["attention"], "phases": phases}


def _path(d, color):
    return f'<path d="{d}" stroke="{color}" stroke-width="1.4" fill="none" stroke-linecap="square"/>'


def _box(kind):
    def draw(x0, y0, color):
        x1, y1 = x0 + CELL_W, y0 + CELL_H
        cx, cy = x0 + CELL_W / 2, y0 + CELL_H / 2
        d = {"│": f"M{cx:.1f} {y0:.1f}V{y1:.1f}",
             "─": f"M{x0:.1f} {cy:.1f}H{x1:.1f}",
             "╭": f"M{x1:.1f} {cy:.1f}Q{cx:.1f} {cy:.1f} {cx:.1f} {y1:.1f}",
             "╮": f"M{x0:.1f} {cy:.1f}Q{cx:.1f} {cy:.1f} {cx:.1f} {y1:.1f}",
             "╰": f"M{cx:.1f} {y0:.1f}Q{cx:.1f} {cy:.1f} {x1:.1f} {cy:.1f}",
             "╯": f"M{cx:.1f} {y0:.1f}Q{cx:.1f} {cy:.1f} {x0:.1f} {cy:.1f}"}[kind]
        return _path(d, color)
    return draw


def _bar(x0, y0, cells, color, opacity):
    return (f'<rect x="{x0:.1f}" y="{y0 + 3:.1f}" width="{cells * CELL_W:.1f}" height="{CELL_H - 6:.1f}" '
            f'fill="{color}" opacity="{opacity}"/>')


SHAPES = {ch: _box(ch) for ch in "│─╭╮╰╯"}


def runs(line):
    """Split an ANSI line into (text, colour, bold, dim) runs."""
    color, bold, dim, pos = None, False, False, 0
    for m in SGR.finditer(line + "\033[0m"):
        if m.start() > pos:
            yield line[pos:m.start()], color, bold, dim
        for code in (m.group(1) or "0").split(";"):
            if code == "0":
                color, bold, dim = None, False, False
            elif code == "1":
                bold = True
            elif code == "2":
                dim = True
            elif code in COLORS:
                color = code
        pos = m.end()


def svg(lines):
    width = PAD * 2 + COLS * CELL_W
    height = PAD * 2 + len(lines) * CELL_H
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
           f'viewBox="0 0 {width:.0f} {height:.0f}">',
           f'<rect width="100%" height="100%" rx="10" fill="{BG}"/>',
           f'<g font-family="Menlo, Consolas, \'DejaVu Sans Mono\', \'PingFang SC\', \'Noto Sans CJK SC\', monospace" '
           f'font-size="{FONT}" xml:space="preserve">']
    for r, line in enumerate(lines):
        y = PAD + r * CELL_H + CELL_H * 0.75
        col = 0
        for text, color, bold, dim in runs(line):
            # Each character sits in its own cell so CJK text lines up; box
            # lines and bar blocks are drawn as shapes so they join up.
            for k, ch in enumerate(text):
                w = board.cw(ch)
                fill = COLORS.get(color, FG)
                x0, y0 = PAD + col * CELL_W, PAD + r * CELL_H
                if ch in "█░":  # draw a run of bar blocks as one rectangle
                    if k == 0 or text[k - 1] != ch:
                        n = len(text[k:]) - len(text[k:].lstrip(ch))
                        out.append(_bar(x0, y0, n, fill, 1 if ch == "█" else 0.25))
                elif ch in SHAPES:
                    out.append(SHAPES[ch](x0, y0, fill))
                elif ch != " " and w:
                    attrs = f'x="{x0:.1f}" y="{y:.1f}" fill="{fill}"'
                    if bold:
                        attrs += ' font-weight="700"'
                    if dim:
                        attrs += ' opacity="0.6"'
                    if w == 2:
                        attrs += f' textLength="{w * CELL_W:.1f}" lengthAdjust="spacingAndGlyphs"'
                    out.append(f"<text {attrs}>{escape(ch)}</text>")
                col += w
    out.append("</g></svg>")
    return "\n".join(out) + "\n"


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in DEMO:
        sys.exit("usage: demo_svg.py en|zh OUT.svg")
    lang, path = sys.argv[1], sys.argv[2]
    state, prompts = demo_state(lang), Prompts(DEMO[lang]["prompts"])
    for rows in range(20, ROWS + 1):  # the shortest pane that shows everything unfolded
        lines = board.View().frame(state, prompts, COLS, rows, 3)
        bare = board.ANSI.sub("", "\n".join(lines))
        if "▸" not in bare and "↓" not in bare and "…" not in bare.split(TEXT_CUT[lang])[0]:
            break
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(svg(lines))


if __name__ == "__main__":
    main()
