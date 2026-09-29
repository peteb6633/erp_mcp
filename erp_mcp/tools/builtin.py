"""Built-in tools.

Every tool runs as the connected user and goes through Frappe's normal
permission checks: ``frappe.get_list`` for lists, ``check_permission`` and the
document lifecycle (``insert``, ``save``, ``submit``, ``cancel``) for single
records, and the desk's own report runner for reports. Nothing here uses
``ignore_permissions`` or raw SQL.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.model import no_value_fields, table_fields

from erp_mcp import guard
from erp_mcp.tools import ToolError, mcp_tool

__all__ = [
	"whoami",
	"describe_doctype",
	"list_documents",
	"search_documents",
	"get_document",
	"create_document",
	"update_document",
	"submit_document",
	"cancel_document",
	"delete_document",
	"list_reports",
	"run_report",
]

# Keys a caller may never set directly; Frappe manages them.
_MANAGED_KEYS = frozenset(
	{
		"doctype",
		"owner",
		"creation",
		"modified",
		"modified_by",
		"docstatus",
		"idx",
		"parent",
		"parentfield",
		"parenttype",
		"amended_from",
		"_user_tags",
		"_comments",
		"_assign",
		"_liked_by",
		"_seen",
	}
)

_DOCTYPE = {"type": "string", "description": "DocType name, e.g. 'Sales Invoice'. Case-sensitive."}
_NAME = {"type": "string", "description": "Document ID (the 'name' field), e.g. 'ACC-SINV-2026-00001'."}


def _obj(properties: dict, required: list[str] | None = None) -> dict:
	schema = {"type": "object", "properties": properties, "additionalProperties": False}
	if required:
		schema["required"] = required
	return schema


def _settings() -> frappe._dict:
	return guard.get_settings()


def _compact(value: Any) -> Any:
	"""Drop empty values so documents fit comfortably in the model's context."""
	if isinstance(value, dict):
		return {
			k: _compact(v) for k, v in value.items() if v not in (None, "", []) and k not in ("__onload",)
		}
	if isinstance(value, list):
		return [_compact(v) for v in value]
	return value


def _clean_values(meta, values: dict, *, allow_name: bool, existing=None) -> dict:
	"""Accept only real fields of the DocType and its child tables.

	Anything else (``_action``, ``flags``, ``__islocal``) could change how Frappe
	saves the document, so it is refused. On update, a child row's ``name`` must
	be one of this document's own rows.
	"""
	if not isinstance(values, dict):
		raise ToolError("values must be an object of field names and values.")
	out = {}
	for key, value in values.items():
		if key == "name":
			if not allow_name:
				raise ToolError("'name' cannot be changed; renaming is not supported.")
			out[key] = value
			continue
		if not isinstance(key, str) or not guard.NAME_RE.match(key) or key in _MANAGED_KEYS:
			raise ToolError(f"'{key}' cannot be set through MCP.")
		df = meta.get_field(key)
		if not df or (df.fieldtype in no_value_fields and df.fieldtype not in table_fields):
			raise ToolError(f"'{key}' is not a field of {meta.name}. Use describe_doctype to see the fields.")
		if df.fieldtype in table_fields:
			value = _clean_rows(df, value, existing)
		out[key] = value
	return out


def _clean_rows(df, rows, existing) -> list[dict]:
	"""Check child rows. On update, a row given by ``name`` keeps its other values."""
	if not isinstance(rows, list):
		raise ToolError(f"'{df.fieldname}' is a child table; give a list of row objects.")
	child_meta = frappe.get_meta(df.options)
	own_rows = {r.name: r for r in existing.get(df.fieldname)} if existing is not None else {}
	clean_rows = []
	for row in rows:
		if not isinstance(row, dict):
			raise ToolError(f"Rows in '{df.fieldname}' must be objects.")
		clean = {}
		for key, value in row.items():
			if key == "name":
				if not isinstance(value, str) or value not in own_rows:
					raise ToolError(
						f"'{value}' is not a row of this document's '{df.fieldname}' table. "
						"Leave 'name' out to add a new row."
					)
			elif (
				not isinstance(key, str)
				or not guard.NAME_RE.match(key)
				or key in _MANAGED_KEYS
				or not child_meta.has_field(key)
			):
				raise ToolError(f"'{key}' is not a field of {df.options}.")
			clean[key] = value
		if "name" in clean:
			current = own_rows[clean["name"]]
			merged = {f: current.get(f) for f in child_meta.get_valid_columns() if f not in _MANAGED_KEYS}
			merged.update(clean)
			clean = merged
		clean_rows.append(clean)
	return clean_rows


