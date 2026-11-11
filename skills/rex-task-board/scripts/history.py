"""Read the user's past prompts from an agent session transcript.

The board shows these at the bottom of the pane so the user can see what they
asked so far. Supported transcripts:

  Claude Code  $CLAUDE_CONFIG_DIR/projects/<cwd>/<session>.jsonl, found through
               $CLAUDE_CODE_SESSION_ID (default config dir ~/.claude)
  Codex        $CODEX_HOME/sessions/YYYY/MM/DD/rollout-*-<thread>.jsonl, found
               through $CODEX_THREAD_ID (default home ~/.codex)

Any other JSONL in one of those formats can be pointed at explicitly. Only the
person's own prompts are kept: tool results, injected reminders, skill bodies,
local command output and harness wrappers are dropped.
"""
import glob
import json
import os
import re
from datetime import datetime

# Leading wrappers that mark harness-injected user turns rather than prompts.
INJECTED = ("<local-command-", "<system-reminder>", "<command-message>", "<bash-stdout",
            "<bash-stderr", "<task-notification>", "<environment_context>",
            "<user_instructions>", "<permissions instructions>", "<turn_aborted>",
            "# AGENTS.md instructions", "Base directory for this skill:",
            "Caveat: The messages below were generated", "[Request interrupted")
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
COMMAND = re.compile(r"<command-name>\s*(/?[^<\s]+)\s*</command-name>")
ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)
BASH = re.compile(r"<bash-input>(.*?)</bash-input>", re.S)
# Bridges (chat bots, IDEs) wrap the typed text in one of these tags.
INNER = re.compile(r"<(user_message|user_query|user_input)>(.*?)</\1>", re.S)
PASTE = re.compile(r"<pasted_content\b[^>]*>.*?</pasted_content\b[^>]*>", re.S)
# Leading <tag ...>...</tag> blocks (or self-closing tags) are harness context.
LEADING_TAG = re.compile(r"\A\s*(?:<([A-Za-z][\w:-]*)\b[^>]*>.*?</\1\b[^>]*>|<[A-Za-z][\w:-]*\b[^>]*/>)\s*", re.S)


def find_transcript(env=None):
    """Return the running agent's own transcript path, or None if unknown."""
    env = os.environ if env is None else env
    sid = env.get("CLAUDE_CODE_SESSION_ID")
    if sid:
        home = env.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
        hits = glob.glob(os.path.join(glob.escape(home), "projects", "*", glob.escape(sid) + ".jsonl"))
        if hits:
            return max(hits, key=os.path.getmtime)
    tid = env.get("CODEX_THREAD_ID") or env.get("CODEX_SESSION_ID")
    if tid:
        home = env.get("CODEX_HOME") or os.path.expanduser("~/.codex")
        pattern = os.path.join(glob.escape(home), "sessions", "*", "*", "*", f"rollout-*{glob.escape(tid)}.jsonl")
        hits = glob.glob(pattern)
        if hits:
            return max(hits, key=os.path.getmtime)
    return None


def clean(text):
    """Turn one raw user turn into the prompt the person typed, or "" to drop it."""
    text = REMINDER.sub("", text or "").strip()
    command = COMMAND.search(text)
    if command:  # slash command: keep it only when it carries arguments
        args = ARGS.search(text)
        args = args.group(1).strip() if args else ""
        return f"{command.group(1)} {args}" if args else ""
    bash = BASH.search(text)
    if bash:
        return "! " + bash.group(1).strip()
    inner = INNER.search(text)
    if inner:
        text = inner.group(2).strip()
    text = PASTE.sub("[pasted]", text).strip()
    if not text or text.startswith(INJECTED):
        return ""
    while True:  # drop wrapper blocks in front of the typed text
        m = LEADING_TAG.match(text)
        if not m:
            return text
        text = text[m.end():]


def local_time(stamp):
    try:
        t = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return t.astimezone().replace(tzinfo=None) if t.tzinfo else t


