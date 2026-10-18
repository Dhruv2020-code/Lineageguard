"""LineageGuard MCP server: exposes the same graph tools the in-app agent uses.

Any MCP client (Claude Desktop, Cursor, MCP Inspector, ...) can now pick and call
these tools against the FalkorDB lineage graph.

Run (stdio):  python mcp_server.py
Inspect:      npx @modelcontextprotocol/inspector python mcp_server.py
"""
from mcp.server.fastmcp import FastMCP

from app import tools

mcp = FastMCP("lineageguard")

for _tool in tools.TOOLS:
    # each tool keeps its name, type hints and docstring -> MCP tool schema
    mcp.tool(name=_tool.__name__, description=(_tool.__doc__ or "").strip())(_tool)

if __name__ == "__main__":
    mcp.run()