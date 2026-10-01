#!/usr/bin/env bash
# Claude Code PreToolUse hook: stop `git commit` until the agent has written a
# decision note. The agent is the only one who still holds the reasons, so the
# refusal tells it what to write, and it writes the note from its own context.
#
# Wired up in .claude/settings.json. Needs jq. If jq or the checker is missing
# it stays out of the way (CI is the backstop).

set -uo pipefail
command -v jq >/dev/null || exit 0

input=$(cat)
cmd=$(jq -r '.tool_input.command // empty' <<<"$input")
cwd=$(jq -r '.cwd // empty' <<<"$input")

# Only `git commit` (also `git -C dir commit`). Everything else passes untouched.
re='(^|[;&|(][[:space:]]*|[[:space:]])git([[:space:]]+-C[[:space:]]+("[^"]+"|'"'"'[^'"'"']+'"'"'|[^[:space:]]+))?[[:space:]]+commit([[:space:]]|$)'
[[ "$cmd" =~ $re ]] || exit 0

unquote() { local c="$1"; c="${c%\"}"; c="${c#\"}"; c="${c%\'}"; c="${c#\'}"; printf '%s' "$c"; }
gitC="${BASH_REMATCH[3]:-}"
dir="${cwd:-$PWD}"
# Follow a leading `cd DIR &&` or `cd DIR;`, as agents often write.
cdre='^[[:space:]]*cd[[:space:]]+("[^"]+"|'"'"'[^'"'"']+'"'"'|[^[:space:];&]+)[[:space:]]*(&&|;)'
if [[ "$cmd" =~ $cdre ]]; then
  c=$(unquote "${BASH_REMATCH[1]}"); c="${c/#\~/$HOME}"
  [[ "$c" = /* ]] && dir="$c" || dir="$dir/$c"
fi
if [[ -n "$gitC" ]]; then
  c=$(unquote "$gitC")
  [[ "$c" = /* ]] && dir="$c" || dir="$dir/$c"
fi
cd "$dir" 2>/dev/null || exit 0
root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
check="$root/scripts/decision-check.sh"
[[ -x "$check" ]] || exit 0

# If the same command stages files (git add, commit -a/--all), judge everything
# pending; otherwise judge only what is already staged.
mode=staged
if [[ "$cmd" =~ git[[:space:]]+(-C[[:space:]]+[^[:space:]]+[[:space:]]+)?add([[:space:]]|$) ]] \
   || [[ "$cmd" =~ commit([[:space:]]+[^;&|]*)?[[:space:]](-a|--all|-[a-zA-Z]*a[a-zA-Z]*)([[:space:]]|$) ]]; then
  mode=pending
fi

if out=$(DECISION_MSG="$cmd" "$check" "$mode" 2>&1); then
  exit 0
fi

reason="Commit blocked: this repo keeps a decision record for every code change, and this commit has none.

$out

Write the note now, while you still hold the context. Follow docs/decisions/TEMPLATE.md. Cover:
- what was asked, and by whom
- each real decision: what you chose, what you rejected, why, and who decided (the user by name, or 'model' if you chose and the user did not review it)
- assumptions you made
- security or risk findings and how each was handled
- traps: code that looks wrong but is deliberate, so a future maintainer must not 'fix' it
- known gaps and open questions
Keep it short and factual. Stage it with the code and commit again."

jq -n --arg r "$reason" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
exit 0
