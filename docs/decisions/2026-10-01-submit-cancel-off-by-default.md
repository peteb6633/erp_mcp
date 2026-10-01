# Submit and cancel off by default

- **Date:** 2026-10-01
- **Asked by:** Pete Bright
- **Built by:** Claude (Opus 5.5) in a cloud Claude session
- **Status:** in force

## What was asked

The 0.1 build recorded that Pete chose "read and draft writes" for v1, but MCP Settings shipped with Allow Submit and Allow Cancel **on**, so a new install let Claude post to and reverse ledger entries. See [2026-09-30-erp-mcp-v0.1.md](2026-09-30-erp-mcp-v0.1.md). Pete asked for the defaults to match his original choice.

## Decisions

| Decision | Chosen | Rejected | Why | Who decided |
| --- | --- | --- | --- | --- |
| v1 write scope | Read plus draft create and update; submit and cancel off by default | Submit and cancel on by default | Drafts can be thrown away; submit and cancel change the ledger | Pete |
| Where the defaults live | Both `DEFAULTS` in `guard.py` and the field defaults in `mcp_settings.json` | Only one of them | `guard.py` is used when no MCP Settings record exists; the JSON sets what a new install saves. They must agree | model |
| Sites already on 0.1 | Leave their saved settings alone; tell admins in the README to check the two boxes | A migration patch that switches them off | A patch would silently override a choice an admin may have made on purpose. The only known install is Pete's test bench | model |
| Tools | Unchanged. `submit_document` and `cancel_document` stay in the code and appear when an admin ticks the box | Removing the tools | Pete chose scope for v1, not removal; admins can still opt in | model |

## Traps for maintainers

- **Changing a field `default` in the JSON does not change existing sites.** `after_install` saves MCP Settings, so every installed site has stored values. Only new installs pick up these defaults.
- **Keep `DEFAULTS` in `guard.py` and the JSON defaults in step.** If they differ, a site with no settings record behaves differently from a freshly installed one.

## How it was checked

- `ruff check` and the protocol unit tests pass.
- **Not** run on a bench. To confirm on a fresh site: install the app and check that `tools/list` has no `submit_document` or `cancel_document` until the boxes are ticked.

## Gaps and open questions

- Existing installs keep submit and cancel on until an admin unticks them.