def _summary(doc) -> dict:
	meta = frappe.get_meta(doc.doctype)
	out = {"doctype": doc.doctype, "name": doc.name, "docstatus": doc.docstatus}
	for field in (meta.get_title_field(), "status", "grand_total", "outstanding_amount", "currency"):
		if field and field != "name" and doc.get(field) not in (None, ""):
			out[field] = doc.get(field)
	return out


def _describe_fields(meta, readable_levels: set[int] | None, include_read_only: bool) -> list[dict]:
	fields = []
	for df in meta.fields:
		if df.fieldtype in no_value_fields and df.fieldtype not in table_fields:
			continue
		if df.hidden and not df.reqd:
			continue
		if df.read_only and not include_read_only:
			continue
		if readable_levels is not None and (df.permlevel or 0) not in readable_levels:
			continue
		item = {"fieldname": df.fieldname, "label": df.label, "fieldtype": df.fieldtype}
		for key in ("options", "reqd", "read_only", "default", "allow_on_submit"):
			value = df.get(key)
			if value not in (None, "", 0, "0"):
				item[key] = value
		fields.append(item)
	return fields


# -- identity ---------------------------------------------------------------


@mcp_tool(
	title="Who am I",
	description=(
		"Show the ERPNext user this connection acts as, their roles, and the companies they can see. "
		"Call this first to check the connection."
	),
	input_schema=_obj({}),
	read_only=True,
	idempotent=True,
)
def whoami() -> dict:
	user = frappe.session.user
	try:
		companies = frappe.get_list("Company", pluck="name", limit_page_length=50)
	except (frappe.PermissionError, frappe.DoesNotExistError):
		companies = []
	return {
		"user": user,
		"full_name": frappe.utils.get_fullname(user),
		"roles": sorted(r for r in frappe.get_roles(user) if r not in ("All", "Guest")),
		"default_company": frappe.defaults.get_user_default("Company"),
		"companies": companies,
		"site": frappe.local.site,
	}


# -- schema -----------------------------------------------------------------


@mcp_tool(
	title="Describe DocType",
	description=(
		"List the fields of a DocType (a record type such as 'Customer' or 'Sales Invoice'), including "
		"child tables, which fields are required, and what the connected user may do with it. "
		"Call this before creating or updating documents. Read-only (calculated) fields are left out "
		"unless include_read_only is true."
	),
	input_schema=_obj(
		{
			"doctype": _DOCTYPE,
			"include_read_only": {
				"type": "boolean",
				"description": "Also list read-only fields such as totals. Default false.",
			},
		},
		["doctype"],
	),
	read_only=True,
	idempotent=True,
)
def describe_doctype(doctype: str, include_read_only: bool = False) -> dict:
	settings = _settings()
	guard.check_doctype(doctype, settings)
	if not frappe.has_permission(doctype, "read"):
		raise ToolError(f"You do not have permission to read {doctype}.")

	meta = frappe.get_meta(doctype)
	levels = set(meta.get_permlevel_access("read")) if hasattr(meta, "get_permlevel_access") else {0}
	levels.add(0)

	child_tables = {}
	for df in meta.get_table_fields():
		child_meta = frappe.get_meta(df.options)
		child_tables[df.fieldname] = {
			"doctype": df.options,
			"fields": _describe_fields(child_meta, None, include_read_only),
		}

	return {
		"doctype": doctype,
		"module": meta.module,
		"is_submittable": bool(meta.is_submittable),
		"is_single": bool(meta.issingle),
		"is_child_table": bool(meta.istable),
		"title_field": meta.get_title_field(),
		"naming": meta.autoname,
		"your_permissions": {
			p: bool(frappe.has_permission(doctype, p))
			for p in ("read", "write", "create", "submit", "cancel", "delete", "report")
		},
		"fields": _describe_fields(meta, levels, include_read_only),
		"child_tables": child_tables,
	}


# -- reading ----------------------------------------------------------------


