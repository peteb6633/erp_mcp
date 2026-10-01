"""Guard the deliberate oddities recorded in docs/decisions/.

Each test pins one "trap": code that looks wrong, odd or redundant but is there
on purpose. If a test fails, read the note it names before changing anything.
If the change is right, update the note (or write a new one) and this test in
the same commit.

These tests read the source rather than running it, so they need no Frappe
install and run anywhere: `pytest tests/`.
"""

import ast
import json
import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "erp_mcp"
NOTE_01 = "docs/decisions/2026-09-30-erp-mcp-v0.1.md"
NOTE_SCOPE = "docs/decisions/2026-10-01-submit-cancel-off-by-default.md"
NOTE_WHOAMI = "docs/decisions/2026-10-01-whoami-limit.md"


def tree(rel: str) -> ast.Module:
	return ast.parse((APP / rel).read_text())


def function(module: ast.Module, name: str) -> ast.FunctionDef:
	for node in ast.walk(module):
		if isinstance(node, ast.FunctionDef) and node.name == name:
			return node
	raise AssertionError(f"function {name} not found")


def calls(node: ast.AST) -> list[str]:
	"""Dotted names of every call under node, in source order."""
	out = []
	for n in ast.walk(node):
		if isinstance(n, ast.Call):
			out.append((n.lineno, n.col_offset, ast.unparse(n.func)))
	return [name for _, _, name in sorted(out)]


def python_files():
	return [p for p in APP.rglob("*.py")] + [p for p in (ROOT / "tests").glob("*.py")]


# -- api.py ---------------------------------------------------------------------


def test_header_auth_only_check_is_kept():
	"""`frappe.session.sid != frappe.session.user` tells header auth from a browser
	session. It looks like a typo; it closed a security hole. See docs/decisions/2026-09-30-erp-mcp-v0.1.md."""
	src = ast.unparse(function(tree("api.py"), "mcp"))
	assert "frappe.session.sid != frappe.session.user" in src, f"session check removed; see {NOTE_01}"


def test_invoke_rolls_back_before_logging():
	"""In _invoke's finally: rollback, then Error Log, then audit row. Logging first
	loses the log in the rollback. See docs/decisions/2026-09-30-erp-mcp-v0.1.md."""
	invoke = function(tree("api.py"), "_invoke")
	tries = [n for n in ast.walk(invoke) if isinstance(n, ast.Try) and n.finalbody]
	assert tries, "_invoke lost its try/finally"
	order = calls(ast.Module(body=tries[0].finalbody, type_ignores=[]))
	want = ["frappe.db.rollback", "frappe.log_error", "_audit"]
	pos = [order.index(w) for w in want if w in order]
	assert len(pos) == 3 and pos == sorted(pos), f"finally order is {order}, want {want}; see {NOTE_01}"


def test_invoke_captures_traceback_in_except():
	"""The traceback must be taken inside `except Exception`; by `finally` it has been
	replaced by the ToolError. See docs/decisions/2026-09-30-erp-mcp-v0.1.md."""
	invoke = function(tree("api.py"), "_invoke")
	handlers = [
		h
		for n in ast.walk(invoke)
		if isinstance(n, ast.Try)
		for h in n.handlers
		if h.type is not None and ast.unparse(h.type) == "Exception"
	]
	assert handlers, "_invoke lost its `except Exception` handler"
	assert "frappe.get_traceback" in calls(handlers[0]), f"traceback not captured in except; see {NOTE_01}"


def test_401_uses_frappe_resource_url():
	"""The WWW-Authenticate header must use get_resource_url(), not the request host,
	to match Frappe's discovery documents. See docs/decisions/2026-09-30-erp-mcp-v0.1.md."""
	src = ast.unparse(function(tree("api.py"), "_unauthorised"))
	assert "get_resource_url()" in src and "host_url" not in src, f"see {NOTE_01}"


# -- guard.py -------------------------------------------------------------------


