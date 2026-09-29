"""HTTP endpoint for MCP clients.

URL: ``https://<site>/api/method/erp_mcp.api.mcp``

Clients authenticate with an ``Authorization`` header: an OAuth bearer token
issued by the site's own OAuth provider (what Claude's custom connectors use),
or a Frappe API key (``token <key>:<secret>``) for scripts and testing. Requests
that carry a browser session cookie are refused, so a web page cannot drive
this endpoint on a logged-in user's behalf.
"""

from __future__ import annotations

import json
import time
from typing import Any

import frappe
from frappe.utils import strip_html
from werkzeug.wrappers import Response

import erp_mcp
from erp_mcp import guard
from erp_mcp.protocol import Server, Tool, ToolError
from erp_mcp.tools import get_tools

INSTRUCTIONS = (
	"This server connects to an ERPNext site. Every call acts as the signed-in ERPNext user and is "
	"limited by that user's roles. Start with whoami. Before creating or changing a record, call "
	"describe_doctype to learn its fields. Documents are created as drafts; submitting posts them to "
	"the ledger, so check a draft with get_document and confirm with the user before submit_document "
	"or cancel_document. Use search_documents to turn a name into an exact document ID, and "
	"list_reports then run_report for financial and stock figures."
)

_SAFE_ERRORS = (
	frappe.PermissionError,
	frappe.DoesNotExistError,
	frappe.ValidationError,
	frappe.DuplicateEntryError,
	frappe.TimestampMismatchError,
)


@frappe.whitelist(allow_guest=True, methods=["GET", "POST", "DELETE"])
def mcp():
	request = frappe.request

	if request.method != "POST":
		# Stateless server: no SSE stream to open and no session to end.
		return Response(status=405, headers={"Allow": "POST"})

	# Only header auth counts. When Frappe authenticates a request from its
	# Authorization header it sets the session ID to the user's name; a browser
	# session (sid cookie or ?sid=) keeps a random ID. Refuse the latter, since a
	# failed header check would otherwise fall back to the browser session.
	from_browser_session = frappe.session.sid != frappe.session.user
	if (
		from_browser_session
		or not frappe.get_request_header("Authorization")
		or frappe.session.user in (None, "", "Guest")
	):
		return _unauthorised()

	settings = guard.get_settings()
	if not settings.enabled:
		return _json(503, {"error": "MCP access is turned off in MCP Settings on this site."})

	frappe.local.erp_mcp_client = _oauth_client()
	if _is_bearer() and not guard.allowed_client(settings, frappe.local.erp_mcp_client):
		return _json(403, {"error": "This OAuth client is not allowed to use MCP on this site."})

	server = Server(
		name="erp-mcp",
		title="ERPNext",
		version=erp_mcp.__version__,
		tools=lambda: get_tools(settings),
		invoke=lambda tool, args: _invoke(tool, args, settings),
		instructions=INSTRUCTIONS,
		dumps=_dumps,
	)
	reply = server.handle(request.get_data(), frappe.get_request_header("MCP-Protocol-Version"))

	if reply.body is None:
		return Response(status=reply.status)
	return _json(reply.status, reply.body)


def _invoke(tool: Tool, args: dict, settings: frappe._dict) -> Any:
	"""Run one tool and record it in the audit log.

	Each HTTP request runs one tool, so on failure the whole transaction is
	rolled back (which also drops queued after-commit jobs) before the audit
	row is written. Frappe commits whatever is left when the request ends.
	"""
	started = time.monotonic()
	status, error, traceback = "Success", None, None

	try:
		return tool.handler(**args)
	except ToolError as e:
		status, error = "Error", str(e)
		raise
	except frappe.PermissionError as e:
		status, error = "Denied", _message(e) or "Permission denied."
		raise ToolError(f"Permission denied: {error}") from None
	except _SAFE_ERRORS as e:
		status, error = "Error", _message(e) or type(e).__name__
		raise ToolError(error) from None
	except Exception as e:
		status, error = "Error", f"{type(e).__name__}: {e}"
		traceback = frappe.get_traceback()
		raise ToolError(
			f"{tool.name} failed unexpectedly. An administrator can see the details in the Error Log."
		) from None
	finally:
		if status != "Success":
			frappe.db.rollback()
		if traceback:
			frappe.log_error(title=f"ERP MCP: {tool.name} failed", message=traceback)
		frappe.clear_messages()
		_audit(tool.name, args, status, error, started, settings)


def _audit(tool: str, args: dict, status: str, error: str | None, started: float, settings) -> None:
	try:
		frappe.get_doc(
			{
				"doctype": "MCP Audit Log",
				"user": frappe.session.user,
				"tool": tool,
				"status": status,
				"client": getattr(frappe.local, "erp_mcp_client", None),
				"arguments": _dumps(_redact(args)) if settings.log_arguments else None,
				"error": (error or "")[:1000] or None,
				"duration_ms": int((time.monotonic() - started) * 1000),
				"ip_address": getattr(frappe.local, "request_ip", None),
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="ERP MCP: could not write audit log")


def _redact(args: dict) -> dict:
	"""Hide Password field values before they are stored in the audit log."""
	doctype, values = args.get("doctype"), args.get("values")
	if not isinstance(doctype, str) or not isinstance(values, dict):
		return args
	try:
		meta = frappe.get_meta(doctype)
	except Exception:
		return args

	def scrub(m, row: dict) -> dict:
		out = {}
		for key, value in row.items():
			df = m.get_field(key) if isinstance(key, str) else None
			if df and df.fieldtype == "Password":
				value = "********"
			elif df and df.fieldtype in ("Table", "Table MultiSelect") and isinstance(value, list):
				child = frappe.get_meta(df.options)
				value = [scrub(child, r) if isinstance(r, dict) else r for r in value]
			out[key] = value
		return out

	return {**args, "values": scrub(meta, values)}


def _is_bearer() -> bool:
	return frappe.get_request_header("Authorization", "").lower().startswith("bearer ")


def _oauth_client() -> str | None:
	if not _is_bearer():
		return None
	token = frappe.get_request_header("Authorization", "").split(" ", 1)[1].strip()
	return frappe.db.get_value("OAuth Bearer Token", token, "client")


def _message(exc: Exception) -> str:
	text = str(exc) or ""
	if not text and frappe.local.message_log:
		last = frappe.local.message_log[-1]
		text = last.get("message", "") if isinstance(last, dict) else str(last)
	return strip_html(text).strip()


def _dumps(value: Any) -> str:
	"""Compact JSON, keeping key order, so results use less of the model's context."""
	from frappe.utils.response import json_handler

	return json.dumps(value, default=json_handler, separators=(",", ":"), ensure_ascii=False)


def _json(status: int, body: dict) -> Response:
	return Response(json.dumps(body, default=str), status=status, mimetype="application/json")


def _unauthorised() -> Response:
	from frappe.integrations.oauth2 import get_resource_url

	host = get_resource_url()
	return Response(
		json.dumps({"error": "unauthorized", "error_description": "Send an OAuth bearer token."}),
		status=401,
		mimetype="application/json",
		headers={
			"WWW-Authenticate": f'Bearer resource_metadata="{host}/.well-known/oauth-protected-resource"'
		},
	)
