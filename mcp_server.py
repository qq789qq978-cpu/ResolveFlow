"""Standalone stdio MCP server. stdout belongs exclusively to JSON-RPC."""
import os
from mcp.server.fastmcp import FastMCP
from support_data import ORDERS
from rag import retrieve

server = FastMCP("resolveflow-support")

@server.tool()
def search_policy(query: str) -> list[dict]:
    """Retrieve policy document chunks with source, version, line numbers and BM25 scores."""
    if len(query) > 4000:
        raise ValueError("Query too long")
    return retrieve(query)

@server.tool()
def lookup_order() -> dict:
    """Read the order bound by the trusted host to this server process."""
    if os.getenv("DATABASE_URL"):
        from storage import Store
        return Store(os.environ["DATABASE_URL"]).order(os.environ.get("RESOLVEFLOW_ORDER_ID", ""), os.environ.get("RESOLVEFLOW_OWNER", ""))
    order = ORDERS.get(os.environ.get("RESOLVEFLOW_ORDER_ID", ""), {})
    return order if order.get("owner") == os.environ.get("RESOLVEFLOW_OWNER") else {}

if __name__ == "__main__":
    server.run(transport="stdio")
