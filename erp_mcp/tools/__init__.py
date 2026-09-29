"""Tool registry.

Built-in tools live in ``erp_mcp.tools.builtin``. Other Frappe apps add their
own tools by listing functions in their ``hooks.py``::

    erp_mcp_tools = ["my_app.mcp.overdue_by_region"]

and marking each function with the ``mcp_tool`` decorator::

    from erp_mcp.tools import mcp_tool

    @mcp_tool(
        description="Overdue sales invoices grouped by territory.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        read_only=True,
    )
    def overdue_by_region():
        ...

Tools run as the connected user, so they must use Frappe's permission-aware
APIs (``frappe.get_list``, ``doc.check_permission``) rather than raw SQL or
``ignore_permissions``.
"""

from __future__ import annotations

from collections.abc import Callable

from erp_mcp.protocol import Tool, ToolError

__all__ = ["Tool", "ToolError", "mcp_tool", "get_tools"]

_ATTR = "_erp_mcp_tool"
HOOK = "erp_mcp_tools"


def mcp_tool(
	*,
	description: str,
	input_schema: dict,
	name: str | None = None,
	title: str | None = None,
	read_only: bool = False,
	destructive: bool = False,
	idempotent: bool = False,
	setting: str | None = None,
) -> Callable:
	"""Mark a function as an MCP tool.

	``setting`` names a Check field on MCP Settings that must be ticked for the
	tool to be offered, for example ``allow_submit``.
	"""

	def wrap(fn: Callable) -> Callable:
		tool = Tool(
			name=name or fn.__name__,
			description=description,
			input_schema=input_schema,
			handler=fn,
			title=title,
			read_only=read_only,
			destructive=destructive,
			idempotent=idempotent,
		)
		setattr(fn, _ATTR, (tool, setting))
		return fn

	return wrap


def _builtin() -> list[Callable]:
	from erp_mcp.tools import builtin

	return [getattr(builtin, n) for n in builtin.__all__]


def get_tools(settings) -> dict[str, Tool]:
	"""Every tool the site offers, filtered by MCP Settings."""
	import frappe

	functions = _builtin()
	for path in frappe.get_hooks(HOOK) or []:
		try:
			functions.append(frappe.get_attr(path))
		except Exception:
			frappe.log_error(title="ERP MCP: could not load tool", message=path)

	tools: dict[str, Tool] = {}
	for fn in functions:
		marker = getattr(fn, _ATTR, None)
		if marker is None:
			frappe.log_error(title="ERP MCP: hook entry is not an @mcp_tool", message=repr(fn))
			continue
		tool, setting = marker
		if setting and not settings.get(setting):
			continue
		if tool.name in tools:
			frappe.log_error(title="ERP MCP: duplicate tool name", message=tool.name)
			continue
		tools[tool.name] = tool
	return tools
