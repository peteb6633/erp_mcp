"""Quick check that a site's MCP endpoint works, using a Frappe API key.

    python scripts/smoke_test.py https://your-site.example --key <api_key> --secret <api_secret>

Needs only the Python standard library. It reads data and changes nothing.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request


def rpc(url: str, auth: str, method: str, params: dict | None = None, msg_id: int = 1):
	body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
	if params is not None:
		body["params"] = params
	req = urllib.request.Request(
		url,
		data=json.dumps(body).encode(),
		headers={"Authorization": auth, "Content-Type": "application/json", "Accept": "application/json"},
		method="POST",
	)
	with urllib.request.urlopen(req, timeout=30) as resp:
		return json.load(resp)


def main() -> int:
	parser = argparse.ArgumentParser(
		description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
	)
	parser.add_argument("site", help="Site URL, e.g. https://acme.frappe.cloud")
	parser.add_argument("--key", required=True)
	parser.add_argument("--secret", required=True)
	args = parser.parse_args()

	base = args.site.rstrip("/")
	url = f"{base}/api/method/erp_mcp.api.mcp"
	auth = f"token {args.key}:{args.secret}"

	try:
		meta = urllib.request.urlopen(f"{base}/.well-known/oauth-protected-resource", timeout=30)
		resource = json.load(meta)
		print(f"OAuth discovery: on (resource {resource.get('resource')})")
		if base.startswith("https://") and str(resource.get("resource", "")).startswith("http://"):
			print("  Warning: the site reports an http:// resource URL behind https. Claude may refuse it.")
	except urllib.error.HTTPError as e:
		print(f"OAuth discovery: off (HTTP {e.code}). Claude connectors need it; see MCP Settings.")

	init = rpc(url, auth, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
	info = init["result"]["serverInfo"]
	print(f"Server: {info['name']} {info['version']}, protocol {init['result']['protocolVersion']}")

	tools = rpc(url, auth, "tools/list", msg_id=2)["result"]["tools"]
	print(f"Tools ({len(tools)}): {', '.join(t['name'] for t in tools)}")

	who = rpc(url, auth, "tools/call", {"name": "whoami", "arguments": {}}, msg_id=3)["result"]
	print("whoami:", who["content"][0]["text"])

	custs = rpc(
		url,
		auth,
		"tools/call",
		{"name": "list_documents", "arguments": {"doctype": "Customer", "limit": 3}},
		msg_id=4,
	)["result"]
	print("Customers:", custs["content"][0]["text"][:300])
	return 1 if custs.get("isError") or who.get("isError") else 0


if __name__ == "__main__":
	sys.exit(main())
