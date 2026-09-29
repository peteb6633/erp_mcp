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
| `list_reports` / `run_report` | Find and run query and script reports | always on |
| `create_document` / `update_document` | Drafts and edits | Allow Create and Update |
| `submit_document` | Post a draft to the ledger | Allow Submit |
| `cancel_document` | Reverse a submitted document | Allow Cancel |
| `delete_document` | Delete a draft or cancelled record | Allow Delete (off by default) |

There is no tool that runs arbitrary Python or SQL.

## Safety model

- **Frappe does the permission checks.** Tools call `frappe.get_list`, `check_permission`, `insert`, `save`, `submit` and the desk's report runner. Nothing uses `ignore_permissions`, except the write to the audit log.
- **Blocked DocTypes.** MCP Settings holds a list of DocTypes that MCP can never touch, even as an administrator. By default it lists users, roles, permissions, scripts, webhooks and credentials. OAuth records and MCP Settings are always blocked.
- **One savepoint per call.** If a tool fails halfway, its changes are rolled back.
- **Bearer tokens only.** The endpoint refuses browser session cookies, so a web page cannot use a logged-in user's session to drive it.
- **Audit log.** Each call records the user, tool, arguments, result, duration and IP address in **MCP Audit Log**. Rows older than 90 days are cleared by Log Settings.

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
3. Copy the **MCP Endpoint URL**. It looks like `https://<site>/api/method/erp_mcp.api.mcp`.
4. In Claude, go to **Settings → Connectors → Add custom connector**, paste the URL, then connect. Claude registers itself with the site, and you sign in as the user from step 1.

### Test without OAuth

For scripts and quick tests, an API key works too. Generate one for the user under **User → API Access**, then:

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
	input_schema={"type": "object", "properties": {"company": {"type": "string"}}, "required": ["company"], "additionalProperties": False},
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
- `erp_mcp/api.py` is the HTTP endpoint. It checks auth and settings, runs each tool in a savepoint and writes the audit log.
- `erp_mcp/tools/` holds the tool registry and the built-in tools.
- `erp_mcp/guard.py` holds the blocked-DocType and row-limit checks.

The server uses MCP's Streamable HTTP transport in stateless mode. Each request gets one JSON response; it issues no session IDs and opens no event streams. It supports protocol versions 2025-03-26, 2025-06-18 and 2025-11-25.

Sign-in uses Frappe's own OAuth 2 provider. Frappe v16 provides dynamic client registration (RFC 7591), authorisation server metadata (RFC 8414) and protected resource metadata (RFC 9728).
