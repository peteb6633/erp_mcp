"""Site-level limits that sit on top of Frappe's own role permissions.

Frappe decides what the connected user may do. These checks narrow that
further, so a powerful account connected by mistake still cannot reach
credentials or security settings, or change code, SQL, schema or permissions,
through MCP.
"""

from __future__ import annotations

import re

import frappe
from frappe.model import table_fields

from erp_mcp.protocol import ToolError

SETTINGS = "MCP Settings"

# Never reachable through MCP, whatever MCP Settings says.
ALWAYS_BLOCKED = frozenset(
	{
		"OAuth Bearer Token",
		"OAuth Authorization Code",
		"OAuth Client",
		"OAuth Settings",
		"OAuth Provider Settings",
		"MCP Settings",
	}
)

# Readable, but never writable through MCP. These hold code, SQL, templates,
# schema or permissions; some also commit the transaction (DDL), which would
# defeat the per-call rollback.
WRITE_BLOCKED = frozenset(
	{
		"MCP Audit Log",
		"Report",
		"DocType",
		"Custom Field",
		"Property Setter",
		"Customize Form",
		"DocType Layout",
		"Print Format",
		"Notification",
		"Server Script",
		"Client Script",
		"Web Form",
		"Web Page",
		"Page",
		"Workspace",
		"Module Def",
		"Role",
		"Role Profile",
		"Module Profile",
		"DocPerm",
		"Custom DocPerm",
		"User Permission",
		"Webhook",
		"Auto Email Report",
		"Scheduled Job Type",
		"Data Import",
		"Dashboard Chart Source",
		"Number Card",
		"System Settings",
		"User",
		"Custom Role",
		"Workflow",
		"Custom HTML Block",
		"Website Script",
		"Website Settings",
		"Web Template",
		"Letter Head",
	}
)

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
ORDER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\s+(asc|desc))?$", re.IGNORECASE)

# Keep in step with the field defaults in mcp_settings.json; submit and cancel
# are off by Pete's choice. See docs/decisions/2026-10-01-submit-cancel-off-by-default.md.
DEFAULTS = frappe._dict(
	enabled=1,
	allow_writes=1,
	allow_submit=0,
	allow_cancel=0,
	allow_delete=0,
	allow_custom_reports=0,
	max_rows=100,
	blocked_doctypes="",
	allowed_oauth_clients="",
	log_arguments=1,
)


def get_settings() -> frappe._dict:
	"""MCP Settings as a plain dict, with defaults if the record is missing."""
	settings = frappe._dict(DEFAULTS)
	try:
		doc = frappe.get_cached_doc(SETTINGS)
	except frappe.DoesNotExistError:
		return settings
	for key in DEFAULTS:
		value = doc.get(key)
		if value is not None:
			settings[key] = value
	return settings


def _lines(text: str | None) -> set[str]:
	return {line.strip() for line in (text or "").splitlines() if line.strip()}


def blocked_doctypes(settings: frappe._dict) -> set[str]:
	"""Blocked DocType names, case-folded for comparison."""
	return {name.casefold() for name in _lines(settings.blocked_doctypes) | ALWAYS_BLOCKED}


def is_blocked(doctype: str, settings: frappe._dict) -> bool:
	"""True if the DocType, or any DocType it is a child table of, is blocked."""
	blocked = blocked_doctypes(settings)
	if doctype.casefold() in blocked:
		return True
	return any(parent.casefold() in blocked for parent in _parents_of(doctype))


def _parents_of(doctype: str) -> set[str]:
	"""DocTypes that hold this one as a child table. Cached for the request."""
	# Not frappe.local.__dict__: that failed on a live site. See docs/decisions/2026-09-30-erp-mcp-v0.1.md (Traps).
	cache = getattr(frappe.local, "erp_mcp_parents", None)
	if cache is None:
		cache = frappe.local.erp_mcp_parents = {}
	if doctype not in cache:
		parents: set[str] = set()
		if frappe.db.get_value("DocType", doctype, "istable"):
			for source in ("DocField", "Custom Field"):
				parents.update(
					frappe.get_all(
						source,
						filters={"options": doctype, "fieldtype": ["in", list(table_fields)]},
						pluck="dt" if source == "Custom Field" else "parent",
					)
				)
		cache[doctype] = parents
	return cache[doctype]


