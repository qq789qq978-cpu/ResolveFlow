"""Sync bridge for synchronous graph nodes to official async MCP sessions."""
import asyncio
import json
import os
from pathlib import Path
import sys
import sysconfig
from datetime import timedelta
from mcp import ClientSession, StdioServerParameters
import mcp
from mcp.client.stdio import stdio_client

ALLOWED = {"search_policy", "lookup_order"}

async def exchange(order_id, owner, calls):
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
    env.update(RESOLVEFLOW_ORDER_ID=order_id, RESOLVEFLOW_OWNER=owner, PYTHONPATH=sysconfig.get_paths()["purelib"])
    env['RF_TASK_PARENT_PID']=str(os.getpid())
    env['RF_DB_MCP']='1'
    # Forward only retrieval configuration; model credentials stay out of the child.
    for key in ("RETRIEVAL_MODE", "SEMANTIC_WEIGHT", "EMBEDDING_URL", "RF_DB_APPLICATION_NAME"):
        if key in os.environ:
            env[key] = os.environ[key]
    bootstrap = "import site,runpy,sys,pathlib; site.addsitedir(sys.argv[1]); sys.path.insert(0,str(pathlib.Path(sys.argv[2]).parent)); runpy.run_path(sys.argv[2],run_name='__main__')"
    if os.getenv("DATABASE_URL"):
        from runtime_db import runtime_dsn
        url = os.getenv('READONLY_DATABASE_URL')
        if not url and os.getenv('RF_ENFORCE_DB_ROLES') == '1':
            raise ValueError('Restricted MCP requires READONLY_DATABASE_URL')
        url = url or os.environ["DATABASE_URL"]
        # Bound SQL inside the child before the 15s MCP client deadline. Killing
        # a blocked stdio child alone can leave its PostgreSQL query waiting.
        # Preserve existing options (including test-schema search_path).
        env["DATABASE_URL"] = runtime_dsn(url,application='resolveflow-mcp',mcp=True)
    parameters = StdioServerParameters(command=getattr(sys, "_base_executable", sys.executable), args=["-c", bootstrap, str(Path(mcp.__file__).parent.parent), str(Path(__file__).with_name("mcp_server.py"))], env=env)
    async with asyncio.timeout(30):
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=15)) as session:
                await session.initialize()
                discovered = await session.list_tools()
                names = {tool.name for tool in discovered.tools}
                if not ALLOWED.issubset(names):
                    raise RuntimeError("MCP server missing required tools")
                results = []
                for name, arguments in calls:
                    if name not in ALLOWED:
                        raise ValueError("Tool not allowed")
                    result = await session.call_tool(name, arguments)
                    if result.isError:
                        raise RuntimeError(f"MCP tool failed: {name}")
                    content = "".join(item.text for item in result.content if item.type == "text")
                    # SDK serializes list results as separate text blocks; use structured result.
                    if result.structuredContent is not None:
                        value = result.structuredContent
                        value = value.get("result", value)
                    else:
                        value = json.loads(content)
                    results.append(value)
                return results

def call_tools(order_id, owner, calls):
    return asyncio.run(exchange(order_id, owner, calls))
