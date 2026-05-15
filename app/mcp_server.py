"""
MCP Server for DevGordon
========================
Exposes DevGordon's DevOps tools as MCP (Model Context Protocol) endpoints.
This allows external LLM clients (Claude, other agents, etc.) to call your
infrastructure tools without knowing the FastAPI internals.

MCP is like LSP for servers — it's a standardized interface for tool capabilities.

To use:
- Add this to your FastAPI app startup
- External tools connect via stdio or HTTP transport
- They can discover/call your tools via MCP protocol
"""

from app.tools import TOOL_DEFINITIONS, execute_tool


class MCPServer:
    """Simple MCP server interface for DevGordon tools."""

    def __init__(self):
        self.tools = TOOL_DEFINITIONS

    def list_tools(self) -> dict:
        """MCP: List all available tools."""
        return {
            "tools": [
                {
                    "name": func["name"],
                    "description": func["description"],
                    "inputSchema": func.get("parameters", {}),
                }
                for t in self.tools
                for func in (dict(t["function"]),)  # type: ignore[arg-type]
            ]
        }

    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """MCP: Call a tool by name with arguments."""
        result = execute_tool(tool_name, arguments)
        return {
            "content": [{"type": "text", "text": result.get("output", str(result))}],
            "isError": not result.get("success", False),
        }


# To expose via MCP HTTP transport:
#
# @app.get("/mcp/tools")
# async def mcp_list_tools():
#     """MCP endpoint: list tools."""
#     mcp = MCPServer()
#     return mcp.list_tools()
#
# @app.post("/mcp/call")
# async def mcp_call_tool(body: dict):
#     """MCP endpoint: call a tool."""
#     mcp = MCPServer()
#     tool_name = body.get("tool")
#     args = body.get("arguments", {})
#     return mcp.call_tool(tool_name, args)
#
# For stdio transport, you'd wrap this differently (see mcp docs).
