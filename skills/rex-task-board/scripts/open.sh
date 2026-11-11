#!/bin/zsh
# Open the task board in a pane to the right of the agent's own Rex pane.
# Usage: open.sh STATE_JSON [WIDTH_PERCENT]
# Outside Rex this prints a note and exits 0, so callers never need to branch.
# Reruns are safe: an open board for the same state file is reused.
emulate -L zsh
if [[ -z $REX_SESSION || -z $REX_BLOCK ]] || ! command -v rex >/dev/null; then
  echo "not in Rex: board skipped"
  exit 0
fi
state=${1:?usage: open.sh STATE_JSON [WIDTH_PERCENT]}
state=${state:A}
pct=${2:-27}
dir=${0:A:h}
idfile=$state.block
[[ -f $state ]] || { echo "state file missing: $state (create it with set.py init)" >&2; exit 1; }
# Record this agent's session transcript so the board can list past prompts.
python3 -I "$dir/set.py" "$state" sync 2>/dev/null

ids() { rex block ls -s "$REX_SESSION" --json 2>/dev/null | python3 -I -c 'import json,sys;[print(b["block_id"]) for b in json.load(sys.stdin)["blocks"]]'; }

if [[ -f $idfile ]] && ids | grep -qx -- "$(<$idfile)"; then
  echo "board already open: $(<$idfile)"
  exit 0
fi

label=$(python3 -I -c 'import json,os,sys
lang = str(json.load(open(sys.argv[1])).get("lang") or "")[:2] or (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or "")[:2]
print("任务看板" if lang.lower() == "zh" else "Task board")' "$state" 2>/dev/null || echo "Task board")
new=$(rex split -s "$REX_SESSION" -b "$REX_BLOCK" --split=right --ratio "$pct" --label "$label" \
  --focus=false --json -- python3 -I "$dir/board.py" "$state" |
  python3 -I -c 'import json,sys;print(json.load(sys.stdin)["block_ids"][0])') || { echo "rex split failed" >&2; exit 1; }
print -r -- $new > $idfile
# --ratio is not always honoured when the pane was split before; pin the border.
rex api call -s "$REX_SESSION" session.resize_pane \
  "{\"direction\":\"horizontal\",\"before_block_id\":\"$REX_BLOCK\",\"after_block_id\":\"$new\",\"ratio\":$(( (100 - pct) / 100.0 ))}" >/dev/null 2>&1
echo "board open: $new"
