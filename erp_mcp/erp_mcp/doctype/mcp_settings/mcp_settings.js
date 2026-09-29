// Copyright (c) 2026, Node4 and contributors
// For license information, please see license.txt

frappe.ui.form.on("MCP Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Enable OAuth for MCP clients"), () => {
			frappe.confirm(
				__(
					"This turns on OAuth discovery and dynamic client registration in OAuth Settings, so MCP clients such as Claude can register and ask users to sign in. Continue?"
				),
				() =>
					frappe.call("erp_mcp.setup.enable_oauth_discovery").then((r) => {
						frappe.msgprint(r.message);
					})
			);
		});
	},
});
