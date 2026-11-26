-- An MCP tool's "Only reads" (backend/mcp_connect.py `only_reads`): JSON [{name, by, at}], the tool names an
-- administrator marked as only reading though the server sends no readOnlyHint for them. A bot at Read may call them
-- through the proxy (backend/tool_proxy.py). NULL for none.
ALTER TABLE tools ADD COLUMN mcp_only_reads TEXT;
