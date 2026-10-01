# Agent instructions

<!-- decision-notes:start -->
## Decision notes (required)

Every commit that changes code must add or update a note in `docs/decisions/`, written from `docs/decisions/TEMPLATE.md`. Write it before you commit, while you still hold the context. A future maintainer, human or model, will have only the code and these notes.

- Record what was asked, each real decision (chosen, rejected, why), assumptions, security and risk findings, traps (deliberate code that looks wrong) and open gaps.
- Mark who decided each point: the user by name if they chose or approved it, otherwise `model`. Never claim the user approved something they did not see.
- Before changing existing code, read the notes that mention it. Do not "fix" something a note lists as a trap without saying why in a new note.
- If a change overturns an old note, mark the old note `replaced by` the new one in the same commit.
- Only for changes with no decision at all (typo, formatting, version bump) may the commit message carry `Decision-Note: none - <reason>` instead.

`scripts/decision-check.sh` enforces this in a Claude Code hook, a git `commit-msg` hook and CI. Do not bypass it with `--no-verify`.
<!-- decision-notes:end -->
