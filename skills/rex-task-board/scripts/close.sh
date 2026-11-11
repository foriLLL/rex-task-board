#!/bin/zsh
# Close the board pane opened for STATE_JSON. Usage: close.sh STATE_JSON
emulate -L zsh
state=${1:?usage: close.sh STATE_JSON}
idfile=${state:A}.block
[[ -f $idfile && -n $REX_SESSION ]] || { echo "no open board"; exit 0; }
read -r block < $idfile
rex block close -s "$REX_SESSION" "$block" >/dev/null 2>&1
rm -f -- $idfile
echo "board closed"
