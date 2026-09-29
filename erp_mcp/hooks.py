app_name = "erp_mcp"
app_title = "ERP MCP"
app_publisher = "Node4"
app_description = "Connects MCP clients such as Claude to ERPNext, acting as the signed-in user."
app_email = "pbright@aramoko.com"
app_license = "mit"

required_apps = ["frappe"]

after_install = "erp_mcp.setup.after_install"

# Old audit rows are removed by Frappe's Log Settings job after this many days.
default_log_clearing_doctypes = {"MCP Audit Log": 90}

# Other apps add tools with the same hook in their own hooks.py:
# erp_mcp_tools = ["my_app.mcp.my_tool"]