@mcp_tool(
	title="List documents",
	description=(
		"List documents of a DocType with optional filters, fields and sort order. Filters use Frappe "
		'syntax: an object such as {"status": "Overdue", "customer": "Acme Ltd"}, or a list such as '
		'[["grand_total", ">", 1000], ["posting_date", "between", ["2026-01-01", "2026-03-31"]]]. '
		"Returns 'more': true when there are further rows; page with 'start'."
	),
	input_schema=_obj(
		{
			"doctype": _DOCTYPE,
			"filters": {"type": ["object", "array"], "description": "Frappe filters (object or list)."},
			"fields": {
				"type": "array",
				"items": {"type": "string"},
				"description": "Field names to return. Defaults to name, title, status and modified.",
			},
			"order_by": {
				"type": "string",
				"description": "e.g. 'posting_date desc'. Default 'modified desc'.",
			},
			"limit": {
				"type": "integer",
				"description": "Rows to return (default 20, capped by site setting).",
			},
			"start": {"type": "integer", "description": "Row offset for paging. Default 0."},
		},
		["doctype"],
	),
	read_only=True,
	idempotent=True,
)
def list_documents(
	doctype: str,
	filters: dict | list | None = None,
	fields: list[str] | None = None,
	order_by: str | None = None,
	limit: int | None = None,
	start: int = 0,
) -> dict:
	settings = _settings()
	guard.check_doctype(doctype, settings)
	meta = frappe.get_meta(doctype)

	if fields:
		guard.check_fieldnames(fields)
	else:
		fields = ["name"]
		title = meta.get_title_field()
		if title and title != "name":
			fields.append(title)
		if meta.has_field("status"):
			fields.append("status")
		if meta.is_submittable:
			fields.append("docstatus")
		fields.append("modified")
	guard.check_order_by(order_by)
	guard.check_filters(filters, doctype, settings)

	limit = guard.clamp_limit(limit, settings)
	start = max(0, int(start or 0))
	rows = frappe.get_list(
		doctype,
		fields=fields,
		filters=filters,
		order_by=order_by or "modified desc",
		offset=start,
		limit=limit + 1,
	)
	return {
		"doctype": doctype,
		"start": start,
		"rows": rows[:limit],
		"more": len(rows) > limit,
	}


@mcp_tool(
	title="Search documents",
	description=(
		"Find documents of a DocType whose ID, title or search fields contain some text. "
		"Use it to turn a name such as 'acme' into the exact document ID."
	),
	input_schema=_obj(
		{
			"doctype": _DOCTYPE,
			"text": {"type": "string", "description": "Text to look for."},
			"limit": {"type": "integer", "description": "Rows to return (default 10)."},
		},
		["doctype", "text"],
	),
	read_only=True,
	idempotent=True,
)
def search_documents(doctype: str, text: str, limit: int | None = None) -> dict:
	settings = _settings()
	guard.check_doctype(doctype, settings)
	meta = frappe.get_meta(doctype)

	permitted = set(meta.get_permitted_fieldnames())
	search_fields = ["name"]
	for f in [meta.get_title_field(), *(meta.get_search_fields() or [])]:
		if f and f not in search_fields and f in permitted:
			search_fields.append(f)

	pattern = f"%{text.strip()}%"
	rows = frappe.get_list(
		doctype,
		fields=search_fields,
		or_filters=[[f, "like", pattern] for f in search_fields],
		order_by="modified desc",
		limit=guard.clamp_limit(limit, settings, default=10),
	)
	return {"doctype": doctype, "rows": _compact(rows)}


@mcp_tool(
	title="Get document",
	description="Fetch one document with all the fields the connected user may read, including child table rows.",
	input_schema=_obj({"doctype": _DOCTYPE, "name": _NAME}, ["doctype", "name"]),
	read_only=True,
	idempotent=True,
)
def get_document(doctype: str, name: str) -> dict:
	guard.check_doctype(doctype, _settings())
	from frappe.client import get

	return _compact(get(doctype, name))


# -- writing ----------------------------------------------------------------


@mcp_tool(
	title="Create document",
	description=(
		"Create a new document as a draft. 'values' maps field names to values; child tables are lists "
		'of row objects, e.g. {"customer": "Acme Ltd", "items": [{"item_code": "WIDGET", "qty": 2}]}. '
		"Use describe_doctype first to see required fields. Submittable documents stay as drafts until "
		"submit_document is called."
	),
	input_schema=_obj(
		{
			"doctype": _DOCTYPE,
			"values": {"type": "object", "description": "Field values for the new document."},
		},
		["doctype", "values"],
	),
	setting="allow_writes",
)
def create_document(doctype: str, values: dict) -> dict:
	guard.check_doctype(doctype, _settings(), write=True)
	values = _clean_values(frappe.get_meta(doctype), values, allow_name=True)
	doc = frappe.get_doc({"doctype": doctype, **values})
	doc.insert()
	doc.apply_fieldlevel_read_permissions()
	return {"created": _summary(doc), "document": _compact(doc.as_dict())}


