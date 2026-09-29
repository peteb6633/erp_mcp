"""MCP (Model Context Protocol) message handling over JSON-RPC 2.0.

This module knows nothing about Frappe, so it can be unit tested on its own.
The HTTP endpoint in ``erp_mcp.api`` feeds it the raw request body and turns
the ``Reply`` it gets back into an HTTP response.

Transport: MCP "Streamable HTTP", answering every request with a single
``application/json`` body. The server is stateless: it issues no session ID and
never streams, which the specification allows.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

# Newest first. The first entry is what we offer when the client asks for a
# version we do not know.
SUPPORTED_VERSIONS: tuple[str, ...] = ("2025-11-25", "2025-06-18", "2025-03-26")
LATEST_VERSION = SUPPORTED_VERSIONS[0]

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class ToolError(Exception):
	"""An error the model should see and can act on (bad input, no permission).

	Raised inside a tool. It is returned as a tool result with ``isError: true``
	rather than as a protocol error, so the model can read it and try again.
	"""


@dataclass
class Tool:
	name: str
	description: str
	input_schema: dict
	handler: Callable[..., Any]
	title: str | None = None
	read_only: bool = False
	destructive: bool = False
	idempotent: bool = False

	def definition(self) -> dict:
		annotations = {
			"readOnlyHint": self.read_only,
			"destructiveHint": self.destructive,
			"idempotentHint": self.idempotent,
			"openWorldHint": False,
		}
		out = {
			"name": self.name,
			"description": self.description,
			"inputSchema": self.input_schema,
			"annotations": annotations,
		}
		if self.title:
			out["title"] = self.title
			annotations["title"] = self.title
		return out


@dataclass
class Reply:
	"""What the HTTP layer should send back. ``body`` of None means no body."""

	status: int
	body: dict | None = None


def _default_dumps(value: Any) -> str:
	return json.dumps(value, default=str, ensure_ascii=False, indent=1)


def _error(msg_id: Any, code: int, message: str) -> dict:
	return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _result(msg_id: Any, result: dict) -> dict:
	return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def negotiate_version(requested: Any) -> str:
	if isinstance(requested, str) and requested in SUPPORTED_VERSIONS:
		return requested
	return LATEST_VERSION


_JSON_TYPES: dict[str, Callable[[Any], bool]] = {
	"string": lambda v: isinstance(v, str),
	"integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
	"number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
	"boolean": lambda v: isinstance(v, bool),
	"object": lambda v: isinstance(v, dict),
	"array": lambda v: isinstance(v, list),
	"null": lambda v: v is None,
}


def validate_arguments(schema: Mapping, args: Mapping) -> list[str]:
	"""Check top-level arguments against a tool's JSON schema.

	Covers what our tools use: required keys, unknown keys, basic types and
	enums. Deeper checks are left to Frappe, which validates documents itself.
	"""
	problems: list[str] = []
	props: Mapping = schema.get("properties", {})

	for key in schema.get("required", []):
		if key not in args or args[key] is None:
			problems.append(f"'{key}' is required.")

	if schema.get("additionalProperties") is False:
		for key in args:
			if key not in props:
				problems.append(f"'{key}' is not a known argument.")

	for key, value in args.items():
		spec = props.get(key)
		if not spec or value is None:
			continue
		types = spec.get("type")
		if types:
			types = [types] if isinstance(types, str) else list(types)
			if not any(_JSON_TYPES.get(t, lambda _v: True)(value) for t in types):
				problems.append(f"'{key}' must be {' or '.join(types)}.")
				continue
		if "enum" in spec and value not in spec["enum"]:
			problems.append(f"'{key}' must be one of {spec['enum']}.")

	return problems


class Server:
	"""Dispatches MCP requests.

	``tools`` returns the tools available to the current caller; it is a
	function so the list can depend on who is asking and on site settings.
	``invoke`` runs a tool; the HTTP layer uses it to add transactions and
	audit logging. By default it just calls the handler.
	"""

	def __init__(
		self,
		*,
		name: str,
		version: str,
		tools: Callable[[], Mapping[str, Tool]],
		invoke: Callable[[Tool, dict], Any] | None = None,
		instructions: str | None = None,
		title: str | None = None,
		dumps: Callable[[Any], str] = _default_dumps,
	):
		self.name = name
		self.version = version
		self.title = title
		self.tools = tools
		self.invoke = invoke or (lambda tool, args: tool.handler(**args))
		self.instructions = instructions
		self.dumps = dumps

	# -- entry point -------------------------------------------------------

	def handle(self, raw: bytes | str, protocol_header: str | None = None) -> Reply:
		if protocol_header and protocol_header not in SUPPORTED_VERSIONS:
			return Reply(
				400,
				_error(None, INVALID_REQUEST, f"Unsupported MCP-Protocol-Version: {protocol_header}"),
			)

		try:
			msg = json.loads(raw or b"")
		except (ValueError, UnicodeDecodeError):
			return Reply(400, _error(None, PARSE_ERROR, "Body is not valid JSON."))

		if isinstance(msg, list):
			return Reply(400, _error(None, INVALID_REQUEST, "Batch requests are not supported."))
		if not isinstance(msg, dict):
			return Reply(400, _error(None, INVALID_REQUEST, "Body must be a JSON-RPC object."))

		# A response from the client (we never send requests) or a notification:
		# acknowledge and do nothing.
		if "method" not in msg or "id" not in msg:
			return Reply(202)

		msg_id = msg["id"]
		if msg.get("jsonrpc") != "2.0" or not isinstance(msg_id, str | int) or isinstance(msg_id, bool):
			return Reply(400, _error(None, INVALID_REQUEST, "Invalid JSON-RPC request."))

		params = msg.get("params") or {}
		if not isinstance(params, dict):
			return Reply(200, _error(msg_id, INVALID_PARAMS, "params must be an object."))

		method = msg["method"]
		handler = {
			"initialize": self._initialize,
			"ping": lambda _p: {},
			"tools/list": self._tools_list,
			"tools/call": self._tools_call,
		}.get(method)
		if handler is None:
			return Reply(200, _error(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}"))

		try:
			return Reply(200, _result(msg_id, handler(params)))
		except _ProtocolError as e:
			return Reply(200, _error(msg_id, e.code, e.message))

	# -- methods -----------------------------------------------------------

	def _initialize(self, params: dict) -> dict:
		info = {"name": self.name, "version": self.version}
		if self.title:
			info["title"] = self.title
		out = {
			"protocolVersion": negotiate_version(params.get("protocolVersion")),
			"capabilities": {"tools": {"listChanged": False}},
			"serverInfo": info,
		}
		if self.instructions:
			out["instructions"] = self.instructions
		return out

	def _tools_list(self, _params: dict) -> dict:
		return {"tools": [t.definition() for t in self.tools().values()]}

	def _tools_call(self, params: dict) -> dict:
		name = params.get("name")
		args = params.get("arguments") or {}
		if not isinstance(name, str):
			raise _ProtocolError(INVALID_PARAMS, "Tool name is required.")
		if not isinstance(args, dict):
			raise _ProtocolError(INVALID_PARAMS, "arguments must be an object.")

		tool = self.tools().get(name)
		if tool is None:
			raise _ProtocolError(INVALID_PARAMS, f"Unknown tool: {name}")

		if problems := validate_arguments(tool.input_schema, args):
			return self._tool_result("Invalid arguments: " + " ".join(problems), is_error=True)

		try:
			value = self.invoke(tool, args)
		except ToolError as e:
			return self._tool_result(str(e), is_error=True)
		except Exception as e:  # never leak a traceback to the client
			return self._tool_result(f"Internal error in {name}: {type(e).__name__}", is_error=True)

		return self._tool_result(value)

	def _tool_result(self, value: Any, is_error: bool = False) -> dict:
		text = value if isinstance(value, str) else self.dumps(value)
		return {"content": [{"type": "text", "text": text}], "isError": is_error}


class _ProtocolError(Exception):
	def __init__(self, code: int, message: str):
		super().__init__(message)
		self.code = code
		self.message = message
