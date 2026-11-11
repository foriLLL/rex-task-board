---
name: rex-task-board
description: Show a live ASCII task board in a pane to the right of the agent's own Rex terminal pane — phases, progress, the decisions or warnings that need the user, and the user's past prompts in this session. Use it for multi-step work (3+ steps, releases, CI or long builds, parallel tracks) or whenever something needs the user's call. Only when the environment variables REX_SESSION and REX_BLOCK are both set; never outside Rex.
---

# Rex task board

The board pane redraws a JSON state file twice a second. You only edit the state file through `scripts/set.py`; never touch the pane.

## When to use

- **Only inside Rex**: both `REX_SESSION` and `REX_BLOCK` set. Outside Rex `open.sh` prints a note and exits 0; don't look for a substitute.
- **Open one** for work with 3+ steps or phases, background jobs to wait on (deploys, CI, long builds, polling), or anything that needs the user's decision or attention. Skip it for quick questions and one- or two-step edits.
- **One board per session**: add later work as new phases instead of opening another pane.
- **When done**, leave the final state up. Run `close.sh $B` only when the user asks or the session moves on to unrelated work.

## Use

```bash
S=<directory of this SKILL.md>/scripts
B=<session scratch directory>/board/board.json   # never inside the user's project
mkdir -p "$(dirname "$B")"
python3 -I $S/set.py $B init "Task title" --lang en   # en or zh: the language you use with the user
python3 -I $S/set.py $B phase "Develop" "Investigate" "Implement" "Test"
python3 -I $S/set.py $B phase "Release" "Build" "Deploy"
zsh $S/open.sh $B            # optional second argument: width in percent, default 27
```

Update at every step, not at the end:

```bash
python3 -I $S/set.py $B mark "Investigate" done --note "key finding in a few words"
python3 -I $S/set.py $B mark "Implement" run        # stamps the start; the board shows elapsed time
python3 -I $S/set.py $B add "Release" "Verify live"   # the plan changed
python3 -I $S/set.py $B attn decide "Sync to prod?" --note "affects …"
python3 -I $S/set.py $B attn watch "CI still running" --note "will compare after"
python3 -I $S/set.py $B unattn "Sync to prod"         # remove as soon as the user answers
```

Item states: `todo`, `run`, `done`, `ask` (waiting on the user), `fail`, `skip` (not counted). `mark` and `unattn` match by text prefix. Run `python3 -I $S/set.py` with no arguments for every command.

## Prompt history

The bottom of the board lists the user's prompts in this session, read straight from your own transcript (Claude Code through `CLAUDE_CODE_SESSION_ID`, Codex through `CODEX_THREAD_ID`). `set.py` records the transcript on every write, so there is nothing to maintain. Optional:

```bash
python3 -I $S/set.py $B queries               # numbered list of prompts
python3 -I $S/set.py $B query "short label"   # show a label instead of the latest prompt; --n K for prompt K; "" removes it
python3 -I $S/set.py $B history off           # hide the list; a JSONL path pins the source instead
```

Label a prompt only when its raw text is long or noisy (pasted documents, wrappers). Without a transcript (other agents), `query` appends a hand-written entry instead.

## Writing rules

- **Short**: the pane is about 35 columns and CJK characters take two. Keep items within about 24 columns (12 CJK characters) and notes within about 28; longer text is cut with "…".
- **True**: mark `done` only when it is. On failure mark `fail` and add an `attn block`. The board must match reality.
- **Needs-you box**: `decide` when the user must choose (phrase it as a question); `watch` for a risk, external event or wait the user should know about; `block` for a blocker. Remove each one as soon as it is handled.
- **Not a substitute for the reply**: still state conclusions and questions in your answer; the board only mirrors them.
- Never put secrets, tokens or personal data on the board.

## Troubleshooting

- See what is drawn: `rex capture -s "$REX_SESSION" -b "$(cat $B.block)" --trim | tail -40`.
- Wrong width: `close.sh $B`, then `open.sh $B <percent>`.
- A running board reloads itself when its scripts change. If the user closed the pane, `open.sh` opens it again.