@mcp_tool(
	title="Update document",
	description=(
		"Change fields on an existing document. Only the fields given are changed. Giving a child table "
		"(a list) replaces its rows: rows left out are removed. To keep or change an existing row, include "
		"its 'name' plus only the fields you are changing; rows without 'name' are added. Submitted "
		"documents only accept fields marked allow_on_submit."
	),
	input_schema=_obj(
		{
			"doctype": _DOCTYPE,
			"name": _NAME,
			"values": {"type": "object", "description": "Fields to change and their new values."},
		},
		["doctype", "name", "values"],
	),
	idempotent=True,
	setting="allow_writes",
)
def update_document(doctype: str, name: str, values: dict) -> dict:
	guard.check_doctype(doctype, _settings(), write=True)
	if not values:
		raise ToolError("values is empty; nothing to change.")
	doc = frappe.get_doc(doctype, name)
	values = _clean_values(doc.meta, values, allow_name=False, existing=doc)
	doc.update(values)
	doc.save()
	doc.apply_fieldlevel_read_permissions()
	return {"updated": _summary(doc), "document": _compact(doc.as_dict())}


@mcp_tool(
	title="Submit document",
	description=(
		"Submit a draft document (docstatus 0 to 1). For accounting and stock documents this posts to the "
		"ledger. Check the draft with get_document first."
	),
	input_schema=_obj({"doctype": _DOCTYPE, "name": _NAME}, ["doctype", "name"]),
	destructive=True,
	setting="allow_submit",
)
def submit_document(doctype: str, name: str) -> dict:
	guard.check_doctype(doctype, _settings(), write=True)
	if not frappe.get_meta(doctype).is_submittable:
		raise ToolError(f"{doctype} is not a submittable DocType.")
	doc = frappe.get_doc(doctype, name)
	if doc.docstatus != 0:
		raise ToolError(f"{doctype} {name} is not a draft (docstatus {doc.docstatus}).")
	doc.submit()
	doc.apply_fieldlevel_read_permissions()
	return {"submitted": _summary(doc)}


@mcp_tool(
	title="Cancel document",
	description="Cancel a submitted document (docstatus 1 to 2). This reverses its ledger entries.",
	input_schema=_obj({"doctype": _DOCTYPE, "name": _NAME}, ["doctype", "name"]),
	destructive=True,
	setting="allow_cancel",
)
def cancel_document(doctype: str, name: str) -> dict:
	guard.check_doctype(doctype, _settings(), write=True)
	doc = frappe.get_doc(doctype, name)
	if doc.docstatus != 1:
		raise ToolError(f"{doctype} {name} is not submitted (docstatus {doc.docstatus}).")
	doc.cancel()
	doc.apply_fieldlevel_read_permissions()
	return {"cancelled": _summary(doc)}


@mcp_tool(
	title="Delete document",
	description="Permanently delete a draft or cancelled document. This cannot be undone.",
	input_schema=_obj({"doctype": _DOCTYPE, "name": _NAME}, ["doctype", "name"]),
	destructive=True,
	setting="allow_delete",
)
def delete_document(doctype: str, name: str) -> dict:
	guard.check_doctype(doctype, _settings(), write=True)
	if not frappe.db.exists(doctype, name):
		raise ToolError(f"{doctype} {name} does not exist.")
	frappe.delete_doc(doctype, name)
	return {"deleted": {"doctype": doctype, "name": name}}


# -- reports ----------------------------------------------------------------