def _content_text(content):
    """Text of a message body; None when it is a tool result, not a prompt."""
    if isinstance(content, str):
        return content
    parts, images = [], 0
    for block in content or []:
        kind = block.get("type") if isinstance(block, dict) else None
        if kind in ("tool_result", "function_call_output"):
            return None
        if kind in ("text", "input_text"):
            parts.append(block.get("text", ""))
        elif kind in ("image", "input_image"):
            images += 1
    text = "\n".join(p for p in parts if p.strip())
    if images:
        text = (text + " " if text else "") + "[image]" * images
    return text


def parse_line(record):
    """Return (id, time, kind, text) for a prompt record, else None.

    kind is "codex-event" for Codex user_message events, which duplicate the
    response_item copy of the same prompt; readers prefer them when present.
    """
    kind = record.get("type")
    if kind == "user":  # Claude Code
        if record.get("isMeta") or record.get("isCompactSummary") or record.get("isSidechain"):
            return None
        raw = _content_text((record.get("message") or {}).get("content"))
        text = clean(raw) if raw is not None else ""
        if text:
            return record.get("uuid") or record.get("timestamp"), local_time(record.get("timestamp")), "claude", text
        return None
    if kind == "attachment":  # Claude Code: a prompt typed while the agent was busy
        att = record.get("attachment") or {}
        if att.get("type") == "queued_command" and att.get("commandMode", "prompt") == "prompt":
            text = clean(att.get("prompt") if isinstance(att.get("prompt"), str) else _content_text(att.get("prompt")))
            if text:
                stamp = att.get("timestamp") or record.get("timestamp")
                return record.get("uuid") or stamp, local_time(stamp), "claude", text
        return None
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    if kind == "event_msg" and payload.get("type") == "user_message":  # Codex
        text = clean(payload.get("message", ""))
        if text:
            return record.get("timestamp"), local_time(record.get("timestamp")), "codex-event", text
    if kind == "response_item" and payload.get("type") == "message" and payload.get("role") == "user":
        raw = _content_text(payload.get("content"))
        text = clean(raw) if raw is not None else ""
        if text:
            return record.get("timestamp"), local_time(record.get("timestamp")), "codex", text
    return None


# Cheap byte probes so the reader only json-decodes lines that can be prompts.
PROBES = (b'"user"', b'queued_command', b'user_message')


class Reader:
    """Incrementally read prompts from a growing JSONL transcript.

    poll() reads only the bytes appended since the last call, so a long
    session costs one full read and then almost nothing per redraw.
    """

    def __init__(self, path):
        self.path = path
        self.reset()

    def reset(self):
        self.pos, self.rest, self.ino = 0, b"", None
        self.claude, self.codex, self.codex_events = [], [], []

    def poll(self):
        try:
            st = os.stat(self.path)
        except OSError:
            return self.prompts()
        if st.st_ino != self.ino or st.st_size < self.pos:  # replaced or truncated
            self.reset()
            self.ino = st.st_ino
        if st.st_size > self.pos:
            with open(self.path, "rb") as fh:
                fh.seek(self.pos)
                chunk = fh.read(st.st_size - self.pos)
            self.pos += len(chunk)
            lines = (self.rest + chunk).split(b"\n")
            self.rest = lines.pop()
            for line in lines:
                self._take(line)
        return self.prompts()

    def _take(self, line):
        if not any(p in line for p in PROBES):
            return
        try:
            hit = parse_line(json.loads(line))
        except (ValueError, AttributeError, TypeError):
            return
        if hit:
            pid, when, kind, text = hit
            {"claude": self.claude, "codex": self.codex, "codex-event": self.codex_events}[kind].append(
                {"id": pid, "at": when, "t": text})

    def prompts(self):
        # Every record is one prompt: a mid-turn prompt is logged only as its
        # queued_command attachment, so identical texts are real repeats.
        return list(self.claude or self.codex_events or self.codex)


def read_prompts(path):
    return Reader(path).poll()
