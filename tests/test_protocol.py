"""Tests for the framework-free MCP protocol layer. Run with: pytest tests/"""

import importlib.util
import json
import pathlib
import sys

import pytest

# Load protocol.py directly so the tests need no Frappe install.
_path = pathlib.Path(__file__).resolve().parents[1] / "erp_mcp" / "protocol.py"
_spec = importlib.util.spec_from_file_location("protocol", _path)
protocol = importlib.util.module_from_spec(_spec)
sys.modules["protocol"] = protocol
_spec.loader.exec_module(protocol)

Server, Tool, ToolError = protocol.Server, protocol.Tool, protocol.ToolError


def _echo(text: str, times: int = 1):
	return {"echo": text * times}


def _boom():
	raise ToolError("customer not found")


def _crash():
	raise RuntimeError("secret internal detail")


TOOLS = {
	"echo": Tool(
		name="echo",
		description="Echo text.",
		input_schema={
			"type": "object",
			"properties": {"text": {"type": "string"}, "times": {"type": "integer"}},
			"required": ["text"],
			"additionalProperties": False,
		},
		handler=_echo,
		read_only=True,
	),
	"boom": Tool(name="boom", description="", input_schema={"type": "object"}, handler=_boom),
	"crash": Tool(name="crash", description="", input_schema={"type": "object"}, handler=_crash),
}


@pytest.fixture
def server():
	return Server(name="t", version="0.0.1", tools=lambda: TOOLS, instructions="hi")


def call(server, payload, header=None):
	return server.handle(json.dumps(payload), header)


def rpc(method, params=None, msg_id=1):
	msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
	if params is not None:
		msg["params"] = params
	return msg


def test_initialize_echoes_known_version(server):
	r = call(server, rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}))
	assert r.status == 200
	res = r.body["result"]
	assert res["protocolVersion"] == "2025-06-18"
	assert res["capabilities"] == {"tools": {"listChanged": False}}
	assert res["serverInfo"]["name"] == "t"
	assert res["instructions"] == "hi"


def test_initialize_unknown_version_gets_latest(server):
	r = call(server, rpc("initialize", {"protocolVersion": "1999-01-01"}))
	assert r.body["result"]["protocolVersion"] == protocol.LATEST_VERSION


def test_notification_is_accepted_without_body(server):
	r = call(server, {"jsonrpc": "2.0", "method": "notifications/initialized"})
	assert (r.status, r.body) == (202, None)


def test_client_response_is_accepted(server):
	r = call(server, {"jsonrpc": "2.0", "id": 5, "result": {}})
	assert r.status == 202


def test_ping(server):
	assert call(server, rpc("ping")).body["result"] == {}


def test_tools_list_has_schema_and_annotations(server):
	tools = call(server, rpc("tools/list")).body["result"]["tools"]
	echo = next(t for t in tools if t["name"] == "echo")
	assert echo["inputSchema"]["required"] == ["text"]
	assert echo["annotations"]["readOnlyHint"] is True


def test_tools_call_success(server):
	r = call(server, rpc("tools/call", {"name": "echo", "arguments": {"text": "ab", "times": 2}}))
	res = r.body["result"]
	assert res["isError"] is False
	assert json.loads(res["content"][0]["text"]) == {"echo": "abab"}


def test_missing_required_argument_is_tool_error(server):
	res = call(server, rpc("tools/call", {"name": "echo", "arguments": {}})).body["result"]
	assert res["isError"] is True
	assert "'text' is required" in res["content"][0]["text"]


def test_wrong_type_and_unknown_argument(server):
	res = call(server, rpc("tools/call", {"name": "echo", "arguments": {"text": 1, "x": 2}})).body["result"]
	text = res["content"][0]["text"]
	assert res["isError"] is True
	assert "'text' must be string" in text
	assert "'x' is not a known argument" in text


def test_bool_is_not_integer(server):
	res = call(server, rpc("tools/call", {"name": "echo", "arguments": {"text": "a", "times": True}})).body[
		"result"
	]
	assert res["isError"] is True


def test_tool_error_is_shown_to_model(server):
	res = call(server, rpc("tools/call", {"name": "boom"})).body["result"]
	assert res == {"content": [{"type": "text", "text": "customer not found"}], "isError": True}


def test_unexpected_exception_does_not_leak(server):
	res = call(server, rpc("tools/call", {"name": "crash"})).body["result"]
	assert res["isError"] is True
	assert "secret" not in res["content"][0]["text"]


def test_unknown_tool_is_protocol_error(server):
	r = call(server, rpc("tools/call", {"name": "nope"}))
	assert r.body["error"]["code"] == protocol.INVALID_PARAMS


def test_unknown_method(server):
	r = call(server, rpc("resources/list"))
	assert r.body["error"]["code"] == protocol.METHOD_NOT_FOUND


def test_parse_error(server):
	r = server.handle(b"{not json")
	assert r.status == 400
	assert r.body["error"]["code"] == protocol.PARSE_ERROR


def test_batch_rejected(server):
	r = call(server, [rpc("ping")])
	assert r.status == 400
	assert r.body["error"]["code"] == protocol.INVALID_REQUEST


def test_bad_protocol_header(server):
	r = call(server, rpc("ping"), header="1999-01-01")
	assert r.status == 400


def test_good_protocol_header(server):
	assert call(server, rpc("ping"), header="2025-06-18").status == 200


def test_invalid_id_rejected(server):
	assert call(server, rpc("ping", msg_id=None)).status == 400
	assert call(server, rpc("ping", msg_id=True)).status == 400


def test_custom_invoke_is_used(server):
	seen = []

	def invoke(tool, args):
		seen.append(tool.name)
		return tool.handler(**args)

	s = Server(name="t", version="1", tools=lambda: TOOLS, invoke=invoke)
	call(s, rpc("tools/call", {"name": "echo", "arguments": {"text": "x"}}))
	assert seen == ["echo"]
