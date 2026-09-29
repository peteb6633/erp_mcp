# Copyright (c) 2026, Node4 and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class MCPSettings(Document):
	@property
	def endpoint(self) -> str:
		return frappe.utils.get_url("/api/method/erp_mcp.api.mcp")

	def validate(self):
		if not 1 <= (self.max_rows or 0) <= 1000:
			frappe.throw(_("Maximum Rows per Call must be between 1 and 1000."))
