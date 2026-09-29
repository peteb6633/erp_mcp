"""Site-level limits that sit on top of Frappe's own role permissions.

Frappe decides what the connected user may do. These checks narrow that
further, so a powerful account connected by mistake still cannot reach
credentials, security settings or code-execution DocTypes through MCP.
"""

from __future__ import annotations

import re

import frappe

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

# Readable but never writable through MCP.
READ_ONLY = frozenset({"MCP Audit Log"})

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ORDER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\s+(asc|desc))?$", re.IGNORECASE)

DEFAULTS = frappe._dict(
	enabled=1,
	allow_writes=1,
	allow_submit=1,
	allow_cancel=1,
	allow_delete=0,
	max_rows=100,
	blocked_doctypes="",
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


def blocked_doctypes(settings: frappe._dict) -> set[str]:
	listed = {line.strip() for line in (settings.blocked_doctypes or "").splitlines() if line.strip()}
	return listed | ALWAYS_BLOCKED


def check_doctype(doctype: str, settings: frappe._dict, write: bool = False) -> None:
	"""Raise ToolError unless the DocType exists and MCP may touch it."""
	if not doctype or not isinstance(doctype, str):
		raise ToolError("A DocType name is required.")
	if doctype in blocked_doctypes(settings):
		raise ToolError(f"{doctype} is not available through MCP on this site.")
	if write and doctype in READ_ONLY:
		raise ToolError(f"{doctype} is read-only through MCP.")
	if not frappe.db.exists("DocType", doctype):
		raise ToolError(f"No DocType called '{doctype}'. Names are case-sensitive, e.g. 'Sales Invoice'.")


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
