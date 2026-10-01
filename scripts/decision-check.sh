#!/usr/bin/env bash
# decision-check.sh — does every code change come with a decision note?
#
# One rule, used by three gates (the Claude Code hook, the local git hook and CI):
#   If a commit changes code, it must also add or change a note in docs/decisions/.
#   A commit can opt out with a trailer line in its message:
#       Decision-Note: none - <why, e.g. typo fix>
#
# Usage:
#   decision-check.sh staged              files staged for the next commit
#   decision-check.sh pending             staged + unstaged + untracked files
#   decision-check.sh commit <sha>        one existing commit
#   decision-check.sh range <base> <head> every commit in base..head, taken together
#
# The commit message for the opt-out is read from $DECISION_MSG_FILE (a file) or
# $DECISION_MSG (a string); in commit and range mode it comes from git itself.
#
# Exit 0: fine. Exit 1: code changed with no note. Exit 2: usage error.
# What counts as "not code" lives in .decision-exempt at the repo root.

set -euo pipefail

NOTES_DIR="docs/decisions"
root=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "decision-check: not a git repo" >&2; exit 2; }
cd "$root"

# Patterns for files that never need a note. One glob per line; '*' matches '/' too.
exempt=()
if [[ -f .decision-exempt ]]; then
  while IFS= read -r line; do
    line="${line%%#*}"; line="${line%"${line##*[![:space:]]}"}"
    [[ -n "$line" ]] && exempt+=("$line")
  done < .decision-exempt
else
  exempt=("docs/*" "*.md" "LICENSE*" "license.txt" ".gitignore")
fi

is_note() {
  [[ "$1" == "$NOTES_DIR"/*.md ]] || return 1
  case "${1##*/}" in README.md|TEMPLATE.md) return 1 ;; esac
  return 0
}

is_exempt() {
  local f="$1" p
  for p in "${exempt[@]}"; do
    # shellcheck disable=SC2053
    [[ "$f" == $p ]] && return 0
  done
  return 1
}

# Print "STATUS<TAB>PATH" lines for the files in scope.
changes() {
  case "$1" in
    staged)  git diff --cached --name-status --no-renames ;;
    pending) git diff --cached --name-status --no-renames
             git diff --name-status --no-renames
             git ls-files --others --exclude-standard | sed 's/^/A\t/' ;;
    commit)  git diff-tree --root --no-commit-id -r --name-status --no-renames "$2" ;;
    range)   git diff --name-status --no-renames "$2" "$3" ;;
  esac
}

message() {
  case "$1" in
    commit) git log -1 --format=%B "$2" ;;
    range)  git log --format=%B "$2..$3" ;;
    *)      if [[ -n "${DECISION_MSG_FILE:-}" && -f "$DECISION_MSG_FILE" ]]; then cat "$DECISION_MSG_FILE"
            else printf '%s' "${DECISION_MSG:-}"; fi ;;
  esac
}

mode="${1:-}"
case "$mode" in
  staged|pending) ;;
  commit) [[ $# -eq 2 ]] || { echo "usage: $0 commit <sha>" >&2; exit 2; } ;;
  range)  [[ $# -eq 3 ]] || { echo "usage: $0 range <base> <head>" >&2; exit 2; } ;;
  *) sed -n '2,20p' "$0" >&2; exit 2 ;;
esac

# A note still carrying the template's placeholder title has not been written.
note_content() {
  case "$mode" in
    commit) git show "$2:$1" 2>/dev/null ;;
    range)  git show "$3:$1" 2>/dev/null ;;
    staged) git show ":$1" 2>/dev/null ;;
    *)      cat "$1" 2>/dev/null ;;
  esac
}
unfilled=""
code=() note=""
while IFS=$'\t' read -r status path; do
  [[ -z "${path:-}" ]] && continue
  if is_note "$path"; then
    [[ "$status" == D ]] && continue
    if note_content "$path" "${2:-}" "${3:-}" | grep -q '^# <Short title'; then
      unfilled="$path"
    else
      note="$path"
    fi
  elif ! is_exempt "$path"; then
    code+=("$path")
  fi
done < <(changes "$@" | sort -u)

if [[ ${#code[@]} -eq 0 ]]; then
  echo "decision-check: no code changes; no note needed."
  exit 0
fi
if [[ -n "$note" ]]; then
  echo "decision-check: ok, ${#code[@]} code file(s) covered by $note"
  exit 0
fi
OPT_OUT='(^|[^[:alnum:]_-])Decision-Note:[[:space:]]*none[[:space:]]*[-—:]+[[:space:]]*[^[:space:]]'
if [[ "$mode" == range ]]; then
  # No note anywhere in the range: every commit that touches code must opt out itself.
  bad=0
  for c in $(git rev-list "$2..$3"); do
    touches=0
    while IFS=$'\t' read -r _ p; do
      [[ -n "${p:-}" ]] && ! is_note "$p" && ! is_exempt "$p" && { touches=1; break; }
    done < <(git diff-tree --root --no-commit-id -r --name-status --no-renames "$c")
    if (( touches )) && ! git log -1 --format=%B "$c" | grep -qiE "$OPT_OUT"; then bad=1; fi
  done
  if (( bad == 0 )); then
    echo "decision-check: ok, every code commit in the range says no decision note is needed."
    exit 0
  fi
elif message "$@" | grep -qiE "$OPT_OUT"; then
  echo "decision-check: ok, commit says no decision note is needed (Decision-Note: none)."
  exit 0
fi

{
  echo "decision-check: ${#code[@]} code file(s) changed but no decision note in $NOTES_DIR/."
  [[ -n "$unfilled" ]] && echo "  ($unfilled is still the blank template; fill it in.)"
  printf '  %s\n' "${code[@]:0:10}"
  [[ ${#code[@]} -gt 10 ]] && echo "  ...and $(( ${#code[@]} - 10 )) more"
  echo
  echo "Write $NOTES_DIR/$(date +%Y-%m-%d)-<short-topic>.md from $NOTES_DIR/TEMPLATE.md and stage it with the code."
  echo "If the change really involves no decision (typo, formatting, version bump), add this line"
  echo "to the commit message instead:  Decision-Note: none - <reason>"
} >&2
exit 1
