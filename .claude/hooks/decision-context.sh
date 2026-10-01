#!/usr/bin/env bash
# decision-context.sh — put the decision notes in front of Claude when it matters,
# instead of trusting it to go and read them.
#
# One script, three Claude Code hook events (wired in .claude/settings.json):
#   SessionStart          a one-line-per-note index of docs/decisions/
#   PostToolUse  (Read)   when Claude reads a file a note mentions, add those
#                         lines from the note alongside what it read
#   PreToolUse   (Edit,   if Claude is about to change such a file and has not
#                 Write…) seen its notes this session, refuse once with the
#                         notes as the reason; the retry goes through
#
# A note "mentions" a file if it names its repo path, a path ending (tools/x.py)
# or its file name in backticks (`x.py`). Notes marked replaced or withdrawn are
# skipped. Needs jq; without it the hook stands aside.

set -uo pipefail
command -v jq >/dev/null || exit 0

input=$(cat)
event=$(jq -r '.hook_event_name // empty' <<<"$input")
session=$(jq -r '.session_id // "nosession"' <<<"$input")
session=$(printf '%s' "$session" | tr -c 'A-Za-z0-9._-' '_')
cwd=$(jq -r '.cwd // empty' <<<"$input")
cd "${CLAUDE_PROJECT_DIR:-${cwd:-.}}" 2>/dev/null || exit 0
root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$root"
notes_dir="docs/decisions"
[[ -d "$notes_dir" ]] || exit 0
MAX=6000

live_notes() {
  local f
  for f in "$notes_dir"/*.md; do
    [[ -e "$f" ]] || continue
    case "${f##*/}" in README.md|TEMPLATE.md) continue ;; esac
    grep -qiE '^\s*-\s*\*\*Status:\*\*\s*(replaced|withdrawn)' "$f" && continue
    printf '%s\n' "$f"
  done
}

title_of() { grep -m1 '^# ' "$1" | sed 's/^# //'; }

# Lines in live notes that mention $1 (a repo-relative path), with the note they came from.
excerpts_for() {
  local rel="$1" base="${1##*/}" f lines terms=() out=""
  terms+=("$rel")
  local p="$rel"
  while [[ "$p" == */*/* ]]; do p="${p#*/}"; terms+=("$p"); done   # path endings
  terms+=("\`$base\`")
  while IFS= read -r f; do
    lines=$(for t in "${terms[@]}"; do grep -nF -- "$t" "$f"; done | sort -t: -k1,1n -u | cut -d: -f2- | sed 's/^/  /')
    [[ -n "$lines" ]] && out+="${f}: $(title_of "$f")"$'\n'"${lines}"$'\n\n'
  done < <(live_notes)
  printf '%s' "${out:0:$MAX}"
}

seen_file() {
  local d; d="$(git rev-parse --git-common-dir)/decision-notes-seen"
  mkdir -p "$d" 2>/dev/null && printf '%s/%s' "$d" "$session"
}
mark_seen() { local s; s=$(seen_file) && printf '%s\n' "$1" >> "$s"; }
was_seen()  { local s; s=$(seen_file) && [[ -f "$s" ]] && grep -qxF "$1" "$s"; }

relpath() {
  local f="$1"
  [[ "$f" = /* ]] || f="${cwd:-$root}/$f"
  f=$(realpath -m "$f" 2>/dev/null || printf '%s' "$f")
  [[ "$f" == "$root"/* ]] || return 1
  printf '%s' "${f#"$root"/}"
}

case "$event" in
  SessionStart)
    list=$(live_notes)
    [[ -z "$list" ]] && exit 0
    echo "This repo keeps decision notes in $notes_dir/: why the code is the way it is, who decided, and code that looks wrong but is deliberate. Before changing a file, read the notes that mention it. Notes in force:"
    while IFS= read -r f; do echo "- ${f##*/}: $(title_of "$f")"; done <<<"$list" | head -60
    ;;

  PostToolUse|PreToolUse)
    path=$(jq -r '.tool_input.file_path // .tool_input.notebook_path // empty' <<<"$input")
    [[ -n "$path" ]] || exit 0
    rel=$(relpath "$path") || exit 0
    [[ "$rel" == "$notes_dir"/* ]] && exit 0
    was_seen "$rel" && exit 0
    ex=$(excerpts_for "$rel")
    [[ -n "$ex" ]] || exit 0
    mark_seen "$rel"
    msg="Decision notes that mention $rel. These record why the code is as it is; respect any traps listed, and record any change of course in a new note:

$ex"
    if [[ "$event" == PostToolUse ]]; then
      jq -n --arg c "$msg" '{hookSpecificOutput:{hookEventName:"PostToolUse",additionalContext:$c}}'
    else
      jq -n --arg r "Not yet: read these decision notes before changing $rel, then make the edit again (it will go through).

$ex" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
    fi
    ;;
esac
exit 0
