"""Install-time and admin helpers."""

import frappe
from frappe import _

OAUTH_FLAGS = (
	"show_auth_server_metadata",
	"show_protected_resource_metadata",
	"enable_dynamic_client_registration",
)


def after_install():
	settings = frappe.get_single("MCP Settings")
	settings.save(ignore_permissions=True)  # store the JSON defaults
	missing = [f for f in OAUTH_FLAGS if not frappe.db.get_single_value("OAuth Settings", f)]
	if missing:
		print(
			"ERP MCP: OAuth discovery is off on this site, so Claude connectors cannot sign in yet. "
			"Open MCP Settings and click 'Enable OAuth for MCP clients'."
		)


@frappe.whitelist(methods=["POST"])
def enable_oauth_discovery() -> str:
	"""Turn on the OAuth Settings that MCP clients need to register and sign in."""
	frappe.only_for("System Manager")
	oauth = frappe.get_single("OAuth Settings")
	for field in OAUTH_FLAGS:
		oauth.set(field, 1)
	oauth.save()
	return _("OAuth discovery and dynamic client registration are on. MCP endpoint: {0}").format(
		frappe.utils.get_url("/api/method/erp_mcp.api.mcp")
	)
