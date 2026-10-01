# whoami: use `limit`, not the deprecated `limit_page_length`

- **Date:** 2026-10-01
- **Asked by:** Pete Bright
- **Built by:** Claude (Opus 5.5) in a cloud Claude session
- **Status:** in force

## What was asked

Pete asked for the `whoami` fix flagged in [2026-09-30-erp-mcp-v0.1.md](2026-09-30-erp-mcp-v0.1.md): `whoami` was the one call still passing `limit_page_length` to `frappe.get_list`.

## Decisions

| Decision | Chosen | Rejected | Why | Who decided |
| --- | --- | --- | --- | --- |
| Make the fix | Change `whoami` to `limit=50` | Leave it | Matches the rest of the code; the old name is going | Pete |
| Scope | One argument name; the cap stays at 50 companies | Paging the company list | Nothing suggests a user sees more than 50 companies | model |

## What the source says

Checked against the Frappe `version-16` branch (commit `97a5dd9`, 2026-09-30), `frappe/model/qb_query.py`:

- `limit_page_length` and `limit_start` still work. Frappe maps them to `limit` and `offset` and logs a deprecation warning.
- The warning says both names go in **v17**. So `whoami` worked on v16, but its 50-company cap would have broken on v17.

This corrects the 0.1 note, which said the old names "silently misbehaved" in v16. That came from the session record of the build and does not match the current v16 source. It may have been true of the 16.35.1 build used in testing, or the cause may have been something else; it was not re-checked. The rule stands either way: use `limit` and `offset`.

## How it was checked

- Read the v16 source as above.
- `ruff check` and the protocol unit tests pass. **Not** run against a live bench this time; the change is one keyword argument that v16 documents as the replacement.

## Gaps and open questions

- Other gap from the 0.1 note still open: MCP Settings ship with Allow Submit and Allow Cancel on, while Pete chose read plus draft writes.