@mcp_tool(
	title="List reports",
	description=(
		"List reports the connected user can run, such as 'General Ledger', 'Accounts Receivable' or "
		"'Stock Balance'. Filter by DocType or by text in the report name."
	),
	input_schema=_obj(
		{
			"doctype": {"type": "string", "description": "Only reports based on this DocType."},
			"text": {"type": "string", "description": "Only reports whose name contains this text."},
		}
	),
	read_only=True,
	idempotent=True,
)
def list_reports(doctype: str | None = None, text: str | None = None) -> dict:
	settings = _settings()
	filters: dict[str, Any] = {"disabled": 0}
	if doctype:
		filters["ref_doctype"] = doctype
	if text:
		filters["name"] = ["like", f"%{text}%"]

	# Reports restricted to roles: keep those that share a role with the user.
	user_roles = set(frappe.get_roles())
	report_roles: dict[str, set[str]] = {}
	for row in frappe.get_all("Has Role", filters={"parenttype": "Report"}, fields=["parent", "role"]):
		report_roles.setdefault(row.parent, set()).add(row.role)

	can_report: dict[str, bool] = {}
	reports = []
	for r in frappe.get_all(
		"Report",
		filters=filters,
		fields=["name", "ref_doctype", "report_type", "is_standard", "module"],
		order_by="name asc",
		limit=500,
	):
		if not _report_allowed(r, settings):
			continue
		if r.report_type == "Custom Report" and not _report_allowed(_source_report(r.name), settings):
			continue
		roles = report_roles.get(r.name)
		if roles and not roles & user_roles:
			continue
		if r.ref_doctype not in can_report:
			can_report[r.ref_doctype] = bool(frappe.has_permission(r.ref_doctype, "report"))
		if can_report[r.ref_doctype]:
			reports.append({k: r[k] for k in ("name", "ref_doctype", "report_type", "module")})
	return {"reports": reports}


def _source_report(report_name: str):
	"""The report a Custom Report is built on (itself for other types)."""
	from frappe.desk.query_report import get_reference_report

	return get_reference_report(frappe.get_doc("Report", report_name))


def _report_allowed(report, settings) -> bool:
	"""Standard reports only, unless the site allows custom SQL and script reports."""
	if not report.ref_doctype or guard.is_blocked(report.ref_doctype, settings):
		return False
	if report.report_type in ("Query Report", "Script Report") and report.is_standard != "Yes":
		return bool(settings.allow_custom_reports)
	return True


def _normalise_columns(columns: list) -> list[dict]:
	out = []
	for i, col in enumerate(columns or []):
		if isinstance(col, dict):
			out.append(
				{
					"fieldname": col.get("fieldname") or col.get("id") or f"col_{i}",
					"label": col.get("label") or col.get("fieldname"),
					"fieldtype": col.get("fieldtype") or "Data",
				}
			)
		else:  # legacy "Label:Fieldtype/Options:Width"
			parts = str(col).split(":")
			label = parts[0]
			fieldtype = parts[1].split("/")[0] if len(parts) > 1 and parts[1] else "Data"
			out.append(
				{"fieldname": frappe.scrub(label) or f"col_{i}", "label": label, "fieldtype": fieldtype}
			)
	return out


@mcp_tool(
	title="Run report",
	description=(
		"Run a query or script report and return its rows. 'filters' is an object, e.g. "
		'{"company": "Acme Ltd", "from_date": "2026-01-01", "to_date": "2026-03-31"}. '
		"If a required filter is missing, the error says which one."
	),
	input_schema=_obj(
		{
			"report_name": {"type": "string", "description": "Report name as shown by list_reports."},
			"filters": {"type": "object", "description": "Report filters."},
			"limit": {"type": "integer", "description": "Rows to return (capped by site setting)."},
		},
		["report_name"],
	),
	read_only=True,
	idempotent=True,
)
def run_report(report_name: str, filters: dict | None = None, limit: int | None = None) -> dict:
	settings = _settings()
	if not frappe.db.exists("Report", report_name):
		raise ToolError(f"No report called '{report_name}'. Use list_reports to find the exact name.")

	from frappe.desk.query_report import run

	# A Custom Report runs the report it is based on, so check both.
	for r in (frappe.get_doc("Report", report_name), _source_report(report_name)):
		if not _report_allowed(r, settings):
			raise ToolError(f"The report '{r.name}' is not available through MCP on this site.")

	data = run(report_name, filters=filters or {}, ignore_prepared_report=True)
	columns = _normalise_columns(data.get("columns"))
	names = [c["fieldname"] for c in columns]

	rows = []
	for row in data.get("result") or []:
		if isinstance(row, dict):
			rows.append(row)
		elif isinstance(row, list | tuple):
			rows.append(dict(zip(names, row, strict=False)))

	cap = guard.clamp_limit(limit, settings, default=int(settings.max_rows))
	return {
		"report": report_name,
		"columns": columns,
		"rows": rows[:cap],
		"total_rows": len(rows),
		"truncated": len(rows) > cap,
		"message": data.get("message"),
	}
