import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "rex-task-board", "scripts"))
import history  # noqa: E402


def claude_user(text, uuid, ts="2026-11-11T06:48:58.494Z", **extra):
    rec = {"type": "user", "uuid": uuid, "timestamp": ts, "message": {"role": "user", "content": text}}
    rec.update(extra)
    return rec


CLAUDE = [
    claude_user("<local-command-caveat>Caveat...</local-command-caveat>", "a1", isMeta=True),
    claude_user("<command-name>/clear</command-name>\n<command-message>clear</command-message>\n<command-args></command-args>", "a2"),
    claude_user("<command-message>x</command-message>\n<command-name>/review</command-name>\n<command-args>check auth</command-args>", "a3"),
    claude_user("Base directory for this skill: /x", "a4", isMeta=True),
    claude_user([{"type": "tool_result", "tool_use_id": "t", "content": "ok"}], "a5"),
    claude_user("<system-reminder>noise</system-reminder>\nfix the login bug", "a6"),
    {"type": "attachment", "uuid": "a7", "timestamp": "2026-11-11T06:49:09.965Z",
     "attachment": {"type": "queued_command", "prompt": "also add a test", "commandMode": "prompt",
                    "timestamp": "2026-11-11T06:49:09.965Z"}},
    claude_user([{"type": "text", "text": "what is this"}, {"type": "image", "source": {}}], "a8"),
    claude_user("<bash-input>git status</bash-input>", "a9"),
    claude_user("<bash-stdout>clean</bash-stdout><bash-stderr></bash-stderr>", "a10"),
    claude_user("[Request interrupted by user]", "a11"),
    claude_user('<pasted_content id="1">long doc</pasted_content id="1">\n\nsummarize it', "a12"),
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "\"user\" said hi"}]}},
]

CODEX = [
    {"type": "session_meta", "payload": {"id": "t1"}},
    {"type": "response_item", "timestamp": "2026-10-20T06:14:09Z",
     "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>x</environment_context>"}]}},
    {"type": "response_item", "timestamp": "2026-10-20T06:15:09Z",
     "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<session_id>s</session_id>\n<user_message>\nhow many people\n</user_message>\n<sender name=\"x\" />"}]}},
]


def write_jsonl(records):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return path


class CleanTest(unittest.TestCase):
    def test_claude_prompts(self):
        path = write_jsonl(CLAUDE)
        self.addCleanup(os.remove, path)
        self.assertEqual([p["t"] for p in history.read_prompts(path)], [
            "/review check auth", "fix the login bug", "also add a test",
            "what is this [image]", "! git status", "[pasted]\n\nsummarize it"])

    def test_codex_prompts(self):
        path = write_jsonl(CODEX)
        self.addCleanup(os.remove, path)
        self.assertEqual([p["t"] for p in history.read_prompts(path)], ["how many people"])

    def test_codex_events_preferred(self):
        path = write_jsonl(CODEX + [{"type": "event_msg", "timestamp": "2026-10-20T06:15:09Z",
                                      "payload": {"type": "user_message", "message": "how many people"}}])
        self.addCleanup(os.remove, path)
        self.assertEqual(len(history.read_prompts(path)), 1)

    def test_repeated_prompts_all_count(self):
        path = write_jsonl([claude_user("ping", "b1"), claude_user("ping", "b2"), CLAUDE[6],
                            {"type": "attachment", "uuid": "b3", "timestamp": "2026-11-11T06:50:00Z",
                             "attachment": {"type": "queued_command", "prompt": "also add a test"}}])
        self.addCleanup(os.remove, path)
        self.assertEqual([p["t"] for p in history.read_prompts(path)],
                         ["ping", "ping", "also add a test", "also add a test"])


class ReaderTest(unittest.TestCase):
    def test_incremental_and_partial_lines(self):
        path = write_jsonl([claude_user("one", "c1")])
        self.addCleanup(os.remove, path)
        reader = history.Reader(path)
        self.assertEqual(len(reader.poll()), 1)
        line = json.dumps(claude_user("two", "c2"))
        with open(path, "a") as fh:
            fh.write(line[:20])
        self.assertEqual(len(reader.poll()), 1)  # half a line is held back
        with open(path, "a") as fh:
            fh.write(line[20:] + "\n")
        self.assertEqual([p["t"] for p in reader.poll()], ["one", "two"])

    def test_replaced_file_rereads(self):
        path = write_jsonl([claude_user("one", "c1"), claude_user("two", "c2")])
        self.addCleanup(os.remove, path)
        reader = history.Reader(path)
        reader.poll()
        tmp = write_jsonl([claude_user("fresh", "d1")])
        os.replace(tmp, path)
        self.assertEqual([p["t"] for p in reader.poll()], ["fresh"])


class FindTest(unittest.TestCase):
    def test_claude_and_codex_lookup(self):
        root = tempfile.mkdtemp()
        claude = os.path.join(root, "claude", "projects", "-p")
        codex = os.path.join(root, "codex", "sessions", "2026", "11", "11")
        os.makedirs(claude)
        os.makedirs(codex)
        open(os.path.join(claude, "sid.jsonl"), "w").close()
        open(os.path.join(codex, "rollout-2026-11-11T00-00-00-tid.jsonl"), "w").close()
        self.assertTrue(history.find_transcript(
            {"CLAUDE_CODE_SESSION_ID": "sid", "CLAUDE_CONFIG_DIR": os.path.join(root, "claude")}).endswith("sid.jsonl"))
        self.assertTrue(history.find_transcript(
            {"CODEX_THREAD_ID": "tid", "CODEX_HOME": os.path.join(root, "codex")}).endswith("tid.jsonl"))
        self.assertIsNone(history.find_transcript({}))


if __name__ == "__main__":
    unittest.main()