def check_doctype(doctype: str, settings: frappe._dict, write: bool = False) -> None:
	"""Raise ToolError unless the DocType exists, is spelt exactly, and MCP may touch it.

	The database compares names without regard to case, so 'user' would find
	'User'. Checking the exact spelling stops case variants slipping past the
	block list.
	"""
	if not doctype or not isinstance(doctype, str):
		raise ToolError("A DocType name is required.")
	canonical = frappe.db.get_value("DocType", doctype, "name")
	if not canonical:
		raise ToolError(f"No DocType called '{doctype}'.")
	if is_blocked(canonical, settings):
		raise ToolError(f"{canonical} is not available through MCP on this site.")
	if canonical != doctype:
		raise ToolError(f"DocType names are case-sensitive. Did you mean '{canonical}'?")
	if write and doctype in WRITE_BLOCKED:
		raise ToolError(f"{doctype} can be read but not changed through MCP.")
	if write and frappe.get_meta(doctype).istable:
		raise ToolError(
			f"{doctype} is a child table. Change its rows through the parent document with update_document."
		)


def check_filters(filters, doctype: str, settings: frappe._dict) -> None:
	"""Allow plain field filters on the DocType and its child tables only.

	Frappe accepts 'link_field.other_field' and filters naming other DocTypes,
	which would let a caller probe blocked DocTypes through joins. Refuse both.
	"""
	if filters is None:
		return
	children = {df.options for df in frappe.get_meta(doctype).get_table_fields()}

	def field(name):
		if not isinstance(name, str) or not NAME_RE.match(name):
			raise ToolError(
				f"Filter field '{name}' is not allowed. Use plain field names of {doctype} or its child tables."
			)

	if isinstance(filters, dict):
		for key in filters:
			field(key)
		return
	if not isinstance(filters, list):
		raise ToolError("filters must be an object or a list.")
	for item in filters:
		if not isinstance(item, list | tuple):
			raise ToolError("Each filter in a list must itself be a list, e.g. ['status', '=', 'Paid'].")
		if len(item) == 3:
			field(item[0])
		elif len(item) == 4:
			if item[0] != doctype and item[0] not in children:
				raise ToolError(f"Filters may only name {doctype} or one of its child tables.")
			if is_blocked(item[0], settings):
				raise ToolError(f"{item[0]} is not available through MCP on this site.")
			field(item[1])
		else:
			raise ToolError(
				"Each filter must be [field, operator, value] or [doctype, field, operator, value]."
			)


def clamp_limit(requested: int | None, settings: frappe._dict, default: int = 20) -> int:
	ceiling = max(1, int(settings.max_rows or DEFAULTS.max_rows))
	if requested is None:
		return min(default, ceiling)
	return max(1, min(int(requested), ceiling))


def check_fieldnames(names: list[str]) -> None:
	for n in names:
		if not isinstance(n, str) or not NAME_RE.match(n):
			raise ToolError(f"'{n}' is not a valid field name. Use plain field names such as 'grand_total'.")


def check_order_by(order_by: str | None) -> None:
	if order_by and not ORDER_RE.match(order_by.strip()):
		raise ToolError("order_by must be a field name, optionally followed by asc or desc.")


def allowed_client(settings: frappe._dict, client_id: str | None) -> bool:
	"""If MCP Settings lists OAuth Client IDs, only tokens from those clients may connect.

	Matches on the client ID only. Anyone can register a client with any name,
	so a name such as "Claude" proves nothing.
	"""
	allowed = _lines(settings.allowed_oauth_clients)
	if not allowed:
		return True
	return bool(client_id) and client_id in allowed
