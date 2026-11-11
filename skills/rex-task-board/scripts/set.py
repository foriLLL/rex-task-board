#!/usr/bin/env python3
"""Edit a task board state file in one command; writes are atomic.

  set.py STATE init "Title" [--lang en|zh]       new board (overwrites)
  set.py STATE phase "Phase" ["item" ...]        add a phase with todo items
  set.py STATE add "Phase|number" "item" [--s S] [--note N]
  set.py STATE mark "item prefix" S [--t new text] [--note N|--note ""]
  set.py STATE attn decide|watch|block "text" [--note N]
  set.py STATE unattn "text prefix"
  set.py STATE query "short label" [--n K]       label prompt K (default: latest)
  set.py STATE queries                          list prompts with their numbers
  set.py STATE history auto|off|PATH             where prompt history comes from
  set.py STATE show

S is one of: todo run done ask fail skip. Marking an item run stamps its start
time (the board shows elapsed time); any other state clears it. Prefixes match
the start of the item text; the first match wins, and no match is an error.
--lang sets the interface language; without it the board follows the locale.

Prompt history: every write records the agent's own session transcript
(found from CLAUDE_CODE_SESSION_ID or CODEX_THREAD_ID) so the board can list
the user's prompts. `query` attaches a short label to one prompt; K counts
from 1, negative K from the end. Without a transcript, `query` appends the
text to a hand-kept list instead. `history off` hides the list; a PATH pins a
JSONL transcript in Claude Code or Codex format.
"""
import json
import os
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import history  # noqa: E402

STATES = {"todo", "run", "done", "ask", "fail", "skip"}
KINDS = {"decide", "watch", "block"}


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def stamp_transcript(state):
    """Point the board at this agent's transcript unless history is pinned or off."""
    mode = state.get("history", "auto")
    if mode == "off":
        state.pop("transcript", None)
    elif mode != "auto":
        state["transcript"] = os.path.abspath(os.path.expanduser(mode))
    else:
        found = history.find_transcript()
        if found:
            state["transcript"] = found


def save(path, state):
    stamp_transcript(state)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def opts(args):
    rest, flags = [], {}
    it = iter(args)
    for a in it:
        if a.startswith("--"):
            flags[a[2:]] = next(it, "")
        else:
            rest.append(a)
    return rest, flags


def apply_state(item, s):
    if s not in STATES:
        sys.exit(f"unknown state {s!r}; use one of {sorted(STATES)}")
    item["s"] = s
    if s == "run":
        item["start"] = datetime.now().strftime("%H:%M:%S")
    else:
        item.pop("start", None)


def find_phase(state, key):
    phases = state.setdefault("phases", [])
    if key.isdigit() and 1 <= int(key) <= len(phases):
        return phases[int(key) - 1]
    for p in phases:
        if p.get("name", "").startswith(key):
            return p
    sys.exit(f"no phase matches {key!r}")


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    path, cmd = os.path.abspath(sys.argv[1]), sys.argv[2]
    args, flags = opts(sys.argv[3:])
    if cmd == "init":
        state = {"title": args[0] if args else "", "attention": [], "phases": []}
        if flags.get("lang"):
            state["lang"] = flags["lang"]
        save(path, state)
        return
    if not os.path.exists(path):
        sys.exit(f"state file missing: {path} (create it with init)")
    state = load(path)
    if cmd == "show":
        print(json.dumps(state, ensure_ascii=False, indent=1))
        return
    if cmd == "phase":
        state.setdefault("phases", []).append(
            {"name": args[0], "items": [{"s": "todo", "t": t} for t in args[1:]]})
    elif cmd == "add":
        item = {"t": args[1]}
        apply_state(item, flags.get("s", "todo"))
        if flags.get("note"):
            item["note"] = flags["note"]
        find_phase(state, args[0]).setdefault("items", []).append(item)
    elif cmd == "mark":
        prefix, s = args[0], args[1]
        for p in state.get("phases", []):
            hit = next((i for i in p.get("items", []) if i.get("t", "").startswith(prefix)), None)
            if hit:
                break
        else:
            sys.exit(f"no item starts with {prefix!r}")
        apply_state(hit, s)
        if "t" in flags:
            hit["t"] = flags["t"]
        if "note" in flags:
            if flags["note"]:
                hit["note"] = flags["note"]
            else:
                hit.pop("note", None)
    elif cmd == "attn":
        kind, text = args[0], args[1]
        if kind not in KINDS:
            sys.exit(f"unknown attention kind {kind!r}; use one of {sorted(KINDS)}")
        entry = {"k": kind, "t": text}
        if flags.get("note"):
            entry["note"] = flags["note"]
        asks = [a for a in state.get("attention", []) if a.get("t") != text]
        state["attention"] = asks + [entry]
    elif cmd == "query":
        text, source = args[0] if args else "", state.get("transcript")
        if source and os.path.exists(source):
            prompts = history.read_prompts(source)
            n = int(flags.get("n") or -1)
            if not prompts or not (1 <= n <= len(prompts) or -len(prompts) <= n <= -1):
                sys.exit(f"no prompt number {n}; {len(prompts)} prompts so far")
            notes = state.setdefault("query_notes", {})
            pid = prompts[n - 1 if n > 0 else n]["id"]
            if text:
                notes[pid] = text
            else:
                notes.pop(pid, None)
        elif text:
            state.setdefault("queries", []).append({"t": text, "at": datetime.now().strftime("%H:%M")})
    elif cmd == "queries":
        source = state.get("transcript")
        if source and os.path.exists(source):
            notes = state.get("query_notes") or {}
            for n, p in enumerate(history.read_prompts(source), 1):
                label = f"  [{notes[p['id']]}]" if p["id"] in notes else ""
                at = p["at"].strftime("%m-%d %H:%M") if p["at"] else "?"
                print(f"{n:3d}  {at}  {' '.join(p['t'].split())[:100]}{label}")
        else:
            for n, q in enumerate(state.get("queries") or [], 1):
                print(f"{n:3d}  {q.get('at', '?')}  {q.get('t', '')}")
        return
    elif cmd == "history":
        if not args:
            sys.exit("usage: history auto|off|PATH")
        state["history"] = args[0]
    elif cmd == "sync":
        pass
    elif cmd == "unattn":
        before = state.get("attention", [])
        state["attention"] = [a for a in before if not a.get("t", "").startswith(args[0])]
        if len(state["attention"]) == len(before):
            sys.exit(f"no attention item starts with {args[0]!r}")
    else:
        sys.exit(__doc__)
    save(path, state)


if __name__ == "__main__":
    main()