def test_request_cache_does_not_touch_local_dict():
	"""`frappe.local.__dict__` failed on a live site; use getattr. See docs/decisions/2026-09-30-erp-mcp-v0.1.md."""
	for path in APP.rglob("*.py"):
		for node in ast.walk(ast.parse(path.read_text())):
			if isinstance(node, ast.Attribute) and node.attr == "__dict__":
				assert ast.unparse(node.value) != "frappe.local", f"{path.name}:{node.lineno}; see {NOTE_01}"


def test_oauth_allow_list_matches_ids_only():
	"""Anyone can register a client called 'Claude', so names are never trusted.
	See docs/decisions/2026-09-30-erp-mcp-v0.1.md."""
	fn = function(tree("guard.py"), "allowed_client")
	code = ast.unparse(ast.Module(body=fn.body[1:], type_ignores=[]))  # skip the docstring
	assert "app_name" not in code and "OAuth Client" not in code, (
		f"allow-list matches names again; see {NOTE_01}"
	)


def _guard_defaults() -> dict:
	for node in ast.walk(tree("guard.py")):
		if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "DEFAULTS" for t in node.targets):
			return {k.arg: ast.literal_eval(k.value) for k in node.value.keywords}
	raise AssertionError("DEFAULTS not found in guard.py")


def _json_defaults() -> dict:
	path = APP / "erp_mcp" / "doctype" / "mcp_settings" / "mcp_settings.json"
	return {f["fieldname"]: f.get("default") for f in json.loads(path.read_text())["fields"]}


def test_defaults_agree_between_guard_and_settings_json():
	"""A site with no settings record must behave like a fresh install. See docs/decisions/2026-10-01-submit-cancel-off-by-default.md."""
	code, js = _guard_defaults(), _json_defaults()
	for key, value in code.items():
		if key in js and js[key] is not None and isinstance(value, int):
			assert str(value) == str(js[key]), f"{key}: guard.py={value}, json={js[key]}; see {NOTE_SCOPE}"


def test_v1_scope_is_read_and_draft_writes():
	"""Pete chose read plus draft writes for v1: submit, cancel and delete off by
	default. See docs/decisions/2026-10-01-submit-cancel-off-by-default.md."""
	d = _guard_defaults()
	assert (d["allow_writes"], d["allow_submit"], d["allow_cancel"], d["allow_delete"]) == (1, 0, 0, 0)


# -- tools/builtin.py -----------------------------------------------------------

DEPRECATED_LIST_ARGS = {"limit_page_length", "limit_start", "start", "page_length"}


def test_get_list_uses_limit_and_offset():
	"""v16 deprecates limit_page_length/limit_start/start/page_length; they go in v17.
	See docs/decisions/2026-10-01-whoami-limit.md."""
	for path in python_files():
		for node in ast.walk(ast.parse(path.read_text())):
			if isinstance(node, ast.Call) and ast.unparse(node.func).split(".")[-1] in (
				"get_list",
				"get_all",
			):
				bad = DEPRECATED_LIST_ARGS & {k.arg for k in node.keywords}
				assert not bad, f"{path.name}:{node.lineno} uses {bad}; see {NOTE_WHOAMI}"


def test_describe_doctype_hides_read_only_by_default():
	"""Saves the model's context; include_read_only=True brings them back. See docs/decisions/2026-09-30-erp-mcp-v0.1.md."""
	fn = function(tree("tools/builtin.py"), "describe_doctype")
	defaults = dict(
		zip([a.arg for a in fn.args.args][-len(fn.args.defaults) :], fn.args.defaults, strict=True)
	)
	assert ast.literal_eval(defaults["include_read_only"]) is False


# -- Python version -------------------------------------------------------------


def test_code_stays_valid_before_python_314():
	"""Ruff for py314 strips the brackets from `except (A, B):`, which is a syntax
	error before 3.14. Keep ruff's target at py311. See docs/decisions/2026-09-30-erp-mcp-v0.1.md."""
	config = tomllib.loads((ROOT / "pyproject.toml").read_text())
	assert config["tool"]["ruff"]["target-version"] == "py311", f"see {NOTE_01}"
	for path in python_files():
		for node in ast.walk(ast.parse(path.read_text())):
			if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Tuple):
				line = path.read_text().splitlines()[node.lineno - 1]
				assert "except (" in line, f"{path.name}:{node.lineno} unbracketed except; see {NOTE_01}"
