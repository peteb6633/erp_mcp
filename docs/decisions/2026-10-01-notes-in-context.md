# Make future models see the notes, not just be told to read them

- **Date:** 2026-10-01
- **Asked by:** Pete Bright
- **Built by:** Claude (Opus 5.5) in a cloud Claude session
- **Status:** in force

## What was asked

Pete asked whether he could be sure a future Claude would read these notes. The honest answer was no: `AGENTS.md` tells it to, and nothing makes it. Pete agreed to four fixes: tests for the traps, a comment at each trap, an edit-time hook, and an index of notes at session start.

## Decisions

| Decision | Chosen | Rejected | Why | Who decided |
| --- | --- | --- | --- | --- |
| Overall approach | Four layers: tests, code comments, edit-time hook, session index | Relying on the instruction in `AGENTS.md` | A model can skip an instruction; it cannot skip a failing test or a comment in the line it edits | Pete |
| How the trap tests work | Read the source with `ast` and check its structure | Running the code against a fake or real Frappe | No Frappe install in CI; structure checks are enough to catch a "tidy-up" | model |
| Proving the tests | Broke each of the 12 traps on purpose; every one failed the right test | Trusting that the tests pass | A test that never fails proves nothing | model |
| Running the tests in CI | New `Tests` workflow: ruff, ruff format, pytest on Python 3.14 | Leaving tests local only | Until today nothing in CI ran the tests at all | model |
| Edit-time hook behaviour | First edit to a file a note mentions is **refused once** with the note lines as the reason; the retry goes through. Reading the file first shows the same lines alongside it, so no refusal | Only adding context after the edit | Claude Code adds PreToolUse context next to the tool result, which is after the edit has happened. Refusing once is the only way to put the notes in front of the model *before* it changes the file | model |
| How a note "mentions" a file | Repo path, a path ending such as `tools/builtin.py`, or the file name in backticks | Any bare file name | Bare names such as `__init__.py` would match far too much | model |
| Trap entries | Every trap now names its file, and the function where useful | Leaving them as written | The hook can only surface a trap that names the file | model |

## Traps for maintainers

- **`tests/test_traps.py` reads source, it does not run it.** That is deliberate (no Frappe in CI). If you restructure a function, update the test and the note in the same commit rather than deleting the test.
- **The edit-time hook refuses the first edit on purpose** (`.claude/hooks/decision-context.sh`). It is not a bug. It remembers per session which files it has shown, in `.git/decision-notes-seen/`.
- **When writing a trap into a note, name the file.** Otherwise the hook cannot find it.

## How it was checked

- 31 tests pass; ruff clean.
- Each of the 12 traps was broken in turn and the matching test failed (sid check, finally order, traceback capture, resource URL, `frappe.local.__dict__`, allow-list by name, submit default, cancel JSON default, old limit name, read-only default, ruff py314, unbracketed `except`).
- The hook was run by hand with sample hook input: the index lists the three notes; a first edit to `erp_mcp/api.py` is refused with its four traps; the retry is allowed; a file no note mentions, or a file outside the repo, is allowed.
- **Not checked:** the hooks inside a live Claude Code session. They follow the input and output formats in Claude Code's hooks reference.

## Gaps and open questions

- The `Tests` check is not yet required by the "Decision notes" ruleset. Pete to add `tests` to it, or the tests can fail without blocking a merge.
- All of this works in Claude Code only. Someone who pastes the code into another tool gets the comments and, at merge time, the tests, but not the hooks.
