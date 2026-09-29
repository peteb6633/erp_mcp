# ERP MCP

A Frappe app that lets MCP clients, such as Claude, work with an ERPNext site. Every call runs as the ERPNext user who signed in, so their roles and permissions apply. Every call is also written to an audit log.

Licence: MIT. Needs Frappe and ERPNext v16.

## What it offers

| Tool | What it does | Setting that gates it |
| --- | --- | --- |
| `whoami` | The connected user, roles and companies | always on |
| `describe_doctype` | Fields, child tables and the user's permissions for a DocType | always on |
| `list_documents` | Filtered, sorted, paged lists | always on |
| `search_documents` | Find a record by part of its name or title | always on |
| `get_document` | One record with its child rows | always on |
| `list_reports` / `run_report` | Find and run the site's standard reports | always on (custom SQL and script reports need Allow Custom SQL and Script Reports) |
| `create_document` / `update_document` | Drafts and edits | Allow Create and Update |
| `submit_document` | Post a draft to the ledger | Allow Submit |
| `cancel_document` | Reverse a submitted document | Allow Cancel |
| `delete_document` | Delete a draft or cancelled record | Allow Delete (off by default) |

There is no tool that runs arbitrary Python or SQL.

## Safety model

- **Frappe does the permission checks.** Tools call `frappe.get_list`, `check_permission`, `insert`, `save`, `submit` and the desk's report runner, and hide fields the user's permission level does not cover. Nothing uses `ignore_permissions`, except the write to the audit log.
- **Only real fields can be set.** Values must be fields of the DocType or its child tables. Internal keys such as `_action` or `flags`, which could change how Frappe saves a document, are refused. A child row named in an update must belong to that document.
- **Blocked DocTypes.** MCP Settings lists DocTypes that MCP can never read or change, even as an administrator, along with their child tables. By default it lists users, roles, permissions, scripts, webhooks and credentials. OAuth records and MCP Settings are always blocked.
- **Read-only DocTypes.** DocTypes that hold code, SQL, templates, schema or permissions can be read but never changed through MCP. These include Report, DocType, Custom Field, Property Setter, Print Format, Notification, scripts, Role, User, Workflow, Webhook and the website script settings. Child tables can only be changed through their parent document.
- **No joins to other DocTypes in filters.** Filters may name fields of the DocType and its own child tables only. Link traversal such as `owner.api_key` is refused, so a blocked DocType cannot be probed through a join.
- **Standard reports only by default.** Query and script reports made on the site can read any table, so they stay off until an administrator ticks **Allow Custom SQL and Script Reports**.
- **All or nothing per call.** Each request runs one tool. If it fails, the whole transaction is rolled back before the audit row is written.
- **Header auth only.** The endpoint accepts an OAuth bearer token or an API key in the `Authorization` header and refuses browser sessions, so a web page cannot drive it with a logged-in user's cookie. **Allowed OAuth Client IDs** can limit bearer tokens to the client Claude registered.
- **Audit log.** Each call records the user, OAuth client, tool, arguments (with Password fields masked), result, duration and IP address in **MCP Audit Log**. Rows older than 90 days are cleared by Log Settings.

What it does not protect against: code in ERPNext or other apps that commits the transaction itself, and anything the connected user's roles already allow. Connect with a dedicated user that has only the roles the job needs.

## Install

On a self-hosted bench:

```bash
bench get-app https://github.com/<org>/erp_mcp
bench --site <site> install-app erp_mcp
```

On Frappe Cloud, add the repo as a custom app on a private bench, then install it on the site.

## Connect Claude

1. Create an ERPNext user for the connection and give it only the roles it needs, such as Accounts User.
2. Open **MCP Settings** in the desk. Check the allowed actions. If the site's OAuth discovery is off, click **Enable OAuth for MCP clients**. (It is on by default in Frappe v16.)
   Before connecting Claude to real data, untick **Allow Submit** and **Allow Cancel** until you are happy with what it does on drafts.
3. Copy the **MCP Endpoint URL**. It looks like `https://<site>/api/method/erp_mcp.api.mcp`.
4. In Claude, go to **Settings → Connectors → Add custom connector**, paste the URL, then connect. Claude registers itself with the site, and you sign in as the user from step 1.
5. Optional: open **OAuth Client**, find the client Claude registered, and paste its ID into **Allowed OAuth Client IDs** in MCP Settings.

### Test without OAuth

For scripts and quick tests, an API key works too. Generate one for the user under **User → API Access**, then run the smoke test, which reads data and changes nothing:

```bash
python scripts/smoke_test.py https://<site> --key <api_key> --secret <api_secret>
```

Or call it directly:

```bash
curl -s https://<site>/api/method/erp_mcp.api.mcp \
  -H "Authorization: token <api_key>:<api_secret>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"whoami","arguments":{}}}'
```

## Add tools from your own app

Each customer's app can add tools without changing this one. In the customer app:

```python
# my_app/mcp.py
import frappe
from erp_mcp.tools import ToolError, mcp_tool


@mcp_tool(
	description="Total overdue sales invoices per customer group.",
	input_schema={
		"type": "object",
		"properties": {"company": {"type": "string"}},
		"required": ["company"],
		"additionalProperties": False,
	},
	read_only=True,
)
def overdue_by_group(company: str):
	rows = frappe.get_list(  # permission-aware
		"Sales Invoice",
		filters={"company": company, "status": "Overdue"},
		fields=["customer_group", "sum(outstanding_amount) as outstanding"],
		group_by="customer_group",
	)
	if not rows:
		raise ToolError("No overdue invoices.")
	return rows
```

```python
# my_app/hooks.py
required_apps = ["erp_mcp"]
erp_mcp_tools = ["my_app.mcp.overdue_by_group"]
```

Raise `ToolError` for problems the model should see and can fix. Other errors are logged and shown to the model as a short message without the traceback.

## How it works

- `erp_mcp/protocol.py` handles MCP over JSON-RPC and knows nothing about Frappe, so it has its own unit tests (`pytest tests/`).
- `erp_mcp/api.py` is the HTTP endpoint. It checks auth and settings, runs the tool, rolls back on failure and writes the audit log.
- `erp_mcp/tools/` holds the tool registry and the built-in tools.
- `erp_mcp/guard.py` holds the block lists and the checks on DocTypes, filters, field names and row limits.

The server uses MCP's Streamable HTTP transport in stateless mode. Each request gets one JSON response; it issues no session IDs and opens no event streams. It supports protocol versions 2025-03-26, 2025-06-18 and 2025-11-25.

Sign-in uses Frappe's own OAuth 2 provider. Frappe v16 provides dynamic client registration (RFC 7591), authorisation server metadata (RFC 8414) and protected resource metadata (RFC 9728), with PKCE and refresh tokens. Frappe v15 has none of these, so v16 is required.

## Tested

Against Frappe 16.35.1 and ERPNext 16.36.1 on a local bench:

- The full sign-in flow a Claude connector uses: discovery from a 401, dynamic registration, user sign-in and consent, PKCE code exchange with a `resource` parameter, a bearer call and a refresh.
- Reading, creating, updating, submitting and cancelling a Sales Invoice, and running Accounts Receivable and General Ledger.
- Refusals: role permission errors, blocked DocTypes in any case, child tables of blocked DocTypes, internal keys, rows from another document, link-traversal filters, writes to Report and Custom Field, custom SQL reports and Custom Reports wrapping them, browser sessions with a junk bearer token, and OAuth clients outside the allow-list.
- A failed create leaves no rows behind.

Not yet tested with Claude itself, or behind Frappe Cloud's proxy. The smoke test warns if the site reports an `http://` resource URL while being served over `https://`.
